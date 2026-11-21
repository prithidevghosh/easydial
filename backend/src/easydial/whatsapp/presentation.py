"""WhatsApp-native presentation; no intent, pricing, consent or order decisions live here."""

import re
import textwrap

from ..billing import breakdown, can_choose_payment
from ..providers.images import product_image_url


def plain(value):
    """Merchant/user labels must not inject WhatsApp formatting or extra paragraphs."""
    return re.sub(r"\s+", " ", re.sub(r"[*_~`\x00-\x1f]", " ", str(value or ""))).strip()


def short(value, limit):
    value = plain(value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def money(paise):
    return "Unverified" if paise is None else f"{'−' if paise < 0 else ''}₹{abs(paise) / 100:,.2f}"


def text_message(body):
    return {"type": "text", "text": {"body": body}}


def text_messages(body):
    """Split long receipts without dropping items or breaking monospace bill blocks."""
    chunks, lines, size, in_code = [], [], 0, False
    for original in body.split("\n"):
        parts = (
            textwrap.wrap(original, width=3500, replace_whitespace=False)
            if len(original) > 3500
            else [original]
        )
        for line in parts:
            if size + len(line) + 5 > 3800:
                if in_code:
                    lines.append("```")
                chunks.append(text_message("\n".join(lines)))
                lines = ["```"] if in_code else []
                size = 4 if in_code else 0
            lines.append(line)
            size += len(line) + 1
            if line == "```":
                in_code = not in_code
    if lines:
        chunks.append(text_message("\n".join(lines)))
    return chunks


def card(body, *, heading=None, footer=None, buttons=None, rows=None, label="Choose an option"):
    interactive = {"type": "list" if rows is not None else "button", "body": {"text": body}}
    if heading:
        interactive["header"] = {"type": "text", "text": heading}
    if footer:
        interactive["footer"] = {"text": footer}
    if rows is not None:
        interactive["action"] = {"button": label, "sections": [{"title": "Available options", "rows": rows}]}
    else:
        interactive["action"] = {
            "buttons": [{"type": "reply", "reply": {"id": id, "title": title}} for id, title in buttons]
        }
    return {"type": "interactive", "interactive": interactive}


def product_caption(offer, number):
    quantity = offer.suggested_quantity
    available = "In stock" if offer.available else "Unavailable"
    pack = short(offer.pack_size, 140) or "Pack size not provided"
    lines = [
        f"*{number}. {short(offer.name, 500)}*",
        f"{pack} · {offer.provider.value.title()}",
        "",
        f"*{money(offer.price_paise)} per pack* · {available}",
    ]
    if quantity > 1:
        total = offer.price_paise * quantity if offer.price_paise is not None else None
        lines.append(f"{quantity} packs · *{money(total)}* for your quantity")
    else:
        lines.append("Quantity: 1 pack")
    return "\n".join(lines)


def choice_messages(session, choices, partial=False):
    replies, rows, text_cards, photo_count = [], [], [], 0

    def flush_text():
        if text_cards:
            replies.append(text_message("\n\n".join(text_cards)))
            text_cards.clear()

    for number, id in enumerate(choices, 1):
        offer = session.offers[id]
        caption = product_caption(offer, number)
        url = product_image_url(offer.image_url)
        if url and photo_count < 2:
            flush_text()  # Keep mixed image/text results in their numbered order.
            replies.append({"type": "image", "image": {"link": url, "caption": caption}})
            photo_count += 1
        else:
            if sum(map(len, text_cards)) + len(caption) + 2 * len(text_cards) > 3800:
                flush_text()
            text_cards.append(caption)
        rows.append(
            {
                "id": f"pick:{session.view.version}:{id}",
                "title": short(f"{number}. {offer.name}", 24),
                "description": short(
                    f"{offer.provider.value.title()} · {offer.pack_size or 'Pack unspecified'} · {money(offer.price_paise)} per pack · Qty {offer.suggested_quantity}",
                    72,
                ),
            }
        )
    flush_text()
    body = "Choose a product below, or reply with its number."
    if partial:
        body += "\n\nThese are the options I could verify. Some stores were unavailable."
    replies.append(
        card(
            body,
            heading="Your product options",
            footer="Delivery and fees are checked at review.",
            rows=rows,
            label="Choose a product",
        )
    )
    return replies


def basket_text(session):
    if not session.view.lines:
        return "Your basket is empty."
    stores = ", ".join(sorted({line.offer.provider.value.title() for line in session.view.lines}))
    lines = [f"🛒 *Your {stores} basket*", ""]
    for line in session.view.lines:
        lines.extend(
            [
                f"{line.quantity} × {plain(line.offer.name)}",
                f"{plain(line.offer.pack_size) or 'Pack unspecified'} · *{money(line.subtotal)}*",
                "",
            ]
        )
    subtotal = (
        sum(line.subtotal for line in session.view.lines)
        if all(line.offer.price_paise is not None for line in session.view.lines)
        else None
    )
    lines.extend([f"*Items subtotal · {money(subtotal)}*", "Delivery and fees are checked at review."])
    return "\n".join(lines)


def basket_messages(session):
    if not session.view.lines:
        return [
            text_message("🛒 *Your basket is empty*\n\nSend a voice note or type what you’d like to order.")
        ]
    return [
        *text_messages(basket_text(session)),
        card(
            "Review prices and delivery fees, or tell me what you’d like to change.",
            buttons=[(f"review:{session.view.version}:current", "Review basket")],
        ),
    ]


def bill_table(review):
    """A narrow, real monospace table, not unsupported Markdown table syntax."""
    rows = breakdown(review)["rows"]
    result = ["Charge               Amount", "──────────────────────────────"]
    for row in rows:
        label = "Total payable" if row["label"] == "TOTAL PAYABLE" else plain(row["label"])
        parts = textwrap.wrap(label, width=18) or [""]
        value = money(row["amount_paise"])
        for index, part in enumerate(parts):
            result.append(f"{part:<18} {value:>11}" if index == len(parts) - 1 else part)
    return "```\n" + "\n".join(result) + "\n```"


def review_messages(session):
    review = session.view.review
    ready = can_choose_payment(review)
    items = "\n".join(
        f"• {x.quantity} × {plain(x.offer.name)} · {plain(x.offer.pack_size)}" for x in session.view.lines
    )
    body = f"🧾 *Your bill · {review.provider.value.title()}*\n\n{items}\n\n{bill_table(review)}"
    warnings = breakdown(review)["warnings"]
    if warnings:
        body += "\n\n" + "\n\n".join(f"⚠️ {plain(warning)}" for warning in warnings)
    body += f"\n\n*Deliver to*\n{plain(review.address_label)}"
    if not ready:
        body += "\n\nYour basket is saved. No payment has been started."
    buttons = (
        [(f"checkout:{session.view.version}:{review.id}", "Choose payment")]
        if ready
        else [
            (f"change_basket:{session.view.version}:{review.id}", "Change basket"),
            (f"save_basket:{session.view.version}:{review.id}", "Save for later"),
        ]
    )
    return [
        *text_messages(body),
        card(
            f"Your total is *{money(review.total_paise)}*. Check the bill above, then choose how to pay."
            if ready
            else "I can’t confirm this bill yet. You can change your basket or save it for later.",
            heading="Ready to continue?" if ready else "Your next step",
            buttons=buttons,
        ),
    ]


def payment_choices(session, menu):
    return card(
        f"*{money(session.view.review.total_paise)} · {session.view.review.provider.value.title()}*\n\nChoose how you’d like to pay.",
        heading="Payment options",
        label="Choose payment",
        rows=[
            {
                "id": f"payment:{session.view.version}:{menu['id']}_{item['id']}",
                "title": short(item["title"], 24),
                "description": short(
                    item.get("note")
                    or (
                        "Scan the store’s QR in your UPI app"
                        if item["id"] == "upi"
                        else "Continue securely with the store"
                    ),
                    72,
                ),
            }
            for item in menu["methods"]
        ],
    )


def qr_caption(total):
    return f"*UPI payment · Blinkit*\n*Amount due · {money(total)}*\n\nPlease complete your payment using the QR above.\nIt expires in a few minutes. Approve only in your UPI app."


def onboarding_card(body, nonce, options, *, heading=None, footer=None):
    keys = {key for key, _ in options}
    if not heading:
        if "agree" in keys:
            heading = "🔐 Your permission"
        elif "check_login" in keys or "retry_login" in keys:
            heading = "Login update"
        elif "same" in keys:
            heading = "Confirm your address"
        elif keys & {"blinkit", "instamart"}:
            heading = "Connect a store"
        else:
            heading = "Account setup"
    return card(
        body,
        heading=heading,
        footer=footer,
        buttons=[(f"onboard:{nonce}:{key}", title) for key, title in options],
    )


def progress_copy(stage):
    # A small vocabulary of factual activity, never fabricated progress percentages.
    return {
        "Listening to your voice note…": "🎙️ Listening to your voice note…",
        "Finding matching products…": "Finding the best matches for you…",
        "Checking your cart and fees…": "Checking your basket, delivery and fees…",
        "Finding payment options…": "Checking the store’s payment options…",
        "Preparing your payment option…": "Preparing your chosen payment option…",
    }.get(stage, stage)


def prepare_outbound(payload):
    """Style remaining recovery/action cards without changing their action IDs or meaning."""
    from copy import deepcopy

    result = deepcopy(payload)
    if result.get("type") == "text":
        result["text"].setdefault("preview_url", False)
    if result.get("type") == "interactive":
        item = result["interactive"]
        buttons = item.get("action", {}).get("buttons", [])
        key = buttons[0].get("reply", {}).get("id", "").split(":")[0] if buttons else ""
        headings = {
            "place_cod": "Confirm your order",
            "retry_payment_confirm": "Before you retry",
            "retry_payment": "Payment update",
            "status": "Order update",
            "clear_cart": "Clear your basket",
            "restore": "Restore your basket",
            "change_basket": "Your next step",
            "review": "Review your basket",
            "checkout": "Ready to continue?",
            "retry_search": "Find another match",
        }
        if key in headings:
            item.setdefault("header", {"type": "text", "text": headings[key]})
    return result
