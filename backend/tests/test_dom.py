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


async def test_onboarding_reads_only_instamart_saved_address_cards(page):
    from types import SimpleNamespace

    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content("""<div data-testid="modal-overlay" style="width:10px;height:10px"></div>
      <div class="FObAQ">
       <div class="_1gcSd"><div class="_2wcdd"><div>Home</div><div>Flat 1, Test road</div></div></div>
       <div class="_1gcSd"><div class="_2wcdd"><div>Work</div><div>Office 2, Test road</div></div></div>
      </div><div>Unrelated product address text</div>""")

    async def opened():
        return page, None

    adapter = StoreLogin(SimpleNamespace(), SimpleNamespace(consented=lambda *a: True))
    adapter.sessions["alice", "instamart"] = SimpleNamespace(open=opened)
    rows = await adapter.addresses("alice", "instamart")
    assert [x["address"] for x in rows] == ["Flat 1, Test road", "Office 2, Test road"]
    assert rows[0]["id"] != rows[1]["id"]


async def test_onboarding_otp_fields_exclude_mobile_number_and_search(page):
    from types import SimpleNamespace

    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content("""<input type="tel" maxlength="10"><input name="auth-sidebar-mobile" type="tel">
      <input placeholder="Search"><input autocomplete="one-time-code" inputmode="numeric">""")
    adapter = StoreLogin(SimpleNamespace(), None)
    assert await adapter.otp_inputs(page).count() == 1


async def test_customer_browser_cookie_contexts_are_separate_and_encrypted(tmp_path):
    from cryptography.fernet import Fernet

    from easydial.providers.browser import BrowserSession
    from easydial.whatsapp.security import SecretBox

    box = SecretBox(Fernet.generate_key())
    sessions = []
    try:
        for name in ["alice", "bob"]:
            path = tmp_path / (name + ".enc")
            box.write(
                path,
                {
                    "cookies": [
                        {
                            "name": "store-session",
                            "value": name + "-secret",
                            "domain": "example.test",
                            "path": "/",
                            "expires": -1,
                            "httpOnly": True,
                            "secure": True,
                            "sameSite": "Lax",
                        }
                    ],
                    "origins": [],
                },
            )
            session = BrowserSession(storage_state=str(path))
            session.headless = True
            session.secret_box = box
            sessions.append(session)
            _, context = await session.open()
            assert [x["value"] for x in await context.cookies()] == [name + "-secret"]
            await session.save()
            assert name + "-secret" not in path.read_text()
        assert sessions[0].context is not sessions[1].context
        await sessions[0].context.clear_cookies()
        assert len(await sessions[1].context.cookies()) == 1
    finally:
        for session in sessions:
            await session.close()


@pytest.mark.parametrize("delayed_location", [True, False])
async def test_blinkit_login_waits_for_client_rendered_entry(page, delayed_location):
    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('<main id="root"></main>')
    await page.evaluate("""(locationGate) => {
      const root = document.querySelector('#root');
      const showLogin = () => {
        root.innerHTML = '<button id="login">Login</button>';
        document.querySelector('#login').onclick = () => {
          root.innerHTML = '<input placeholder="Enter mobile number">';
        };
      };
      setTimeout(() => {
        if (!locationGate) { showLogin(); return; }
        root.innerHTML = '<input placeholder="search delivery location">';
        root.querySelector('input').oninput = (event) => {
          window.entryLocality = event.target.value;
          if (root.querySelector('button')) return;
          setTimeout(() => {
            const result = document.createElement('button');
            result.textContent = 'Bengaluru, Karnataka, India';
            result.onclick = showLogin;
            root.appendChild(result);
          }, 200);
        };
      }, 350);
    }""", delayed_location)
    await StoreLogin.open_blinkit_login(page)
    assert await page.get_by_placeholder('Enter mobile number').is_visible()
    assert await page.get_by_placeholder('Enter mobile number').input_value() == ''
    if delayed_location:
        assert await page.evaluate('window.entryLocality') == 'Koramangala Bengaluru'


async def test_blinkit_location_gate_can_arrive_after_login_button(page):
    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('<button id="login">Login</button><main id="root"></main>')
    await page.evaluate("""() => {
      const root = document.querySelector('#root');
      let located = false;
      document.querySelector('#login').onclick = () => {
        if (located) root.innerHTML = '<input placeholder="Enter mobile number">';
      };
      setTimeout(() => {
        root.innerHTML = '<input placeholder="search delivery location">';
        root.querySelector('input').oninput = () => {
          if (root.querySelector('button')) return;
          const result = document.createElement('button');
          result.textContent = 'Bengaluru, Karnataka, India';
          result.onclick = () => { located = true; root.innerHTML = ''; };
          root.appendChild(result);
        };
      }, 400);
    }""")
    await StoreLogin.open_blinkit_login(page)
    assert await page.get_by_placeholder('Enter mobile number').is_visible()


async def test_blinkit_onboarding_nested_address_selection(page):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from easydial.controller import DomainError
    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('''<div class="LocationBar__Subtitle">Test locality</div>
    <section id="addresses">
    <div class="AddressListItem__AddressItemWrapperItem">
      <div class="AddressListItem__AddressDetailsWrapper"><div class="AddressListItem__AddressLabel">Home</div><div class="AddressListItem__AddressDetails">Flat 1, Test Road</div></div>
    </div>
    <div class="AddressListItem__AddressItemWrapperItem">
      <div class="AddressListItem__AddressDetailsWrapper"><div class="AddressListItem__AddressLabel">Work</div><div class="AddressListItem__AddressDetails">Flat 1, Test Road Extension</div></div>
    </div></section>''')
    await page.evaluate('''() => {
      document.querySelectorAll('[class="AddressListItem__AddressDetails"]').forEach((el, i) => {
        el.onclick = () => { window.selectedRow = i; document.querySelector('#addresses').style.display='none'; };
      });
    }''')
    async def opened():
        return page, None
    session = SimpleNamespace(open=opened, save=AsyncMock())
    accounts = SimpleNamespace(consented=lambda *a: True, session_path=lambda *a: 'unused',
                               session_box=lambda *a: object())
    adapter = StoreLogin(SimpleNamespace(), accounts)
    adapter.sessions['alice', 'blinkit'] = session
    rows = await adapter.addresses('alice', 'blinkit')
    assert rows[0]['label'] == 'Home: Flat 1, Test Road'
    assert rows[0]['address'] == 'Flat 1, Test Road'
    # The longer second address contains the first; substring matching is unsafe.
    result = await adapter.select_address('alice', 'blinkit', rows[0])
    assert await page.evaluate('window.selectedRow') == 0
    assert result['address'] == 'Flat 1, Test Road'
    session.save.assert_awaited_once()
    await page.locator('#addresses').evaluate('(el) => el.style.display="block"')
    await page.locator('[class="AddressListItem__AddressDetails"]').first.evaluate('(el) => el.textContent="Changed road"')
    with pytest.raises(DomainError, match='address changed'):
        await adapter.select_address('alice', 'blinkit', rows[0])
    assert session.save.await_count == 1


@pytest.mark.parametrize('provider', ['blinkit', 'instamart'])
async def test_onboarding_duplicate_addresses_are_selectable_and_reordering_rejected(page, provider):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from easydial.controller import DomainError
    from easydial.whatsapp.store_login import StoreLogin

    rows = [('Home', 'Flat 1, Test Road'), ('Home', 'Flat 1, Test Road'), ('Work', 'Flat 2, Test Road')]
    if provider == 'blinkit':
        cards = ''.join(f'<div class="AddressListItem__AddressItemWrapperItem"><div class="AddressListItem__AddressDetailsWrapper"><div class="AddressListItem__AddressLabel">{label}</div><div class="AddressListItem__AddressDetails">{address}</div></div></div>' for label, address in rows)
        html = f'<div class="LocationBar__Subtitle">Test locality</div><section id="cards">{cards}</section>'
    else:
        cards = ''.join(f'<div class="_1gcSd"><div class="_2wcdd"><div>{label}</div><div>{address}</div></div></div>' for label, address in rows)
        html = f'<div data-testid="address-bar"><div data-testid="address-line">Test locality</div></div><div data-testid="modal-overlay" style="height:10px;width:10px"></div><section id="cards" class="FObAQ">{cards}</section>'
    await page.set_content(html)
    await page.evaluate('''() => {
      [...document.querySelector('#cards').children].forEach((el, i) => el.onclick = () => {
        window.selectedRow = i;
        document.querySelector('#cards').style.display = 'none';
        const overlay = document.querySelector('[data-testid="modal-overlay"]');
        if (overlay) overlay.style.display = 'none';
      });
    }''')
    async def opened():
        return page, None
    session = SimpleNamespace(open=opened, save=AsyncMock())
    accounts = SimpleNamespace(consented=lambda *a: True, session_path=lambda *a: 'unused',
                               session_box=lambda *a: object())
    adapter = StoreLogin(SimpleNamespace(), accounts)
    adapter.sessions['alice', provider] = session
    choices = await adapter.addresses('alice', provider)
    assert len(choices) == 3
    assert len({a['id'] for a in choices}) == 3
    await adapter.select_address('alice', provider, choices[1])
    assert await page.evaluate('window.selectedRow') == 1
    session.save.assert_awaited_once()
    await page.evaluate('''() => {
      const cards = document.querySelector('#cards');
      cards.style.display = 'block'; cards.prepend(cards.lastElementChild);
      const overlay = document.querySelector('[data-testid="modal-overlay"]');
      if (overlay) overlay.style.display = 'block';
    }''')
    with pytest.raises(DomainError, match='address changed'):
        await adapter.select_address('alice', provider, choices[1])
    assert session.save.await_count == 1

@pytest.mark.parametrize("matches", [0, 1, 2])
async def test_blinkit_checkout_selects_only_unique_onboarded_address(page, matches):
    from easydial.controller import DomainError
    from easydial.providers.blinkit import BlinkitProvider

    worker = BlinkitProvider("", "Test locality", "Flat 7, Test Road")
    cards = [
        '<div class="AddressList__AddressItemWrapper-x"><div class="AddressList__AddressDetails-x">Other Road</div></div>'
    ]
    cards += [
        '<div class="AddressList__AddressItemWrapper-x"><div class="AddressList__AddressDetails-x">Home<div class="AddressList__AddressDetails-x">Flat 7, Test Road</div></div></div>'
    ] * matches
    await page.set_content(
        """<div class="CheckoutStrip__CTAText-x" onclick="document.querySelector('#addresses').style.display='block'">Proceed</div>
        <div id="addresses" style="display:none">"""
        + "".join(cards)
        + """</div>
        <script>document.querySelectorAll('.AddressList__AddressItemWrapper-x').forEach(e => e.onclick=()=>{
          window.selected=e.innerText; document.querySelector('#addresses').style.display='none';
        });</script>"""
    )
    if matches == 1:
        await worker.select_checkout_address(page)
        assert "Flat 7, Test Road" in await page.evaluate("window.selected")
    else:
        with pytest.raises(DomainError, match="reconnect"):
            await worker.select_checkout_address(page)
        assert await page.evaluate("window.selected") is None


async def test_blinkit_address_recovery_never_clicks_payment_button(page):
    from easydial.controller import DomainError
    from easydial.providers.blinkit import BlinkitProvider

    worker = BlinkitProvider("", "Test locality", "Flat 7, Test Road")
    await page.set_content('<div class="CheckoutStrip__CTAText-x" onclick="window.paid=true">Pay now</div>')
    with pytest.raises(DomainError, match="confirm your delivery address"):
        await worker.select_checkout_address(page)
    assert await page.evaluate("window.paid") is None


@pytest.mark.parametrize("bill_changed", [False, True])
async def test_blinkit_payment_address_detour_rechecks_bill(page, monkeypatch, bill_changed):
    from easydial.controller import DomainError
    from easydial.providers import payments
    from easydial.providers.blinkit import BlinkitProvider

    worker = BlinkitProvider("", "Test locality", "Flat 7, Test Road")
    await page.set_content('''<div id="addresses">
      <div class="AddressList__AddressItemWrapper-x" onclick="this.parentElement.remove();document.querySelector('#cart').hidden=false">
        <div class="AddressList__AddressDetails-x">Flat 7, Test Road</div></div></div>
      <div id="cart" hidden><button onclick="window.proceeded=true;document.body.innerHTML='Select Payment Method'">Proceed To Pay</button></div>''')
    checked = []

    async def read_cart(_page):
        return {"test": "cart"}

    def verify(_worker, _view, _review, cart):
        checked.append(cart)
        if bill_changed:
            raise DomainError("The bill changed")

    monkeypatch.setattr(worker, "read_cart", read_cart)
    monkeypatch.setattr(payments, "verify_checkout_cart", verify)
    if bill_changed:
        with pytest.raises(DomainError, match="bill changed"):
            await payments.continue_blinkit_address(worker, None, None, page)
        assert await page.evaluate("window.proceeded") is None
    else:
        await payments.continue_blinkit_address(worker, None, None, page)
        assert await page.evaluate("window.proceeded") is True
    assert checked == [{"test": "cart"}]


async def test_blinkit_payment_transition_handles_direct_entry_and_store_error(page):
    from easydial.controller import DomainError
    from easydial.providers.payments import continue_blinkit_address

    await page.set_content('<div>Select Payment Method</div>')
    await continue_blinkit_address(None, None, None, page)
    await page.set_content('<div>Oops! something went wrong</div>')
    with pytest.raises(DomainError, match="couldn't open payment"):
        await continue_blinkit_address(None, None, None, page)


async def test_blinkit_known_cart_waits_for_hydration(page):
    from easydial.providers.blinkit import BlinkitProvider

    worker = BlinkitProvider("", "Test locality")
    worker.guard.expected = (("diet coke", "330 ml", 4),)
    await page.set_content('''<div class="CartButton__Button-test" disabled
      onclick="document.querySelector('.cart-modal-rn').style.display='block'">My Cart</div>
      <div class="cart-modal-rn" style="display:none">
        <div class="DefaultProductCard__Container-test">
          <div class="ProductTitle">Diet Coke</div><div class="ProductVariant">330 ml</div>
          <div class="AddToCart__UpdatedButtonContainer">4</div>
        </div><div class="BillCard__BillItemContainer">Items total<br>₹200</div>
        <div class="CheckoutStrip__NetPriceText">₹200</div>
      </div><script>setTimeout(()=>document.querySelector('[disabled]').removeAttribute('disabled'), 350)</script>''')
    cart = await worker.read_cart(page)
    assert cart["items"] == [{"name": "Diet Coke", "pack": "330 ml", "quantity": 4}]


async def test_instamart_login_waits_for_delayed_location_gate(page):
    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('''<main>Loading</main><script>
    window.headerClicks = 0;
    setTimeout(() => {
      document.querySelector('main').innerHTML = `<button onclick="window.headerClicks++">Sign in</button>
      <div style="position:fixed;inset:0;background:white;z-index:10">
        <span id="gate-login">Login</span><p>to see your saved addresses</p>
      </div>`;
      document.querySelector('#gate-login').onclick = () => {
        document.body.innerHTML = '<input name="auth-sidebar-mobile" maxlength="10" type="tel">';
      };
    }, 300);
    </script>''')
    await StoreLogin.open_instamart_login(page)
    assert await page.locator('input[name="auth-sidebar-mobile"]').is_visible()
    assert await page.evaluate('window.headerClicks') == 0


async def test_instamart_login_reobserves_overlay_after_header_race(page):
    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('''<button id="header">Sign in</button><script>
    document.querySelector('#header').onclick = () => {
      document.body.innerHTML = `<div style="position:fixed;inset:0;background:white">
      <span id="login">Login</span></div>`;
      document.querySelector('#login').onclick = () => {
        document.body.innerHTML = '<input name="auth-sidebar-mobile" maxlength="10" type="tel">';
      };
    };
    </script>''')
    await StoreLogin.open_instamart_login(page)
    assert await page.locator('input[name="auth-sidebar-mobile"]').is_visible()


async def test_instamart_login_accepts_existing_phone_form_without_click(page):
    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('''<input name="auth-sidebar-mobile" maxlength="10" type="tel">
    <button onclick="window.clicked=true">LOGIN</button>''')
    await StoreLogin.open_instamart_login(page)
    assert not await page.evaluate('Boolean(window.clicked)')


async def test_instamart_otp_to_address_selection_ignores_duplicate_overlays(page):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('''<div data-testid="modal-overlay" style="display:none">Old auth dialog</div>
    <div data-testid="modal-overlay">Location backdrop</div>
    <div style="display:none" class="FObAQ"><div class="_1gcSd">
      <div class="_2wcdd"><div>Hidden</div><div>Wrong address</div></div></div></div>
    <input autocomplete="one-time-code" type="tel" maxlength="6" oninput="
      document.querySelector('#account').style.display='block';this.style.display='none';">
    <button id="account" data-testid="user-account-button" style="display:none">Account</button>
    <button data-testid="address-bar" onclick="document.querySelector('#addresses').style.display='block'">
      <span data-testid="address-line">Test locality</span></button>
    <div id="addresses" class="FObAQ" style="display:none">
      <div class="_1gcSd" onclick="document.querySelector('#addresses').style.display='none'">
        <div class="_2wcdd"><div>Home</div><div>Flat 1, Test road</div></div></div>
    </div>''')
    session = SimpleNamespace(open=AsyncMock(return_value=(page, None)),
                              check_page=AsyncMock(), save=AsyncMock())
    accounts = SimpleNamespace(consented=lambda *args: True,
                               session_path=lambda *args: 'test.enc', session_box=lambda *args: object())
    adapter = StoreLogin(SimpleNamespace(), accounts)
    adapter.sessions['alice', 'instamart'] = session
    addresses = await adapter.verify_otp('alice', 'instamart', '123456')
    assert len(addresses) == 1
    assert addresses[0]['address'] == 'Flat 1, Test road'
    selected = await adapter.select_address('alice', 'instamart', addresses[0])
    assert selected['address'] == 'Flat 1, Test road'
    assert selected['location'] == 'Test locality'
    session.save.assert_awaited_once()
    assert await page.get_by_test_id('modal-overlay').count() == 2


async def test_checkout_waits_for_delayed_order_acknowledgement(page):
    from easydial.providers.payments import wait_for_submission_response

    await page.set_content("<main>Processing your payment…</main>")
    await page.evaluate("""() => setTimeout(() => {
        document.querySelector('main').innerText = 'Order summary Arriving in 8 minutes Order details';
    }, 250)""")
    await wait_for_submission_response(page)
    assert "Order summary" in await page.locator("body").inner_text()


async def test_blinkit_upstream_overflow_is_retryable_before_submission(page):
    from easydial.providers.payments import CheckoutTemporarilyUnavailable, continue_blinkit_address

    await page.set_content("upstream connect error or disconnect/reset before headers. reset reason: overflow")
    with pytest.raises(CheckoutTemporarilyUnavailable):
        await continue_blinkit_address(None, None, None, page)


async def test_login_wait_handles_delayed_otp_without_reclick(page):
    from types import SimpleNamespace

    from easydial.whatsapp.store_login import StoreLogin

    await page.set_content('<input name="auth-sidebar-mobile"><button onclick="window.clicks=(window.clicks||0)+1">LOGIN</button>')
    await page.evaluate("""() => setTimeout(() => {
        const input = document.createElement('input');
        input.autocomplete = 'one-time-code';
        document.body.appendChild(input);
    }, 200)""")
    adapter = StoreLogin(SimpleNamespace(), None)

    async def open_page():
        return page, None

    adapter.session = lambda *_: SimpleNamespace(open=open_page)
    await adapter.wait_for_otp("test", "instamart", timeout=2)
    assert await page.evaluate("window.clicks || 0") == 0


async def test_login_timeout_is_recoverable_and_does_not_reload(page):
    from types import SimpleNamespace

    from easydial.whatsapp.store_login import LoginStillPending, StoreLogin

    await page.set_content('<input name="auth-sidebar-mobile">')
    adapter = StoreLogin(SimpleNamespace(), None)

    async def open_page():
        return page, None

    adapter.session = lambda *_: SimpleNamespace(open=open_page)
    with pytest.raises(LoginStillPending):
        await adapter.wait_for_otp("test", "instamart", timeout=0)
    assert await page.locator('input[name="auth-sidebar-mobile"]').count() == 1


@pytest.mark.parametrize("wording,limited", [
    ("Didn't receive OTP? Try again in 30 seconds", False),
    ("Something went wrong. Try again after some time", False),
    ("Too many login attempts. Please try again later", True),
    ("Too many requests", True),
])
async def test_login_limit_requires_explicit_evidence(page, wording, limited):
    from types import SimpleNamespace

    from easydial.whatsapp.store_login import LoginRateLimited, LoginStillPending, StoreLogin

    await page.set_content("<main></main>")
    await page.locator("main").evaluate("(el, text) => el.textContent = text", wording)
    adapter = StoreLogin(SimpleNamespace(), None)

    async def open_page():
        return page, None

    adapter.session = lambda *_: SimpleNamespace(open=open_page)
    with pytest.raises(LoginStillPending) as caught:
        await adapter.wait_for_otp("test", "instamart", timeout=0)
    assert isinstance(caught.value, LoginRateLimited) is limited
    assert await page.locator("main").inner_text() == wording


async def test_observed_instamart_phone_error_is_not_loading_or_rate_limit(page):
    from types import SimpleNamespace

    from easydial.whatsapp.store_login import LoginSubmissionRejected, StoreLogin

    await page.set_content('''<input name="auth-sidebar-mobile" type="tel">
    <div>Something went wrong, please try again after sometime.</div><button>LOGIN</button>''')
    adapter = StoreLogin(SimpleNamespace(), None)

    async def open_page():
        return page, None

    adapter.session = lambda *_: SimpleNamespace(open=open_page)
    with pytest.raises(LoginSubmissionRejected):
        await adapter.wait_for_otp("test", "instamart", timeout=0)
