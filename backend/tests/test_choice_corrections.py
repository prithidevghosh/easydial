import copy
import json
from types import SimpleNamespace

from easydial.controller import Session
from easydial.models import SessionView
from easydial.whatsapp.conversation import ShoppingChat
from tests.fakes import intent_tool_responses
from tests.test_whatsapp import intent, make_runtime, settings


async def selected(runtime):
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    ids = list(state["choices"])
    state, _ = await runtime.chat.handle("1", state)
    return state, ids


async def test_correct_previous_selection_keeps_menu_and_replaces_without_adding(tmp_path):
    runtime = make_runtime(tmp_path)
    state, ids = await selected(runtime)
    # First two options belong to the same provider; no active choices remain for milk.
    assert not state["choices"]
    assert ids[1] in state["offers"]
    line = state["view"]["lines"][0]
    assert line["quantity"] == 2
    state = json.loads(json.dumps(state))  # Persisted context survives restart/serialization.

    async def interpret(*args):
        return intent("select", offer_id=ids[1], replaces_line_id=line["id"])

    runtime.chat.interpret = interpret
    state, replies = await runtime.chat.handle("first wla nhi, ap second wla choose kro", state)
    assert len(state["view"]["lines"]) == 1
    assert state["view"]["lines"][0]["offer"]["id"] == ids[1]
    assert state["view"]["lines"][0]["quantity"] == 2
    assert state["recent_selection"]["choices"] == ids
    assert "Your Blinkit basket" in replies[0]["text"]["body"]
    runtime.inbox.close()


async def test_new_search_discards_old_selection_context(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await selected(runtime)
    state, _ = await runtime.chat.handle("another search", state)
    assert state.get("recent_selection") is None
    runtime.inbox.close()


async def test_hindi_model_clarification_is_not_sent_to_customer(tmp_path):
    async def parse(**kwargs):
        assert "MUST be in English" in kwargs["instructions"]
        assert "in the user's language" not in kwargs["instructions"]
        return SimpleNamespace(output_parsed=intent("clarify", clarification="कृपया फिर से बोलें"))

    master = SimpleNamespace(require_client=lambda: SimpleNamespace(responses=intent_tool_responses(parse)))
    chat = ShoppingChat(settings(tmp_path), None, master)
    session = Session(SessionView(id="test", mode="live", address_label="test"))
    result = await chat.interpret("second wala", session, {"choices": []})
    assert result.clarification == "Which product or option would you like?"


async def test_legacy_recovery_refuses_ambiguous_multi_provider_menu(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await selected(runtime)
    state.pop("recent_selection")
    session = Session(SessionView.model_validate(state["view"]), offers={})
    before = copy.deepcopy(state)
    ShoppingChat.restore_legacy_choices(session, state)
    assert state == before
    # With only one store's results, the historical deterministic first-two list is recoverable.
    session.view.results = [session.view.results[0]]
    ShoppingChat.restore_legacy_choices(session, state)
    assert len(state["recent_selection"]["choices"]) == 2
    runtime.inbox.close()
