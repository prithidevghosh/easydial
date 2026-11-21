"""Execution regressions separate from the opt-in live language eval."""

import copy

import pytest

from easydial.controller import DomainError
from easydial.models import IntentEdit, RequestedProduct
from tests.test_whatsapp import intent, make_runtime


async def basket(runtime):
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    return state


async def test_remove_and_discover_completes_both_parts_without_selecting(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await basket(runtime)
    old_id = state["view"]["lines"][0]["id"]

    async def interpret(*args):
        return intent(
            "search",
            queries=[RequestedProduct(query="bread", quantity=1, replaces_line_id=None)],
            edits=[IntentEdit(kind="remove", line_id=old_id, quantity=1)],
        )

    runtime.chat.interpret = interpret
    state, replies = await runtime.chat.handle("doodh nahi, ek bread dikhao", state)
    assert state["view"]["lines"] == []
    assert state["choices"]
    assert all("Bread" in state["offers"][key]["name"] for key in state["choices"])
    assert all(state["offers"][key]["suggested_quantity"] == 1 for key in state["choices"])
    assert replies[-1]["type"] == "interactive"
    assert not any("Your basket is empty" in r.get("text", {}).get("body", "") for r in replies)
    runtime.inbox.close()


async def test_compound_search_failure_does_not_partially_change_draft(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await basket(runtime)
    before = copy.deepcopy(state)

    async def interpret(*args):
        return intent(
            "search",
            queries=[RequestedProduct(query="bread", quantity=1, replaces_line_id=None)],
            edits=[IntentEdit(kind="remove", line_id=state["view"]["lines"][0]["id"], quantity=1)],
        )

    async def fail(*args):
        raise DomainError("Store unavailable")

    runtime.chat.interpret = interpret
    runtime.chat.workers.search = fail
    with pytest.raises(DomainError):
        await runtime.chat.handle("remove milk and find bread", state)
    assert state == before
    runtime.inbox.close()


async def test_compound_edits_blocked_after_payment_request(tmp_path):
    from types import SimpleNamespace

    runtime = make_runtime(tmp_path)
    state = await basket(runtime)
    before = copy.deepcopy(state)
    runtime.chat.payments = SimpleNamespace(existing=lambda *args: True)

    async def interpret(*args):
        return intent(
            "search",
            queries=[RequestedProduct(query="bread", quantity=1, replaces_line_id=None)],
            edits=[IntentEdit(kind="remove", line_id=state["view"]["lines"][0]["id"], quantity=1)],
        )

    runtime.chat.interpret = interpret
    with pytest.raises(DomainError, match="payment request"):
        await runtime.chat.handle("replace everything with bread", state, customer="test")
    assert state == before
    runtime.inbox.close()
