"""Durable, read-only merchant reconciliation. Absence is never payment failure."""

import asyncio
import json
import time

from ..controller import DomainError
from ..models import SessionView
from ..observability import emit, error_fields, traced
from ..providers.blinkit import normalized
from ..providers.blinkit_identity import offer_cart_name


def mismatch_reason(order, view, method, baseline, started_at):
    if not order.get("order_id") or order.get("history_id", order["order_id"]) in baseline:
        return "identity"
    if order.get("provider") != view.review.provider.value:
        return "provider"
    placed = order.get("placed_at")
    if not isinstance(placed, (float, int)) or not started_at - 60 <= placed <= time.time() + 60:
        return "placed_at"
    if order.get("total_paise") != view.review.total_paise:
        return "total"
    expected_address = normalized(view.review.address_label)
    if not expected_address or expected_address not in normalized(order.get("address", "")):
        return "address"
    if order["provider"] == "instamart":
        # Merchant catalog IDs identify the purchased variant when history omits pack text.
        expected = sorted(
            (x.offer.sku_id.split(":")[0], normalized(x.offer.name), x.quantity) for x in view.lines
        )
        actual = sorted(
            (str(x.get("catalog_id", "")), normalized(x.get("name", "")), x.get("quantity", 0))
            for x in order.get("items", [])
        )
    else:
        expected = sorted(
            (normalized(offer_cart_name(x.offer)), normalized(x.offer.pack_size), x.quantity) for x in view.lines
        )
        # History can use either the catalogue or the verified product-page label.
        # Resolve only unique exact labels; never strip variant/translation text.
        labels = {}
        for line in view.lines:
            pack = normalized(line.offer.pack_size)
            canonical = normalized(offer_cart_name(line.offer))
            for name in (line.offer.name, offer_cart_name(line.offer)):
                labels.setdefault((normalized(name), pack), set()).add(canonical)
        actual = []
        for item in order.get("items", []):
            key = (normalized(item.get("name", "")), normalized(item.get("pack", "")))
            candidates = labels.get(key, set())
            if len(candidates) != 1:
                return "items"
            actual.append((next(iter(candidates)), key[1], item.get("quantity", 0)))
        actual.sort()
    if not expected or actual != expected:
        return "items"
    if order.get("method") not in ({"cash", "upi", "online"} if method == "observe" else {"cash"} if method == "cash" else {"upi", "online"}):
        return "payment_method"
    return None


def matches(order, view, method, baseline, started_at):
    return mismatch_reason(order, view, method, baseline, started_at) is None


class OrderMonitor:
    def __init__(self, inbox):
        self.db = inbox.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS order_checks (
            sender TEXT NOT NULL, order_id TEXT NOT NULL, method TEXT NOT NULL,
            snapshot TEXT NOT NULL, baseline TEXT NOT NULL, started_at REAL NOT NULL,
            status TEXT NOT NULL, result TEXT, checks INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(sender,order_id))""")

    def get(self, sender, order_id):
        return self.db.execute(
            "SELECT * FROM order_checks WHERE sender=? AND order_id=?", (sender, order_id)
        ).fetchone()

    @traced('order.capture')
    async def capture(self, sender, view, method, workers):
        old = self.get(sender, view.id)
        if old and old["status"] != "prepared" and not (old["method"] == "observe" and old["status"] == "pending"):
            raise DomainError("An order attempt is already being checked. Use Check status.")
        history = await workers.order_history(view)
        if not isinstance(history.get("ids"), list):
            raise DomainError("I couldn’t check your order history. No payment or order was started.")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO order_checks VALUES (?,?,?,?,?,?,?,NULL,0)",
                (
                    sender,
                    view.id,
                    method,
                    view.model_dump_json(),
                    json.dumps(history["ids"]),
                    time.time(),
                    "prepared",
                ),
            )

    def activate(self, sender, order_id):
        with self.db:
            self.db.execute(
                "UPDATE order_checks SET status='pending' WHERE sender=? AND order_id=? AND status='prepared'",
                (sender, order_id),
            )

    @traced('order.refresh')
    async def refresh_confirmed(self, row, workers, *, sleep=asyncio.sleep, max_checks=3):
        saved = json.loads(row["result"])
        view = SessionView.model_validate_json(row["snapshot"])
        baseline = json.loads(row["baseline"])
        blinkit = view.review.provider.value == "blinkit"
        deadline = time.monotonic() + (60 if blinkit else 30)
        attempts = 0
        diagnostic = {}
        for attempt in range(max_checks if view.review.provider.value == "blinkit" else 1):
            attempts += 1
            diagnostic = {"refresh_reason": "no_matching_order"}
            try:
                history = await asyncio.wait_for(workers.order_history(view, baseline),
                    timeout=min(30, max(0.01, deadline - time.monotonic())))
                same_order = [order for order in history["orders"]
                              if order.get("order_id") == saved["order"]["order_id"]]
                rejected = [mismatch_reason(order, view, row["method"], baseline, row["started_at"])
                            for order in same_order]
                candidates = [order for order, reason in zip(same_order, rejected) if reason is None]
                diagnostic.update(rejected_fields=sorted({reason for reason in rejected if reason}),
                                  history_issues=history.get("issues", []))
                if len(candidates) == 1:
                    order = candidates[0]
                    if order.get("status") in {"confirmed", "preparing", "on_way", "delivered", "cancelled", "failed"}:
                        result = {"status": "failed" if order["status"] in {"cancelled", "failed"} else "confirmed", "order": order}
                        with self.db:
                            self.db.execute(
                                "UPDATE order_checks SET status=?,result=?,checks=checks+? WHERE sender=? AND order_id=?",
                                (result["status"], json.dumps(result), attempts, row["sender"], row["order_id"]),
                            )
                        emit('order.status_observed', provider=getattr(view.review, 'provider', None),
                             status=result['status'], attempt=attempts, max_attempts=max_checks)
                        return result
                    diagnostic["refresh_reason"] = "unreadable_status"
                elif len(candidates) > 1:
                    diagnostic["refresh_reason"] = "ambiguous_matches"
            except Exception as exc:
                emit('order.history_read_failed', provider=getattr(view.review, 'provider', None), **error_fields(exc))
                diagnostic = {"refresh_reason": "history_read_error", "refresh_error_type": type(exc).__name__}
            emit('order.refresh_incomplete', provider=getattr(view.review, 'provider', None), attempt=attempts,
                 max_attempts=max_checks, status='unknown', cached=True, error_code=diagnostic.get('refresh_reason'))
            if time.monotonic() + 1 >= deadline:
                break
            if attempt < max_checks - 1 and view.review.provider.value == "blinkit":
                await sleep(1)
        # Preserve verified purchase evidence and diagnostic reason across restart,
        # but never present a cached ETA as current or make failure mean unpaid.
        result = {**saved, **diagnostic, "stale": True, "order": {**saved["order"], "eta": None}}
        with self.db:
            self.db.execute("UPDATE order_checks SET result=?,checks=checks+? WHERE sender=? AND order_id=?",
                            (json.dumps(result), attempts, row["sender"], row["order_id"]))
        return result

    @traced('order.check')
    async def check(self, sender, order_id, workers, *, sleep=asyncio.sleep, max_checks=3):
        row = self.get(sender, order_id)
        if not row or row["status"] == "prepared":
            emit('order.status_observed', status='pending', error_code='no_verified_attempt')
            return {"status": "pending", "reason": "no_verified_attempt"}
        if row["status"] in {"confirmed", "failed"}:
            return await self.refresh_confirmed(row, workers, sleep=sleep, max_checks=max_checks)
        view = SessionView.model_validate_json(row["snapshot"])
        baseline = json.loads(row["baseline"])
        saved = json.loads(row["result"] or "{}")
        empty_checks = saved.get("empty_checks", 0)
        result = {"status": "pending"}
        deadline = time.monotonic() + 30
        for attempt in range(max_checks):
            previous = result
            result = {"status": "pending"}
            try:
                history = await asyncio.wait_for(
                    workers.order_history(view, baseline), timeout=max(0.01, deadline - time.monotonic())
                )
                candidates = []
                rejected = set()
                for order in history["orders"]:
                    reason = mismatch_reason(order, view, row["method"], baseline, row["started_at"])
                    if reason:
                        rejected.add(reason)
                    else:
                        candidates.append(order)
                result = {
                    "status": "pending",
                    "reason": "no_matching_order",
                    "rejected_fields": sorted(rejected),
                    "history_issues": history.get("issues", []),
                }
                if len(candidates) > 1:
                    result["reason"] = "ambiguous_matches"
                if len(candidates) == 1:
                    order = candidates[0]
                    if order.get("status") in {"failed", "cancelled"}:
                        result = {"status": "failed", "order": order}
                    elif order.get("status") in {"confirmed", "preparing", "on_way", "delivered"} and (
                        row["method"] == "cash" or (row["method"] == "observe" and order.get("method") == "cash") or order.get("payment_status") == "paid"
                    ):
                        result = {"status": "confirmed", "order": order}
                    else:
                        result = {"status": "pending", "order": order, "reason": "awaiting_store_or_payment"}
            except Exception as exc:
                emit('order.history_read_failed', provider=getattr(view.review, 'provider', None), **error_fields(exc))
                # A blocked browser, timeout, or unreadable history is not a failed payment.
                result = {
                    "status": "pending",
                    "reason": "history_read_error",
                    "error_type": type(exc).__name__,
                    "rejected_fields": previous.get("rejected_fields", []),
                    "history_issues": previous.get("history_issues", []),
                }
            if (result.get("reason") == "no_matching_order"
                and not result.get("rejected_fields") and not result.get("history_issues")):
                empty_checks += 1
                result["empty_checks"] = empty_checks
                if empty_checks >= 3 and row["method"] != "observe":
                    result["status"] = "not_found"
            else:
                empty_checks = 0
            with self.db:
                self.db.execute(
                    "UPDATE order_checks SET status=?,result=?,checks=checks+1 WHERE sender=? AND order_id=?",
                    (result["status"], json.dumps(result), sender, order_id),
                )
            emit('order.status_observed', provider=getattr(view.review, 'provider', None), status=result['status'],
                 attempt=attempt + 1, max_attempts=max_checks, error_code=result.get('reason'))
            if result["status"] != "pending":
                return result
            if time.monotonic() + 3 >= deadline:
                break
            if attempt < max_checks - 1:
                await sleep(3)
        return result


def status_messages(result, view):
    from .presentation import card, money, plain, text_message, text_messages

    order = result.get("order", {})
    if result["status"] == "not_found":
        return [card(
            "No matching order appeared after 3 checks. You can retry payment or check again.",
            heading="Order not confirmed", buttons=[
                (f"retry_payment:{view.version}:{view.id}", "Retry payment"),
                (f"status:{view.version}:{view.id}", "Check status")])]
    if result.get("stale"):
        return [text_message("*Status unavailable*\n\nI couldn’t refresh delivery status from the store. Don’t pay or order again."), status_button(view)]
    if result["status"] == "confirmed":
        delivered = order.get("status") == "delivered"
        payment = ("" if delivered else "Pay cash on delivery.") if order["method"] == "cash" else "Payment verified."
        label = "Order delivered" if delivered else "Order confirmed"
        icon = "📦" if delivered else "✅"
        items = "\n".join(f"• {x.quantity} × {plain(x.offer.name)} · {plain(x.offer.pack_size)}" for x in view.lines)
        body = f"{icon} *{label}*\n\nOrder ID: {plain(order['order_id'])}\n*Total · {money(order['total_paise'])}*"
        if payment:
            body += "\n" + payment
        if order.get("eta") and not delivered:
            body += "\n" + plain(order['eta'])
        body += "\n\n" + items
        return text_messages(body)
    body = (
        "*Order update*\n\nThe store shows this order as cancelled or failed. Don’t pay again until any payment or refund is checked."
        if result["status"] == "failed" else
        "*Awaiting confirmation*\n\nI couldn’t verify your order yet. Don’t pay or order again. Tap below for a fresh check."
    )
    return [text_message(body), status_button(view)]


def status_button(view):
    from .presentation import card

    return card("Check the latest update directly with your store.",
                buttons=[(f"status:{view.version}:{view.id}", "Check status")])
