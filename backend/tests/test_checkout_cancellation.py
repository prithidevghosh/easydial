import time
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.models import SessionView
from tests.test_payment_handoff import reviewed
from tests.test_whatsapp import intent, make_runtime


async def pending_qr(runtime):
    state = await reviewed(runtime)
    view = SessionView.model_validate(state["view"])
    await runtime.payments.monitor.capture("alice", view, "upi", runtime.chat.workers)
    runtime.payments.monitor.activate("alice", view.id)
    with runtime.inbox.db:
        runtime.inbox.db.execute(
            "INSERT INTO payment_requests VALUES (?,?,?,?,?,?,?,?,?)",
            ("qr-test", "alice", view.id, "bill", "ready", "token", "{}", int(time.time()), int(time.time()) + 300),
        )
    runtime.payments.schedule_check("alice", view.id)
    runtime.payments.monitor.check = AsyncMock(return_value={
        "status": "pending", "reason": "no_matching_order", "history_issues": [], "rejected_fields": [],
    })
    runtime.chat.workers.clear = AsyncMock()
    return state


@pytest.mark.parametrize("phrase", [
    "ap order cancel kr dijiye and cart khali kr dijiye",
    "ap cart khali kr dijiye",
])
async def test_clear_cart_stops_qr_without_payment_guard_loop(tmp_path, phrase):
    runtime = make_runtime(tmp_path)
    state = await pending_qr(runtime)
    old_id = state["view"]["id"]
    runtime.chat.interpret = AsyncMock(return_value=intent("cancel_checkout", clear_cart=True))
    state, replies = await runtime.chat.handle(phrase, state, customer="alice")
    assert not state["view"]["lines"]
    assert state["view"]["id"] != old_id
    runtime.chat.workers.clear.assert_awaited_once()
    assert not runtime.chat.workers.clear.call_args.args[0].lines
    runtime.payments.monitor.check.assert_awaited_once()
    assert runtime.payments.existing("alice", old_id)["status"] == "revoked"
    assert runtime.inbox.db.execute("SELECT done FROM automatic_order_checks").fetchone()[0] == 1
    with pytest.raises(DomainError):
        runtime.payments.qr_artifact("alice", "qr-test")
    assert "Don’t pay the old QR" in replies[0]["text"]["body"]
    assert runtime.payments.existing("alice", state["view"]["id"]) is None
    runtime.inbox.close()


async def test_clear_failure_discards_local_draft_preserves_old_payment_and_can_start_again(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await pending_qr(runtime)
    runtime.chat.interpret = AsyncMock(return_value=intent("cancel_checkout", clear_cart=True))
    runtime.chat.workers.clear.side_effect = DomainError("unavailable")
    old_id = state["view"]["id"]
    old_lines = state["view"]["lines"]
    state, replies = await runtime.chat.handle("empty cart", state, customer="alice")
    assert not state["view"]["lines"]
    assert state["view"]["id"] != old_id
    assert state["view"]["payment_preparation"] is None
    assert "cart_checkpoint" not in state
    assert state["store_cleanup_lines"] == old_lines
    assert state["last_order_id"] == old_id
    assert runtime.payments.existing("alice", old_id)["status"] == "revoked"
    assert runtime.payments.existing("alice", state["view"]["id"]) is None
    assert "EasyDial basket is cleared" in replies[0]["text"]["body"]
    assert "couldn’t confirm" in replies[0]["text"]["body"]
    assert "Don’t pay the old QR" in replies[0]["text"]["body"]
    # Repeating clear on the now-empty local draft must retain the separate
    # leftover identities needed for the next store reconciliation.
    again, _ = await runtime.chat.handle("empty cart", state, customer="alice")
    assert again['view']['lines'] == []
    assert again['store_cleanup_lines'] == old_lines
    assert again['last_order_id'] == old_id
    runtime.inbox.close()


@pytest.mark.parametrize("result", [
    {"status": "confirmed", "order": {"order_id": "placed"}},
    {"status": "pending", "reason": "history_read_error"},
])
async def test_cancellation_does_not_claim_placed_or_uncertain_order_cancelled(tmp_path, result):
    runtime = make_runtime(tmp_path)
    state = await pending_qr(runtime)
    runtime.chat.interpret = AsyncMock(return_value=intent("cancel_checkout", clear_cart=True))
    runtime.payments.monitor.check.return_value = result
    state, replies = await runtime.chat.handle("cancel and clear", state, customer="alice")
    runtime.chat.workers.clear.assert_awaited_once()
    assert not state["view"]["lines"]
    assert "couldn’t confirm cancellation" in replies[0]["text"]["body"]
    assert "Orders page" in replies[0]["text"]["body"]
    runtime.inbox.close()
