"""Payment choices observed in store UI. Never press Pay or submit payment credentials."""

import asyncio
import base64
import io
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

import zxingcpp
from PIL import Image

from ..billing import fingerprint
from ..controller import DomainError
from ..models import Provider
from .blinkit import cart_signature, normalized
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


async def open_options(worker, view, review, page):
    await worker.navigate(page, "milk")
    cart = await worker.read_cart(page)
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
    label = "Proceed To Pay" if worker.provider == Provider.BLINKIT else "Proceed to Pay"
    await page.get_by_text(label, exact=True).click()
    if worker.provider == Provider.BLINKIT:
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
        raise DomainError("Blinkit payment options did not load. Try again later.")
    await page.get_by_text("Payment Options", exact=True).wait_for()
    verify_instamart_payment_total(await page.locator("body").inner_text(), review.total_paise)
    return page


def verify_instamart_payment_total(text, approved_paise):
    amounts = re.findall(r"\bTotal:\s*₹\s*([\d,]+(?:\.\d{1,2})?)", text)
    if not amounts or any(paise(value) != approved_paise for value in amounts):
        raise DomainError("Instamart’s payment amount changed or could not be verified. No payment was started.")


async def options(worker, view, review):
    async with worker.lock, worker.browser_page() as (page, context):
        scope = await open_options(worker, view, review, page)
        body = await scope.locator("body").inner_text()
        found = []
        for key, title, label in LABELS[worker.provider]:
            if await scope.get_by_text(label, exact=True).first.is_visible():
                note = ""
                if key == "cash":
                    restriction = re.search(r"Cash on delivery is not available[^\n]*", body, re.I)
                    note = (
                        restriction.group(0)
                        if restriction
                        else "Requires placing an order; not submitted by EasyDial."
                    )
                found.append({"id": key, "title": title, "note": note})
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
        await scope.get_by_text("UPI", exact=True).click()
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
