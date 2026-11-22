"""Verified PDP/cart aliases must survive the entire guarded basket lifecycle."""
import os
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.models import Provider, SessionView
from easydial.providers import ProviderWorkers
from easydial.providers.blinkit import cart_signature
from easydial.providers.cart_guard import CartConflict
from easydial.settings import Settings
from easydial.whatsapp import reconciliation
from easydial.whatsapp.order_tracking import matches
from tests.test_browser_contract import ContractBlinkit, basket
from tests.test_order_tracking import merchant_order

CATALOGUE = 'Aashirvaad Shudh Chakki Atta (Godihittu)'
CANONICAL = 'Aashirvaad Shudh Chakki Atta'


def atta_basket():
    view = basket()
    view.lines[0].offer.name = CATALOGUE
    view.lines[0].offer.pack_size = '5 kg'
    view.synced_provider = Provider.BLINKIT
    return view


class AliasCartBlinkit(ContractBlinkit):
    @asynccontextmanager
    async def browser_page(self):
        yield object(), None

    async def set_quantity(self, page, line, desired):
        await super().set_quantity(page, line, desired)
        for item in self.cart['items']:
            item['name'] = CANONICAL


@pytest.fixture
def proof(monkeypatch):
    read = AsyncMock(return_value=CANONICAL)
    monkeypatch.setattr('easydial.providers.blinkit_identity._read_cart_name', read)
    monkeypatch.setattr('easydial.providers.blinkit.goto_with_retry', AsyncMock())
    return read


async def test_catalogue_alias_review_replace_revalidate_restart_and_clear(proof):
    worker, view = AliasCartBlinkit(), atta_basket()
    view.review = await worker.review(view)
    assert view.lines[0].offer.cart_name == CANONICAL
    assert view.lines[0].offer.name == CATALOGUE
    assert worker.guard.expected == cart_signature(worker.cart)
    view.lines[0].offer = view.lines[0].offer.model_copy(update={
        'sku_id': 'larger-pack', 'pack_size': '10 kg', 'cart_name': None,
    })
    view.review = await worker.review(view)
    assert worker.writes[-2:] == [('127081', 0), ('127081', 2)]
    assert worker.cart['items'][0]['pack'] == '10 kg'
    writes = list(worker.writes)
    await worker.revalidate(view)
    assert worker.writes == writes

    restored = SessionView.model_validate_json(view.model_dump_json())
    fresh = AliasCartBlinkit()
    fresh.cart = deepcopy(worker.cart)
    workers = ProviderWorkers(Settings(_env_file=None))
    workers.workers[0] = fresh
    workers.restore_checkpoint(restored.model_dump(mode='json'), restored.id)
    assert fresh.guard.expected == cart_signature(fresh.cart)
    await fresh.revalidate(restored)
    await fresh.clear(restored.id)
    assert fresh.cart['items'] == []
    assert fresh.writes == [('127081', 0)]
    assert fresh.guard.expected == ()


async def test_verified_alias_does_not_adopt_foreign_or_increased_cart(proof):
    worker, view = AliasCartBlinkit(), atta_basket()
    await worker.review(view)
    writes = list(worker.writes)
    worker.cart['items'][0]['quantity'] += 1
    with pytest.raises(DomainError):
        await worker.clear(view.id)
    assert worker.writes == writes
    worker.cart['items'][0]['name'] = 'Another brand Atta'
    with pytest.raises(DomainError):
        await worker.review(view)
    assert worker.writes == writes


def test_reconciliation_recognizes_only_verified_alias_and_exact_pack():
    view = atta_basket()
    view.lines[0].offer.cart_name = CANONICAL
    conflict = CartConflict()
    conflict.cart = {'items': [{'name': CANONICAL, 'pack': '5 kg', 'quantity': 1}],
                     'address': 'Test home'}
    pending = reconciliation.proposal(view, conflict)
    assert pending['checkpoint']['lines'][0]['quantity'] == 1
    assert pending['checkpoint']['lines'][0]['offer']['cart_name'] == CANONICAL
    conflict.cart['items'][0]['pack'] = '10 kg'
    assert reconciliation.proposal(view, conflict)['checkpoint'] is None
    conflict.cart['items'][0]['pack'] = '5 kg'
    view.lines[0].offer.cart_name = None
    assert reconciliation.proposal(view, conflict)['checkpoint'] is None


async def test_order_history_uses_verified_alias_without_weakening_matching(proof):
    worker, view = AliasCartBlinkit(), atta_basket()
    view.review = await worker.review(view)
    order = merchant_order(view)
    order['items'][0]['name'] = CANONICAL
    assert matches(order, view, 'cash', [], time.time())
    for field, value in [('pack', '10 kg'), ('quantity', 1), ('name', 'Different atta')]:
        wrong = deepcopy(order)
        wrong['items'][0][field] = value
        assert not matches(wrong, view, 'cash', [], time.time())
    unverified = view.model_copy(deep=True)
    unverified.lines[0].offer.cart_name = None
    assert not matches(order, unverified, 'cash', [], time.time())


async def test_failed_identity_proof_never_records_an_alias(proof):
    from easydial.providers.blinkit_identity import resolve_cart_names

    worker, view = AliasCartBlinkit(), atta_basket()
    proof.side_effect = DomainError('Product identity could not be verified')
    cart = {'items': [{'name': CANONICAL, 'pack': '5 kg', 'quantity': 2}]}
    with pytest.raises(DomainError):
        await resolve_cart_names(worker, object(), view.lines, cart)
    assert view.lines[0].offer.cart_name is None
    assert worker.writes == []


async def test_legacy_checkpoint_without_alias_is_rebound_only_after_proof(proof):
    view = atta_basket()
    # A draft saved by the older build knows catalogue labels only.
    worker = AliasCartBlinkit()
    worker.cart = {
        'items': [{'name': CANONICAL, 'pack': '5 kg', 'quantity': 2}],
        'address': 'Test home', 'bill': [{'label': 'Items total', 'value': '₹66'}], 'total': '₹93',
    }
    workers = ProviderWorkers(Settings(_env_file=None))
    workers.workers[0] = worker
    workers.restore_checkpoint(view.model_dump(mode='json'), view.id)
    old_signature = worker.guard.expected
    assert old_signature != cart_signature(worker.cart)
    await worker.revalidate(view)
    proof.assert_awaited_once()
    assert worker.guard.expected == cart_signature(worker.cart)
    assert view.lines[0].offer.cart_name == CANONICAL
    assert worker.writes == []


async def test_revalidate_resolves_alias_after_initial_empty_snapshot(proof, monkeypatch):
    worker, view = AliasCartBlinkit(), atta_basket()
    from easydial.providers.blinkit_identity import lines_signature
    worker.guard.owner = view.id
    worker.guard.expected = lines_signature(view.lines)
    loaded = {'items': [{'name': CANONICAL, 'pack': '5 kg', 'quantity': 2}],
              'address': 'Test home', 'bill': [], 'total': '₹93'}
    worker.read_cart = AsyncMock(side_effect=[{'items': [], 'text': 'Your cart is empty'}, loaded])
    monkeypatch.setattr('easydial.providers.blinkit.asyncio.sleep', AsyncMock())
    await worker.revalidate(view)
    assert worker.read_cart.await_count == 2
    assert view.lines[0].offer.cart_name == CANONICAL
    assert worker.writes == []


@pytest.mark.parametrize('collision', ['expected', 'attempted', 'products'])
async def test_identity_collision_never_partially_changes_offers_or_guards(proof, collision):
    from easydial.providers.blinkit_identity import lines_signature, resolve_cart_names
    worker, view = AliasCartBlinkit(), atta_basket()
    original = lines_signature(view.lines)
    canonical = (('aashirvaadshudhchakkiatta', '5kg', 2),)
    worker.guard.expected = original + canonical if collision == 'expected' else original
    worker.guard.attempted = original + canonical if collision == 'attempted' else original
    if collision == 'products':
        other = view.lines[0].model_copy(deep=True)
        other.offer.product_id = other.offer.sku_id = '456'
        view.lines.append(other)
    before = view.model_dump_json(), deepcopy(vars(worker.guard))
    with pytest.raises(DomainError):
        await resolve_cart_names(worker, object(), view.lines,
                                 {'items': [{'name': CANONICAL, 'pack': '5 kg', 'quantity': 2}]})
    assert (view.model_dump_json(), vars(worker.guard)) == before


async def test_partial_first_write_reconciles_alias_after_restart(proof):
    worker, view = AliasCartBlinkit(), atta_basket()
    worker.cap = True
    with pytest.raises(CartConflict) as error:
        await worker.review(view)
    pending = reconciliation.proposal(view, error.value)
    assert pending['checkpoint']['lines'][0]['quantity'] == 1
    assert pending['checkpoint']['lines'][0]['offer']['cart_name'] == CANONICAL
    fresh = AliasCartBlinkit()
    fresh.cart = deepcopy(worker.cart)
    checkpoint = SessionView.model_validate_json(SessionView.model_validate(pending['checkpoint']).model_dump_json())
    await fresh.adopt_checkpoint(checkpoint, pending['address'])
    assert fresh.writes == []
    await fresh.review(SessionView.model_validate_json(view.model_dump_json()))
    assert fresh.cart['items'][0]['quantity'] == 2
    assert fresh.writes == [('127081', 2)]


@pytest.mark.skipif(os.getenv('EASYDIAL_DOM_TESTS') != '1', reason='opt-in local Chrome DOM test')
@pytest.mark.parametrize('invalid', [None, 'wrong_id', 'wrong_pack', 'duplicate_title', '404_recover', '404_persistent', 'reload_wrong_id'])
async def test_real_identity_dom_proof_closes_isolated_tab_and_preserves_checkout(invalid):
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    from easydial.providers.blinkit import BlinkitProvider
    from easydial.providers.blinkit_identity import _read_cart_name

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        context = await browser.new_context()
        primary = await context.new_page()
        await primary.set_content('<div id="checkout">Existing payment page</div>')
        requests = []
        pdp_reads = 0
        target = '999' if invalid == 'wrong_id' else '127081'
        pack = '10 kg' if invalid == 'wrong_pack' else '5 kg'
        title = f'<div class="ProductInfoCard__BreadcrumbProductName-test">{CANONICAL}</div>'

        async def route_handler(route):
            nonlocal pdp_reads
            requests.append((route.request.method, route.request.url))
            if '/search' in route.request.url:
                body = f'''<div role="button" id="127081">
                <div class="tw-line-clamp-2" onclick="location.href='/prn/atta/prid/{target}'">{CATALOGUE}</div>
                <div class="tw-line-clamp-1">5 kg</div><div>₹100</div><button>ADD</button></div>'''
            else:
                pdp_reads += 1
                if invalid in {'404_recover', '404_persistent', 'reload_wrong_id'} and (
                    pdp_reads == 1 or invalid == '404_persistent'
                ):
                    await route.fulfill(content_type='text/html; charset=utf-8',
                                        body='<div>The page you are looking for can’t be found.</div>')
                    return
                body = title * (2 if invalid == 'duplicate_title' else 1) + f'''
                <div class="ProductDesktopBffEnabled__ProductWrapperRightSection-test">
                <div class="tw-flex-col tw-gap-1.5"><div class="tw-text-300 tw-font-semibold">{pack}</div></div></div>'''
            if invalid == 'reload_wrong_id' and pdp_reads == 2:
                body += "<script>history.replaceState(null, '', '/prn/other/prid/999')</script>"
            await route.fulfill(content_type='text/html; charset=utf-8', body=body)

        await context.route('**/*', route_handler)
        worker = BlinkitProvider('', 'Test locality', 'Test home')
        async def navigate(tab, query):
            await tab.goto('https://blinkit.com/search')
        worker.navigate = navigate
        worker.wait_for_products = AsyncMock()
        try:
            offer = atta_basket().lines[0].offer
            if invalid in {'404_persistent', 'reload_wrong_id'}:
                from easydial.providers.blinkit_identity import resolve_cart_names
                lines = atta_basket().lines
                before = deepcopy(vars(worker.guard))
                with pytest.raises(DomainError):
                    await resolve_cart_names(worker, primary, lines,
                                             {'items': [{'name': CANONICAL, 'pack': '5 kg', 'quantity': 2}]})
                assert lines[0].offer.cart_name is None and vars(worker.guard) == before
            elif invalid and invalid != '404_recover':
                with pytest.raises((DomainError, PlaywrightError)):
                    await _read_cart_name(worker, primary, offer)
            else:
                assert await _read_cart_name(worker, primary, offer) == CANONICAL
            assert pdp_reads == (2 if invalid in {'404_recover', '404_persistent', 'reload_wrong_id'} else 1)
            assert context.pages == [primary]
            assert primary.url == 'about:blank'
            assert await primary.locator('#checkout').inner_text() == 'Existing payment page'
            assert requests and all(method == 'GET' for method, _ in requests)
        finally:
            await browser.close()


async def test_known_same_pack_row_does_not_trigger_lookup_for_absent_product(proof):
    from easydial.providers.blinkit_identity import resolve_cart_names
    worker, view = AliasCartBlinkit(), atta_basket()
    other = view.lines[0].model_copy(deep=True)
    other.offer.product_id = other.offer.sku_id = '456'
    other.offer.name = 'Other flour'
    view.lines.append(other)
    await resolve_cart_names(worker, object(), view.lines,
                             {'items': [{'name': CATALOGUE, 'pack': '5 kg', 'quantity': 2}]})
    proof.assert_not_awaited()
    assert all(line.offer.cart_name is None for line in view.lines)
