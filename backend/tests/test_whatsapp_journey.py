"""One connected runtime journey; external login/catalogue/payment are controlled fakes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.models import Provider, RequestedProduct, SessionView
from easydial.providers.payments import decode_qr
from tests.fakes import FixtureProvider, intent_tool_responses
from tests.test_onboarding import connect, runtime, turn
from tests.test_order_tracking import merchant_order
from tests.test_payment_handoff import qr
from tests.test_whatsapp import intent


@pytest.mark.parametrize('use_tool_harness', [False, True])
async def test_onboarding_to_confirmation_and_fresh_basket(tmp_path, use_tool_harness):
    r = runtime(tmp_path)
    sender = '919999999999'
    await connect(r, provider='blinkit')
    assert r.onboarding.accounts.consented(sender, 'blinkit')
    chat = await r.customer_chats.get(sender)
    chat.workers.workers = [FixtureProvider(Provider.BLINKIT)]
    chat.workers.ranker = SimpleNamespace(
        rank=AsyncMock(side_effect=lambda query, offers: offers), close=AsyncMock())
    chat.interpret = AsyncMock(return_value=intent(
        'search', queries=[RequestedProduct(query='milk', quantity=2, replaces_line_id=None)]))
    if use_tool_harness:
        from easydial.whatsapp.conversation import ShoppingChat
        parse = AsyncMock(return_value=SimpleNamespace(output_parsed=chat.interpret.return_value))
        responses = intent_tool_responses(parse)
        chat.master = SimpleNamespace(require_client=lambda: SimpleNamespace(responses=responses))
        chat.interpret = ShoppingChat.interpret.__get__(chat)
    chat.workers.payment_options = AsyncMock(return_value=[{'id': 'upi', 'title': 'UPI', 'note': ''}])
    chat.workers.order_history = AsyncMock(return_value={'ids': [], 'orders': []})
    r.payments.public_url = 'https://example.test'
    chat.workers.generate_upi = AsyncMock(return_value=decode_qr(
        qr('upi://pay?pa=test@bank&tr=journey&am=56&cu=INR'), 5600) | {'ttl_seconds': 300})

    await turn(r, 'two milk')
    await turn(r, '1')
    assert r.inbox.load(sender)['view']['lines'][0]['quantity'] == 2
    await turn(r, 'review')
    view = SessionView.model_validate(r.inbox.load(sender)['view'])
    assert view.review.total_paise == 5600
    assert view.review.address_label == sender + ' home'
    replies = await turn(r, 'checkout')
    menu = next(p for p in replies if p.get('_watch_order_id'))
    assert not chat.workers.generate_upi.called
    while await r.send_one():
        pass
    assert r.inbox.db.execute('SELECT attempts FROM automatic_order_checks').fetchone()[0] == 0
    row = menu['interactive']['action']['sections'][0]['rows'][0]
    await turn(r, 'UPI', action=row['id'])
    assert chat.workers.generate_upi.await_count == 1
    while await r.send_one():
        pass

    chat.workers.order_history.return_value = {
        'ids': ['new'], 'orders': [merchant_order(view, method='online', payment_status='paid')]}
    with r.inbox.db:
        r.inbox.db.execute('UPDATE automatic_order_checks SET due_at=0')
    sent_before = len(r.transport.sent)
    assert await r.check_due_order()
    while await r.send_one():
        pass
    assert len(r.transport.sent) > sent_before
    state = r.inbox.load(sender)
    assert state['view']['lines'] == []
    assert state['view']['id'] != view.id
    assert state['last_order_id'] == view.id
    assert chat.status_view(state, sender).id == view.id
    sent_after = len(r.transport.sent)
    assert not await r.check_due_order()
    assert not await r.send_one()
    assert len(r.transport.sent) == sent_after

    await turn(r, 'two milk again')
    await turn(r, '1')
    fresh = r.inbox.load(sender)
    assert fresh['view']['id'] != view.id
    assert len(fresh['view']['lines']) == 1
    assert fresh['view']['lines'][0]['quantity'] == 2
    assert fresh['view']['payment_preparation'] is None
    assert r.payments.existing(sender, fresh['view']['id']) is None
    if use_tool_harness:
        # Both ordinary discovery turns hand off in one model call each.
        assert parse.await_count == 2
    r.inbox.close()
