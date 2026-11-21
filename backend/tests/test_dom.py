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
