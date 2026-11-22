import asyncio
import time
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


async def test_first_page_never_loads_more_or_opens_eleventh_order(monkeypatch):
    page = Page([payload([card(i) for i in range(10)]), payload([card(i) for i in range(10, 25)])])
    navigate = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=AsyncMock(return_value=page))
    worker = SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page)
    result = await read_past_orders(worker)
    assert result['scanned_count'] == 10
    assert len(result['orders']) == 10
    assert not result['issues']
    assert navigate.await_count == 11  # One list page, ten detail pages.
    assert all('private-not-replayed' not in call.args[1] for call in navigate.await_args_list)
    page.evaluate.assert_not_awaited()
    assert page.close.await_count == 11  # List plus ten independently closed detail tabs.


async def test_date_filter_does_not_expand_limit_to_older_orders(monkeypatch):
    page = Page([payload([card(i) for i in range(10)]), payload([card(i) for i in range(10, 20)])])
    navigate = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=AsyncMock(return_value=page))
    worker = SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page)
    result = await read_past_orders(worker, date(2025, 1, 1), date(2025, 1, 31))
    assert result == {'scanned_count': 10, 'orders': [], 'issues': []}
    assert navigate.await_count == 1


async def test_cancelled_cards_count_toward_limit_but_are_not_repeated(monkeypatch):
    cards = [card(i) for i in range(10)]
    cards[0]['items'][0]['data']['title']['text'] = 'Order cancelled'
    page = Page([payload(cards)])
    navigate = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=AsyncMock(return_value=page))
    result = await read_past_orders(SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page))
    assert result['scanned_count'] == 10
    assert len(result['orders']) == 9
    assert not result['issues']
    page.evaluate.assert_not_awaited()


async def test_first_match_stops_after_one_bounded_batch_and_preserves_newest_order(monkeypatch):
    listing = Page([payload([card(i) for i in range(10)])])
    pages = [listing]
    active = peak = 0
    async def new_page():
        if len(pages) == 1 and not getattr(listing, 'used', False):
            listing.used = True
            return listing
        page = Page([])
        pages.append(page)
        return page
    async def navigate(page, url, **kwargs):
        nonlocal active, peak
        if url.endswith('/orders'):
            return
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.03 if url.endswith('/200') else .001)
        active -= 1
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=new_page)
    batches = []
    async def match(batch):
        batches.append([order['history_id'] for order in batch['orders']])
        return True
    result = await read_past_orders(SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page), on_batch=match)
    assert peak == 3
    assert batches == [['0', '1', '2']]
    assert result['scanned_count'] == 3
    assert len(pages) == 4  # No next batch opened after the first match.
    assert all(page.close.await_count == 1 for page in pages)


async def test_encrypted_cache_reuses_details_but_still_refreshes_first_ten(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from easydial.providers.history_cache import HistoryCache
    from easydial.whatsapp.security import SecretBox

    pages = []
    cards = [card(i) for i in range(10)]
    async def new_page():
        page = Page([payload(cards, more=False)])
        pages.append(page)
        return page
    navigate = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=new_page)
    worker = SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page,
                             browser=SimpleNamespace(storage_state=str(tmp_path/'session.enc'), secret_box=SecretBox(Fernet.generate_key())))
    first = await read_past_orders(worker)
    assert len(first['orders']) == 10
    path = tmp_path/'session.enc.history'
    assert path.exists() and 'Milk' not in path.read_text()
    cached = HistoryCache(worker).entries['0']['order']
    assert not {'address', 'method', 'total_paise'} & cached.keys()
    navigate.reset_mock()
    # A fresh worker/session can reuse encrypted records; the list is still fetched.
    second = await read_past_orders(worker)
    assert len(second['orders']) == 10
    assert navigate.await_count == 1
    # A new latest order displaces the oldest; never reuse cached records outside the cap.
    cards.insert(0, card(99))
    navigate.reset_mock()
    third = await read_past_orders(worker)
    assert third['orders'][0]['history_id'] == '99'
    assert len(third['orders']) == 10 and navigate.await_count == 2
    assert '9' not in HistoryCache(worker).entries
    # Terminal status changes invalidate the old item entry and cancelled cards are excluded.
    cards[1]['items'][0]['data']['title']['text'] = 'Order cancelled'
    fourth = await read_past_orders(worker)
    assert '0' not in [order['history_id'] for order in fourth['orders']]
    other = SimpleNamespace(browser=SimpleNamespace(storage_state=str(tmp_path/'session.enc'), secret_box=SecretBox(Fernet.generate_key())))
    assert HistoryCache(other).entries == {}


async def test_unreadable_newer_order_cannot_be_skipped_for_older_match(monkeypatch):
    pages = []
    async def new_page():
        page = Page([payload([card(i) for i in range(10)], more=False)])
        pages.append(page)
        return page
    async def navigate(page, url, **kwargs):
        if url.endswith('/200'):
            page.get_by_text = lambda *a, **k: SimpleNamespace(wait_for=AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=new_page)
    match = AsyncMock(return_value=True)
    result = await read_past_orders(SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page), on_batch=match)
    assert result['issues'] == ['past_order_not_loaded']
    match.assert_not_awaited()
    assert len(pages) == 4


async def test_cancelling_scan_closes_all_detail_tabs_and_releases_lock(monkeypatch):
    pages = []
    started = asyncio.Event()
    async def new_page():
        page = Page([payload([card(i) for i in range(10)], more=False)])
        pages.append(page)
        return page
    async def navigate(page, url, **kwargs):
        if not url.endswith('/orders'):
            if len(pages) == 4:
                started.set()
            await asyncio.Event().wait()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=new_page)
    worker = SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page)
    task = asyncio.create_task(read_past_orders(worker))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    import pytest
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not worker.lock.locked()
    assert len(pages) == 4 and all(page.close.await_count == 1 for page in pages)


def test_invalid_cached_payload_cannot_break_live_history(tmp_path):
    from cryptography.fernet import Fernet

    from easydial.providers.history_cache import HistoryCache, fingerprint
    from easydial.whatsapp.security import SecretBox

    box = SecretBox(Fernet.generate_key())
    worker = SimpleNamespace(browser=SimpleNamespace(storage_state=str(tmp_path/'a.enc'), secret_box=box))
    cache = HistoryCache(worker)
    cache.entries = {'1': {'fingerprint': fingerprint(card(1)), 'saved_at': 'bad-clock', 'order': {}}}
    cache.save()
    assert HistoryCache(worker).get(card(1)) is None
    cache.entries = {'1': {'fingerprint': fingerprint(card(1)), 'saved_at': time.time(), 'order': {}}}
    cache.save()
    assert HistoryCache(worker).get(card(1)) is None


async def test_newest_match_cancels_slower_older_reads_without_waiting(monkeypatch):
    pages = []
    async def new_page():
        page = Page([payload([card(i) for i in range(10)], more=False)])
        pages.append(page)
        return page
    async def navigate(page, url, **kwargs):
        if url.endswith('/201') or url.endswith('/202'):
            await asyncio.Event().wait()
    monkeypatch.setattr('easydial.providers.past_orders.goto_with_retry', navigate)
    @asynccontextmanager
    async def browser_page():
        yield None, SimpleNamespace(new_page=new_page)
    match = AsyncMock(return_value=True)
    result = await asyncio.wait_for(read_past_orders(
        SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page), on_batch=match), timeout=1)
    assert result['scanned_count'] == 1
    assert [o['history_id'] for o in result['orders']] == ['0']
    assert len(pages) == 4 and all(p.close.await_count == 1 for p in pages)
