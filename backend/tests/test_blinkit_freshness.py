from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.providers.blinkit import BlinkitProvider, cart_signature


@pytest.mark.parametrize("outcome", ["restored", "empty", "changed_quantity"])
@pytest.mark.parametrize("payment_open", [False, True])
async def test_revalidate_retries_without_erasing_checkpoint(monkeypatch, outcome, payment_open):
    worker = BlinkitProvider("", "Locality", "Flat 7, Road")
    session = SimpleNamespace(
        id="customer-cart",
        address_label="Flat 7, Road",
        lines=[SimpleNamespace(offer=SimpleNamespace(name="Diet Coke", pack_size="330 ml"), quantity=4)],
    )
    good = {"items": [{"name": "Diet Coke", "pack": "330 ml", "quantity": 4}], "address": "Flat 7, Road"}
    empty = {"items": [], "address": None, "text": "Your cart is empty"}
    wrong = {"items": [{"name": "Diet Coke", "pack": "330 ml", "quantity": 1}], "address": "Flat 7, Road"}
    worker.guard.owner = session.id
    worker.guard.accepted(cart_signature(good))
    original = worker.guard.expected

    checkout = object()
    inspection = SimpleNamespace(close=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=inspection))
    if payment_open:
        worker._payment_checkpoint = {"active": True}

    @asynccontextmanager
    async def browser_page():
        yield checkout, context

    worker.browser_page = browser_page
    worker.navigate_cart = AsyncMock()
    worker.read_cart = AsyncMock(
        side_effect=[empty, good]
        if outcome == "restored"
        else [empty] * 3
        if outcome == "empty"
        else [wrong] * 3
    )
    worker.verified_review = lambda *_: "verified"
    monkeypatch.setattr("easydial.providers.blinkit.asyncio.sleep", AsyncMock())
    if outcome == "restored":
        assert await worker.revalidate(session) == "verified"
        assert worker.read_cart.await_count == 2
    else:
        with pytest.raises(DomainError, match="store cart changed"):
            await worker.revalidate(session)
        assert worker.read_cart.await_count == 3
    worker.navigate_cart.assert_awaited_once_with(inspection if payment_open else checkout)
    assert inspection.close.await_count == (1 if payment_open else 0)
    assert worker.guard.owner == session.id
    assert worker.guard.expected == original


@pytest.mark.parametrize("address", ["Flat 7, Road", "Flat 8, Road", None])
async def test_changed_header_requires_full_address_proof(monkeypatch, address):
    worker = BlinkitProvider("", "Short locality", "Flat 7, Road")
    subtitle = SimpleNamespace(inner_text=AsyncMock(return_value="Expanded delivery label"))
    page = SimpleNamespace(goto=AsyncMock(), locator=lambda _: SimpleNamespace(first=subtitle, inner_text=AsyncMock(return_value="Store page")))
    worker.browser.check_page = AsyncMock()
    worker.read_cart = AsyncMock(return_value={"address": address})
    monkeypatch.setattr("easydial.providers.blinkit.asyncio.sleep", AsyncMock())
    if address == worker.verified_address:
        await worker.navigate(page, "diet coke")
        assert worker.verified_location == "Expanded delivery label"
        assert page.goto.await_count == 2
    else:
        with pytest.raises(DomainError, match="delivery address"):
            await worker.navigate(page, "diet coke")
        assert worker.verified_location == "Short locality"
        assert page.goto.await_count == 1
    worker.read_cart.assert_awaited_once_with(page, resolve_address=False)


async def test_empty_cart_restores_exact_approved_address_and_persists_location(monkeypatch):
    from unittest.mock import Mock

    worker = BlinkitProvider('', 'Koramangala', 'Flat 7, Road')
    subtitle = SimpleNamespace(inner_text=AsyncMock(return_value='Home\nActual locality'))
    page = SimpleNamespace(goto=AsyncMock(), locator=lambda _: SimpleNamespace(first=subtitle, inner_text=AsyncMock(return_value="Store page")))
    worker.browser.check_page = AsyncMock()
    worker.browser.save = AsyncMock()
    worker.read_cart = AsyncMock(return_value={'items': [], 'address': None})
    worker.on_location_verified = Mock()
    restore = AsyncMock(return_value='Home\nActual locality')
    monkeypatch.setattr('easydial.providers.blinkit.restore_saved_address', restore)
    monkeypatch.setattr('easydial.providers.blinkit.asyncio.sleep', AsyncMock())
    await worker.navigate(page, 'Diet Coke 330 ml')
    restore.assert_awaited_once_with(page, 'Flat 7, Road')
    worker.browser.save.assert_awaited_once()
    worker.on_location_verified.assert_called_once_with('Home\nActual locality')
    assert worker.verified_location == 'Home\nActual locality'
    assert page.goto.await_count == 2


async def test_nonempty_cart_wrong_address_is_not_reselected(monkeypatch):
    from easydial.providers.blinkit_location import BlinkitAddressError

    worker = BlinkitProvider('', 'Old locality', 'Flat 7, Road')
    subtitle = SimpleNamespace(inner_text=AsyncMock(return_value='Wrong locality'))
    page = SimpleNamespace(goto=AsyncMock(), locator=lambda _: SimpleNamespace(first=subtitle, inner_text=AsyncMock(return_value="Store page")))
    worker.browser.check_page = AsyncMock()
    worker.read_cart = AsyncMock(return_value={'items': [{'name': 'Milk'}], 'address': 'Other address'})
    restore = AsyncMock()
    monkeypatch.setattr('easydial.providers.blinkit.restore_saved_address', restore)
    monkeypatch.setattr('easydial.providers.blinkit.asyncio.sleep', AsyncMock())
    with pytest.raises(BlinkitAddressError):
        await worker.navigate(page, 'milk')
    restore.assert_not_awaited()
    assert worker.verified_location == 'Old locality'


@pytest.mark.parametrize('failures', [1, 3])
async def test_location_loading_retries_same_page_and_never_saves_unverified_state(monkeypatch, failures):
    from easydial.providers.blinkit_location import BlinkitAddressLoadingError

    worker = BlinkitProvider('', 'Saved locality', 'Approved address')
    page = object()
    worker.verify_location = AsyncMock(side_effect=[BlinkitAddressLoadingError()] * failures + [None])
    worker.browser.save = AsyncMock()
    worker.browser.check_page = AsyncMock()
    goto = AsyncMock()
    monkeypatch.setattr('easydial.providers.blinkit.goto_with_retry', goto)
    monkeypatch.setattr('easydial.providers.blinkit.asyncio.sleep', AsyncMock())
    if failures == 3:
        with pytest.raises(BlinkitAddressLoadingError, match='connection is saved'):
            await worker.navigate(page, 'milk')
        goto.assert_not_awaited()
    else:
        await worker.navigate(page, 'milk')
        assert goto.await_args.args[0] is page
        assert '/s/?q=milk' in goto.await_args.args[1]
    assert worker.verify_location.await_count == min(failures + 1, 3)
    assert all(call.args == (page,) for call in worker.verify_location.await_args_list)
    worker.browser.save.assert_not_awaited()
    assert worker.verified_location == 'Saved locality'


async def test_loading_failure_keeps_connection_and_search_allows_recovery_budget(monkeypatch):
    from easydial.models import Provider
    from easydial.providers import ProviderWorkers
    from easydial.providers.blinkit_location import BlinkitAddressLoadingError
    from easydial.settings import Settings

    workers = ProviderWorkers(Settings(_env_file=None, openai_api_key=''))
    deadlines = []
    async def bounded(awaitable, *, timeout):
        deadlines.append(timeout)
        return await awaitable
    monkeypatch.setattr('easydial.providers.asyncio.wait_for', bounded)
    workers.workers[0].search = AsyncMock(side_effect=BlinkitAddressLoadingError())
    result = await workers.search('milk', 'Test home', Provider.BLINKIT)
    assert deadlines == [90]
    assert result[0].recovery is None
    assert 'connection is saved' in result[0].message
    await workers.close()


@pytest.mark.parametrize('persistent', [False, True])
async def test_inventory_reload_is_read_only_and_bounded(monkeypatch, persistent):
    from playwright.async_api import TimeoutError

    worker = BlinkitProvider('', 'Locality', 'Approved address')
    page = object()
    ready = AsyncMock(side_effect=[TimeoutError('loading'), TimeoutError('loading')] if persistent else [TimeoutError('loading'), None])
    cards = SimpleNamespace(first=SimpleNamespace(wait_for=ready))
    goto = AsyncMock()
    worker.browser.check_page = AsyncMock()
    monkeypatch.setattr('easydial.providers.blinkit.goto_with_retry', goto)
    if persistent:
        with pytest.raises(DomainError, match='products did not finish loading'):
            await worker.wait_for_products(page, cards, 'milk')
    else:
        await worker.wait_for_products(page, cards, 'milk')
    assert ready.await_count == 2
    goto.assert_awaited_once_with(page, 'https://blinkit.com/s/?q=milk', wait_until='domcontentloaded', timeout=20000)


async def test_review_mismatch_preserves_checkpoint_and_proposes_reconciliation(monkeypatch, tmp_path):
    from easydial.models import SessionView
    from easydial.providers.cart_guard import CartConflict
    from tests.test_payment_handoff import reviewed
    from tests.test_whatsapp import make_runtime

    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))['view'])
    worker = BlinkitProvider('', 'Locality', view.address_label)
    before = {'items': [], 'text': 'Your cart is empty', 'header_address': 'Locality'}
    after = {'items': [{'name': line.offer.name, 'pack': line.offer.pack_size, 'quantity': line.quantity}
                       for line in view.lines] + [{'name': 'Old popcorn', 'pack': '120 g', 'quantity': 1}],
             'address': view.address_label}
    @asynccontextmanager
    async def browser_page():
        yield object(), None
    worker.browser_page = browser_page
    worker.navigate_cart = AsyncMock()
    worker.read_cart = AsyncMock(side_effect=[before, after])
    worker.set_quantity = AsyncMock()
    worker.browser.save = AsyncMock()
    with pytest.raises(CartConflict) as caught:
        await worker.review(view)
    assert caught.value.cart == after
    assert caught.value.changed
    assert worker.guard.expected == ()
    assert worker.previous_lines == []
    worker.browser.save.assert_not_awaited()
    runtime.inbox.close()


async def test_cart_navigation_avoids_search_but_keeps_location_check(monkeypatch):
    worker = BlinkitProvider('', 'Locality', 'Home')
    worker.verify_location = AsyncMock()
    worker.browser.check_page = AsyncMock()
    goto = AsyncMock()
    monkeypatch.setattr('easydial.providers.blinkit.goto_with_retry', goto)
    page = object()
    await worker.navigate_cart(page)
    worker.verify_location.assert_awaited_once_with(page)
    goto.assert_not_awaited()
    await worker.navigate(page, 'Diet Coke')
    assert worker.verify_location.await_count == 2
    assert goto.await_count == 1
    assert '/s/?q=Diet%20Coke' in goto.call_args.args[1]


async def test_instamart_keeps_existing_navigation():
    from easydial.providers.instamart import InstamartProvider

    worker = InstamartProvider()
    worker.navigate = AsyncMock()
    await worker.navigate_cart('page')
    worker.navigate.assert_awaited_once_with('page', 'milk')
