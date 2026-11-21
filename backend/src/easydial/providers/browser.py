"""Dedicated, configurable browser sessions. No stealth, fingerprint spoofing or challenge bypass."""

from pathlib import Path

from playwright.async_api import async_playwright

from ..controller import DomainError


class BrowserSession:
    def __init__(self, profile: str = "", storage_state: str = ""):
        self.profile, self.storage_state = profile, storage_state
        self.headless, self.channel = False, "chrome"
        self.runtime = self.context = self.page = self.browser = None
        self.secret_box = None
        self.allow_empty_state = False

    @property
    def configured(self):
        return bool(
            (self.profile and Path(self.profile).is_dir())
            or (self.storage_state and Path(self.storage_state).is_file())
        )

    async def open(self):
        if self.context and self.page and not self.page.is_closed():
            return self.page, self.context
        await self.close()
        self.runtime = await async_playwright().start()
        try:
            if self.profile:
                self.context = await self.runtime.chromium.launch_persistent_context(
                    self.profile, channel=self.channel, headless=self.headless
                )
            else:
                self.browser = await self.runtime.chromium.launch(
                    channel=self.channel, headless=self.headless
                )
                state = self.storage_state
                if self.secret_box:
                    state = (
                        self.secret_box.open(Path(self.storage_state).read_text())
                        if Path(self.storage_state).is_file()
                        else None
                    )
                    if state is None and not self.allow_empty_state:
                        raise DomainError("Connect this store first.")
                self.context = await self.browser.new_context(storage_state=state or None)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.set_default_timeout(8000)
            return self.page, self.context
        except Exception:
            await self.close()
            raise DomainError(
                "Close the store's setup browser, then retry. Chrome must be installed.", 503
            ) from None

    @staticmethod
    async def check_page(page):
        text = (await page.locator("body").inner_text()).casefold()
        if any(x in text for x in ["we'll be back shortly", "temporary glitch", "something’s not right"]):
            raise DomainError("The store website is temporarily unavailable. Try again later.", 503)
        if any(x in text for x in ["verify you are human", "access denied", "unusual traffic"]):
            raise DomainError(
                "The store needs attention in its browser. Complete its check manually before retrying.", 503
            )

    async def save(self):
        if self.storage_state and self.context:
            if self.secret_box:
                self.secret_box.write(self.storage_state, await self.context.storage_state(indexed_db=True))
            else:
                await self.context.storage_state(path=self.storage_state)
                Path(self.storage_state).chmod(0o600)

    async def close(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        finally:
            if self.runtime:
                await self.runtime.stop()
            self.runtime = self.context = self.page = self.browser = None


async def click_and_settle(page, button):
    """Wait for requests started by this UI action, without waiting on unrelated page traffic.

    Only completion events are observed. No request bodies, response payloads, cookies or
    private endpoints are read or replayed. Provider DOM readback remains mandatory.
    """
    import asyncio
    import time
    from urllib.parse import urlsplit

    pending = set()
    last_event = time.monotonic()

    def started(request):
        nonlocal last_event
        host = urlsplit(request.url).hostname or ""
        site = urlsplit(page.url).hostname or ""
        roots = (
            ("instamart.in", "swiggy.com") if site.endswith("instamart.in") else (site.removeprefix("www."),)
        )
        first_party = any(host == root or host.endswith("." + root) for root in roots)
        if first_party and request.resource_type in {"xhr", "fetch"}:
            pending.add(request)
            last_event = time.monotonic()

    def finished(request):
        nonlocal last_event
        if request in pending:
            pending.discard(request)
            last_event = time.monotonic()

    page.on("request", started)
    page.on("requestfinished", finished)
    page.on("requestfailed", finished)
    try:
        await button.click()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if not pending and time.monotonic() - last_event >= 0.8:
                return
            await asyncio.sleep(0.1)
        raise DomainError("The store has not finished saving its cart. Inspect it before retrying.", 502)
    finally:
        page.remove_listener("request", started)
        page.remove_listener("requestfinished", finished)
        page.remove_listener("requestfailed", finished)


async def goto_with_retry(page, url, **kwargs):
    """Retry transient read-only navigation, never a payment/order click."""
    import asyncio

    from playwright.async_api import Error as PlaywrightError

    markers = ("upstream connect error", "reset reason: overflow", "disconnect/reset before headers")
    for attempt in range(3):
        try:
            response = await page.goto(url, **kwargs)
            text = (await page.locator("body").inner_text()).casefold()
            transient = any(marker in text for marker in markers) or (
                response is not None and response.status in {502, 503, 504}
            )
            if not transient:
                return response
        except PlaywrightError as error:
            if not any(marker in str(error).casefold() for marker in (
                *markers, "err_connection_reset", "err_connection_closed", "err_empty_response",
            )):
                raise
        if attempt < 2:
            await asyncio.sleep(attempt + 1)
    raise DomainError("The store is temporarily busy. Your basket is saved; please retry shortly.", 503)
