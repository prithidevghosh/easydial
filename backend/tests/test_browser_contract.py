"""Transport-level safety tests. These are NOT live-provider success evidence."""

from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from easydial.controller import DomainError
from easydial.models import BasketLine, Offer, Provider, SessionView
from easydial.providers.blinkit import BlinkitProvider, parse_card


class ContractBlinkit(BlinkitProvider):
    def __init__(self):
        super().__init__("", "Test locality", "Test home")
        self.cart = {"items": [], "text": "Your cart is empty", "address": "Test home"}
        self.writes = []
        self.cap = False
        self.changed_address = False

    @asynccontextmanager
    async def browser_page(self):
        yield None, None

    async def navigate(self, page, query):
        pass

    async def read_cart(self, page):
        return deepcopy(self.cart)

    async def set_quantity(self, page, line, desired):
        self.writes.append((line.offer.product_id, desired))
        self.cart = {
            "items": [
                {
                    "name": line.offer.name,
                    "pack": line.offer.pack_size,
                    "quantity": min(desired, 1) if self.cap else desired,
                }
            ]
            if desired
            else [],
            "text": "Your cart is empty" if not desired else "Basket",
            "address": "Other home" if self.changed_address else "Test home",
            "bill": [{"label": "Items total", "value": "₹66"}],
            "total": "₹93",
        }


def basket():
    offer = Offer(
        id="live:blinkit:127081",
        provider=Provider.BLINKIT,
        product_id="127081",
        sku_id="127081",
        name="Test milk",
        pack_size="500 ml",
        price_paise=3300,
        available=True,
        mode="live",
        address_label="Test home",
    )
    return SessionView(
        id="test-session", mode="live", address_label="Test home", lines=[BasketLine(offer=offer, quantity=2)]
    )


async def test_owned_cart_quantity_and_clear():
    worker, session = ContractBlinkit(), basket()
    review = await worker.review(session)
    assert review.total_paise == 9300 and review.item_subtotal_paise == 6600
    session.lines[0].quantity = 3
    await worker.review(session)
    assert worker.writes[-1] == ("127081", 3)
    await worker.clear(session.id)
    assert worker.cart["items"] == []


async def test_foreign_cart_never_mutated():
    worker = ContractBlinkit()
    worker.cart["items"] = [{"name": "Someone else's food", "pack": "1 kg", "quantity": 1}]
    with pytest.raises(DomainError, match="saved checkpoint"):
        await worker.review(basket())
    assert worker.writes == []


@pytest.mark.parametrize("flag", ["cap", "changed_address"])
async def test_provider_changes_prevent_successful_review(flag):
    worker = ContractBlinkit()
    setattr(worker, flag, True)
    with pytest.raises(DomainError):
        await worker.review(basket())


def test_litre_pack_and_current_price():
    offer = parse_card(
        {
            "id": "123",
            "name": "Test Milk",
            "pack": "1 ltr",
            "text": "Test Milk\n1 ltr\n₹61\n₹65\nADD",
            "current_prices": ["₹61"],
        },
        "home",
    )
    assert offer.pack_size == "1 ltr" and offer.price_paise == 6100


async def test_replacing_pack_within_product_family_removes_previous_sku():
    worker, session = ContractBlinkit(), basket()
    await worker.review(session)
    session.lines[0].offer = session.lines[0].offer.model_copy(
        update={"sku_id": "other-pack", "pack_size": "1 ltr"}
    )
    await worker.review(session)
    assert worker.writes[-2:] == [("127081", 0), ("127081", 2)]
