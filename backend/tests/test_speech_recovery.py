import copy
from types import SimpleNamespace

import pytest

from easydial.controller import DomainError, Session
from easydial.models import IntentEdit, SessionView, now
from easydial.whatsapp.conversation import RemovalCheck, ShoppingChat
from tests.test_choice_corrections import selected
from tests.test_whatsapp import intent, make_runtime, message, settings


async def test_unclear_removal_gets_independent_check_before_any_edit(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await selected(runtime)
    view = SessionView.model_validate(state["view"])
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs["text_format"])
        if kwargs["text_format"] is RemovalCheck:
            return SimpleNamespace(output_parsed=RemovalCheck(explicit_removal=False))
        return SimpleNamespace(
            output_parsed=intent(
                "edit", edits=[IntentEdit(kind="remove", line_id=view.lines[0].id, quantity=1)]
            )
        )

    chat = ShoppingChat(
        settings(tmp_path),
        None,
        SimpleNamespace(require_client=lambda: SimpleNamespace(responses=SimpleNamespace(parse=parse))),
    )
    result = await chat.interpret("हरके के मूव कीजिए", Session(view), state)
    assert result.action == "clarify" and result.edits == []
    assert result.clarification == "I couldn't understand that clearly. Could you say it again?"
    assert len(calls) == 2
    runtime.inbox.close()


async def empty_with_history(runtime):
    state, _ = await selected(runtime)
    original = copy.deepcopy(state["view"]["lines"])

    async def remove(*args):
        return intent("edit", edits=[IntentEdit(kind="remove", line_id=original[0]["id"], quantity=1)])

    runtime.chat.interpret = remove
    state, _ = await runtime.chat.handle("remove milk", state)
    assert state["previous_basket"] == original
    assert not state["view"]["lines"]

    async def restore(*args):
        return intent("restore")

    runtime.chat.interpret = restore
    return state, original


async def test_restore_rechecks_exact_products_and_preserves_quantities(tmp_path):
    runtime = make_runtime(tmp_path)
    state, original = await empty_with_history(runtime)
    state, _ = await runtime.chat.handle("Put the same order in the cart again", state)
    assert len(state["view"]["lines"]) == 1
    assert state["view"]["lines"][0]["quantity"] == original[0]["quantity"]
    assert state["view"]["lines"][0]["offer"]["product_id"] == original[0]["offer"]["product_id"]
    assert state["view"]["review"] is None and state["view"]["payment_preparation"] is None
    runtime.inbox.close()


async def test_restore_changed_prices_wait_for_explicit_confirmation(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await empty_with_history(runtime)

    async def refresh(offer, address):
        return offer.model_copy(update={"retrieved_at": now(), "price_paise": 9900})

    runtime.chat.workers.refresh_offer = refresh
    state, replies = await runtime.chat.handle("restore previous", state)
    assert not state["view"]["lines"]
    action = replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["id"]
    state, _ = await runtime.chat.handle("Restore basket", state, action_id=action)
    assert state["view"]["lines"][0]["offer"]["price_paise"] == 9900
    runtime.inbox.close()


async def test_restore_failure_does_not_partially_restore(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await empty_with_history(runtime)
    before = copy.deepcopy(state)

    async def refresh(*args):
        raise DomainError("Store unavailable")

    runtime.chat.workers.refresh_offer = refresh
    with pytest.raises(DomainError):
        await runtime.chat.handle("restore previous", state)
    assert state == before
    runtime.inbox.close()


async def test_historical_context_is_scoped_and_does_not_restore_automatically(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await selected(runtime)
    inbox = runtime.inbox
    inbox.enqueue(message("with-items"))
    inbox.finish(inbox.claim(), state, [])
    original = copy.deepcopy(state["view"]["lines"])
    state["view"]["lines"] = []
    state["view"]["version"] += 1
    state.pop("previous_basket", None)
    inbox.enqueue(message("empty"))
    inbox.finish(inbox.claim(), state, [])
    restored_context = inbox.load("919999999999")
    assert restored_context["previous_basket"] == original
    assert restored_context["view"]["lines"] == []
    assert inbox.load("another-user") is None
    inbox.close()


async def test_number_after_selection_never_guesses_add_or_replace(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await selected(runtime)
    from easydial.models import Offer

    session = Session(
        SessionView.model_validate(state["view"]),
        offers={key: Offer.model_validate(value) for key, value in state["offers"].items()},
    )
    chat = ShoppingChat(
        settings(tmp_path),
        None,
        SimpleNamespace(require_client=lambda: (_ for _ in ()).throw(AssertionError("No model needed"))),
    )
    result = await chat.interpret("2", session, state)
    assert result.action == "clarify"
    assert result.replaces_line_id is None
    runtime.inbox.close()


async def test_selection_verifier_recovers_missing_replacement_target(tmp_path):
    from easydial.models import Offer
    from easydial.whatsapp.conversation import SelectionDecision

    runtime = make_runtime(tmp_path)
    state, ids = await selected(runtime)
    session = Session(
        SessionView.model_validate(state["view"]),
        offers={key: Offer.model_validate(value) for key, value in state["offers"].items()},
    )

    async def parse(**kwargs):
        if kwargs["text_format"] is SelectionDecision:
            return SimpleNamespace(output_parsed=SelectionDecision(operation="replace"))
        return SimpleNamespace(output_parsed=intent("select", offer_id=ids[1]))

    chat = ShoppingChat(
        settings(tmp_path),
        None,
        SimpleNamespace(require_client=lambda: SimpleNamespace(responses=SimpleNamespace(parse=parse))),
    )
    result = await chat.interpret("first nahi second", session, state)
    assert result.replaces_line_id == session.view.lines[0].id
    runtime.inbox.close()


@pytest.mark.parametrize("action", ["search", "edit", "select", "review", "checkout", "restore", "payment"])
async def test_unclear_request_blocks_every_action(tmp_path, action):
    runtime = make_runtime(tmp_path)
    state, _ = await selected(runtime)
    before = copy.deepcopy(state["view"]["lines"])

    async def parse(**kwargs):
        proposed = intent(action)
        proposed.unclear_request = True
        proposed.clarification = "Did you mean checkout?"
        return SimpleNamespace(output_parsed=proposed)

    runtime.chat.master = SimpleNamespace(
        require_client=lambda: SimpleNamespace(responses=SimpleNamespace(parse=parse))
    )
    del runtime.chat.interpret  # Remove the fixture interpreter; exercise the real harness.
    state, replies = await runtime.chat.handle("garbled request", state)
    assert state["view"]["lines"] == before
    assert replies == [{"type": "text", "text": {
        "body": "I couldn't understand that clearly. Could you say it again?"
    }}]
    runtime.inbox.close()
