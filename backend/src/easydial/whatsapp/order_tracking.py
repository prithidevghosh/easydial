"""Durable, read-only merchant reconciliation. Absence is never payment failure."""

import asyncio
import json
import time

from ..controller import DomainError
from ..models import SessionView
from ..providers.blinkit import normalized


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
            (normalized(x.offer.name), normalized(x.offer.pack_size), x.quantity) for x in view.lines
        )
        actual = sorted(
            (normalized(x.get("name", "")), normalized(x.get("pack", "")), x.get("quantity", 0))
            for x in order.get("items", [])
        )
    if not expected or actual != expected:
        return "items"
    if order.get("method") not in ({"cash"} if method == "cash" else {"upi", "online"}):
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

    async def capture(self, sender, view, method, workers):
        old = self.get(sender, view.id)
        if old and old["status"] != "prepared":
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

    async def refresh_confirmed(self, row, workers):
        saved = json.loads(row["result"])
        view = SessionView.model_validate_json(row["snapshot"])
        baseline = json.loads(row["baseline"])
        try:
            history = await asyncio.wait_for(workers.order_history(view, baseline), timeout=30)
            candidates = [order for order in history["orders"]
                if order.get("order_id") == saved["order"]["order_id"]
                and matches(order, view, row["method"], baseline, row["started_at"])]
            if len(candidates) == 1:
                order = candidates[0]
                if order.get("status") in {"confirmed", "preparing", "on_way", "delivered", "cancelled", "failed"}:
                    result = {"status": "failed" if order["status"] in {"cancelled", "failed"} else "confirmed", "order": order}
                    with self.db:
                        self.db.execute(
                            "UPDATE order_checks SET status=?,result=?,checks=checks+1 WHERE sender=? AND order_id=?",
                            (result["status"], json.dumps(result), row["sender"], row["order_id"]),
                        )
                    return result
        except Exception:
            pass
        # Keep verified purchase evidence, but never present a cached ETA as current.
        return {**saved, "stale": True, "order": {**saved["order"], "eta": None}}

    async def check(self, sender, order_id, workers, *, sleep=asyncio.sleep):
        row = self.get(sender, order_id)
        if not row or row["status"] == "prepared":
            return {"status": "pending", "reason": "no_verified_attempt"}
        if row["status"] == "failed":
            return json.loads(row["result"])
        if row["status"] == "confirmed":
            return await self.refresh_confirmed(row, workers)
        view = SessionView.model_validate_json(row["snapshot"])
        baseline = json.loads(row["baseline"])
        result = {"status": "pending"}
        deadline = time.monotonic() + 30
        for attempt in range(10):
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
                        row["method"] == "cash" or order.get("payment_status") == "paid"
                    ):
                        result = {"status": "confirmed", "order": order}
                    else:
                        result = {"status": "pending", "order": order, "reason": "awaiting_store_or_payment"}
            except Exception as exc:
                # A blocked browser, timeout, or unreadable history is not a failed payment.
                result = {
                    "status": "pending",
                    "reason": "history_read_error",
                    "error_type": type(exc).__name__,
                    "rejected_fields": previous.get("rejected_fields", []),
                    "history_issues": previous.get("history_issues", []),
                }
            with self.db:
                self.db.execute(
                    "UPDATE order_checks SET status=?,result=?,checks=checks+1 WHERE sender=? AND order_id=?",
                    (result["status"], json.dumps(result), sender, order_id),
                )
            if result["status"] != "pending":
                return result
            if time.monotonic() + 3 >= deadline:
                break
            if attempt < 9:
                await sleep(3)
        return result


def status_messages(result, view):
    from .conversation import money, text_message

    order = result.get("order", {})
    if result["status"] == "confirmed":
        delivered = order.get("status") == "delivered"
        payment = ("" if delivered else "Pay cash on delivery.") if order["method"] == "cash" else "Payment verified."
        eta = f" ETA: {order['eta']}." if order.get("eta") and not delivered and not result.get("stale") else ""
        label = "Order delivered" if delivered else "Order confirmed"
        if result.get("stale"):
            label = "Previously verified order"
            eta = " I couldn’t refresh delivery status. Tap Check status to retry."
        items = "; ".join(f"{x.quantity} × {x.offer.name} ({x.offer.pack_size})" for x in view.lines)
        return [
            text_message(
                f"{items}\n{label}: {order['order_id']} · {money(order['total_paise'])}. {payment}{eta}"
            )
        ] + ([status_button(view)] if result.get("stale") else [])
    text = (
        "The store shows this order as cancelled or failed. Don’t pay again until any payment or refund is checked."
        if result["status"] == "failed"
        else "I couldn’t verify your order yet. Don’t pay or order again—I’ll check when you tap below."
    )
    return [text_message(text), status_button(view)]


def status_button(view):
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "Check your order’s latest status."},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": f"status:{view.version}:{view.id}", "title": "Check status"},
                    }
                ]
            },
        },
    }
