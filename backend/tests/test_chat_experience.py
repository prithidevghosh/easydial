import asyncio

import httpx

from easydial.controller import Session
from easydial.models import Offer, Provider, SessionView
from easydial.providers.images import product_image_url
from easydial.providers.instamart import parse_card
from easydial.whatsapp.conversation import ShoppingChat
from easydial.whatsapp.progress import TurnProgress
from easydial.whatsapp.transport import MetaTransport
from tests.test_whatsapp import make_runtime, message, settings


def test_product_image_extraction_and_untrusted_hosts():
    url = "https://instamart-media-assets.swiggy.com/swiggy/image/upload/coke.png"
    item = parse_card(
        {
            "path": "/p/diet-coke-123",
            "name": "Diet Coke",
            "pack": "330 ml",
            "price": "₹40",
            "add": True,
            "image": url,
        },
        "home",
    )
    assert item.image_url == url
    for invalid in [
        "http://cdn.grofers.com/a.png",
        "https://localhost/a.png",
        "file:///a.png",
        "https://cdn.grofers.com.evil.test/a.png",
        "https://u:p@cdn.grofers.com/a.png",
    ]:
        assert product_image_url(invalid) is None


def test_choices_show_at_most_two_photos_and_keep_numbers():
    view = SessionView(id="x", mode="live", address_label="home")
    offers = {
        str(i): Offer(
            id=str(i),
            provider=Provider.INSTAMART,
            product_id=str(i),
            sku_id=str(i),
            name=f"Product {i}",
            pack_size="1 pack",
            price_paise=4000,
            available=True,
            mode="live",
            address_label="home",
            image_url=f"https://instamart-media-assets.swiggy.com/{i}.png",
        )
        for i in range(4)
    }
    replies = ShoppingChat.choice_messages(Session(view, offers=offers), list(offers))
    assert sum(r["type"] == "image" for r in replies) == 2
    assert replies[-1]["type"] == "interactive"
    assert "Product 0" not in next(r["text"]["body"] for r in replies if r["type"] == "text")
    assert "In stock" in replies[0]["image"]["caption"]
    assert "Product 3" in next(r["text"]["body"] for r in replies if r["type"] == "text")
    assert len(replies[-1]["interactive"]["action"]["sections"][0]["rows"]) == 4


async def test_fast_turn_has_no_progress_message_and_slow_turn_has_one(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.inbox.enqueue(message())
    row = runtime.inbox.claim()
    fast = TurnProgress(runtime.inbox, runtime.transport, row, "Checking…", delay=1)
    fast.start()
    await fast.close()
    assert runtime.transport.sent == []
    slow = TurnProgress(runtime.inbox, runtime.transport, row, "Checking…", delay=0.01)
    slow.update("Finding matching products…")
    slow.start()
    await asyncio.sleep(0.03)
    await slow.close()
    assert len(runtime.transport.sent) == 1
    assert runtime.transport.sent[0][1]["text"]["body"] == "Finding matching products…"
    retry = TurnProgress(runtime.inbox, runtime.transport, row, "Checking…", delay=0.01)
    retry.start()
    await asyncio.sleep(0.03)
    await retry.close()
    assert len(runtime.transport.sent) == 1
    runtime.inbox.close()


async def test_typing_payload_and_definite_image_failure_falls_back_to_text(tmp_path):
    transport = MetaTransport(settings(tmp_path))
    await transport.http.aclose()
    sent = []

    def respond(request):
        import json

        payload = json.loads(request.content)
        sent.append(payload)
        if payload.get("typing_indicator"):
            return httpx.Response(200, json={"success": True})
        if payload["type"] == "image":
            return httpx.Response(400, json={"error": {"code": 131053}})
        return httpx.Response(200, json={"messages": [{"id": "ok"}]})

    transport.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    await transport.typing("inbound")
    assert sent[0]["message_id"] == "inbound" and sent[0]["typing_indicator"] == {"type": "text"}
    assert (
        await transport.send(
            "123",
            {
                "type": "image",
                "image": {"link": "https://cdn.grofers.com/a.png", "caption": "Diet Coke · ₹40 · In stock"},
            },
        )
        == "ok"
    )
    assert sent[-1]["text"]["body"] == "Diet Coke · ₹40 · In stock"
    await transport.close()


async def test_hindi_new_product_reinterprets_invalid_edit_without_old_history(tmp_path):
    import json
    from types import SimpleNamespace

    from easydial.models import IntentEdit, RequestedProduct
    from tests.test_whatsapp import intent

    calls = []

    async def parse(**kwargs):
        calls.append(json.loads(kwargs["input"]))
        if len(calls) == 1:
            return SimpleNamespace(
                output_parsed=intent(
                    "edit", edits=[IntentEdit(kind="set_quantity", line_id="invented", quantity=1)]
                )
            )
        return SimpleNamespace(
            output_parsed=intent(
                "search", queries=[RequestedProduct(query="curd", quantity=1, replaces_line_id=None)]
            )
        )

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    chat = ShoppingChat(settings(tmp_path), None, SimpleNamespace(require_client=lambda: client))
    session = Session(SessionView(id="test", mode="live", address_label="home"))
    session.say("Clear the cart now.", "user")
    result = await chat.interpret("ek dahi ka packet order krna h", session, {"choices": []})
    assert result.action == "search" and result.queries[0].quantity == 1
    assert len(calls) == 2 and calls[1]["validation_feedback"]
    assert calls[0]["CURRENT_BASKET"] == []
    assert "Clear the cart" not in json.dumps(calls)
    assert session.view.lines == []


async def test_repeated_invalid_edit_clarifies_without_mutation(tmp_path):
    from types import SimpleNamespace

    from easydial.models import IntentEdit
    from tests.test_whatsapp import intent

    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=intent("edit", edits=[IntentEdit(kind="remove", line_id="missing", quantity=1)])
        )

    chat = ShoppingChat(
        settings(tmp_path),
        None,
        SimpleNamespace(require_client=lambda: SimpleNamespace(responses=SimpleNamespace(parse=parse))),
    )
    session = Session(SessionView(id="test", mode="live", address_label="home"))
    result = await chat.interpret("remove it", session, {"choices": []})
    assert result.action == "clarify"
    assert len(calls) == 2
    assert session.view.lines == []


async def test_search_keeps_provider_diagnostics_out_of_customer_messages(tmp_path):
    import json

    from easydial.models import SearchResult
    from tests.fakes import FixtureProvider

    runtime = make_runtime(tmp_path)
    good = await FixtureProvider(Provider.INSTAMART).search("milk", "test home")
    failure = SearchResult(
        provider=Provider.BLINKIT,
        query="milk",
        status="error",
        message="Complete the browser verification challenge manually",
    )

    async def search(query, address):
        return [failure, good]

    runtime.chat.workers.search = search
    state, replies = await runtime.chat.handle("milk", runtime.chat.initial())
    output = json.dumps(replies)
    assert replies[-1]["type"] == "interactive"
    assert "browser" not in output and "challenge" not in output
    assert "Adding to" not in output
    assert "compare all stores" in output
    assert state["view"]["results"][0]["message"] == failure.message
    good.offers = []
    _, replies = await runtime.chat.handle("milk", runtime.chat.initial())
    assert "try again shortly" in replies[0]["text"]["body"]
    assert "browser" not in json.dumps(replies)
    runtime.inbox.close()


async def test_basket_answers_contents_and_store_without_duplicate_prompt(tmp_path):
    from tests.test_whatsapp import intent

    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("3", state)

    async def interpret(*args):
        return intent("basket")

    runtime.chat.interpret = interpret
    state, replies = await runtime.chat.handle(
        "What is in the cart right now and is it from Instamart or Blinkit?", state
    )
    body = replies[0]["text"]["body"]
    assert body.startswith("Your Instamart basket:")
    assert "Amul Taaza Toned Milk" in body and "500 ml" in body
    assert "Want to change anything?" not in body
    assert replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Review basket"
    runtime.inbox.close()
