import copy
from datetime import timedelta

import pytest

from easydial.controller import DomainError
from easydial.models import Provider, SessionView, now
from easydial.providers.cart_guard import CartConflict
from easydial.whatsapp import reconciliation
from tests.test_browser_contract import ContractBlinkit, basket
from tests.test_whatsapp import make_runtime


def existing(worker, view, quantity=1):
    line = view.lines[0]
    worker.cart = {
        "items": [{"name": line.offer.name, "pack": line.offer.pack_size, "quantity": quantity}],
        "text": "Basket",
        "address": "Test home",
        "bill": [{"label": "Items total", "value": "₹33"}],
        "total": "₹33",
    }


async def test_preview_is_read_only_and_explicit_recovery_survives_fresh_worker():
    worker, view = ContractBlinkit(), basket()
    view.synced_provider = Provider.BLINKIT
    existing(worker, view)
    with pytest.raises(CartConflict) as error:
        await worker.review(view)
    pending = reconciliation.proposal(view, error.value)
    assert worker.writes == []
    assert pending["checkpoint"]["lines"][0]["quantity"] == 1
    # Recreate the worker as on restart, adopt only after explicit confirmation.
    worker = ContractBlinkit()
    existing(worker, view)
    await worker.adopt_checkpoint(SessionView.model_validate(pending["checkpoint"]), pending["address"])
    assert worker.writes == []
    await worker.review(view)
    assert worker.writes == [("127081", 2)]


@pytest.mark.parametrize("change", ["quantity", "address"])
async def test_changed_cart_or_address_rejects_confirmation_without_writes(change):
    worker, view = ContractBlinkit(), basket()
    view.synced_provider = Provider.BLINKIT
    existing(worker, view)
    with pytest.raises(CartConflict) as error:
        await worker.review(view)
    pending = reconciliation.proposal(view, error.value)
    if change == "quantity":
        worker.cart["items"][0]["quantity"] = 4
    else:
        worker.cart["address"] = "Another address"
    with pytest.raises(DomainError, match="changed again"):
        await worker.adopt_checkpoint(SessionView.model_validate(pending["checkpoint"]), pending["address"])
    assert worker.writes == [] and worker.guard.owner is None


async def test_unknown_cart_item_cannot_be_adopted_or_removed():
    worker, view = ContractBlinkit(), basket()
    view.synced_provider = Provider.BLINKIT
    existing(worker, view)
    worker.cart["items"][0]["name"] = "Unknown product"
    with pytest.raises(CartConflict) as error:
        await worker.review(view)
    pending = reconciliation.proposal(view, error.value)
    assert pending["checkpoint"] is None
    assert len(reconciliation.messages(view, pending)) == 1
    assert worker.writes == []


async def test_chat_confirmation_checks_token_expiry_and_saves_checkpoint(tmp_path):
    runtime = make_runtime(tmp_path)
    state = runtime.chat.initial()
    view = basket()
    state["view"] = view.model_dump(mode="json")
    worker = ContractBlinkit()
    existing(worker, view)
    runtime.chat.workers.workers[0] = worker
    state, replies = await runtime.chat.handle("review", state)
    assert worker.writes == []
    assert "Currently in the store" in replies[0]["text"]["body"]
    action = replies[1]["interactive"]["action"]["buttons"][0]["reply"]["id"]
    stale = copy.deepcopy(state)
    stale["cart_reconciliation"]["created_at"] = (now() - timedelta(minutes=6)).isoformat()
    with pytest.raises(DomainError, match="expired"):
        await runtime.chat.handle("Update cart", stale, action)
    with pytest.raises(DomainError, match="expired"):
        await runtime.chat.handle("Update cart", state, action.rsplit(":", 1)[0] + ":wrong")
    state, replies = await runtime.chat.handle("Update cart", state, action)
    assert worker.writes == [("127081", 2)]
    assert state["cart_checkpoint"]["lines"][0]["quantity"] == 2
    assert state["cart_reconciliation"] is None
    assert state["view"]["review"] is not None
    assert state["view"]["payment_preparation"] is None
    with pytest.raises(DomainError):
        await runtime.chat.handle("Update cart", state, action)
    runtime.inbox.close()
