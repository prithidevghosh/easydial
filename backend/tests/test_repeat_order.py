import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError, Session
from easydial.models import Provider, SessionView
from easydial.whatsapp.repeat_order import PastSelection, RepeatRequest, repeat, select_items
from tests.test_payment_handoff import reviewed
from tests.test_whatsapp import intent, make_runtime


async def setup(runtime, *, decision='match', quantity=None):
    state = await reviewed(runtime)
    view = SessionView.model_validate(state['view'])
    offer = view.lines[0].offer
    history = {'scanned_count': 20, 'issues': [], 'orders': [
        {'status': 'delivered', 'placed_at': time.time(), 'items': [
            {'name': offer.name, 'pack': offer.pack_size, 'quantity': 2}]}]}
    runtime.chat.workers.past_orders = AsyncMock(return_value=history)
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=PastSelection(
        decision=decision, item_keys=['0:0'] if decision == 'match' else [], question='Which pack?')))
    runtime.chat.master = SimpleNamespace(require_client=lambda: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    request = RepeatRequest(product_query=offer.name, quantity=quantity)
    view.lines = []
    view.synced_provider = None
    view.review = None
    return Session(view=view), request, history


async def test_repeat_preserves_historical_count_current_price_and_review_required(tmp_path):
    runtime = make_runtime(tmp_path)
    session, request, _ = await setup(runtime)
    fresh, message = await repeat(runtime.chat, session, request)
    assert not session.view.lines
    assert len(fresh.view.lines) == 1
    assert fresh.view.lines[0].quantity == 2
    assert fresh.view.review is None
    assert fresh.view.phase == 'shopping'
    assert 'today' in message
    runtime.inbox.close()


async def test_repeat_explicit_quantity_and_existing_basket_are_preserved(tmp_path):
    runtime = make_runtime(tmp_path)
    session, request, _ = await setup(runtime, quantity=3)
    first, _ = await repeat(runtime.chat, session, request)
    second, _ = await repeat(runtime.chat, first, request)
    assert len(second.view.lines) == 1
    assert second.view.lines[0].quantity == 6
    assert first.view.lines[0].quantity == 3
    runtime.inbox.close()


@pytest.mark.parametrize('decision', ['not_found', 'clarify'])
async def test_no_match_and_ambiguity_do_not_search_or_mutate(tmp_path, decision):
    runtime = make_runtime(tmp_path)
    session, request, _ = await setup(runtime, decision=decision)
    runtime.chat.workers.search = AsyncMock()
    result, message = await repeat(runtime.chat, session, request)
    assert result is session
    assert ('20' in message) if decision == 'not_found' else message == 'Which pack?'
    runtime.chat.workers.search.assert_not_called()
    runtime.inbox.close()


async def test_incomplete_history_never_claims_product_absent(tmp_path):
    runtime = make_runtime(tmp_path)
    session, request, history = await setup(runtime)
    history['issues'] = ['incomplete_past_order']
    with pytest.raises(DomainError, match='couldn’t read all'):
        await repeat(runtime.chat, session, request)
    assert not session.view.lines
    runtime.inbox.close()


async def test_fabricated_history_key_is_rejected(tmp_path):
    runtime = make_runtime(tmp_path)
    _, request, history = await setup(runtime)
    runtime.chat.master.require_client().responses.parse.return_value.output_parsed.item_keys = ['21:9']
    with pytest.raises(DomainError, match='verify'):
        await select_items(runtime.chat, request, history)
    runtime.inbox.close()


async def test_instamart_repeat_does_not_call_history(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.chat.interpret = AsyncMock(return_value=intent('repeat_order', target_provider=Provider.INSTAMART,
                                                         repeat_request=RepeatRequest(product_query='milk')))
    runtime.chat.workers.past_orders = AsyncMock()
    with pytest.raises(DomainError, match='only for Blinkit'):
        await runtime.chat.handle('repeat my milk', runtime.chat.initial())
    runtime.chat.workers.past_orders.assert_not_called()
    runtime.inbox.close()


async def test_different_pack_is_not_silently_substituted(tmp_path):
    runtime = make_runtime(tmp_path)
    session, request, history = await setup(runtime)
    history['orders'][0]['items'][0]['pack'] = '99 litres'
    with pytest.raises(DomainError, match='same product and pack'):
        await repeat(runtime.chat, session, request)
    assert not session.view.lines
    runtime.inbox.close()


async def test_repeat_stops_matching_batches_and_skips_general_catalogue_ranking(tmp_path):
    runtime = make_runtime(tmp_path)
    session, request, history = await setup(runtime)
    parse = runtime.chat.master.require_client().responses.parse
    parse.side_effect = [
        SimpleNamespace(output_parsed=PastSelection(decision='not_found', item_keys=[], question='')),
        SimpleNamespace(output_parsed=PastSelection(decision='match', item_keys=['0:0'], question='')),
    ]
    visited = []
    async def scan(start, end, on_batch):
        for index in range(7):
            visited.append(index)
            if await on_batch(history):
                break
        return history
    runtime.chat.workers.past_orders = scan
    runtime.chat.workers.ranker.rank.reset_mock()
    fresh, _ = await repeat(runtime.chat, session, request)
    assert visited == [0, 1]
    assert parse.await_count == 2
    assert fresh.view.lines[0].quantity == 2
    runtime.chat.workers.ranker.rank.assert_not_awaited()
    # Ordinary product discovery still uses the existing semantic ranker.
    await runtime.chat.workers.search('milk', session.view.address_label)
    runtime.chat.workers.ranker.rank.assert_awaited_once()
    runtime.inbox.close()
