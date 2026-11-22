"""Cross-store uncertainty and browser failure/cancellation must not alter checkout."""

import asyncio
import copy
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError, Session
from easydial.models import Offer, Provider, SearchResult, SessionView
from easydial.providers.blinkit import BlinkitProvider
from easydial.providers.blinkit_location import BlinkitAddressError
from easydial.providers.product_details import read_catalogue, read_product_details
from easydial.whatsapp.exploration import run_agent
from tests.test_exploration import call, response, tool_outputs
from tests.test_payment_handoff import reviewed
from tests.test_whatsapp import make_runtime


@pytest.fixture
async def scenario(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await reviewed(runtime)
    session = Session(view=SessionView.model_validate(state['view']),
                      offers={key: Offer.model_validate(value) for key, value in state['offers'].items()})
    selected = session.view.lines[0].offer
    create = AsyncMock()
    runtime.chat.master = SimpleNamespace(require_client=lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    runtime.chat.workers.allowed_providers = {Provider.BLINKIT, Provider.INSTAMART}
    runtime.chat.workers.explore_search = AsyncMock()
    runtime.chat.workers.product_details = AsyncMock()
    try:
        yield runtime, state, session, selected, create
    finally:
        runtime.inbox.close()


@pytest.mark.parametrize('failed_status', ['error', 'no_matches'])
async def test_partial_comparison_cannot_invent_failed_store_stock_or_restock(scenario, failed_status):
    runtime, state, session, selected, create = scenario
    before = copy.deepcopy(state)
    runtime.chat.workers.explore_search.side_effect = [
        SearchResult(provider=Provider.BLINKIT, query='milk', offers=[selected]),
        SearchResult(provider=Provider.INSTAMART, query='milk', offers=[],
                     status='error' if failed_status == 'error' else 'ok',
                     message='Could not verify this store' if failed_status == 'error' else None),
    ]
    create.side_effect = [
        response(call('search_products', query='milk', provider='blinkit')),
        response(call('search_products', query='milk', provider='instamart')),
        response(text='Blinkit has milk for ₹28. Instamart is out of stock and will restock tomorrow.'),
    ]
    result = await run_agent(runtime.chat, 'Compare milk prices on both stores', session, state,
                             instructions='', context={})
    answer = ' '.join(item['text']['body'] for item in result['replies'] if item['type'] == 'text')
    assert result['action'] is None and state == before
    assert 'will restock tomorrow' not in answer and 'Instamart is out of stock' not in answer
    assert 'Blinkit' in answer and 'Instamart' in answer
    assert any(word in answer.lower() for word in ('unknown', 'couldn’t', 'could not', 'unverified'))


async def test_store_revoked_after_search_cannot_be_inspected(scenario):
    runtime, state, session, selected, create = scenario
    async def search(*args):
        runtime.chat.workers.allowed_providers = {Provider.INSTAMART}
        return SearchResult(provider=Provider.BLINKIT, query='milk', offers=[selected])
    runtime.chat.workers.explore_search.side_effect = search
    create.side_effect = [response(call('search_products', query='milk', provider='blinkit')),
                          response(call('inspect_product', offer_id=selected.id)),
                          response(text='Blinkit must be connected before I can inspect this product.')]
    result = await run_agent(runtime.chat, 'Show milk and its ingredients', session, state,
                             instructions='', context={})
    assert result['action'] is None
    runtime.chat.workers.product_details.assert_not_awaited()
    assert any(value.get('error') == 'unavailable' for value in tool_outputs(create))


@pytest.fixture
async def browser_page():
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        context = await browser.new_context()
        primary = await context.new_page()
        await primary.set_content('<div>Existing merchant payment options</div>')
        try:
            yield primary
        finally:
            await browser.close()


def selected_offer():
    return Offer(id='live:blinkit:123', provider=Provider.BLINKIT, product_id='123', sku_id='123',
                 name='Test milk', pack_size='500 ml', mode='live', address_label='Old private address')


async def make_worker(primary):
    worker = BlinkitProvider('', 'Current locality', 'Current private address')
    @asynccontextmanager
    async def browser_page():
        yield primary, primary.context
    worker.browser_page = browser_page
    return worker


@pytest.mark.skipif(os.getenv('EASYDIAL_DOM_TESTS') != '1', reason='opt-in Chrome DOM test')
@pytest.mark.parametrize('reader', ['catalogue', 'details'])
async def test_navigation_cancellation_closes_new_tab_without_touching_payment(browser_page, reader):
    primary = browser_page
    worker = await make_worker(primary)
    entered = asyncio.Event()
    async def navigate(tab, query):
        await tab.set_content('<div>Partial product loading</div>')
        entered.set()
        await asyncio.Event().wait()
    worker.navigate = navigate
    job = asyncio.create_task(read_catalogue(worker, 'milk', 'Current private address') if reader == 'catalogue'
                              else read_product_details(worker, selected_offer()))
    await asyncio.wait_for(entered.wait(), timeout=3)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert primary.context.pages == [primary] and not worker.lock.locked()
    assert await primary.locator('body').inner_text() == 'Existing merchant payment options'


@pytest.mark.skipif(os.getenv('EASYDIAL_DOM_TESTS') != '1', reason='opt-in Chrome DOM test')
@pytest.mark.parametrize('reader', ['catalogue', 'details'])
async def test_address_mismatch_fails_before_product_read_and_preserves_payment(browser_page, reader):
    primary = browser_page
    worker = await make_worker(primary)
    async def navigate(tab, query):
        await tab.set_content('<div>Wrong address private data</div>')
        raise BlinkitAddressError()
    worker.navigate = navigate
    with pytest.raises(BlinkitAddressError) as error:
        if reader == 'catalogue':
            await read_catalogue(worker, 'milk', 'Old private address')
        else:
            await read_product_details(worker, selected_offer())
    assert 'private' not in str(error.value).lower()
    assert primary.context.pages == [primary] and not worker.lock.locked()
    assert await primary.locator('body').inner_text() == 'Existing merchant payment options'


@pytest.mark.skipif(os.getenv('EASYDIAL_DOM_TESTS') != '1', reason='opt-in Chrome DOM test')
async def test_external_product_redirect_is_rejected_without_returning_external_content(browser_page):
    primary = browser_page
    worker = await make_worker(primary)
    async def route(request):
        if '/s/?' in request.request.url:
            return await request.fulfill(content_type='text/html; charset=utf-8', body='''
                <div role="button" id="123"><div class="tw-line-clamp-2"
                onclick="location.href='https://unexpected.invalid/prn/milk/prid/123'">Test milk</div>
                <div class="tw-line-clamp-1">500 ml</div><div>₹28</div><div>ADD</div></div>''')
        return await request.fulfill(content_type='text/html', body='Secret redirected content')
    await primary.context.route('**/*', route)
    async def navigate(tab, query):
        await tab.goto('https://blinkit.com/s/?q=milk')
    worker.navigate = navigate
    with pytest.raises(DomainError, match='unexpected product page') as error:
        await read_product_details(worker, selected_offer())
    assert 'Secret' not in str(error.value)
    assert primary.context.pages == [primary] and not worker.lock.locked()
    assert await primary.locator('body').inner_text() == 'Existing merchant payment options'
