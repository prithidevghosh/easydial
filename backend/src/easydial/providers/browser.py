"""Dedicated, configurable browser sessions. No stealth, fingerprint spoofing or challenge bypass."""

import asyncio
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright

from ..controller import DomainError
from ..observability import emit, error_fields, traced
from .proxy import BlinkitProxy, ProxyIdentityChanged

log = logging.getLogger(__name__)


class BrowserSession:
    def __init__(self, profile: str = "", storage_state: str = ""):
        self.profile, self.storage_state = profile, storage_state
        self.headless, self.channel = False, "chrome"
        self.runtime = self.context = self.page = self.browser = None
        self.secret_box = None
        self.proxy = None
        self.proxy_recovery_allowed = None
        self._initial_state = None
        self._open_lock = asyncio.Lock()
        self.allow_empty_state = False
        self.blocked_resource_urls = ()
        self._network_session = None

    @property
    def configured(self):
        return bool(
            (self.profile and Path(self.profile).is_dir())
            or (self.storage_state and Path(self.storage_state).is_file())
        )

    @traced('browser.open')
    async def open(self):
        async with self._open_lock:
            await self._open_context()
            await self._verify_proxy()
            return self.page, self.context

    @traced('browser.context_open')
    async def _open_context(self):
        if self.context and self.page and not self.page.is_closed():
            return
        await self.close()
        self.runtime = await async_playwright().start()
        try:
            options = {}
            if self.proxy:
                self.proxy.load(self)
                options["proxy"] = self.proxy.options
            if self.profile:
                self.context = await self.runtime.chromium.launch_persistent_context(
                    self.profile, channel=self.channel, headless=self.headless, **options
                )
            else:
                self.browser = await self.runtime.chromium.launch(
                    channel=self.channel, headless=self.headless, **options
                )
                state = self._initial_state or self.storage_state
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
            if self.blocked_resource_urls:
                # Chrome's network filter leaves the HTTP cache enabled, unlike
                # Playwright request routing. Only provider-configured telemetry
                # hosts are excluded; store and payment traffic remain untouched.
                self._network_session = await self.context.new_cdp_session(self.page)
                await self._network_session.send("Network.enable")
                await self._network_session.send("Network.setBlockedURLs", {"urls": list(self.blocked_resource_urls)})
        except DomainError:
            await self.close()
            raise
        except Exception as error:
            emit('browser.context_failed', error_code='browser_context_failed', **error_fields(error))
            await self.close()
            raise DomainError(
                "Close the store's setup browser, then retry. Chrome must be installed.", 503
            ) from None

    @traced('browser.proxy_verify')
    async def _verify_proxy(self):
        if not self.proxy:
            return
        identity_changed = False
        for attempt in range(1, 4):
            emit('proxy.attempt', provider='blinkit', attempt=attempt, max_attempts=3, pinned=bool(self.proxy.exit_ip))
            try:
                await self.proxy.verify(self)
                return
            except Exception as error:
                identity_changed = identity_changed or isinstance(error, ProxyIdentityChanged)
                if isinstance(error, ProxyIdentityChanged) and self._can_recover_proxy():
                    return await self._recover_proxy(attempt)
                emit('proxy.attempt_failed', provider='blinkit', attempt=attempt, max_attempts=3,
                     pinned=bool(self.proxy.exit_ip), error_code='proxy_verification_failed', **error_fields(error))
                log.warning("Blinkit proxy verification failed: attempt=%s max_attempts=3 pinned=%s category=%s",
                            attempt, bool(self.proxy.exit_ip), type(error).__name__)
                if attempt == 3:
                    if not identity_changed:
                        await self.close()
                    if isinstance(error, DomainError):
                        raise DomainError(error.message, error.status) from None
                    raise DomainError(
                        "Blinkit's India proxy could not be verified after 3 attempts. Please retry later.", 503
                    ) from None
                if not self.proxy.exit_ip:
                    # Chromium proxy credentials belong to the context. A fresh
                    # allocation therefore requires a fresh context, before any
                    # merchant action has been allowed through open().
                    await self.close()
                    self.proxy.renew_unverified()
                    await self._open_context()
                # A pinned journey retries in the SAME context and on the SAME
                # sticky session, preserving the live page, cart and OTP form.

    def _can_recover_proxy(self):
        # Persistent profiles cannot safely be opened twice. Login sessions and
        # checkout contexts have no recovery permission unless their owner grants it.
        if self.profile or self.proxy_recovery_allowed is None:
            return False
        try:
            return self.proxy_recovery_allowed() is True
        except Exception:
            return False

    def _publish_proxy_recovery(self, proxy, state):
        paths = [path for path in (Path(self.storage_state) if self.storage_state else None,
                                  proxy.path(self)) if path is not None]
        originals = {path: path.read_bytes() if path.exists() else None for path in paths}
        try:
            if self.storage_state:
                if self.secret_box:
                    self.secret_box.write(self.storage_state, state)
                else:
                    path = Path(self.storage_state)
                    path.write_text(json.dumps(state))
                    path.chmod(0o600)
            proxy.save(self)
        except BaseException:
            for path, original in originals.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            raise

    @traced('browser.proxy_recover')
    async def _recover_proxy(self, used_attempts):
        state = await self.context.storage_state(indexed_db=True)
        for attempt in range(used_attempts + 1, 4):
            if not self._can_recover_proxy():
                break
            candidate = BrowserSession()
            candidate.headless, candidate.channel = self.headless, self.channel
            candidate.blocked_resource_urls = self.blocked_resource_urls
            candidate._initial_state = state
            candidate.proxy = BlinkitProxy(self.proxy.username, self.proxy.password)
            candidate.proxy.loaded = True
            adopted = False
            try:
                await candidate._open_context()
                emit('proxy.recovery_attempt', provider='blinkit', attempt=attempt, max_attempts=3)
                await candidate.proxy.verify(candidate)
                if not self._can_recover_proxy():
                    break
                # No await between durable pin publication and ownership transfer.
                # Candidate verification never writes the original session files.
                self._publish_proxy_recovery(candidate.proxy, state)
                old = BrowserSession()
                resources = ('runtime', 'context', 'page', 'browser', '_network_session')
                for name in resources:
                    setattr(old, name, getattr(self, name))
                    setattr(self, name, getattr(candidate, name))
                    setattr(candidate, name, None)
                self.proxy = candidate.proxy
                adopted = True
                emit('proxy.recovered', provider='blinkit', attempt=attempt, status='ok', pinned=True)
                await old.close()
                return
            except Exception as error:
                if adopted:
                    raise
                emit('proxy.recovery_failed', provider='blinkit', attempt=attempt,
                     error_code='proxy_recovery_failed', **error_fields(error))
            finally:
                if not adopted:
                    await candidate.close()
        raise DomainError("Blinkit's network session could not be renewed. Please retry shortly.", 503)

    @staticmethod
    @traced('browser.check_page')
    async def check_page(page):
        text = (await page.locator("body").inner_text()).casefold()
        if any(x in text for x in ["we'll be back shortly", "temporary glitch", "something’s not right"]):
            emit('browser.store_unavailable', error_code='store_temporary_unavailable')
            raise DomainError("The store website is temporarily unavailable. Try again later.", 503)
        if any(x in text for x in ["verify you are human", "access denied", "unusual traffic"]):
            emit('browser.access_blocked', error_code='store_access_blocked')
            raise DomainError(
                "The store needs attention in its browser. Complete its check manually before retrying.", 503
            )

    async def save(self):
        if self.proxy:
            self.proxy.save(self)
        if self.storage_state and self.context:
            if self.secret_box:
                self.secret_box.write(self.storage_state, await self.context.storage_state(indexed_db=True))
            else:
                await self.context.storage_state(path=self.storage_state)
                Path(self.storage_state).chmod(0o600)

    @traced('browser.close')
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
            self._network_session = None


@traced('browser.cart_save_wait')
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


@traced('browser.navigation')
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
        emit('browser.navigation_retry', attempt=attempt + 1, max_attempts=3, error_code='transient_navigation_failure')
        if attempt < 2:
            await asyncio.sleep(attempt + 1)
    raise DomainError("The store is temporarily busy. Your basket is saved; please retry shortly.", 503)
