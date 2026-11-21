"""Merchant UI login. Only invoked after recorded customer consent.

No private API replay, challenge bypass, profile sharing, or automatic order action.
"""

import asyncio
import hashlib
import json
import logging
import re
import time

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..controller import DomainError
from ..providers.blinkit import normalized
from ..providers.browser import BrowserSession, goto_with_retry


class LoginStillPending(DomainError):
    """The phone request was clicked but the OTP screen is not ready yet."""


class LoginRateLimited(LoginStillPending):
    """Explicit merchant request limit; retain the page without replaying login."""


log = logging.getLogger(__name__)


class StoreLogin:
    def __init__(self, settings, accounts):
        self.settings, self.accounts = settings, accounts
        self.sessions = {}
        self.deadlines = {}

    async def cancel(self, sender, provider=None):
        for key in list(self.sessions):
            if key[0] == sender and (provider is None or key[1] == provider):
                self.deadlines.pop(key, None)
                await self.sessions.pop(key).close()

    async def expire(self):
        for key, deadline in list(self.deadlines.items()):
            if time.monotonic() > deadline:
                await self.cancel(*key)

    async def close(self):
        for sender, provider in list(self.sessions):
            await self.cancel(sender, provider)

    def session(self, sender, provider):
        if not self.accounts.consented(sender, provider):
            raise DomainError("Permission is required before signing in.")
        session = self.sessions.get((sender, provider))
        if not session:
            raise DomainError("Your login session expired. Please connect again.")
        return session

    async def request_otp(self, sender, provider, phone):
        if not self.accounts.consented(sender, provider):
            raise DomainError("Permission is required before signing in.")
        await self.expire()
        await self.cancel(sender, provider)
        if len(self.sessions) >= 4:
            raise DomainError("Sign-in is busy right now. Please try again in a few minutes.")
        # Always start a fresh context for a new consent/login. Never import a previous account.
        browser = BrowserSession()
        browser.channel = self.settings.browser_channel
        browser.headless = (
            self.settings.blinkit_browser_headless
            if provider == "blinkit" and self.settings.blinkit_browser_headless is not None
            else self.settings.browser_headless
        )
        self.sessions[sender, provider] = browser
        self.deadlines[sender, provider] = time.monotonic() + 600
        page, _ = await browser.open()
        await goto_with_retry(page,
            "https://blinkit.com/" if provider == "blinkit" else "https://instamart.in/",
            wait_until="domcontentloaded",
        )
        try:
            await browser.check_page(page)
        except DomainError:
            raise DomainError(
                "The store could not open a secure login right now. Please try later."
            ) from None
        if provider == "blinkit":
            await self.open_blinkit_login(page)
            await page.get_by_placeholder("Enter mobile number", exact=True).fill(phone)
            await page.get_by_text("Continue", exact=True).click()
        else:
            await self.open_instamart_login(page)
            await page.locator('input[name="auth-sidebar-mobile"]').fill(phone)
            await page.get_by_role("button", name="LOGIN", exact=True).click()
        # Do not claim an OTP was sent just because the phone submit button was clicked.
        await self.wait_for_otp(sender, provider)

    async def wait_for_otp(self, sender, provider, timeout=30):
        session = self.session(sender, provider)
        page, _ = await session.open()
        deadline = time.monotonic() + timeout
        while True:
            if await self.otp_inputs(page).count():
                return
            body = (await page.locator("body").inner_text()).casefold()
            if re.search(r"\btoo many (?:(?:login|otp|sign[ -]?in) )?(?:requests|attempts)\b", body):
                log.warning("Merchant login observation: provider=%s signal=explicit_request_limit", provider)
                raise LoginRateLimited("The store is limiting login requests.")
            # A resend countdown or generic 'try again after' is not evidence
            # of rate limiting. Keep observing the same submitted request.
            if re.search(r"(?:invalid|incorrect) (?:mobile|phone) number", body):
                raise DomainError("The store rejected that mobile number. Please check it and try again.")
            if any(marker in body for marker in ("upstream connect error", "reset reason: overflow")):
                # Do not reload/replay a submitted phone request: the store may
                # still send its OTP. Keep the session for an explicit recheck.
                raise LoginStillPending("The store is slow to respond.")
            if time.monotonic() >= deadline:
                raise LoginStillPending("The store has not opened its OTP screen yet.")
            await asyncio.sleep(0.5)

    @staticmethod
    async def open_instamart_login(page):
        phone = page.locator('input[name="auth-sidebar-mobile"]')
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            await BrowserSession.check_page(page)
            if await phone.is_visible():
                return
            # The fresh-session location dialog hydrates after DOMContentLoaded.
            # Re-observe it after each bounded click instead of committing to a
            # hidden/covered header button for the whole timeout.
            login = page.get_by_text("Login", exact=True).filter(visible=True)
            header = page.get_by_role("button", name="Sign in", exact=True)
            target = login if await login.count() == 1 else header
            if await target.count() == 1 and await target.is_visible():
                try:
                    await target.click(timeout=750)
                except PlaywrightTimeoutError:
                    pass
            try:
                await phone.wait_for(state="visible", timeout=750)
                return
            except PlaywrightTimeoutError:
                pass
        raise DomainError("Instamart’s login screen isn’t loading right now. Please try again shortly.")

    @staticmethod
    async def open_blinkit_login(page):
        # DOMContentLoaded precedes Blinkit's client-rendered location gate. An
        # immediate is_visible() skips it on fresh customer contexts.
        location = page.get_by_placeholder("search delivery location")
        login = page.get_by_text("Login", exact=True)
        phone = page.get_by_placeholder("Enter mobile number", exact=True)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            await BrowserSession.check_page(page)
            if await phone.is_visible():
                return
            if await location.is_visible():
                # Entry locality only. Never persist this as the customer's address.
                await location.fill("Koramangala Bengaluru")
                await page.get_by_text("Bengaluru, Karnataka, India", exact=True).first.click(
                    timeout=15000
                )
                await location.wait_for(state="hidden", timeout=15000)
            elif await login.is_visible():
                # Login may render before the location gate, or before its handler
                # hydrates. Re-observe the gate instead of waiting only for phone UI.
                try:
                    await login.click(timeout=1000)
                except PlaywrightTimeoutError:
                    pass
            try:
                await phone.wait_for(state="visible", timeout=1000)
                return
            except PlaywrightTimeoutError:
                pass
        raise DomainError("Blinkit sign-in is taking too long. Please try again.")

    def otp_inputs(self, page):
        # Exclude the ten-digit phone input; support a single OTP box or digit boxes.
        return (
            page.locator("input:visible")
            .filter(visible=True)
            .locator(
                'xpath=self::input[(@autocomplete="one-time-code" or contains(translate(@name,"OTP","otp"),"otp") or contains(translate(@id,"OTP","otp"),"otp") or @inputmode="numeric" or @type="tel") and not(@maxlength="10") and not(@name="auth-sidebar-mobile")]'
            )
        )

    async def verify_otp(self, sender, provider, otp):
        session = self.session(sender, provider)
        page, _ = await session.open()
        fields = self.otp_inputs(page)
        count = await fields.count()
        if count == 1:
            await fields.fill(otp)
        elif count == len(otp):
            for i, digit in enumerate(otp):
                await fields.nth(i).fill(digit)
        else:
            raise DomainError("The store login screen changed. Please connect again.")
        # Some stores auto-submit digit boxes; only click a visible, unambiguous OTP action.
        submit = page.get_by_role("button", name=re.compile(r"^(verify|verify otp|continue)$", re.I))
        if await submit.count() == 1 and await submit.is_visible():
            await submit.click()
        for _ in range(20):
            await session.check_page(page)
            if provider == "instamart":
                authenticated = await page.get_by_test_id("user-account-button").is_visible()
            else:
                authenticated = await page.get_by_text("Account", exact=True).is_visible()
            if authenticated:
                return await self.addresses(sender, provider)
            if re.search(
                r"(incorrect|invalid|expired)\s+(otp|code)", await page.locator("body").inner_text(), re.I
            ):
                return None
            await asyncio.sleep(0.5)
        return None

    @staticmethod
    def instamart_address_cards(page):
        # Authentication and location dialogs can coexist, including hidden copies.
        # Only the visible saved-address rows define the customer's choices.
        return page.locator(".FObAQ > ._1gcSd").filter(visible=True)

    async def addresses(self, sender, provider):
        page, _ = await self.session(sender, provider).open()
        if provider == "blinkit":
            cards = page.locator('[class*="AddressListItem__AddressItemWrapperItem"]')
            if not await cards.count():
                await page.locator('div[class*="LocationBar__Subtitle"]').first.click()
            await cards.first.wait_for(state="visible", timeout=8000)
            entries = []
            for index, card in enumerate(await cards.all()):
                details = card.locator('[class*="AddressListItem__AddressDetails"]')
                texts = await details.all_inner_texts()
                # AddressDetails also matches its outer wrapper, which includes
                # the label. The innermost details node is the street address.
                address = texts[-1].strip() if texts else ""
                label = await card.locator('[class*="AddressListItem__AddressLabel"]').inner_text()
                if address:
                    entries.append({"label": label.strip() + ": " + address,
                                    "address": address, "card_index": index})
        else:
            cards = self.instamart_address_cards(page)
            deadline = time.monotonic() + 20
            while not await cards.count():
                await BrowserSession.check_page(page)
                if time.monotonic() >= deadline:
                    raise DomainError("I couldn’t load your saved addresses from Instamart. Please try again shortly.")
                bar = page.get_by_test_id("address-bar").filter(visible=True)
                if await bar.count() == 1:
                    try:
                        await bar.click(timeout=750)
                    except PlaywrightTimeoutError:
                        pass
                try:
                    await cards.first.wait_for(state="visible", timeout=750)
                except PlaywrightTimeoutError:
                    pass
            # Read address rows, not generic modal overlays shared by auth/location UI.
            entries = []
            for index, card in enumerate(await cards.all()):
                details = card.locator("._2wcdd > div")
                if await details.count() != 2:
                    raise DomainError("The saved-address layout changed. Please try again later.")
                label = (await details.nth(0).inner_text()).strip()
                address = (await details.nth(1).inner_text()).strip()
                if address:
                    entries.append({"label": label + ": " + address, "address": address,
                                    "card_index": index})
        # A saved-address list can legitimately contain duplicate street text,
        # including duplicate labels. Bind choices to the full visible snapshot
        # plus row position, never reject the customer's whole address book.
        snapshot = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        for entry in entries:
            entry["id"] = hashlib.sha256(f"{snapshot}:{entry['card_index']}".encode()).hexdigest()
        return entries

    async def select_address(self, sender, provider, address):
        session = self.session(sender, provider)
        page, _ = await session.open()
        current = await self.addresses(sender, provider)
        matches = [x for x in current if x["id"] == address["id"] and x["address"] == address["address"]]
        if len(matches) != 1:
            raise DomainError("That address changed. Please choose it again.")
        if provider == "blinkit":
            # Use the index from the freshly validated list, not substring text
            # matching: nested DOM text and inner_text differ at line breaks.
            card = page.locator('[class*="AddressListItem__AddressItemWrapperItem"]').nth(
                matches[0]["card_index"]
            )
            actual = (await card.locator('[class*="AddressListItem__AddressDetails"]').last.inner_text()).strip()
            if actual != address["address"]:
                raise DomainError("That address changed. Please choose it again.")
            await card.locator('[class*="AddressListItem__AddressDetails"]').last.click()
            await card.wait_for(state="hidden")
            location = (await page.locator('div[class*="LocationBar__Subtitle"]').first.inner_text()).strip()
        else:
            card = self.instamart_address_cards(page).nth(matches[0]["card_index"])
            actual = (await card.locator("._2wcdd > div").nth(1).inner_text()).strip()
            if actual != address["address"]:
                raise DomainError("That address changed. Please choose it again.")
            await card.click()
            await self.instamart_address_cards(page).first.wait_for(state="hidden")
            location = (
                await page.get_by_test_id("address-bar").get_by_test_id("address-line").inner_text()
            ).strip()
        if not location or normalized(location) in {"selectlocation", "addyourlocation"}:
            raise DomainError("The store did not confirm your selected delivery location.")
        session.storage_state = self.accounts.session_path(sender, provider)
        session.secret_box = self.accounts.session_box(sender, provider)
        await session.save()
        return {"address": address["address"], "location": location, "address_id": address["id"]}
