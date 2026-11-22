"""Observed payment controls. Order submission requires a separate durable confirmation."""

import asyncio
import base64
import io
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

import zxingcpp
from PIL import Image
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..billing import fingerprint
from ..controller import DomainError
from ..models import Provider
from .blinkit import cart_signature, normalized
from .browser import click_and_settle
from .money import paise

STORE_CHECKOUT = {
    Provider.BLINKIT: "https://blinkit.com/checkout",
    Provider.INSTAMART: "https://instamart.in/payment",
}
LABELS = {
    Provider.BLINKIT: [
        ("upi", "UPI", "UPI"),
        ("cards", "Cards", "Add credit or debit cards"),
        ("wallets", "Wallets", "Wallets"),
        ("netbanking", "Netbanking", "Netbanking"),
        ("cash", "Cash on delivery", "Cash"),
        ("paylater", "Pay later", "Pay Later"),
    ],
    Provider.INSTAMART: [
        ("cards", "Cards", "Credit & Debit Cards"),
        ("wallets", "Wallets", "Wallets"),
        ("netbanking", "Netbanking", "Netbanking"),
        ("cash", "Pay on delivery", "Pay on Delivery"),
    ],
}


class CheckoutTemporarilyUnavailable(DomainError):
    """Store rejected payment entry before any final order/payment action."""


async def current_payment_scope(worker, page):
    if worker.provider == Provider.BLINKIT:
        if not await page.get_by_text("Select Payment Method", exact=True).is_visible():
            return None
        for frame in page.frames:
            url = urlsplit(frame.url)
            if url.hostname == "www.zomato.com" and url.path.startswith("/zpaykit/"):
                if await frame.get_by_text("UPI", exact=True).is_visible():
                    return frame
        return None
    if await page.get_by_text("Payment Options", exact=True).is_visible():
        return page
    return None


async def open_options(worker, view, review, page):
    checkpoint = getattr(worker, "_payment_checkpoint", None)
    if (
        checkpoint and checkpoint["page"] is page
        and checkpoint["fingerprint"] == fingerprint(view, review)
        and checkpoint["address"] == worker.verified_address
        and time.monotonic() - checkpoint["at"] < 300
    ):
        scope = await current_payment_scope(worker, page)
        if scope is not None:
            if worker.provider == Provider.INSTAMART:
                verify_instamart_payment_total(await page.locator("body").inner_text(), review.total_paise)
            return scope
    worker._payment_checkpoint = None
    for attempt in range(2):
        try:
            scope = await open_options_once(worker, view, review, page)
            worker._payment_checkpoint = {
                "page": page, "fingerprint": fingerprint(view, review),
                "address": worker.verified_address, "at": time.monotonic(),
            }
            return scope
        except (CheckoutTemporarilyUnavailable, PlaywrightTimeoutError) as error:
            if isinstance(error, PlaywrightTimeoutError):
                # Payment widgets can receive the proxy error inside an iframe.
                transient = False
                for frame in page.frames:
                    try:
                        body = (await frame.locator("body").inner_text(timeout=1000)).casefold()
                        transient |= any(marker in body for marker in (
                            "upstream connect error", "reset reason: overflow",
                            "disconnect/reset before headers",
                        ))
                    except PlaywrightTimeoutError:
                        continue
                if not transient:
                    raise
            if attempt:
                raise CheckoutTemporarilyUnavailable(
                    "The store is temporarily busy. Your basket is saved; please retry shortly."
                ) from None
            await asyncio.sleep(1)
    raise AssertionError("Unreachable")


async def open_options_once(worker, view, review, page):
    if worker.provider == Provider.BLINKIT:
        # Revalidation has usually just opened this cart on the same page.
        # Re-read and verify it below instead of discarding it with another load.
        drawer = page.locator(
            "div[class*='CartDrawer'], div[class*='CartSidebar'], div.cart-modal-rn, div[class*='CartWrapper__CartContainer']"
        ).first
        payment_frame = any(
            urlsplit(frame.url).hostname == "www.zomato.com"
            and urlsplit(frame.url).path.startswith("/zpaykit/") for frame in page.frames
        )
        if urlsplit(page.url).path not in {"", "/"} or payment_frame or not await drawer.is_visible():
            await worker.navigate_cart(page)
    else:
        await worker.navigate(page, "milk")
    cart = await worker.read_cart(page)
    verify_checkout_cart(worker, view, review, cart)
    label = "Proceed To Pay" if worker.provider == Provider.BLINKIT else "Proceed to Pay"
    await page.get_by_text(label, exact=True).click()
    if worker.provider == Provider.BLINKIT:
        await continue_blinkit_address(worker, view, review, page)
        await page.get_by_text("Select Payment Method", exact=True).wait_for()
        for _ in range(40):
            frames = [
                f
                for f in page.frames
                if urlsplit(f.url).hostname == "www.zomato.com"
                and urlsplit(f.url).path.startswith("/zpaykit/")
            ]
            if frames:
                await frames[0].get_by_text("UPI", exact=True).wait_for()
                return frames[0]
            await asyncio.sleep(0.25)
        raise CheckoutTemporarilyUnavailable("Blinkit payment options did not load. Retrying checkout.")
    await page.get_by_text("Payment Options", exact=True).wait_for()
    verify_instamart_payment_total(await page.locator("body").inner_text(), review.total_paise)
    return page


def verify_checkout_cart(worker, view, review, cart):
    worker.guard.check(view.id, cart_signature(cart))
    desired = tuple(
        sorted((normalized(x.offer.name), normalized(x.offer.pack_size), x.quantity) for x in view.lines)
    )
    if cart_signature(cart) != desired or normalized(
        worker.verified_address or view.address_label
    ) not in normalized(cart.get("address", "")):
        raise DomainError("Your cart or address changed. Review it again.")
    live = worker.verified_review(view, cart)
    if fingerprint(view, live) != fingerprint(view, review):
        raise DomainError("The bill changed. Review it again before choosing payment.")


async def continue_blinkit_address(worker, view, review, page):
    """Follow bounded address/cart/payment transitions without submitting an order."""
    heading = page.get_by_text("Select Payment Method", exact=True)
    rows = page.locator("div[class*='AddressList__AddressItemWrapper-']")
    selected = False
    checkout_clicks = 0
    last_checkout_click = 0.0
    for _ in range(80):
        if await heading.is_visible():
            return
        body = (await page.locator("body").inner_text()).casefold()
        if any(marker in body for marker in ("upstream connect error", "reset reason: overflow", "disconnect/reset before headers")):
            raise CheckoutTemporarilyUnavailable("The store is busy. Retrying checkout.")
        if await page.get_by_text("Oops! something went wrong", exact=True).is_visible():
            raise CheckoutTemporarilyUnavailable("Blinkit couldn't open payment right now. Your basket is saved; please try again shortly.")
        if await rows.first.is_visible():
            if selected:
                raise DomainError("Blinkit couldn't confirm the delivery address. Please try again shortly.")
            await worker.select_checkout_address(page)
            # Address selection returns to the cart, and may recalculate the bill.
            cart = await worker.read_cart(page)
            verify_checkout_cart(worker, view, review, cart)
            selected = True
            await page.get_by_text("Proceed To Pay", exact=True).click()
        elif (
            checkout_clicks < 3 and time.monotonic() - last_checkout_click >= 2.5
            and urlsplit(page.url).path.rstrip("/") == "/checkout"
        ):
            # The first cart CTA can navigate to a full-page checkout cart, not
            # directly to the payment widget. Its CTA can precede hydration, so
            # re-observe the heading and recheck the bill before each bounded retry.
            proceed = page.get_by_text("Proceed To Pay", exact=True)
            if await proceed.count() == 1 and await proceed.is_visible():
                cart = await worker.read_cart(page)
                verify_checkout_cart(worker, view, review, cart)
                checkout_clicks += 1
                await click_and_settle(page, proceed)
                last_checkout_click = time.monotonic()
        await asyncio.sleep(0.25)
    raise CheckoutTemporarilyUnavailable("Blinkit payment options did not load. Retrying checkout.")


def verify_instamart_payment_total(text, approved_paise):
    amounts = re.findall(r"\bTotal:\s*₹\s*([\d,]+(?:\.\d{1,2})?)", text)
    if not amounts or any(paise(value) != approved_paise for value in amounts):
        raise DomainError(
            "Instamart’s payment amount changed or could not be verified. No payment was started."
        )


async def select_blinkit_method(scope, label):
    # Payment methods are accordions; do not toggle an already-open target closed.
    cards = scope.get_by_role("button", name="Add credit or debit cards", exact=True)
    if await cards.count() and await cards.get_attribute("aria-expanded") == "true":
        await cards.click()
    target = scope.get_by_role("button", name=label, exact=True)
    if not await target.is_enabled() or await target.get_attribute("aria-disabled") == "true":
        raise DomainError(f"Blinkit hasn’t enabled {label} for this basket.")
    if await target.get_attribute("aria-expanded") != "true":
        await target.click()


async def options(worker, view, review):
    async with worker.lock, worker.browser_page() as (page, context):
        scope = await open_options(worker, view, review, page)
        body = await scope.locator("body").inner_text()
        found = []
        for key, title, label in LABELS[worker.provider]:
            if key not in ({"cash", "upi"} if worker.provider == Provider.BLINKIT else {"cash"}):
                continue
            control = scope.get_by_text(label, exact=True).first
            if await control.is_visible() and await control.is_enabled():
                note = ""
                if key == "cash":
                    restriction = re.search(r"Cash on delivery is not available[^\n]*", body, re.I)
                    note = restriction.group(0) if restriction else "Pay when your order arrives."
                if key == "cash" and restriction:
                    continue
                found.append(
                    {"id": key, "title": "Pay on delivery" if key == "cash" else "UPI QR", "note": note}
                )
        if not found:
            raise DomainError("Payment options could not be read. Please try again.")
        return found


def decode_qr(source, expected_paise):
    if not source.startswith("data:image/png;base64,") or len(source) > 2_000_000:
        raise DomainError("The store QR could not be verified.")
    png = base64.b64decode(source.split(",", 1)[1], validate=True)
    with Image.open(io.BytesIO(png)) as picture:
        if picture.width > 2048 or picture.height > 2048:
            raise DomainError("The store QR could not be verified.")
        results = zxingcpp.read_barcodes(picture)
    if len(results) != 1:
        raise DomainError("The store QR could not be read.")
    uri = results[0].text
    parsed = urlsplit(uri)
    fields = parse_qs(parsed.query)
    try:
        amount = Decimal(fields["am"][0]) * 100
        valid = (
            parsed.scheme == "upi"
            and parsed.netloc == "pay"
            and amount == expected_paise
            and fields.get("cu") == ["INR"]
            and len(fields["am"]) == 1
            and len(fields.get("pa", [])) == 1
            and len(fields.get("tr", [])) == 1
        )
    except (KeyError, InvalidOperation):
        valid = False
    if not valid:
        raise DomainError("The QR amount or currency does not match your bill. No payment was approved.")
    return {"upi_uri": uri, "qr_data": source, "total_paise": expected_paise}


async def generate_upi(worker, view, review):
    if worker.provider != Provider.BLINKIT:
        raise DomainError("UPI QR is not available on this store’s current payment screen.")
    async with worker.lock, worker.browser_page() as (page, context):
        scope = await open_options(worker, view, review, page)
        await select_blinkit_method(scope, "UPI")
        # This is the only payment-request creation control. Never click Pay Now.
        generate = scope.get_by_text("Generate QR", exact=True)
        await generate.wait_for()
        await generate.click()
        qr = scope.locator('img[class*="QrImage"]')
        await qr.wait_for(state="visible")
        # The visible QR element appears first with a loading/empty source.
        await scope.wait_for_function(
            """() => {
            const image = document.querySelector('img[class*="QrImage"]');
            return image && image.complete && image.naturalWidth > 0 &&
                image.src.startsWith('data:image/png;base64,');
        }""",
            timeout=15000,
        )
        source = await qr.get_attribute("src")
        artifact = decode_qr(source or "", review.total_paise)
        text = await scope.locator("body").inner_text()
        ttl = re.search(r"(\d+)\s*m\s*:\s*(\d+)\s*s", text)
        if not ttl:
            raise DomainError("The QR expiry could not be verified. Check Blinkit before trying again.")
        seconds = min(300, int(ttl[1]) * 60 + int(ttl[2]))
        if seconds <= 15:
            raise DomainError("This QR is expiring. Check Blinkit before trying again.")
        return artifact | {"ttl_seconds": seconds}


async def cash_control(worker, view, review, page):
    scope = await open_options(worker, view, review, page)
    if worker.provider == Provider.INSTAMART:
        await scope.get_by_text("Pay on Delivery", exact=True).click()
        cash = scope.get_by_role("button", name="Cash Pay cash at the time of delivery.", exact=True)
        if not await cash.is_enabled() or await cash.get_attribute("aria-disabled") == "true":
            raise DomainError("Pay on delivery isn’t available for this order.")
        await cash.click()  # Selects cash only; the separate final button submits.
        button = scope.get_by_test_id("pm_cod_pay_btn")
        await button.wait_for(state="visible")
        verify_instamart_payment_total(await scope.locator("body").inner_text(), review.total_paise)
        expected = f"Pay ₹{review.total_paise / 100:g} with Cash"
        if (await button.inner_text()).strip() != expected or not await button.is_enabled():
            raise DomainError("The cash payment amount changed. Please check the latest bill.")
        return button
    await select_blinkit_method(scope, "Cash")
    await scope.get_by_text(
        "Please keep exact change handy to help us serve you better", exact=True
    ).wait_for(state="visible")
    # Blinkit's final Pay Now control is in the parent page, outside zpaykit.
    # Only return it after selecting the verified Cash accordion.
    button = page.get_by_text("Pay Now", exact=True)
    await button.wait_for(state="visible")
    if not await button.is_enabled():
        raise DomainError("Blinkit cash checkout is unavailable right now.")
    return button


async def prepare_cash(worker, view, review):
    async with worker.lock, worker.browser_page() as (page, context):
        await cash_control(worker, view, review, page)
        return {"total_paise": review.total_paise}


async def submit_cash(worker, view, review, before_submit):
    async with worker.lock, worker.browser_page() as (page, context):
        button = await cash_control(worker, view, review, page)
        worker._payment_checkpoint = None
        before_submit()  # Commits the once-only reservation before the irreversible click.
        await button.click()
        # A click only dispatches submission. Keep the checkout page and lock
        # intact while the merchant processes it; never navigate it to history.
        await wait_for_submission_response(page)
        # History reconciliation on a separate tab establishes order identity.
        return {"status": "pending"}


async def wait_for_submission_response(page):
    """Wait for visible acknowledgement; timeout is uncertainty, not failure."""
    try:
        await page.wait_for_function(
            r"""() => {
                const text = document.body?.innerText || "";
                return /order summary/i.test(text) && /order details/i.test(text)
                    || /order (?:has been |is )?(?:placed|confirmed)/i.test(text)
                    || /order (?:could not be placed|failed)/i.test(text)
                    || /payment failed/i.test(text);
            }""",
            timeout=30000,
        )
    except PlaywrightTimeoutError:
        # Leave the page alive: an asynchronous submission may still finish.
        pass
