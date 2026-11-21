"""Customer-visible contracts: honest money, complete receipts and choices delivered last."""

import copy

from easydial.controller import Session
from easydial.models import BasketLine, Offer, Provider, Review, SessionView
from easydial.whatsapp import presentation as ui
from easydial.whatsapp.accounts import CONSENT_TEXT
from easydial.whatsapp.onboarding import button_message


def offer(id="one", **changes):
    return Offer(
        id=id,
        provider=Provider.BLINKIT,
        product_id=id,
        sku_id=id,
        name="Coca-Cola Diet Coke",
        pack_size="330 ml",
        price_paise=5000,
        available=True,
        mode="live",
        address_label="Saved address",
        **changes,
    )


def assert_payload_limits(payload):
    kind = payload["type"]
    if kind == "text":
        assert 0 < len(payload["text"]["body"]) <= 4096
    elif kind == "image":
        assert len(payload["image"]["caption"]) <= 1024
    elif kind == "interactive":
        item = payload["interactive"]
        assert len(item["body"]["text"]) <= 1024
        assert len(item.get("header", {}).get("text", "")) <= 60
        assert len(item.get("footer", {}).get("text", "")) <= 60
        action = item["action"]
        for button in action.get("buttons", []):
            assert len(button["reply"]["title"]) <= 20
        if "sections" in action:
            assert len(action["button"]) <= 20
            rows = [r for section in action["sections"] for r in section["rows"]]
            assert len(rows) <= 10
            for row in rows:
                assert len(row["title"]) <= 24
                assert len(row["description"]) <= 72


def test_mixed_cards_keep_numbers_order_then_one_choice_prompt():
    offers = {
        str(i): offer(
            str(i), suggested_quantity=4, image_url=f"https://cdn.grofers.com/{i}.png" if i % 2 else None
        )
        for i in range(6)
    }
    session = Session(SessionView(id="draft", mode="live", address_label="Saved address"), offers=offers)
    before = copy.deepcopy(session.view)
    replies = ui.choice_messages(session, list(offers), partial=True)
    product_text = "\n".join(
        r.get("image", {}).get("caption", r.get("text", {}).get("body", "")) for r in replies
    )
    positions = [product_text.index(f"*{i}. ") for i in range(1, 7)]
    assert positions == sorted(positions)
    assert product_text.count("₹50.00 per pack") == 6
    assert product_text.count("₹200.00") == 6
    assert "Reply" not in product_text  # No premature choice prompts on individual cards.
    assert sum(r["type"] == "image" for r in replies) == 2
    assert replies[-1]["type"] == "interactive"
    rows = replies[-1]["interactive"]["action"]["sections"][0]["rows"]
    assert [r["id"] for r in rows] == [f"pick:0:{i}" for i in range(6)]
    assert session.view == before
    for reply in replies:
        assert_payload_limits(reply)


def test_long_merchant_labels_cannot_hide_price_or_forge_formatting():
    item = offer(suggested_quantity=3).model_copy(
        update={
            "name": "*Buy now*\n```fake total``` " + "Long product " * 150,
            "pack_size": "Large pack " * 70,
        }
    )
    caption = ui.product_caption(item, 1)
    assert len(caption) <= 1024
    assert "*₹50.00 per pack*" in caption and "*₹150.00*" in caption
    assert "```" not in caption and "*Buy now*" not in caption


def test_long_basket_is_complete_and_has_only_one_final_action():
    lines = [
        BasketLine(
            offer=offer(str(i)).model_copy(update={"name": f"Unique product {i} " + "description " * 20}),
            quantity=2,
        )
        for i in range(45)
    ]
    session = Session(SessionView(id="draft", mode="live", address_label="Saved address", lines=lines))
    messages = ui.basket_messages(session)
    body = "\n".join(m.get("text", {}).get("body", "") for m in messages)
    for i in range(45):
        assert f"Unique product {i} " in body
    assert "₹4,500.00" in body
    assert messages[-1]["type"] == "interactive"
    assert sum(m["type"] == "interactive" for m in messages) == 1
    for message in messages:
        assert_payload_limits(message)


def test_receipt_keeps_every_charge_and_real_total_in_mobile_table():
    bill = Review(
        version=0,
        provider=Provider.BLINKIT,
        address_label="Saved delivery address",
        item_subtotal_paise=5000,
        fees_paise=7300,
        total_paise=12300,
        payment_method="Pending",
        mode="live",
        bill_lines=[
            {"label": "Items total", "value": "₹50"},
            {"label": "Delivery charge", "value": "₹35"},
            {"label": "Handling charge", "value": "₹13"},
            {"label": "Small cart charge", "value": "₹25"},
        ],
    )
    session = Session(
        SessionView(
            id="draft",
            mode="live",
            address_label="Saved address",
            lines=[BasketLine(offer=offer(), quantity=1)],
            review=bill,
        )
    )
    messages = ui.review_messages(session)
    body = messages[0]["text"]["body"]
    assert "Small cart charge: ₹25 extra is included." in body
    assert "Saved delivery address" in body and "₹123.00" in body
    table = body.split("```")[1]
    assert all(len(line) <= 30 for line in table.splitlines())
    assert messages[-1]["interactive"]["action"]["buttons"][0]["reply"]["id"] == f"checkout:0:{bill.id}"
    for message in messages:
        assert_payload_limits(message)
    bill.total_paise += 1
    blocked = ui.review_messages(session)
    assert "Choose payment" not in str(blocked)
    assert "Your basket is saved" in str(blocked)


def test_split_large_bill_preserves_balanced_monospace_blocks():
    body = (
        "🧾 *Your bill*\n\n```\n"
        + "\n".join(f"Charge {i}             ₹10.00" for i in range(250))
        + "\n```\n\n*Deliver to*\nSaved address"
    )
    messages = ui.text_messages(body)
    assert len(messages) > 1
    for message in messages:
        assert_payload_limits(message)
        assert message["text"]["body"].count("```") % 2 == 0
    joined = "\n".join(m["text"]["body"] for m in messages)
    assert all(f"Charge {i} " in joined for i in range(250))


def test_consent_wording_and_action_tokens_survive_styling():
    consent = CONSENT_TEXT.format(store="Blinkit")
    spaced = consent.replace("We encrypt", "\n\nWe encrypt").replace(
        "You can disconnect", "\n\nYou can disconnect"
    )
    payload = button_message(spaced, "nonce", [("agree", "I agree"), ("decline", "Not now")])
    assert " ".join(payload["interactive"]["body"]["text"].split()) == " ".join(consent.split())
    assert [b["reply"]["id"] for b in payload["interactive"]["action"]["buttons"]] == [
        "onboard:nonce:agree",
        "onboard:nonce:decline",
    ]
    assert_payload_limits(payload)


def test_transport_styling_is_idempotent_and_does_not_mutate_durable_payload():
    payload = {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "Confirm this order."},
            "action": {
                "buttons": [{"type": "reply", "reply": {"id": "place_cod:2:opaque", "title": "Place order"}}]
            },
        },
    }
    before = copy.deepcopy(payload)
    styled = ui.prepare_outbound(payload)
    assert payload == before
    assert styled == ui.prepare_outbound(styled)
    assert styled["interactive"]["action"] == payload["interactive"]["action"]
    assert styled["interactive"]["header"]["text"] == "Confirm your order"
    assert ui.prepare_outbound(ui.text_message("Hello"))["text"]["preview_url"] is False


def test_many_saved_addresses_keep_full_labels_and_reply_instruction_last():
    from easydial.whatsapp.onboarding import Onboarding

    addresses = [{"label": f"Address {i}, " + "Full delivery detail " * 30} for i in range(20)]
    messages = Onboarding.address_menu(None, {"addresses": addresses})
    joined = "\n".join(m["text"]["body"] for m in messages)
    assert all(ui.plain(a["label"]) in joined for a in addresses)
    assert messages[-1]["text"]["body"].endswith("Reply with the address number.")
    for message in messages:
        assert_payload_limits(message)
