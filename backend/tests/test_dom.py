"""Redacted structural regressions from inspected pages. Opt-in Chrome, no network."""

import os

import pytest
from playwright.async_api import async_playwright

from easydial.providers import blinkit_dom, instamart_dom

pytestmark = pytest.mark.skipif(
    os.getenv("EASYDIAL_DOM_TESTS") != "1", reason="opt-in local Chrome DOM tests"
)


@pytest.fixture
async def page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        yield page
        await browser.close()


async def test_blinkit_authenticated_cart_nested_wrapper_counted_once(page):
    await page.set_content("""<div class="cart-modal-rn"><div class="CartProduct__Container-sc-test">
      <div class="DefaultProductCard__Container-sc-test">
        <div class="DefaultProductCard__ProductTitle">Test milk</div>
        <div class="DefaultProductCard__ProductVariantContainer">500 ml</div>
        <div class="AddToCart__UpdatedButtonContainer"><div>U</div>2<div>5</div></div>
      </div></div><div class="BillCard__BillItemContainer">Bill details</div>
      <div class="BillCard__BillItemContainer">Items total<br>₹60</div>
      <div class="CheckoutStrip__NetPriceText">₹103</div></div>""")
    cart = await page.locator(".cart-modal-rn").evaluate(blinkit_dom.CART)
    assert cart["items"] == [{"name": "Test milk", "pack": "500 ml", "quantity": 2}]
    assert cart["bill"] == [{"label": "Items total", "value": "₹60"}]


async def test_blinkit_search_mrp_and_pack(page):
    await page.set_content("""<div id="123" role="button"><div class="tw-line-clamp-2">Test milk</div>
      <div class="tw-line-clamp-1">1 ltr</div><div>₹61</div>
      <div style="text-decoration:line-through"><span>₹65</span></div><div>ADD</div></div>""")
    card = await page.locator('[id="123"]').evaluate(blinkit_dom.CARD)
    assert card["current_prices"] == ["₹61"] and card["pack"] == "1 ltr"
    assert await page.locator('[id="123"]').evaluate(blinkit_dom.QUANTITY) == 0


async def test_instamart_cart_ignores_animated_quantity_and_old_total(page):
    await page.set_content("""<div data-testid="item-list-available-items-container"><div role="listitem">
      <div data-testid="cart-item-name">Test milk</div><div data-testid="cart-item-quantity">500 ml</div>
      <div data-testid="add_buttons_center">1</div><div aria-hidden="true">2</div></div></div>
      <div class="_2vw3P"><div data-testid="billline-segment-text">To Pay</div>
      <div data-testid="billline-segment-text" style="text-decoration:line-through">₹102.15</div>
      <div data-testid="billline-segment-text">₹101</div></div>
      <div data-testid="CART_FOOTER_BILL_TOTAL">₹101</div>""")
    data = await page.locator("body").evaluate(instamart_dom.CART)
    assert len(data["items"]) == 1 and data["items"][0]["quantity"] == 1
    assert data["bill"] == [{"label": "To Pay", "value": "₹101"}]


async def test_blinkit_empty_cart_uses_disabled_button_not_wrapper(page):
    from easydial.providers.blinkit import BlinkitProvider

    await page.set_content("""<div class="CartButton__Container-sc-test">
      <div class="CartButton__Button-sc-test" disabled>My Cart</div></div>""")
    worker = BlinkitProvider("", "Test locality", "Test address")
    worker.header_address = "Test locality"
    data = await worker.read_cart(page)
    assert data["items"] == [] and data["header_address"] == "Test locality"


async def test_cart_click_waits_for_delayed_save_without_inspecting_payload(page):
    import asyncio

    from easydial.providers.browser import click_and_settle

    saved = []

    async def complete(route):
        await asyncio.sleep(0.2)
        saved.append(True)
        await route.fulfill(status=200, body="ok", headers={"access-control-allow-origin": "*"})

    await page.route("https://store.test/save", complete)
    await page.route(
        "https://store.test/",
        lambda route: route.fulfill(
            body="<button onclick=\"fetch('/save')\">Add</button>", content_type="text/html"
        ),
    )
    await page.goto("https://store.test/")
    await click_and_settle(page, page.get_by_role("button", name="Add"))
    assert saved == [True]


async def test_instamart_free_delivery_survives_struck_price(page):
    await page.set_content("""<div class="_2vw3P">
      <div data-testid="billline-segment-text">Delivery Partner Fee</div>
      <div data-testid="billline-segment-text" style="text-decoration:line-through">₹18</div>
      <div data-testid="billline-segment-text">FREE</div></div>""")
    data = await page.locator("body").evaluate(instamart_dom.CART)
    assert data["bill"] == [{"label": "Delivery Partner Fee", "value": "FREE"}]


async def test_payment_generator_waits_for_real_qr_not_visible_placeholder(page, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from easydial.models import Provider
    from easydial.providers import payments
    from tests.test_payment_handoff import qr

    image = qr("upi://pay?pa=test@bank&tr=one&am=56&cu=INR")
    await page.set_content("""<button>UPI</button><button id="generate">Generate QR</button>
        <button id="pay">Pay Now</button><img class="QrImage" width="200" height="200">
        <p>Approve payment within 04 m : 59 s</p>""")
    await page.evaluate(
        """(src) => {
        window.generated = 0; window.paid = 0;
        document.querySelector('#pay').onclick = () => window.paid++;
        document.querySelector('#generate').onclick = () => {
            window.generated++;
            setTimeout(() => document.querySelector('img').src = src, 200);
        };
    }""",
        image,
    )

    @asynccontextmanager
    async def browser_page():
        yield page, None

    async def options(*args):
        return page

    monkeypatch.setattr(payments, "open_options", options)
    worker = SimpleNamespace(provider=Provider.BLINKIT, lock=asyncio.Lock(), browser_page=browser_page)
    result = await payments.generate_upi(worker, None, SimpleNamespace(total_paise=5600))
    assert result["total_paise"] == 5600 and result["ttl_seconds"] == 299
    assert await page.evaluate("window.generated") == 1
    assert await page.evaluate("window.paid") == 0


async def test_blinkit_payment_accordions_close_cards_and_do_not_toggle_target_closed(page):
    from easydial.providers.payments import select_blinkit_method

    await page.set_content("""
    <div role="button" aria-label="Add credit or debit cards" aria-expanded="true"
         onclick="this.setAttribute('aria-expanded','false')">Cards</div>
    <div role="button" aria-label="UPI" aria-expanded="false"
         onclick="this.setAttribute('aria-expanded',this.getAttribute('aria-expanded')==='true'?'false':'true')">UPI</div>
    <div role="button" aria-label="Cash" aria-disabled="true">Cash</div>""")
    await select_blinkit_method(page, "UPI")
    await select_blinkit_method(page, "UPI")
    assert await page.get_by_role("button", name="UPI", exact=True).get_attribute("aria-expanded") == "true"
    assert (
        await page.get_by_role("button", name="Add credit or debit cards").get_attribute("aria-expanded")
        == "false"
    )
    from easydial.controller import DomainError

    with pytest.raises(DomainError):
        await select_blinkit_method(page, "Cash")


async def test_blinkit_bill_excludes_struck_mrp_and_savings_badge(page):
    await page.set_content("""<div class="cart-modal-rn">
    <div class="BillCard__BillItemContainer"><div class="BillCard__BillItemLeft">Items total<br>Saved ₹5</div>
      <div class="BillCard__BillItemRight"><span style="text-decoration:line-through">₹55</span><span> ₹50</span></div></div>
    <div class="BillCard__BillItemContainer"><div>Delivery charge</div><div class="BillCard__BillItemRight">₹35</div></div>
    <div class="BillCard__BillItemContainer"><div>Handling charge</div><div class="BillCard__BillItemRight">₹13</div></div>
    <div class="BillCard__BillItemContainer"><div>Small cart charge</div><div class="BillCard__BillItemRight">₹25</div></div>
    <div class="CheckoutStrip__NetPriceText">₹123</div></div>""")
    cart = await page.locator(".cart-modal-rn").evaluate(blinkit_dom.CART)
    assert cart["bill"][0] == {"label": "Items total", "value": "₹50"}
    from easydial.billing import breakdown, can_choose_payment
    from easydial.models import SessionView
    from easydial.providers import ProviderWorkers
    from easydial.settings import Settings

    workers = ProviderWorkers(Settings(_env_file=None))
    cart["address"] = "Test address"
    review = workers.workers[0].verified_review(
        SessionView(id="test", mode="live", address_label="Test address"), cart
    )
    assert review.item_subtotal_paise == 5000
    assert review.total_paise == 12300
    assert breakdown(review)["reconciled"]
    assert can_choose_payment(review)
    assert any("Small cart" in warning for warning in breakdown(review)["warnings"])


async def test_blinkit_bill_free_hidden_prices_and_ambiguous_amounts(page):
    await page.set_content("""<div id="drawer">
    <div class="BillCard__BillItemContainer"><div>Delivery charge</div><div class="BillCard__BillItemRight"><del>₹35</del>FREE</div></div>
    <div class="BillCard__BillItemContainer"><div>Handling charge</div><div class="BillCard__BillItemRight"><span style="display:none">₹99</span>₹13</div></div>
    <div class="BillCard__BillItemContainer"><div>Items total</div><div class="BillCard__BillItemRight">₹55 ₹50</div></div>
    </div>""")
    cart = await page.locator("#drawer").evaluate(blinkit_dom.CART)
    assert [r["value"] for r in cart["bill"]] == ["FREE", "₹13", "Unverified"]
