from copy import deepcopy

import pytest

from easydial.controller import DomainError
from easydial.models import BasketLine, Provider, SessionView
from easydial.providers.blinkit import cart_signature as blinkit_signature
from easydial.providers.cart_guard import CartGuard
from easydial.providers.instamart import InstamartProvider
from tests.fakes import FixtureProvider


class ContractInstamart(InstamartProvider):
    """Transport fake implementing the documented response shape, never the live endpoint."""

    def __init__(self):
        super().__init__("test-only", "address-1")
        self.calls = []
        self.cart = {"items": [], "cartAbsent": True}
        self.cap = False
        self.wrong_address = False
        self.bad_total = False

    async def call(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if name == "update_cart":
            items = [{**x, "isInStockAndAvailable": True} for x in arguments["items"]]
            if self.cap:
                items[0]["quantity"] = 1
            self.cart = {
                "items": items,
                "selectedAddressDetails": {
                    "id": "wrong" if self.wrong_address else "address-1",
                    "address": "Test home",
                },
                "cartTotalAmount": "₹66.00",
                "cartId": "test-cart",
                "billBreakdown": {
                    "lineItems": [
                        {"label": "Item total", "value": "₹56.00"},
                        {"label": "Delivery fee", "value": "₹10.00"},
                    ],
                    "toPay": {"label": "To pay", "value": "₹90" if self.bad_total else "₹66"},
                },
            }
        return deepcopy(self.cart)


async def basket():
    offer = (await FixtureProvider(Provider.INSTAMART).search("milk", "Test home")).offers[0]
    return SessionView(
        id="test-session", mode="live", address_label="Test home", lines=[BasketLine(offer=offer, quantity=2)]
    )


async def test_real_cart_contract_replace_readback_and_bill():
    worker, session = ContractInstamart(), await basket()
    review = await worker.review(session)
    assert review.total_paise == 6600 and review.item_subtotal_paise == 5600
    assert review.bill_lines[1]["label"] == "Delivery fee"
    assert [name for name, _ in worker.calls] == ["get_cart", "update_cart", "get_cart"]
    assert worker.calls[1][1]["selectedAddressId"] == "address-1"
    assert worker.calls[1][1]["items"][0]["quantity"] == 2
    session.lines[0].quantity = 3
    await worker.review(session)
    assert worker.calls[-2][1]["items"][0]["quantity"] == 3


async def test_existing_remote_basket_is_not_replaced():
    worker = ContractInstamart()
    worker.cart = {"items": [{"spinId": "foreign", "skuId": "foreign-sku", "quantity": 1}]}
    with pytest.raises(DomainError, match="existing or externally changed"):
        await worker.review(await basket())
    assert [name for name, _ in worker.calls] == ["get_cart"]


@pytest.mark.parametrize(
    "flag,reason",
    [
        ("cap", "changed quantities"),
        ("wrong_address", "address does not match"),
        ("bad_total", "total could not be verified"),
    ],
)
async def test_provider_changes_cannot_produce_successful_review(flag, reason):
    worker = ContractInstamart()
    setattr(worker, flag, True)
    with pytest.raises(DomainError, match=reason):
        await worker.review(await basket())


async def test_payment_tools_blocked_before_network():
    worker = InstamartProvider("test-only", "address-1")
    for name in ["checkout", "confirm_order", "get_payment_options", "clear_cart"]:
        with pytest.raises(ValueError, match="blocked"):
            await worker.call(name, {})


def test_uncertain_cart_write_recovery_and_external_changes():
    guard = CartGuard()
    guard.check("a", ())
    desired = (("p", "s", 2),)
    guard.before_write(desired)
    guard.check("a", desired)  # An uncertain response may have succeeded; reconcile readback.
    with pytest.raises(DomainError):
        guard.check("b", desired)
    with pytest.raises(DomainError):
        guard.check("a", (("p", "s", 4),))


def test_blinkit_cart_cannot_invent_quantity_or_treat_missing_dom_as_empty():
    with pytest.raises(ValueError):
        blinkit_signature({"items": [], "text": "Your basket"})
    assert blinkit_signature({"items": [], "text": "Your cart is empty"}) == ()
    item = {"name": "Amul Milk", "pack": "500 ml", "quantity": None}
    with pytest.raises(ValueError):
        blinkit_signature({"items": [item]})
    item["quantity"] = 2
    assert blinkit_signature({"items": [item]}) == (("amulmilk", "500ml", 2),)
    with pytest.raises(ValueError):
        blinkit_signature({"items": [item, item]})


def test_production_rejects_demo_mode_and_starts_without_credentials():
    from fastapi.testclient import TestClient

    from easydial.app import create_app
    from easydial.settings import Settings

    with pytest.raises(ValueError, match="Only live"):
        create_app(Settings(_env_file=None, commerce_mode="sample"))
    with TestClient(create_app(Settings(_env_file=None, openai_api_key=""))) as client:
        config = client.get("/config").json()
        assert config["mode"] == "live" and not any(config["providers"].values())
        created = client.post("/sessions").json()
        path = f"/sessions/{created['session']['id']}"
        headers = {"Authorization": f"Bearer {created['token']}"}
        result = client.post(path + "/search", headers=headers, json={"query": "milk", "expected_version": 0})
        assert result.status_code == 503
        assert client.get(path, headers=headers).json()["results"] == []


async def test_owned_cart_can_be_cleared_but_foreign_cart_cannot():
    worker, session = ContractInstamart(), await basket()
    await worker.review(session)
    await worker.clear(session.id)
    assert worker.cart["items"] == []
    worker.cart = {"items": [{"spinId": "foreign", "skuId": "other", "quantity": 1}]}
    count = len(worker.calls)
    with pytest.raises(DomainError):
        await worker.clear(session.id)
    assert [name for name, _ in worker.calls[count:]] == ["get_cart"]


def test_synced_store_must_be_cleared_before_switching():
    from easydial.controller import SessionStore
    from easydial.models import EditRequest, Offer

    current = SessionStore().create("live", "home")
    current.view.synced_provider = Provider.INSTAMART
    offer = Offer(
        id="blinkit-item",
        provider=Provider.BLINKIT,
        product_id="p",
        sku_id="p",
        name="Milk",
        price_paise=1000,
        available=True,
        mode="live",
        address_label="home",
    )
    current.offers[offer.id] = offer
    with pytest.raises(DomainError, match="previous store"):
        current.edit(EditRequest(kind="add", offer_id=offer.id, expected_version=0))
    assert current.view.lines == []
