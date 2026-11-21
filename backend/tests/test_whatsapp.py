import copy
import hashlib
import hmac
import io
import json
import shutil
import time
import wave
from types import SimpleNamespace

import httpx
import pytest

from easydial.controller import DomainError
from easydial.models import IntentEdit, Provider, RequestedProduct
from easydial.providers import ProviderWorkers
from easydial.settings import Settings
from easydial.whatsapp.app import Runtime, create_whatsapp_app
from easydial.whatsapp.conversation import ChatIntent, ShoppingChat
from easydial.whatsapp.storage import Inbox
from easydial.whatsapp.transport import MetaTransport, normalize_audio
from tests.fakes import FixtureProvider


def settings(tmp_path, **updates):
    values = dict(
        _env_file=None,
        openai_api_key="",
        live_address_label="test home",
        whatsapp_phone_number_id="123456",
        whatsapp_allowed_sender="919999999999",
        whatsapp_access_token="test-token",
        meta_app_secret="test-secret",
        whatsapp_verify_token="verify-test",
        whatsapp_support_contact="help@example.test",
        whatsapp_database=str(tmp_path / "private" / "pilot.sqlite3"),
    )
    return Settings(**{**values, **updates})


def intent(action, **updates):
    return ChatIntent(
        **dict(action=action, queries=[], edits=[], offer_id=None, quantity=None, clarification="") | updates
    )


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.downloads = 0

    async def send(self, sender, payload):
        self.sent.append((sender, payload))
        return f"out-{len(self.sent)}"

    async def audio(self, media):
        self.downloads += 1
        return b"test-wav"


def make_runtime(tmp_path):
    config = settings(tmp_path)
    workers = ProviderWorkers(config)
    workers.workers = [FixtureProvider(p) for p in Provider]
    master = SimpleNamespace(require_client=lambda: None)
    chat = ShoppingChat(config, workers, master)

    async def interpret(text, session, state):
        return intent("search", queries=[RequestedProduct(query="milk", quantity=2, replaces_line_id=None)])

    chat.interpret = interpret

    async def payment_options(view, review):
        return [{"id": "upi", "title": "UPI", "note": ""}, {"id": "cards", "title": "Cards", "note": ""}]

    async def order_history(*args):
        return {"ids": [], "orders": []}

    workers.order_history = order_history
    workers.payment_options = payment_options
    runtime = Runtime(config, Inbox(config.whatsapp_database), FakeTransport(), chat)
    return runtime


def message(id="in-1", text="milk", timestamp=None, **updates):
    return (
        dict(
            id=id,
            type="text",
            text={"body": text},
            **{"from": "919999999999"},
            timestamp=str(timestamp or int(time.time())),
        )
        | updates
    )


def event(messages, phone_id="123456", statuses=None):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": phone_id},
                            "messages": messages,
                            "statuses": statuses or [],
                        },
                    }
                ]
            }
        ],
    }


async def post(client, payload, secret="test-secret"):
    data = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    return await client.post("/webhooks/whatsapp", content=data, headers={"x-hub-signature-256": signature})


async def test_ingress_auth_allowlist_duplicate_and_route_isolation(tmp_path):
    runtime = make_runtime(tmp_path)
    app = create_whatsapp_app(runtime.settings)
    app.state.runtime = runtime
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.get(
                "/webhooks/whatsapp",
                params={"hub.mode": "subscribe", "hub.verify_token": "verify-test", "hub.challenge": "123"},
            )
        ).text == "123"
        assert (
            await client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong")
        ).status_code == 403
        assert (await post(client, event([message()]), "wrong")).status_code == 403
        assert (await post(client, event([message(**{"from": "918888888888"})]))).status_code == 200
        assert (await post(client, event([message()], "654321"))).status_code == 200
        assert (await post(client, event([message(timestamp=int(time.time()) - 90000)]))).status_code == 200
        assert runtime.inbox.claim() is None
        for _ in range(2):
            assert (await post(client, event([message()]))).status_code == 200
        assert runtime.inbox.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert (await client.post("/sessions")).status_code == 404
        assert (await client.get("/docs")).status_code == 404
        assert (await client.post("/webhooks/whatsapp", content=b"x" * (256 * 1024 + 1))).status_code == 413
    runtime.inbox.close()


async def test_channel_voice_search_selection_review_pending_and_duplicate(tmp_path):
    runtime = make_runtime(tmp_path)
    calls = []

    async def transcribe(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="दूध दो पैकेट")

    runtime.chat.master.require_client = lambda: SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe))
    )
    voice = message(type="audio", audio={"id": "123"})
    runtime.inbox.enqueue(voice)
    runtime.inbox.enqueue(voice)
    await runtime.process()
    assert not await runtime.process()
    assert calls[0]["file"] == ("recording.wav", b"test-wav", "audio/wav")
    assert runtime.transport.downloads == 1
    state = runtime.inbox.load(voice["from"])
    assert len(state["choices"]) == 4  # Both providers, not a fixture exposed by runtime.
    offer_id = state["choices"][0]
    action = f"pick:{state['view']['version']}:{offer_id}"
    choose = message("in-2", type="interactive", interactive={"list_reply": {"id": action, "title": "Milk"}})
    runtime.inbox.enqueue(choose)
    await runtime.process()
    state = runtime.inbox.load(voice["from"])
    assert state["view"]["lines"][0]["quantity"] == 2
    runtime.inbox.enqueue(choose)
    assert not await runtime.process()
    runtime.inbox.enqueue(message("in-3", "review"))
    await runtime.process()
    state = runtime.inbox.load(voice["from"])
    assert state["view"]["review"]["total_paise"] == 5600
    runtime.inbox.enqueue(message("in-4", "checkout"))
    await runtime.process()
    assert runtime.inbox.load(voice["from"])["view"]["phase"] == "checkout_pending"
    while await runtime.send_one():
        pass
    assert runtime.inbox.load(voice["from"])["payment_menu"]["methods"]
    assert runtime.inbox.db.execute("SELECT COUNT(*) FROM payment_requests").fetchone()[0] == 0
    assert len(calls) == 1
    runtime.inbox.close()


async def test_stale_choice_atomic_edits_and_unknown_offer(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    old = f"pick:{state['view']['version']}:{state['choices'][0]}"
    state, _ = await runtime.chat.handle("1", state)
    before = copy.deepcopy(state)
    with pytest.raises(DomainError):
        await runtime.chat.handle("Milk", state, old)
    assert state == before

    async def bad_edit(*args):
        return intent(
            "edit",
            edits=[
                IntentEdit(kind="set_quantity", line_id=state["view"]["lines"][0]["id"], quantity=5),
                IntentEdit(kind="remove", line_id="missing", quantity=1),
            ],
        )

    runtime.chat.interpret = bad_edit
    with pytest.raises(DomainError):
        await runtime.chat.handle("change both", state)
    assert state == before
    runtime.inbox.close()


async def test_restart_keeps_state_and_never_replays_inflight_work(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.inbox.enqueue(message())
    await runtime.process()
    expected = runtime.inbox.load("919999999999")
    runtime.inbox.enqueue(message("interrupted", "review"))
    assert runtime.inbox.claim()["id"] == "interrupted"
    assert runtime.inbox.claim_reply()
    runtime.inbox.close()
    inbox = Inbox(runtime.settings.whatsapp_database)
    assert inbox.load("919999999999") == expected
    assert inbox.claim() is None
    assert inbox.db.execute("SELECT status FROM outbox LIMIT 1").fetchone()[0] == "uncertain"
    assert inbox.interrupted("919999999999")
    assert not inbox.interrupted("919999999999")
    assert oct(__import__("os").stat(runtime.settings.whatsapp_database).st_mode & 0o777) == "0o600"
    inbox.close()


async def test_stop_resume_window_and_outbound_uncertainty(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("STOP", runtime.chat.initial())
    assert state["paused"]
    state, replies = await runtime.chat.handle("milk", state)
    assert replies == []
    state, replies = await runtime.chat.handle("START", state)
    assert not state["paused"] and replies
    runtime.inbox.enqueue(message(text="hello"))
    await runtime.process()

    async def failed(*args):
        raise httpx.ReadTimeout("secret should not appear in reply")

    runtime.transport.send = failed
    assert await runtime.send_one()
    assert not await runtime.send_one()
    assert runtime.inbox.db.execute("SELECT status FROM outbox").fetchone()[0] == "uncertain"
    runtime.inbox.enqueue(message("old", timestamp=int(time.time()) - 90000))
    with runtime.inbox.db:
        runtime.inbox.db.execute("UPDATE events SET timestamp=?", (int(time.time()) - 90000,))
    await runtime.process()
    assert runtime.inbox.db.execute("SELECT status FROM events WHERE id='old'").fetchone()[0] == "expired"
    runtime.inbox.close()


async def test_late_message_does_not_mutate_basket(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.inbox.enqueue(message())
    await runtime.process()
    before = runtime.inbox.load("919999999999")
    runtime.inbox.enqueue(message("late", "1", timestamp=int(time.time()) - 20))
    await runtime.process()
    assert runtime.inbox.load("919999999999")["view"] == before["view"]
    runtime.inbox.close()


async def test_media_does_not_download_untrusted_url(tmp_path):
    transport = MetaTransport(settings(tmp_path))
    await transport.http.aclose()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"url": "https://attacker.example/audio", "file_size": 100, "mime_type": "audio/ogg"}
        )

    transport.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DomainError):
        await transport.audio({"id": "123", "url": "https://attacker.example/ignored"})
    assert len(requests) == 1
    await transport.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for real decoder regression")
async def test_actual_audio_decoding_and_duration_guard():
    def wav(seconds):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * (16000 * seconds))
        return buffer.getvalue()

    data = await normalize_audio(wav(1))
    with wave.open(io.BytesIO(data)) as audio:
        assert audio.getframerate() == 16000 and audio.getnchannels() == 1
    with pytest.raises(DomainError):
        await normalize_audio(wav(62))
    with pytest.raises(DomainError):
        await normalize_audio(b"not audio")


async def test_setup_missing_and_single_owner_binding(tmp_path):
    config = settings(tmp_path)
    app = create_whatsapp_app(config)
    async with app.router.lifespan_context(app):
        assert app.state.runtime is not None
        other = create_whatsapp_app(config)
        with pytest.raises(RuntimeError, match="Only one"):
            async with other.router.lifespan_context(other):
                pass
    other = create_whatsapp_app(settings(tmp_path, whatsapp_allowed_sender="918888888888"))
    with pytest.raises(RuntimeError, match="configuration changed"):
        async with other.router.lifespan_context(other):
            pass
    missing = create_whatsapp_app(settings(tmp_path, whatsapp_access_token=""))
    async with missing.router.lifespan_context(missing):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=missing), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).json()["status"] == "setup_required"
            assert (await client.post("/webhooks/whatsapp", json={})).status_code == 503


async def test_meta_transport_authenticated_media_and_outbound_contract(tmp_path, monkeypatch):
    config = settings(tmp_path)
    transport = MetaTransport(config)
    await transport.http.aclose()
    audio = b"synthetic encoded audio"
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.host == "lookaside.fbsbx.com":
            return httpx.Response(200, content=audio)
        if request.method == "GET":
            assert request.url.params["phone_number_id"] == "123456"
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=123",
                    "file_size": str(len(audio)),
                    "mime_type": "audio/ogg",
                    "sha256": hashlib.sha256(audio).hexdigest(),
                },
            )
        payload = json.loads(request.content)
        assert payload["messaging_product"] == "whatsapp" and payload["to"] == "919999999999"
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})

    async def normalize(data):
        assert data == audio
        return b"normalized wav"

    monkeypatch.setattr("easydial.whatsapp.transport.normalize_audio", normalize)
    transport.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await transport.audio({"id": "123"}) == b"normalized wav"
    assert await transport.send("919999999999", {"type": "text", "text": {"body": "hello"}}) == "wamid.test"
    assert len(requests) == 3
    await transport.close()


async def test_delivery_receipts_never_become_shopping_turns_or_regress(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.inbox.enqueue(message(text="hello"))
    await runtime.process()
    await runtime.send_one()
    app = create_whatsapp_app(runtime.settings)
    app.state.runtime = runtime
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for status in ["read", "delivered", "sent", "failed"]:
            assert (
                await post(client, event([], statuses=[{"id": "out-1", "status": status}]))
            ).status_code == 200
    assert runtime.inbox.db.execute("SELECT delivery FROM outbox").fetchone()[0] == "read"
    assert not await runtime.process()
    runtime.inbox.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
async def test_real_ogg_opus_voice_note(tmp_path):
    import asyncio

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    source, opus = tmp_path / "sample.wav", tmp_path / "sample.ogg"
    source.write_bytes(buffer.getvalue())
    process = await asyncio.create_subprocess_exec(
        shutil.which("ffmpeg"),
        "-v",
        "error",
        "-i",
        str(source),
        "-c:a",
        "libopus",
        str(opus),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await process.wait() == 0
    decoded = await normalize_audio(opus.read_bytes())
    with wave.open(io.BytesIO(decoded)) as wav:
        assert 0.9 < wav.getnframes() / wav.getframerate() < 1.1


async def test_multi_product_request_keeps_remaining_choices_in_selected_store(tmp_path):
    runtime = make_runtime(tmp_path)

    async def both(*args):
        return intent(
            "search",
            queries=[RequestedProduct(query=q, quantity=4 if q == "milk" else 1, replaces_line_id=None) for q in ("milk", "bread")],
        )

    runtime.chat.interpret = both
    state, _ = await runtime.chat.handle("4 milk and bread", runtime.chat.initial())
    assert all("milk" in id for id in state["choices"])
    assert [r["query"] for r in state["view"]["results"]] == ["milk", "milk"]
    state, replies = await runtime.chat.handle("1", state)
    assert state["choices"] and all("bread" in id and "blinkit" in id for id in state["choices"])
    menu = next(p for p in replies if p.get("interactive", {}).get("type") == "list")
    action = menu["interactive"]["action"]["sections"][0]["rows"][0]["id"]
    state, _ = await runtime.chat.handle("bread", state, action)
    assert len(state["view"]["lines"]) == 2 and not state["choices"]
    assert [line["quantity"] for line in state["view"]["lines"]] == [4, 1]
    runtime.inbox.close()


async def test_add_coke_preserves_milk_and_only_offers_current_store(tmp_path):
    from easydial.models import Offer, SearchResult

    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    milk_id = next(i for i in state["choices"] if state["offers"][i]["provider"] == "instamart")
    state, _ = await runtime.chat.handle(str(state["choices"].index(milk_id) + 1), state)
    state["view"]["lines"][0]["quantity"] = 3
    state["view"]["synced_provider"] = "instamart"

    async def search(query, address):
        return [
            SearchResult(
                provider=p,
                query=query,
                status="ok",
                offers=[
                    Offer(
                        id=f"coke-{p.value}",
                        provider=p,
                        product_id="coke",
                        sku_id="330",
                        name="Diet Coke",
                        pack_size="330 ml",
                        price_paise=4000,
                        available=True,
                        mode="live",
                        address_label=address,
                    )
                ],
            )
            for p in Provider
        ]

    runtime.chat.workers.search = search
    state, _ = await runtime.chat.handle("add diet coke", state)
    assert state["choices"] == ["coke-instamart"]
    assert len(state["view"]["results"]) == 2  # Comparison evidence remains available.
    state, replies = await runtime.chat.handle("1", state)
    assert [(x["offer"]["name"], x["quantity"]) for x in state["view"]["lines"]] == [
        ("Amul Taaza Toned Milk", 3),
        ("Diet Coke", 2),
    ]  # Fake interpreter requests two packs; existing milk quantity is unchanged.
    assert "Diet Coke" in replies[0]["text"]["body"] and "Milk" in replies[0]["text"]["body"]
    runtime.inbox.close()


async def test_old_mixed_store_choice_recovers_without_removing_basket(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    # Simulate a menu issued before the fix, alongside an existing Instamart basket.
    legacy = copy.deepcopy(state)
    item = next(i for i in state["choices"] if state["offers"][i]["provider"] == "instamart")
    state, _ = await runtime.chat.handle(str(state["choices"].index(item) + 1), state)
    before = copy.deepcopy(state["view"]["lines"])
    state["choices"], state["offers"] = legacy["choices"], legacy["offers"]
    state["view"]["synced_provider"] = "instamart"
    state, replies = await runtime.chat.handle("1", state)
    assert state["view"]["lines"] == before
    assert all(state["offers"][i]["provider"] == "instamart" for i in state["choices"])
    assert "existing basket is kept" in replies[0]["text"]["body"]
    runtime.inbox.close()


async def test_compound_request_partial_failure_keeps_missing_quantity_visible(tmp_path):
    from easydial.models import SearchResult

    runtime = make_runtime(tmp_path)
    original_search = runtime.chat.workers.search

    async def both(*args):
        return intent('search', queries=[
            RequestedProduct(query='milk', quantity=4, replaces_line_id=None),
            RequestedProduct(query='bread', quantity=1, replaces_line_id=None),
        ])

    async def partial(query, address, provider=None):
        if query == 'milk':
            return [SearchResult(provider=Provider.BLINKIT, query=query, status='error', message='loading')]
        return await original_search(query, address)

    runtime.chat.interpret = both
    runtime.chat.workers.search = partial
    state, replies = await runtime.chat.handle('4 milk and bread', runtime.chat.initial())
    assert state['unresolved_requests'] == [{'query': 'milk', 'quantity': 4}]
    assert '4 × milk' in json.dumps(replies, ensure_ascii=False)
    assert not state['choices']
    assert state['request_queue'][0]['query'] == 'bread'
    state, replies = await runtime.chat.handle('skip item', state)
    assert state['choices']
    state, replies = await runtime.chat.handle('1', state)
    assert len(state['view']['lines']) == 1
    assert not state['request_queue']
    runtime.inbox.close()
