"""Verify address selection independently of the cart, including empty baskets."""

import asyncio
import time

from playwright.async_api import Error as PlaywrightError

from ..controller import DomainError
from .browser import BrowserSession, click_and_settle

SUBTITLE = "div[class*='LocationBar__Subtitle']"
CARDS = "[class*='AddressListItem__AddressItemWrapperItem']"
DETAILS = "[class*='AddressListItem__AddressDetails']"
LABEL = "[class*='AddressListItem__AddressLabel']"


class BlinkitAddressError(DomainError):
    def __init__(self):
        super().__init__(
            "I couldn't confirm your saved Blinkit delivery address. "
            "Send Connect and select your address again; retrying products won't fix this.", 409
        )


class BlinkitAddressLoadingError(DomainError):
    def __init__(self):
        super().__init__(
            "Blinkit's saved delivery address did not finish loading. Your connection is saved; please retry shortly.",
            503,
        )


def address_key(value):
    return " ".join(value.casefold().split())


async def settled_location(page, label, *, expected=None, timeout=12):
    """The selected row's label must replace the temporary entry locality."""
    deadline = time.monotonic() + timeout
    previous, stable_since = None, None
    while time.monotonic() < deadline:
        text = (await page.locator(SUBTITLE).first.inner_text(timeout=2000)).strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        valid = len(lines) >= 2 and address_key(lines[0]) == address_key(label)
        valid = valid and (expected is None or address_key(text) == address_key(expected))
        if valid:
            if text != previous:
                previous, stable_since = text, time.monotonic()
            elif time.monotonic() - stable_since >= 1:
                return text
        else:
            previous, stable_since = None, None
        await asyncio.sleep(0.2)
    raise BlinkitAddressLoadingError()


async def select_and_verify_address(page, card, address):
    """Click a validated full-address row, then verify its persisted header."""
    try:
        details = card.locator(DETAILS).last
        if address_key(await details.inner_text()) != address_key(address):
            raise BlinkitAddressError()
        label = (await card.locator(LABEL).inner_text()).strip()
        if not label:
            raise BlinkitAddressError()
        await click_and_settle(page, details)
        await card.wait_for(state="hidden")
        location = await settled_location(page, label)
        # Storage must not capture a transient pre-selection locality/cookie state.
        await page.reload(wait_until="domcontentloaded", timeout=20000)
        await BrowserSession.check_page(page)
        return await settled_location(page, label, expected=location)
    except DomainError:
        raise
    except PlaywrightError:
        raise BlinkitAddressLoadingError() from None


async def restore_saved_address(page, address):
    """Only the unique exact address previously approved by this customer is eligible."""
    try:
        await page.locator(SUBTITLE).first.click()
        cards = page.locator(CARDS)
        await cards.first.wait_for(state="visible")
        matches = []
        for card in await cards.all():
            if address_key(await card.locator(DETAILS).last.inner_text()) == address_key(address):
                matches.append(card)
        if len(matches) != 1:
            raise BlinkitAddressError()
        return await select_and_verify_address(page, matches[0], address)
    except DomainError:
        raise
    except PlaywrightError:
        raise BlinkitAddressLoadingError() from None
