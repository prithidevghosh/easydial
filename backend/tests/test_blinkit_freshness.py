from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.providers.blinkit import BlinkitProvider, cart_signature


@pytest.mark.parametrize("outcome", ["restored", "empty", "changed_quantity"])
async def test_revalidate_retries_without_erasing_checkpoint(monkeypatch, outcome):
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

    @asynccontextmanager
    async def browser_page():
        yield None, None

    worker.browser_page = browser_page
    worker.navigate = AsyncMock()
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
    assert worker.guard.owner == session.id
    assert worker.guard.expected == original


@pytest.mark.parametrize("address", ["Flat 7, Road", "Flat 8, Road", None])
async def test_changed_header_requires_full_address_proof(monkeypatch, address):
    worker = BlinkitProvider("", "Short locality", "Flat 7, Road")
    subtitle = SimpleNamespace(inner_text=AsyncMock(return_value="Expanded delivery label"))
    page = SimpleNamespace(goto=AsyncMock(), locator=lambda _: SimpleNamespace(first=subtitle))
    worker.browser.check_page = AsyncMock()
    worker.read_cart = AsyncMock(return_value={"address": address})
    monkeypatch.setattr("easydial.providers.blinkit.asyncio.sleep", AsyncMock())
    if address == worker.verified_address:
        await worker.navigate(page, "diet coke")
        assert worker.verified_location == "Expanded delivery label"
        assert page.goto.await_count == 2
    else:
        with pytest.raises(DomainError, match="delivery location changed"):
            await worker.navigate(page, "diet coke")
        assert worker.verified_location == "Short locality"
        assert page.goto.await_count == 1
    worker.read_cart.assert_awaited_once_with(page, resolve_address=False)
