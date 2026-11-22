"""Observed product-page structures; no store network or cart mutation."""

import base64
import os
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from easydial.controller import DomainError
from easydial.models import Offer, Provider
from easydial.providers.blinkit import BlinkitProvider
from easydial.providers.product_details import read_product_details

pytestmark = pytest.mark.skipif(
    os.getenv('EASYDIAL_DOM_TESTS') != '1', reason='opt-in local Chrome DOM tests'
)

IMAGE = 'https://cdn.grofers.com/da/product/cleanser.jpg'
SECOND_IMAGE = 'https://cdn.grofers.com/da/product/ingredients.jpg'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6XxQAAAAASUVORK5CYII=')


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        context = await browser.new_context()
        primary = await context.new_page()
        yield primary
        await browser.close()


def offer():
    return Offer(id='live:blinkit:123', provider=Provider.BLINKIT, product_id='123', sku_id='123',
                 name='Test Cleanser', pack_size='236 ml', mode='live', address_label='Test address')


async def setup_worker(page, *, details=True, title='Test Cleanser'):
    mutations, visited = [], []
    async def route(request):
        url = request.request.url
        if '/mutation' in url or request.request.method != 'GET':
            mutations.append(url)
            await request.fulfill(body='unexpected write')
        elif 'cdn.grofers.com' in url or 'untrusted.invalid' in url:
            await request.fulfill(body=PNG, content_type='image/png')
        elif '/s/' in url:
            await request.fulfill(content_type='text/html; charset=utf-8', body='''
              <div role="button" id="123">
                <div class="tw-line-clamp-2" onclick="location.href='/prn/test-cleanser/prid/123'">Test Cleanser</div>
                <div class="tw-line-clamp-1">236 ml</div><div>₹100</div>
                <button onclick="fetch('/mutation', {method:'POST'})">ADD</button>
              </div>''')
        elif '/prid/' in url:
            visited.append(url)
            section = '''<div>Product Details</div>
                <div id="ingredients" style="display:none"><div>Ingredients</div><div>Aqua, Glycerin</div>
                  <div>Unit</div><div>236 ml</div><div>View less details</div></div>
                <button onclick="document.querySelector('#ingredients').style.display='block';this.remove()">View more details</button>''' if details else '<div>Product Details</div>'
            await request.fulfill(content_type='text/html; charset=utf-8', body=f'''
                <div>Private customer delivery address</div><div>My Cart</div>{section}
                <div class="ProductInfoCard__BreadcrumbProductName-sc-test">{title}</div>
                <div>236 ml</div><button onclick="fetch('/mutation', {{method:'POST'}})">Add to cart</button>
                <div class="ProductCarousel__ImageContainer-sc-test"><img id="main" src="{IMAGE}"></div>
                <img class="ProductCarousel__CarouselImage-sc-test" src="{IMAGE}" onclick="document.querySelector('#main').src=this.src">
                <img class="ProductCarousel__CarouselImage-sc-test" src="{SECOND_IMAGE}" onclick="document.querySelector('#main').src=this.src">
                <img class="ProductCarousel__CarouselImage-sc-test" src="https://untrusted.invalid/tracker.png">
                <div>Similar products<img src="https://cdn.grofers.com/da/product/unrelated.jpg"></div>
                <div>Private footer</div>''')
        else:
            await request.fulfill(status=404, body='not found')
    context = page.context
    await context.route('**/*', route)
    worker = BlinkitProvider('', 'Test locality', 'Test address')
    @asynccontextmanager
    async def browser_page():
        yield page, context
    async def navigate(tab, query):
        await tab.goto('https://blinkit.com/s/?q=cleanser')
    worker.browser_page = browser_page
    worker.navigate = navigate
    return worker, mutations, visited


async def test_details_reads_exact_product_and_gallery_without_cart_actions(page):
    worker, mutations, visited = await setup_worker(page)
    primary_url = page.url
    result = await read_product_details(worker, offer())
    assert result['status'] == 'ok'
    assert result['details_text'] == 'Ingredients\nAqua, Glycerin\nUnit\n236 ml'
    assert result['images'] == [IMAGE, SECOND_IMAGE]
    assert result['image_count'] == 3 and result['images_truncated']
    assert result['source_url'] == 'https://blinkit.com/prn/test-cleanser/prid/123'
    assert result['price_paise'] == 10000
    assert len(visited) == 1 and mutations == []
    assert page.url == primary_url and page.context.pages == [page]


async def test_wrong_pack_rejects_before_opening_details_and_closes_tab(page):
    worker, mutations, visited = await setup_worker(page)
    with pytest.raises(DomainError, match='product or pack changed'):
        await read_product_details(worker, offer().model_copy(update={'pack_size': '500 ml'}))
    assert not visited and not mutations
    assert page.context.pages == [page]


async def test_wrong_product_title_rejects_without_returning_other_details(page):
    worker, mutations, visited = await setup_worker(page, title='Unrelated product')
    with pytest.raises(DomainError, match='selected product'):
        await read_product_details(worker, offer())
    assert len(visited) == 1 and not mutations
    assert page.context.pages == [page]


async def test_unavailable_details_are_unknown_and_do_not_include_customer_text(page):
    worker, mutations, _ = await setup_worker(page, details=False)
    result = await read_product_details(worker, offer(), include_images=False)
    assert result['status'] == 'ok' and result['details_text'] == ''
    assert result['images'] == [] and 'unknown' in result['message']
    assert 'Private' not in str(result) and not mutations
    assert page.context.pages == [page]


@pytest.mark.parametrize('provider', [Provider.BLINKIT, Provider.INSTAMART])
async def test_catalogue_exploration_preserves_open_payment_page(page, provider):
    from easydial.providers.instamart import InstamartProvider
    from easydial.providers.product_details import read_catalogue

    mutations = []
    origin = 'https://blinkit.com' if provider == Provider.BLINKIT else 'https://instamart.in'
    async def route(request):
        url = request.request.url
        if '/mutate' in url or request.request.method != 'GET':
            mutations.append(url)
            return await request.fulfill(body='unexpected write')
        if url.endswith('/checkout'):
            return await request.fulfill(content_type='text/html; charset=utf-8',
                                         body='<div id="payment-state">Existing payment option</div>')
        if url.endswith('/product.png'):
            return await request.fulfill(content_type='image/png', body=PNG)
        if provider == Provider.BLINKIT:
            body = '''<div role="button" id="123"><div class="tw-line-clamp-2">Test milk</div>
              <div class="tw-line-clamp-1">500 ml</div><div>₹28</div>
              <button onclick="fetch('/mutate', {method:'POST'})">ADD</button></div>'''
        else:
            body = '''<div><a data-testid="item-collection-card-full" href="/p/test-milk-123">
              <img src="/product.png" alt="Test milk"><button data-testid="buttonpair-add"
              onclick="fetch('/mutate', {method:'POST'})">ADD</button></a>
              <div class="_3wq_F">500 ml</div><div class="_2jn41">₹28</div></div>'''
        return await request.fulfill(content_type='text/html; charset=utf-8', body=body)
    await page.context.route('**/*', route)
    await page.goto(origin + '/checkout')
    initial_body, initial_url = await page.locator('body').inner_text(), page.url
    worker = (BlinkitProvider('', 'Test locality', 'Test address') if provider == Provider.BLINKIT
              else InstamartProvider('', 'Test address', 'Test locality'))
    @asynccontextmanager
    async def browser_page():
        yield page, page.context
    async def navigate(tab, query):
        await tab.goto(origin + '/s/?q=milk')
    worker.browser_page, worker.navigate = browser_page, navigate
    result = await read_catalogue(worker, 'milk', 'Test address')
    assert result.provider == provider and result.status == 'ok' and len(result.offers) == 1
    assert result.offers[0].name == 'Test milk' and result.offers[0].pack_size == '500 ml'
    assert result.offers[0].price_paise == 2800 and result.offers[0].available is True
    assert page.url == initial_url and await page.locator('body').inner_text() == initial_body
    assert page.context.pages == [page] and not worker.lock.locked() and not mutations


async def test_failed_catalogue_read_closes_temporary_tab_and_preserves_primary(page):
    from easydial.providers.product_details import read_catalogue

    await page.set_content('<div>Existing payment option</div>')
    initial_url = page.url
    worker = BlinkitProvider('', 'Test locality', 'Test address')
    @asynccontextmanager
    async def browser_page():
        yield page, page.context
    async def navigate(tab, query):
        await tab.set_content('<div>Store failed to load</div>')
        raise DomainError('Store unavailable')
    worker.browser_page, worker.navigate = browser_page, navigate
    with pytest.raises(DomainError, match='Store unavailable'):
        await read_catalogue(worker, 'milk', 'Test address')
    assert page.url == initial_url and await page.locator('body').inner_text() == 'Existing payment option'
    assert page.context.pages == [page] and not worker.lock.locked()
