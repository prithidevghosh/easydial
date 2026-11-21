"""Read order history through authenticated website navigation, never replay private APIs."""

import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from ..controller import DomainError
from ..models import Provider
from .money import paise


def order_status(text):
    text = text.lower()
    if "cancel" in text:
        return "cancelled"
    if "fail" in text:
        return "failed"
    if "delivered" in text or text.startswith("arrived in") or text == "leaf_order_status_completed":
        return "delivered"
    if any(
        x in text
        for x in [
            "order confirmed",
            "order placed",
            "preparing",
            "packing",
            "arriving in",
            "out for delivery",
        ]
    ):
        return "confirmed"
    if text.upper() in {"PLACED", "CONFIRMED", "PREPARING", "DISPATCHED", "OUT_FOR_DELIVERY"}:
        return "confirmed"
    return "pending"


def parse_instamart(row):
    data = row.get("order_data", {})
    orders = data.get("orders", [])
    jobs = [j for o in orders for j in o.get("order_jobs", [])]
    # Multiple shipments need separate matching; never silently omit one.
    if len(jobs) != 1:
        return None
    meta = json.loads(jobs[0].get("metadata") or "{}")
    payment = data.get("payment_info_v2") or {}
    transactions = payment.get("payment_transaction_reference") or []
    if len(transactions) != 1:
        return None
    transaction = transactions[0]
    raw_method = transaction.get("primary_payment_method", "").casefold()
    method = (
        "cash"
        if raw_method == "cash"
        else "upi"
        if "upi" in raw_method
        else "online"
        if raw_method in {"juspay", "card", "netbanking"}
        else None
    )
    address = meta.get("address", {})
    v2 = row.get("order_data_v2", {})
    total = v2.get("total", {})
    if total.get("currency_code") != "INR":
        return None
    amount = int(total["units"]) * 100 + round(int(total.get("nanos", 0)) / 10_000_000)
    placed = row.get("created_at")
    if isinstance(placed, (int, float)) and placed >= 1_000_000_000_000:
        placed /= 1000  # Instamart history timestamps are Unix milliseconds.
    return {
        "order_id": str(row["order_id"]),
        "history_id": str(row["order_id"]),
        "provider": "instamart",
        "placed_at": placed,
        "total_paise": amount,
        "address": " ".join(str(address.get(k) or "") for k in ["flatNo", "address"]),
        "items": [
            {"name": x["name"], "pack": "", "quantity": int(x["quantity"]), "catalog_id": x["itemId"]}
            for x in meta.get("items", [])
        ],
        "method": method,
        "payment_status": "paid" if method != "cash" and transaction.get("state") == "SUCCESS" else "pending",
        "status": order_status(row.get("history_status") or v2.get("status", "")),
        "eta": None,
    }


def parse_blinkit(card, text):
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    def after(label):
        return lines[lines.index(label) + 1] if label in lines and lines.index(label) + 1 < len(lines) else ""

    match = re.search(r"(\d+) items? in this order", text, re.I)
    if not match:
        return None
    start = next(i for i, x in enumerate(lines) if re.search(r"\d+ items? in this order", x, re.I))
    items = []
    for i in range(start + 1, len(lines)):
        if len(items) == int(match[1]):
            break
        pack = re.fullmatch(r"(.+?)\s+[x×]\s*(\d+)", lines[i])
        if pack:
            items.append({"name": lines[i - 1], "pack": pack[1], "quantity": int(pack[2])})
    head = card["items"][0]["data"]
    stamp = head.get("subtitle", {}).get("text", "")
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    placed = None
    # History displays day/month/time without year. Resolve only dates in the
    # current year; the matcher rejects everything older than the attempt window.
    for fmt in ("%d %b, %I:%M %p", "%d %b, %Y, %I:%M %p"):
        try:
            dt = datetime.strptime(stamp, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
            placed = dt.replace(tzinfo=now.tzinfo).timestamp()
            break
        except ValueError:
            pass
    method_text = after("Payment").casefold()
    method = "cash" if "cash" in method_text else "online" if method_text == "paid online" else None
    title = head.get("title", {}).get("text", "")
    # The detail page can have a newer status than the history card that opened it.
    # Only use the status immediately under Order summary, not unrelated page text.
    detail_title = after("Order summary")
    detail_status = order_status(detail_title)
    card_status = order_status(title)
    # Cancellation/failure is terminal; never overwrite it with an earlier ETA.
    if card_status in {"cancelled", "failed"}:
        status = card_status
    elif detail_status != "pending":
        status = detail_status
        title = detail_title
    else:
        status = card_status
    return {
        "order_id": after("Order id"),
        "history_id": card["identity"]["id"],
        "provider": "blinkit",
        "placed_at": placed,
        "total_paise": paise(after("Bill total")),
        "items": items if len(items) == int(match[1]) else [],
        "address": after("Deliver to"),
        "method": method,
        "payment_status": "paid" if method_text == "paid online" else "pending",
        "status": status,
        "eta": title if "arriving in" in title.lower() else None,
    }


async def read_history(worker, view, exclude_ids=None):
    async with worker.lock, worker.browser_page() as (page, context):
        if worker.provider == Provider.INSTAMART:
            await page.goto("https://instamart.in", wait_until="domcontentloaded")
            async with page.expect_response(
                lambda r: urlsplit(r.url).path == "/api/instamart/orders" and r.request.method == "GET",
                timeout=20000,
            ) as response:
                await page.get_by_test_id("user-account-button").click()
            payload = await (await response.value).json()
            rows = payload.get("data", {}).get("orders")
            if not isinstance(rows, list):
                raise DomainError("Order history is unavailable.")
            ids = [str(r["order_id"]) for r in rows]
            parsed = []
            if exclude_ids is not None:
                for row in rows:
                    if str(row["order_id"]) not in exclude_ids:
                        try:
                            value = parse_instamart(row)
                            if value:
                                parsed.append(value)
                        except (KeyError, ValueError, TypeError):
                            continue
            return {"ids": ids, "orders": parsed}
        async with page.expect_response(
            lambda r: urlsplit(r.url).path == "/v1/layout/order_history", timeout=20000
        ) as response:
            await page.goto("https://blinkit.com/account/orders", wait_until="domcontentloaded")
        payload = await (await response.value).json()
        if not payload.get("is_success"):
            raise DomainError("Order history is unavailable.")
        cards = [s["data"] for s in payload["response"]["snippets"] if s.get("data", {}).get("identity")]
        ids = [c["identity"]["id"] for c in cards]
        results = []
        issues = []
        if exclude_ids is not None:
            for card in cards:
                if card["identity"]["id"] in exclude_ids:
                    continue
                # This URL is constructed only from the observed history-card deep link.
                link = urlsplit(card.get("click_action", {}).get("blinkit_deeplink", {}).get("url", ""))
                params = parse_qs(link.query)
                cart = params.get("cart_id", [""])[0]
                order = params.get("order_id", [""])[0]
                if not cart.isdigit() or not order.isdigit():
                    issues.append("unreadable_history_link")
                    continue
                await page.goto(
                    f"https://blinkit.com/account/orders/{cart}/{order}", wait_until="domcontentloaded"
                )
                await page.get_by_text("Order details", exact=True).wait_for()
                try:
                    parsed = parse_blinkit(card, await page.locator("body").inner_text())
                except (KeyError, ValueError, TypeError, IndexError):
                    issues.append("unreadable_order_details")
                    continue
                if parsed:
                    results.append(parsed)
                else:
                    issues.append("missing_order_items")
        return {"ids": ids, "orders": results, "issues": sorted(set(issues))}
