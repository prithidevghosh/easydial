import pytest

from easydial.controller import DomainError
from easydial.models import Provider
from easydial.providers.blinkit import cart_signature as blinkit_signature
from easydial.providers.cart_guard import CartGuard
from easydial.providers.instamart import InstamartProvider


async def test_disconnected_instamart_cannot_return_prices_or_review():
    worker = InstamartProvider()
    assert not worker.connected()
    result = await worker.search("milk", "Test home")
    assert result.status == "not_connected" and result.offers == []
    from tests.test_browser_contract import basket

    with pytest.raises(DomainError, match="Connect"):
        await worker.review(basket())
    with pytest.raises(DomainError, match="Connect"):
        await worker.clear("session")


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
    with pytest.raises(DomainError, match="Instamart cart has not been confirmed empty"):
        current.edit(EditRequest(kind="add", offer_id=offer.id, expected_version=0))
    assert current.view.lines == []


def test_store_switch_with_items_explains_removal_before_review():
    from easydial.controller import Session
    from easydial.models import BasketLine, EditRequest, Offer, SessionView

    old = Offer(
        id="old",
        provider=Provider.INSTAMART,
        product_id="p",
        sku_id="p",
        name="Milk",
        price_paise=1000,
        available=True,
        mode="live",
        address_label="home",
    )
    new = old.model_copy(update={"id": "new", "provider": Provider.BLINKIT})
    view = SessionView(
        id="test",
        mode="live",
        address_label="home",
        synced_provider=Provider.INSTAMART,
        lines=[BasketLine(offer=old, quantity=3)],
    )
    session = Session(view, offers={new.id: new})
    with pytest.raises(DomainError, match="first ask me to remove all items"):
        session.edit(EditRequest(kind="add", offer_id=new.id, expected_version=0))
    assert session.view.lines[0].quantity == 3
    assert session.view.version == 0
