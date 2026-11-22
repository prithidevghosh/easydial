"""Move verified purchases out of the active basket without touching the merchant cart."""

from copy import deepcopy
from uuid import uuid4

from ..models import SessionView

CART_STATE = {
    'active_request', 'cart_checkpoint', 'cart_reconciliation', 'cash_confirmation',
    'payment_menu', 'pending_restore', 'recent_selection', 'request_queue',
    'repeat_question', 'retry_confirmation', 'store_switch', 'unresolved_requests',
}


def completed_state(state, purchased):
    """Subtract only purchased quantities; never clear a newer order's basket."""
    if state['view']['id'] != purchased.id:
        return state
    result = deepcopy(state)
    current = SessionView.model_validate(state['view'])
    def identity(line):
        offer = line.offer
        return offer.provider, offer.product_id, offer.sku_id, offer.name.casefold(), offer.pack_size
    quantities = {}
    for line in purchased.lines:
        key = identity(line)
        quantities[key] = quantities.get(key, 0) + line.quantity
    remaining = []
    for line in current.lines:
        key = identity(line)
        consumed = min(line.quantity, quantities.get(key, 0))
        quantities[key] = quantities.get(key, 0) - consumed
        if line.quantity > consumed:
            remaining.append(line.model_copy(update={'quantity': line.quantity - consumed}))
    result['view'] = SessionView(id=uuid4().hex, mode=current.mode, address_label=current.address_label,
                                 lines=remaining).model_dump(mode='json')
    result['last_order_id'] = purchased.id
    result['previous_basket'] = [line.model_dump(mode='json') for line in purchased.lines]
    result['offers'], result['choices'] = {}, []
    for key in CART_STATE:
        result.pop(key, None)
    return result


def release_checkpoint(workers, order_id):
    for worker in getattr(workers, 'workers', []):
        if worker.guard.owner == order_id:
            worker.guard.owner = None
            worker.guard.expected = ()
            worker.guard.attempted = None
            worker.previous_lines = []
            worker._payment_checkpoint = None
