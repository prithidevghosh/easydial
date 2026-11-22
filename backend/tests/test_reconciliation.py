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


@pytest.mark.parametrize("source", ["receipt", "discarded_draft"])
async def test_completed_receipt_allows_preview_of_leftover_item_without_implicit_removal(tmp_path, source):
    from types import SimpleNamespace

    runtime = make_runtime(tmp_path)
    state = runtime.chat.initial()
    view = basket()
    state['view'] = view.model_dump(mode='json')
    state['last_order_id'] = 'completed-order'
    popcorn = view.lines[0].model_copy(deep=True)
    popcorn.offer = popcorn.offer.model_copy(update={'id': 'old-popcorn', 'product_id': 'popcorn',
                                                    'sku_id': 'popcorn', 'name': 'Old popcorn', 'pack_size': '120 g'})
    popcorn.quantity = 1
    receipt = SimpleNamespace(review=SimpleNamespace(provider=Provider.BLINKIT), lines=[popcorn])
    runtime.chat.status_view = lambda state, customer, requested_id: receipt if requested_id == 'completed-order' else None
    if source == "discarded_draft":
        state.pop('last_order_id')
        state['store_cleanup_lines'] = [popcorn.model_dump(mode='json')]

    worker = ContractBlinkit()
    existing(worker, view, quantity=2)
    worker.cart['items'].append({'name': 'Old popcorn', 'pack': '120 g', 'quantity': 1})
    runtime.chat.workers.workers[0] = worker
    state, replies = await runtime.chat.handle('review', state, customer='alice')
    assert worker.writes == []
    assert len(state['cart_reconciliation']['checkpoint']['lines']) == 2
    action = replies[1]['interactive']['action']['buttons'][0]['reply']['id']
    state, _ = await runtime.chat.handle('Update cart', state, action, customer='alice')
    assert worker.writes == [('popcorn', 0), ('127081', 2)]
    assert state['view']['review'] is not None
    assert state['cart_reconciliation'] is None
    runtime.inbox.close()


def test_partial_update_preview_does_not_claim_nothing_changed():
    view = basket()
    view.synced_provider = Provider.BLINKIT
    replies = reconciliation.messages(view, {'items': [], 'checkpoint': {}, 'changed': True})
    assert 'partially updated' in replies[0]['text']['body']
    assert 'Nothing has changed yet' not in replies[0]['text']['body']
