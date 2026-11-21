import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from easydial.controller import DomainError
from easydial.models import Provider
from easydial.providers import ProviderWorkers
from easydial.whatsapp.accounts import CONSENT_TEXT, CONSENT_VERSION
from easydial.whatsapp.app import Runtime, create_whatsapp_app
from easydial.whatsapp.conversation import ShoppingChat
from easydial.whatsapp.onboarding import Onboarding
from easydial.whatsapp.security import SecretBox
from easydial.whatsapp.storage import Inbox
from tests.test_whatsapp import FakeTransport, event, message, post, settings


class Login:
    def __init__(self):
        self.calls = []

    async def cancel(self, *args):
        self.calls.append(("cancel", args))

    async def request_otp(self, *args):
        self.calls.append(("request", args))

    async def verify_otp(self, sender, provider, otp):
        self.calls.append(("verify", (sender, provider)))
        return (
            None
            if otp == "000000"
            else [{"id": "address1", "label": sender + " home", "address": sender + " home"}]
        )

    async def addresses(self, sender, provider):
        return [{"id": "address1", "label": sender + " home", "address": sender + " home"}]

    async def select_address(self, sender, provider, address):
        self.calls.append(("address", (sender, provider, address["id"])))
        return {"address": address["address"], "location": sender + " locality", "address_id": address["id"]}

    async def close(self):
        pass


def runtime(tmp_path):
    config = settings(
        tmp_path,
        whatsapp_multiuser=True,
        customer_session_key=Fernet.generate_key().decode(),
        customer_session_directory=str(tmp_path / "sessions"),
        whatsapp_business_number="15556358602",
    )
    box = SecretBox(config.customer_session_key)
    inbox = Inbox(config.whatsapp_database, box)
    chat = ShoppingChat(
        config,
        ProviderWorkers(config),
        SimpleNamespace(require_client=lambda: pytest.fail("Onboarding must not reach AI")),
    )
    result = Runtime(config, inbox, FakeTransport(), chat)
    result.onboarding.adapter = Login()
    return result


async def turn(r, text, sender="919999999999", action=None):
    counter = r.inbox.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    msg = message("event-" + str(counter), text, **{"from": sender})
    if action:
        msg.update(type="interactive", interactive={"button_reply": {"id": action, "title": text}})
    r.inbox.enqueue(msg)
    await r.process()
    return [
        json.loads(x[0])
        for x in r.inbox.db.execute("SELECT payload FROM outbox WHERE event_id=?", (msg["id"],))
    ]


async def consent(r, sender="919999999999", provider="instamart"):
    await turn(r, "Start", sender)
    await turn(r, provider, sender)
    return await turn(r, "I agree", sender)


async def connect(r, sender="919999999999", provider="instamart", phone="9999999999"):
    await consent(r, sender, provider)
    await turn(r, phone, sender)
    await turn(r, "123456", sender)
    await turn(r, "1", sender)


async def test_consent_precedes_any_login_and_is_exact_recorded(tmp_path):
    r = runtime(tmp_path)
    await turn(r, "Start")
    await turn(r, "instamart")
    await turn(r, "9999999999")
    assert not any(x[0] == "request" for x in r.onboarding.adapter.calls)
    await turn(r, "yes")  # Ambiguous acknowledgement is not explicit consent.
    assert not r.onboarding.accounts.consented("919999999999", "instamart")
    await turn(r, "I agree")
    record = r.inbox.db.execute("SELECT * FROM consent_events").fetchone()
    assert record["action"] == "accepted"
    assert record["version"] == CONSENT_VERSION
    assert record["text"] == CONSENT_TEXT.format(store="Instamart")
    assert record["event_id"] == "event-4"
    await turn(r, "9999999999")
    assert sum(x[0] == "request" for x in r.onboarding.adapter.calls) == 1
    r.inbox.close()


async def test_decline_never_starts_login(tmp_path):
    r = runtime(tmp_path)
    await turn(r, "Start")
    await turn(r, "blinkit")
    await turn(r, "Not now")
    assert not any(x[0] == "request" for x in r.onboarding.adapter.calls)
    assert not r.onboarding.accounts.consented("919999999999", "blinkit")
    r.inbox.close()


async def test_phone_and_otp_encrypted_on_arrival_and_erased_not_in_chat(tmp_path):
    r = runtime(tmp_path)
    await consent(r)
    await turn(r, "9999999999")
    msg = message("otp-secret", "123456")
    r.inbox.enqueue(msg)
    raw = r.inbox.db.execute("SELECT payload FROM events WHERE id='otp-secret'").fetchone()[0]
    assert "123456" not in raw and "text" not in raw
    assert r.inbox.decode_event({"payload": raw})["text"]["body"] == "123456"
    await r.process()
    assert r.inbox.db.execute("SELECT payload FROM events WHERE id='otp-secret'").fetchone()[0] == "{}"
    stored = r.inbox.db.execute("SELECT state FROM customers").fetchone()[0]
    assert "123456" not in stored and "9999999999" not in stored
    assert r.onboarding.accounts.state(msg["from"])["stage"] == "address"
    r.inbox.close()


async def test_two_users_have_independent_consent_addresses_and_sessions(tmp_path):
    r = runtime(tmp_path)
    await connect(r, "919999999999", "instamart", "9999999999")
    await connect(r, "918888888888", "instamart", "8888888888")
    a = r.onboarding.accounts
    assert a.connections("919999999999")["instamart"]["address"] == "919999999999 home"
    assert a.connections("918888888888")["instamart"]["address"] == "918888888888 home"
    first = await r.customer_chats.get("919999999999")
    second = await r.customer_chats.get("918888888888")
    assert first is not second
    assert first.workers.workers[1].browser.storage_state != second.workers.workers[1].browser.storage_state
    assert first.workers.workers[1].browser.profile == second.workers.workers[1].browser.profile == ""
    assert second.workers.workers[0].browser.storage_state == ""
    with pytest.raises(DomainError):
        second.workers._worker(Provider.BLINKIT)
    assert r.payments.existing("918888888888", r.inbox.load("919999999999")["view"]["id"]) is None
    await r.customer_chats.close()
    r.inbox.close()


async def test_same_merchant_account_cannot_be_shared_by_two_customers(tmp_path):
    r = runtime(tmp_path)
    await connect(r, "919999999999")
    await consent(r, "918888888888")
    replies = await turn(r, "9999999999", "918888888888")
    assert "already connected" in replies[0]["interactive"]["body"]["text"]
    assert sum(x[0] == "request" for x in r.onboarding.adapter.calls) == 1
    r.inbox.close()


async def test_cross_customer_consent_button_is_rejected(tmp_path):
    r = runtime(tmp_path)
    for sender in ["919999999999", "918888888888"]:
        await turn(r, "Start", sender)
        await turn(r, "instamart", sender)
    nonce = r.onboarding.accounts.state("919999999999")["nonce"]
    await turn(r, "I agree", "918888888888", f"onboard:{nonce}:agree")
    assert not r.onboarding.accounts.consented("918888888888", "instamart")
    r.inbox.close()


async def test_expired_otp_and_restart_never_submit_code(tmp_path):
    r = runtime(tmp_path)
    await consent(r)
    await turn(r, "9999999999")
    a = r.onboarding.accounts
    s = a.state("919999999999")
    s["expires"] = 0
    a.put("919999999999", s)
    await turn(r, "123456")
    assert not any(x[0] == "verify" for x in r.onboarding.adapter.calls)
    await consent(r)
    await turn(r, "9999999999")
    r.onboarding = Onboarding(a, r.onboarding.adapter, r.customer_chats, r.payments)
    await turn(r, "123456")
    assert not any(x[0] == "verify" for x in r.onboarding.adapter.calls)
    r.inbox.close()


async def test_withdrawal_erases_session_and_records_audit(tmp_path):
    r = runtime(tmp_path)
    await connect(r)
    a = r.onboarding.accounts
    path = a.session_path("919999999999", "instamart")
    a.box.write(path, {"cookies": [{"secret": "COOKIE"}]})
    await turn(r, "disconnect instamart")
    from pathlib import Path

    assert not Path(path).exists()
    assert a.connections("919999999999") == {}
    assert not a.consented("919999999999", "instamart")
    assert (
        r.inbox.db.execute("SELECT action FROM consent_events ORDER BY id DESC LIMIT 1").fetchone()[0]
        == "withdrawn"
    )
    r.inbox.close()


async def test_multiuser_signed_ingress_and_shared_qr(tmp_path):
    r = runtime(tmp_path)
    app = create_whatsapp_app(r.settings)
    app.state.runtime = r
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await post(client, event([message(**{"from": "918888888888"})]))).status_code == 200
        assert r.inbox.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert (await post(client, event([message("bad")]), secret="wrong")).status_code == 403
        link = await client.get("/join")
        assert link.headers["location"] == "https://wa.me/15556358602?text=Start"
        qr = await client.get("/join/qr.png")
        import io

        import zxingcpp
        from PIL import Image

        assert zxingcpp.read_barcode(Image.open(io.BytesIO(qr.content))).text == link.headers["location"]
    r.inbox.close()


def test_ciphertext_authenticated_and_tenant_paths_safe(tmp_path):
    from cryptography.fernet import InvalidToken

    a = SecretBox(Fernet.generate_key())
    b = SecretBox(Fernet.generate_key())
    ciphertext = a.seal({"cookies": "secret"})
    assert "secret" not in ciphertext
    with pytest.raises(InvalidToken):
        b.open(ciphertext)
    a.write(tmp_path / "session.enc", {"cookies": "secret"})
    assert (tmp_path / "session.enc").stat().st_mode & 0o777 == 0o600


async def test_invalid_codes_bounded_and_voice_never_transcribed(tmp_path):
    r = runtime(tmp_path)
    await consent(r)
    await turn(r, "9999999999")
    msg = message("voice-code", type="audio", audio={"id": "secret-audio"})
    r.inbox.enqueue(msg)
    await r.process()
    assert r.transport.downloads == 0
    for _ in range(4):
        await turn(r, "000000")
    assert sum(x[0] == "verify" for x in r.onboarding.adapter.calls) == 3
    assert r.onboarding.accounts.state("919999999999")["stage"] == "store"
    r.inbox.close()


async def test_unverified_phone_does_not_reserve_another_persons_account(tmp_path):
    r = runtime(tmp_path)
    await consent(r)
    await turn(r, "8888888888")
    assert r.inbox.db.execute("SELECT COUNT(*) FROM merchant_accounts").fetchone()[0] == 0
    await connect(r, "918888888888", "instamart", "8888888888")
    assert r.onboarding.accounts.connections("918888888888")
    r.inbox.close()


async def test_second_store_needs_explicit_same_address_confirmation(tmp_path):
    r = runtime(tmp_path)
    await connect(r)
    await consent(r, provider="blinkit")
    await turn(r, "9999999999")
    await turn(r, "123456")
    await turn(r, "1")
    assert r.onboarding.accounts.state("919999999999")["stage"] == "same_address"
    assert "blinkit" not in r.onboarding.accounts.connections("919999999999")
    await turn(r, "Same address")
    assert set(r.onboarding.accounts.connections("919999999999")) == {"blinkit", "instamart"}
    r.inbox.close()


async def test_search_only_calls_connected_store(tmp_path):
    r = runtime(tmp_path)
    await connect(r)
    chat = await r.customer_chats.get("919999999999")

    async def forbidden(*args):
        pytest.fail("Disconnected provider invoked")

    chat.workers.workers[0].search = forbidden

    async def search(query, address):
        from easydial.models import SearchResult

        return SearchResult(provider=Provider.INSTAMART, query=query, status="ok")

    chat.workers.workers[1].search = search
    assert len(await chat.workers.search("milk", "home")) == 1
    await r.customer_chats.close()
    r.inbox.close()


async def test_consent_evidence_is_append_only(tmp_path):
    import sqlite3

    r = runtime(tmp_path)
    await consent(r)
    with pytest.raises(sqlite3.IntegrityError):
        r.inbox.db.execute("UPDATE consent_events SET action='withdrawn'")
    r.inbox.db.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        r.inbox.db.execute("DELETE FROM consent_events")
    r.inbox.db.rollback()
    r.inbox.close()


async def test_copied_session_ciphertext_cannot_be_used_by_other_customer_or_store(tmp_path):
    from cryptography.fernet import InvalidToken

    r = runtime(tmp_path)
    a = r.onboarding.accounts
    encrypted = a.session_box("alice", "blinkit").seal({"cookies": "alice-secret"})
    for sender, provider in [("bob", "blinkit"), ("alice", "instamart")]:
        with pytest.raises(InvalidToken):
            a.session_box(sender, provider).open(encrypted)
    r.inbox.close()


async def test_login_rate_limit_is_per_store_phone_not_chat(tmp_path):
    import time

    r = runtime(tmp_path)
    sender = '919999999999'
    phone = '9888888888'
    key = r.onboarding.accounts.account_key('blinkit', phone)
    # Preserve legacy records, but they cannot be attributed to a login phone.
    with r.inbox.db:
        for _ in range(3):
            r.inbox.db.execute(
                'INSERT INTO login_requests(sender,provider,created_at,account_key) VALUES (?,?,?,?)',
                (sender, 'blinkit', time.time() - 420, key),
            )
            r.inbox.db.execute(
                'INSERT INTO login_requests(sender,provider,created_at) VALUES (?,?,?)',
                (sender, 'blinkit', time.time()),
            )
    await consent(r, sender, 'blinkit')
    reply = await turn(r, '+91 ' + phone, sender)
    assert '8 minutes' in reply[0]['text']['body']
    assert not any(c[0] == 'request' for c in r.onboarding.adapter.calls)
    # Changing the login phone in the same chat is allowed.
    await turn(r, '9777777777', sender)
    assert sum(c[0] == 'request' for c in r.onboarding.adapter.calls) == 1
    # A second chat cannot bypass the same phone's limit.
    other = '916666666666'
    await consent(r, other, 'blinkit')
    reply = await turn(r, phone, other)
    assert 'Too many login requests' in reply[0]['text']['body']
    assert sum(c[0] == 'request' for c in r.onboarding.adapter.calls) == 1
    # The same phone on a different store has its own budget.
    await consent(r, other, 'instamart')
    await turn(r, phone, other)
    assert sum(c[0] == 'request' for c in r.onboarding.adapter.calls) == 2
    rows = r.inbox.db.execute('SELECT account_key FROM login_requests').fetchall()
    assert all(phone not in (row[0] or '') for row in rows)
    r.inbox.close()


async def test_store_prompt_phone_reply_returns_action_buttons(tmp_path):
    r = runtime(tmp_path)
    await turn(r, 'Start')
    reply = await turn(r, '9888888888')
    assert reply[0]['type'] == 'interactive'
    interactive = reply[0]['interactive']
    assert 'First choose which store' in interactive['body']['text']
    assert [b['reply']['title'] for b in interactive['action']['buttons']] == ['Blinkit', 'Instamart']
    assert not any(c[0] == 'request' for c in r.onboarding.adapter.calls)
    r.inbox.close()


async def test_restart_preserves_phone_prompt_and_recorded_consent(tmp_path):
    r = runtime(tmp_path)
    await consent(r, provider='blinkit')
    before = r.onboarding.accounts.state('919999999999')
    r.onboarding = Onboarding(r.onboarding.accounts, r.onboarding.adapter, r.onboarding.chats,
                              r.onboarding.payments)
    after = r.onboarding.accounts.state('919999999999')
    assert before == after
    assert after['stage'] == 'phone'
    await turn(r, '9888888888')
    assert sum(c[0] == 'request' for c in r.onboarding.adapter.calls) == 1
    r.inbox.close()
