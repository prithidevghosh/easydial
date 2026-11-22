import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from easydial.providers.past_orders import card_date, read_past_orders


def card(index, *, returned=False):
    return {'identity': {'id': str(index)}, 'items': [{'data': {
        'title': {'text': 'Return completed' if returned else 'Arrived in 5 minutes'},
        'left_underlined_subtitle' if returned else 'subtitle': {'text': '13 Oct, 11:53 am'}}}],
        'click_action': {'blinkit_deeplink': {'url': f'blinkit://order?cart_id={100+index}&order_id={200+index}'}}}


def test_returned_order_uses_observed_alternate_date_field():
    parsed = datetime.fromtimestamp(card_date(card(1, returned=True)), ZoneInfo('Asia/Kolkata'))
    assert (parsed.month, parsed.day, parsed.hour, parsed.minute) == (10, 13, 11, 53)


class Page:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.evaluate = AsyncMock()
        self.close = AsyncMock()
        self.body = SimpleNamespace(inner_text=AsyncMock(return_value='''Order details
Order summary
Arrived in 5 minutes
1 item in this order
Milk
500 ml x 1
Order id
123
Bill total
₹55
Payment
Paid online
Deliver to
Test home'''))

    @asynccontextmanager
    async def expect_response(self, predicate, **kwargs):
        payload = next(self.pages)
        response = SimpleNamespace(json=AsyncMock(return_value=payload))
        future = asyncio.get_running_loop().create_future()
        future.set_result(response)
        yield SimpleNamespace(value=future)

    def get_by_text(self, text, **kwargs):
        locator = SimpleNamespace(wait_for=AsyncMock())
        locator.first = locator
        return locator

    def locator(self, selector):
        assert selector == 'body'
        return self.body


def payload(cards, more=True):
    return {'is_success': True, 'response': {'snippets': [{'data': c} for c in cards],
                                           'pagination': {'next_url': 'private-not-replayed' if more else ''}}}


async def test_pagination_never_opens_twenty_first_order(monkeypatch):
    page = Page([payload([card(i) for i in range(10)]), payload([card(i) for i in range(10, 25)])])
    navigate = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=AsyncMock(return_value=page))
    worker = SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page)
    result = await read_past_orders(worker)
    assert result['scanned_count'] == 20
    assert len(result['orders']) == 20
    assert not result['issues']
    assert navigate.await_count == 21  # One list page, twenty detail pages.
    assert all('private-not-replayed' not in call.args[1] for call in navigate.await_args_list)
    page.evaluate.assert_awaited_once()
    page.close.assert_awaited_once()


async def test_date_filter_does_not_expand_limit_to_older_orders(monkeypatch):
    page = Page([payload([card(i) for i in range(10)]), payload([card(i) for i in range(10, 20)])])
    navigate = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=AsyncMock(return_value=page))
    worker = SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page)
    result = await read_past_orders(worker, date(2025, 1, 1), date(2025, 1, 31))
    assert result == {'scanned_count': 20, 'orders': [], 'issues': []}
    assert navigate.await_count == 1


async def test_cancelled_cards_count_toward_limit_but_are_not_repeated(monkeypatch):
    cards = [card(i) for i in range(20)]
    cards[0]['items'][0]['data']['title']['text'] = 'Order cancelled'
    page = Page([payload(cards)])
    navigate = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=AsyncMock(return_value=page))
    result = await read_past_orders(SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page))
    assert result['scanned_count'] == 20
    assert len(result['orders']) == 19
    assert not result['issues']
    page.evaluate.assert_not_awaited()
