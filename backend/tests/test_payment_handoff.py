import base64
import io
import time
from types import SimpleNamespace

import httpx
import pytest
import zxingcpp
from PIL import Image

from easydial.controller import DomainError
from easydial.models import SessionView
from easydial.providers.payments import decode_qr
from easydial.whatsapp.app import create_whatsapp_app
from tests.test_whatsapp import make_runtime


def qr(uri):
    code = zxingcpp.create_barcode(uri, zxingcpp.BarcodeFormat.QRCode)
    image = zxingcpp.write_barcode_to_image(code, scale=4)
    picture = Image.frombytes("L", (image.shape[1], image.shape[0]), bytes(image))
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_qr_verifies_amount_currency_and_upi_destination():
    uri = "upi://pay?pa=merchant@test&pn=Test&tr=reference&am=56&cu=INR"
    assert decode_qr(qr(uri), 5600)["upi_uri"] == uri
    for wrong in [
        uri.replace("am=56", "am=57"),
        uri.replace("cu=INR", "cu=USD"),
        uri.replace("upi://pay", "https://evil.test"),
        uri + "&am=5",
    ]:
        with pytest.raises(DomainError):
            decode_qr(qr(wrong), 5600)


async def reviewed(runtime):
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    state, _ = await runtime.chat.handle("review", state)
    return state


async def test_menu_first_then_once_only_request_and_expiring_read_only_link(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.payments.public_url = "https://easydial.example"
    state = await reviewed(runtime)
    calls = []

    async def generate(view, review):
        calls.append(view.id)
        return decode_qr(qr("upi://pay?pa=test@bank&tr=unique&am=56&cu=INR"), 5600) | {"ttl_seconds": 300}

    runtime.chat.workers.generate_upi = generate
    state, replies = await runtime.chat.handle("checkout", state, customer="alice")
    assert calls == []
    rows = replies[-1]["interactive"]["action"]["sections"][0]["rows"]
    assert {r["title"] for r in rows} == {"UPI", "Cards"}
    state, replies = await runtime.chat.handle("UPI", state, rows[0]["id"], customer="alice")
    assert len(calls) == 1
    prepared = state["view"]["payment_preparation"]
    token = prepared["url"].rsplit("/", 1)[-1]
    row = runtime.payments.lookup(token)
    assert row["sender"] == "alice"
    assert runtime.payments.existing("bob", state["view"]["id"]) is None
    with pytest.raises(DomainError):
        await runtime.chat.handle("UPI", state, rows[0]["id"], customer="alice")
    assert len(calls) == 1
    app = create_whatsapp_app(runtime.settings)
    app.state.runtime = runtime
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/pay/" + token)
        assert response.status_code == 200 and "Open UPI app" in response.text
        assert "no-store" in response.headers["cache-control"]
        assert "script" not in response.text.lower() and "test home" not in response.text
        assert (await client.get("/pay/not-a-valid-token")).status_code == 410
        with runtime.inbox.db:
            runtime.inbox.db.execute("UPDATE payment_requests SET expires_at=?", (int(time.time()) - 1,))
        assert (await client.get("/pay/" + token)).status_code == 410
    assert len(calls) == 1  # GET requests cannot generate or approve payments.
    runtime.inbox.close()


async def test_uncertain_generation_is_not_retried_and_is_durable(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.payments.public_url = "https://easydial.example"
    state = await reviewed(runtime)
    view = SessionView.model_validate(state["view"])

    async def fail(*args):
        raise TimeoutError("response lost after generation")

    with pytest.raises(TimeoutError):
        await runtime.payments.create("alice", view, view.review, SimpleNamespace(generate_upi=fail))
    assert runtime.payments.existing("alice", view.id)["status"] == "unknown"
    with pytest.raises(DomainError, match="already exists"):
        await runtime.payments.create("alice", view, view.review, SimpleNamespace(generate_upi=fail))
    runtime.inbox.close()


async def test_card_selection_hands_off_without_generating_or_charging(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await reviewed(runtime)
    state, replies = await runtime.chat.handle("checkout", state, customer="alice")
    rows = replies[-1]["interactive"]["action"]["sections"][0]["rows"]
    state, replies = await runtime.chat.handle("Cards", state, rows[1]["id"], customer="alice")
    assert "https://blinkit.com/checkout" in replies[0]["text"]["body"]
    assert runtime.inbox.db.execute("SELECT COUNT(*) FROM payment_requests").fetchone()[0] == 0
    runtime.inbox.close()


async def test_stale_method_choice_cannot_prepare_a_payment(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await reviewed(runtime)
    state, replies = await runtime.chat.handle("checkout", state, customer="alice")
    action = replies[-1]["interactive"]["action"]["sections"][0]["rows"][0]["id"]
    state["view"]["version"] += 1
    with pytest.raises(DomainError):
        await runtime.chat.handle("UPI", state, action, customer="alice")
    assert runtime.inbox.db.execute("SELECT COUNT(*) FROM payment_requests").fetchone()[0] == 0
    runtime.inbox.close()
