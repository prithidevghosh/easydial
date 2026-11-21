import copy

import pytest

from easydial.controller import DomainError
from easydial.models import Provider, RequestedProduct, SearchResult
from tests.test_whatsapp import intent, make_runtime


async def setup_switch(runtime):
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("3", state)
    assert state["view"]["lines"][0]["offer"]["provider"] == "instamart"
    state, _ = await runtime.chat.handle("review", state)
    line = state["view"]["lines"][0]

    async def interpret(*args):
        return intent(
            "search",
            target_provider=Provider.BLINKIT,
            switch_store=True,
            queries=[RequestedProduct(query="milk", quantity=line["quantity"], replaces_line_id=line["id"])],
            clarification="I have already moved your order to Blinkit.",
        )

    runtime.chat.interpret = interpret
    return state


async def test_explicit_switch_searches_only_destination_and_clears_source_on_selection(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await setup_switch(runtime)
    previous = copy.deepcopy(state["view"]["lines"])
    calls = []

    async def wrong_store(*args):
        raise AssertionError("Must not search Instamart for a Blinkit-only request")

    runtime.chat.workers.workers[1].search = wrong_store

    async def clear(view):
        assert view.lines == [] and view.synced_provider == Provider.INSTAMART
        calls.append("clear")

    runtime.chat.workers.clear = clear
    state, replies = await runtime.chat.handle("move to Blinkit", state)
    assert state["view"]["lines"] == previous
    assert calls == []
    assert all(state["offers"][key]["provider"] == "blinkit" for key in state["choices"])
    assert not any("already moved" in r.get("text", {}).get("body", "") for r in replies)
    assert replies[-1]["type"] == "interactive"
    state, _ = await runtime.chat.handle("1", state)
    assert calls == ["clear"]
    assert len(state["view"]["lines"]) == 1
    assert state["view"]["lines"][0]["offer"]["provider"] == "blinkit"
    assert state["view"]["lines"][0]["quantity"] == previous[0]["quantity"]
    assert state["cart_checkpoint"] is None
    assert state["view"]["review"] is None
    state, _ = await runtime.chat.handle("review", state)
    assert state["view"]["review"]["provider"] == "blinkit"
    runtime.inbox.close()


async def test_unavailable_destination_never_falls_back_or_removes_source(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await setup_switch(runtime)
    before = copy.deepcopy(state["view"]["lines"])

    async def unavailable(query, address):
        return SearchResult(provider=Provider.BLINKIT, query=query, status="error", message="blocked")

    runtime.chat.workers.workers[0].search = unavailable
    state, replies = await runtime.chat.handle("move to Blinkit", state)
    assert state["choices"] == []
    assert state["view"]["lines"] == before
    assert "Instamart basket is unchanged" in replies[-1]["text"]["body"]
    runtime.inbox.close()


async def test_failed_source_clear_preserves_draft_and_blocks_destination(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await setup_switch(runtime)
    state, _ = await runtime.chat.handle("move to Blinkit", state)
    before = copy.deepcopy(state["view"])

    async def clear(view):
        raise DomainError("Could not confirm removal")

    runtime.chat.workers.clear = clear
    with pytest.raises(DomainError):
        await runtime.chat.handle("1", state)
    assert state["view"] == before
    runtime.inbox.close()


async def test_pending_switch_cannot_review_or_checkout_old_store(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await setup_switch(runtime)
    state, _ = await runtime.chat.handle("move to Blinkit", state)

    async def forbidden(*args):
        raise AssertionError("Do not review the source store during a pending switch")

    runtime.chat.workers.review = forbidden
    for text in ["review", "checkout"]:
        state, replies = await runtime.chat.handle(text, state)
        assert "Choose your Blinkit replacement first" in replies[0]["text"]["body"]
        assert replies[-1]["type"] == "interactive"
    runtime.inbox.close()


async def test_switch_is_blocked_after_payment_attempt(tmp_path):
    from types import SimpleNamespace

    runtime = make_runtime(tmp_path)
    state = await setup_switch(runtime)
    before = copy.deepcopy(state)
    runtime.chat.payments = SimpleNamespace(existing=lambda *args: True)
    with pytest.raises(DomainError, match="payment request"):
        await runtime.chat.handle("move to Blinkit", state, customer="alice")
    assert state == before
    runtime.inbox.close()


async def test_followup_search_keeps_pending_destination(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await setup_switch(runtime)
    state, _ = await runtime.chat.handle("move to Blinkit", state)

    async def followup(*args):
        return intent("search", queries=[RequestedProduct(query="bread", quantity=1, replaces_line_id=None)])

    runtime.chat.interpret = followup
    state, _ = await runtime.chat.handle("show bread instead", state)
    assert state["store_switch"]["target"] == "blinkit"
    assert all(state["offers"][key]["provider"] == "blinkit" for key in state["choices"])
    runtime.inbox.close()
