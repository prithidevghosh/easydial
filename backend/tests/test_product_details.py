"""Product readers fail closed on identity mismatches and never click cart controls."""

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.models import Offer, Provider
from easydial.providers.product_details import detail_text, image_asset, read_product_details


def offer(**updates):
    return Offer(**dict(id='live:blinkit:123', provider=Provider.BLINKIT, product_id='123', sku_id='123',
                        name='Test milk', pack_size='500 ml', price_paise=2800, available=True,
                        mode='live', address_label='test home') | updates)


def test_detail_extraction_excludes_account_and_recommendations():
    body = 'Private customer address\nProduct Details\nIngredients\nMilk\nUnit\n500 ml\nView less details\nSimilar products\nOther product'
    assert detail_text(body) == 'Ingredients\nMilk\nUnit\n500 ml'
    assert detail_text('Private header\nIngredients\nMilk') == ''
    assert detail_text('Product Details\nIngredients\nMilk\nView more details') == ''
    assert detail_text('View less details\nPrivate header\nProduct Details\nIngredients\nMilk') == ''


def test_thumbnail_and_main_image_deduplicate_without_rewriting_urls():
    first = 'https://cdn.grofers.com/cdn-cgi/image/w=120/da/cms/front.jpg'
    second = 'https://cdn.grofers.com/cdn-cgi/image/w=480/da/cms/front.jpg'
    assert image_asset(first) == image_asset(second)
    assert image_asset(second) != image_asset('https://cdn.grofers.com/da/cms/back.jpg')


async def test_unsupported_provider_does_not_open_browser():
    worker = SimpleNamespace(provider=Provider.INSTAMART, browser_page=AsyncMock())
    result = await read_product_details(worker, offer(provider=Provider.INSTAMART))
    assert result['status'] == 'unavailable' and result['images'] == [] and result['details_text'] == ''
    worker.browser_page.assert_not_called()


@pytest.mark.parametrize('selected', [offer(product_id='123"] button'), offer(pack_size=None)])
async def test_invalid_identity_does_not_open_browser(selected):
    worker = SimpleNamespace(provider=Provider.BLINKIT, browser_page=AsyncMock())
    with pytest.raises(DomainError):
        await read_product_details(worker, selected)
    worker.browser_page.assert_not_called()


async def test_wrong_store_does_not_open_browser():
    worker = SimpleNamespace(provider=Provider.BLINKIT, browser_page=AsyncMock())
    with pytest.raises(DomainError, match='different store'):
        await read_product_details(worker, offer(provider=Provider.INSTAMART))
    worker.browser_page.assert_not_called()


@pytest.mark.skipif(os.getenv('EASYDIAL_DOM_TESTS') != '1', reason='opt-in local Chrome DOM test')
@pytest.mark.parametrize('pack', ['500 ml', '1 ltr'])
async def test_reader_only_opens_verified_title_and_closes_tab(pack):
    from playwright.async_api import async_playwright

    from easydial.providers.blinkit import BlinkitProvider
    mutations = []
    requests = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        context = await browser.new_context()
        async def route(request):
            url = request.request.url
            requests.append(url)
            if url.endswith('/mutate'):
                mutations.append(url)
                return await request.fulfill(body='mutation')
            if '/s/?' in url:
                return await request.fulfill(content_type='text/html; charset=utf-8', body=f'''<html><body>
                  <div role="button" id="123"><div class="tw-line-clamp-2"
                    onclick="location.href='/prn/test-milk/prid/123'">Test milk</div>
                    <div class="tw-line-clamp-1">{pack}</div><div>₹28</div>
                    <button onclick="fetch('/mutate')">ADD</button></div></body></html>''')
            return await request.fulfill(content_type='text/html; charset=utf-8', body='''<html><body>
                <header>Private customer address</header><div>Product Details</div>
                <section id="details" hidden>Ingredients<br>Milk<br>Unit<br>500 ml</section>
                <button id="expand" onclick="document.querySelector('#details').hidden=false;this.textContent='View less details'">View more details</button>
                <div class="ProductInfoCard__BreadcrumbProductName">Test milk</div>
                <button onclick="fetch('/mutate')">Add to cart</button>
                <div>Similar products</div><div>Unrelated ingredients</div></body></html>''')
        await context.route('https://blinkit.com/**', route)
        worker = BlinkitProvider('', 'test location', 'test home')
        @asynccontextmanager
        async def browser_page():
            yield None, context
        worker.browser_page = browser_page
        async def navigate(page, query):
            await page.goto('https://blinkit.com/s/?q=milk')
        worker.navigate = navigate
        try:
            if pack == '500 ml':
                result = await read_product_details(worker, offer(), include_images=False)
                assert result['details_text'] == 'Ingredients\nMilk\nUnit\n500 ml'
                assert result['source_url'].endswith('/prid/123')
                assert result['price_paise'] == 2800
            else:
                with pytest.raises(DomainError, match='pack changed'):
                    await read_product_details(worker, offer(), include_images=False)
                assert not any('/prid/' in url for url in requests)
            assert not mutations and not context.pages and not worker.lock.locked()
        finally:
            await browser.close()


async def test_tab_is_closed_and_lock_released_when_navigation_fails():
    page = SimpleNamespace(close=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    @asynccontextmanager
    async def browser_page():
        yield None, context
    worker = SimpleNamespace(provider=Provider.BLINKIT, lock=asyncio.Lock(), browser_page=browser_page,
                             navigate=AsyncMock(side_effect=DomainError('Store unavailable')))
    with pytest.raises(DomainError, match='Store unavailable'):
        await read_product_details(worker, offer())
    page.close.assert_awaited_once()
    assert not worker.lock.locked()
