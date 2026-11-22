from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from easydial.providers.blinkit import BlinkitProvider, cart_signature


async def test_clear_owned_basket_does_not_search_or_select_delivery_address(monkeypatch):
    worker = BlinkitProvider("", "locality", "address")
    item = {"name": "milk", "pack": "500 ml", "quantity": 1}
    cart = {"items": [item]}
    worker.guard.owner = "draft"
    worker.guard.accepted(cart_signature(cart))
    line = SimpleNamespace(offer=SimpleNamespace(name="milk", pack_size="500 ml"))
    worker.previous_lines = [line]
    page = object()

    @asynccontextmanager
    async def browser_page():
        yield page, None

    worker.browser_page = browser_page
    worker.navigate = AsyncMock()
    worker.read_cart = AsyncMock(side_effect=[cart, {"items": [], "text": "Your cart is empty"}])
    worker.set_quantity = AsyncMock()
    worker.browser.save = AsyncMock()
    goto = AsyncMock()
    monkeypatch.setattr("easydial.providers.blinkit.goto_with_retry", goto)
    await worker.clear("draft")
    worker.navigate.assert_not_awaited()
    assert worker.read_cart.call_args_list[0].kwargs == {"resolve_address": False}
    assert worker.read_cart.call_args_list[-1].kwargs == {"resolve_address": False, "after_verified_removal": True}
    worker.set_quantity.assert_awaited_once_with(page, line, 0)
    assert worker.previous_lines == []
    assert worker.guard.expected == ()
