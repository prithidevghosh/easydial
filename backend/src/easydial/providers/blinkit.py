import asyncio
import re
from contextlib import asynccontextmanager
from urllib.parse import quote

from ..controller import DomainError
from ..models import Offer, Provider, Review, SearchResult, SessionView
from . import blinkit_dom
from .browser import BrowserSession, click_and_settle
from .cart_guard import CartConflict, CartGuard
from .images import product_image_url
from .money import paise


def parse_card(raw: dict, address: str) -> Offer | None:
    product_id = raw.get("id")
    name = (raw.get("name") or "").strip()
    if not product_id or not name:
        return None
    text = raw.get("text", "")
    sizes = re.findall(
        r"\b(?:\d+\s*[x×]\s*)?\d+(?:\.\d+)?\s*(?:kg|g|ml|ltr|l|pc|pcs|pieces)\b", text, re.IGNORECASE
    )
    prices = re.findall(r"₹\s*([\d,]+(?:\.\d{1,2})?)", text)
    # If MRP and offer price cannot be distinguished, price remains unknown.
    visible_prices = raw.get("current_prices", prices)
    price = paise(visible_prices[0]) if len(visible_prices) == 1 else None
    return Offer(
        id=f"live:blinkit:{product_id}",
        provider=Provider.BLINKIT,
        product_id=product_id,
        sku_id=product_id,
        name=name,
        image_url=product_image_url(raw.get("image")),
        pack_size=(raw.get("pack") or (sizes[0] if len(set(sizes)) == 1 else "")).strip().lower() or None,
        brand=None,
        variant=None,
        price_paise=price,
        available=True if re.search(r"\bADD\b", text) or raw.get("quantity", 0) else None,
        mode="live",
        address_label=address,
    )


def normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def cart_signature(data: dict) -> tuple:
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("Unrecognized Blinkit cart")
    if not items:
        if not re.search(
            r"(?:cart|basket).*empty|don't have any items|no items in", data.get("text", ""), re.I
        ):
            raise ValueError("Could not verify that Blinkit cart is empty")
        return ()
    result = []
    for item in items:
        q = item.get("quantity")
        if (
            not item.get("name")
            or not item.get("pack")
            or isinstance(q, bool)
            or not isinstance(q, int)
            or q < 1
        ):
            raise ValueError("Unrecognized Blinkit cart quantity or pack")
        result.append((normalized(item["name"]), normalized(item["pack"]), q))
    if len({(n, p) for n, p, _ in result}) != len(result):
        raise ValueError("Ambiguous Blinkit basket products")
    return tuple(sorted(result))


class BlinkitProvider:
    provider = Provider.BLINKIT

    def __init__(
        self,
        storage_state: str,
        verified_location: str,
        verified_address: str = "",
        browser_profile: str = "",
    ):
        self.storage_state, self.verified_location = storage_state, verified_location
        self.verified_address = verified_address
        self.lock = asyncio.Lock()
        self.guard = CartGuard()
        self.known: dict[str, str] = {}
        self.previous_lines = []
        self.browser = BrowserSession(browser_profile, storage_state)
        self.header_address = ""

    def connected(self):
        return bool(self.browser.configured and self.verified_location and self.verified_address)

    @asynccontextmanager
    async def browser_page(self):
        if not self.connected():
            raise DomainError("Connect the store and verify your delivery address first.", 503)
        page, context = await self.browser.open()
        yield page, context

    async def navigate(self, page, query):
        # The search layout has no LocationBar. Verify locality on the homepage first.
        await page.goto("https://blinkit.com/", wait_until="domcontentloaded", timeout=20000)
        await self.browser.check_page(page)
        subtitle = page.locator("div[class*='LocationBar__Subtitle']").first
        location = await subtitle.inner_text()
        # Fresh contexts briefly render a default locality before restoring the
        # saved one. Wait for the expected value; never accept a different one.
        for _ in range(20):
            if normalized(self.verified_location) == normalized(location):
                break
            await asyncio.sleep(0.25)
            location = await subtitle.inner_text()
        if normalized(self.verified_location) != normalized(location):
            # Checkout can replace the short locality label with an expanded
            # address label. Verify the actual delivery address, never infer
            # identity from truncated header text or select a different address.
            cart = await self.read_cart(page, resolve_address=False)
            if not self.verified_address or normalized(self.verified_address) not in normalized(
                cart.get("address") or ""
            ):
                raise DomainError(
                    "Blinkit's selected delivery location changed. Reconnect with the intended address."
                )
            self.verified_location = location
        self.header_address = location
        await page.goto(
            f"https://blinkit.com/s/?q={quote(query)}", wait_until="domcontentloaded", timeout=20000
        )
        await self.browser.check_page(page)

    async def close(self):
        await self.browser.close()

    async def search(self, query: str, address: str) -> SearchResult:
        if not self.connected():
            return SearchResult(
                provider=Provider.BLINKIT,
                query=query,
                status="not_connected",
                message="Connect Blinkit and verify the delivery location first.",
            )
        async with self.lock, self.browser_page() as (page, _):
            await self.navigate(page, query)
            cards = page.locator("div[role='button'][id]").filter(has_text="₹")
            await cards.first.wait_for(state="visible")
            count = await cards.count()
            offers = []
            for i in range(min(count, 20)):
                card = cards.nth(i)
                raw = await card.evaluate(blinkit_dom.CARD)
                raw["quantity"] = await card.evaluate(blinkit_dom.QUANTITY)
                parsed = parse_card(raw, address)
                if (
                    parsed
                    and parsed.id not in {offer.id for offer in offers}
                ):
                    offers.append(parsed)
                    self.known[parsed.product_id] = query
            return SearchResult(
                provider=Provider.BLINKIT,
                query=query,
                offers=offers,
                truncated=count > 20,
                message="Live Blinkit prices; final bill is checked during basket review.",
            )

    async def read_cart(self, page, *, resolve_address=True):
        drawer = page.locator(
            "div[class*='CartDrawer'], div[class*='CartSidebar'], div.cart-modal-rn, div[class*='CartWrapper__CartContainer']"
        ).first
        if not await drawer.is_visible():
            button = page.locator("div[class*='CartButton__Button']").first
            await button.wait_for(state="visible")
            # During hydration Blinkit renders a disabled, apparently empty cart
            # before restoring its contents. A known nonempty cart must get time
            # to load before absence is treated as evidence.
            if self.guard.expected or self.guard.attempted:
                for _ in range(20):
                    if await button.get_attribute("disabled") is None:
                        break
                    await asyncio.sleep(0.25)
            if await button.get_attribute("disabled") is not None:
                return {
                    "items": [],
                    "text": "Your cart is empty",
                    "header_address": self.header_address,
                    "address": None,
                    "bill": [],
                    "total": None,
                }
            await button.click()
        await drawer.wait_for(state="visible")
        # The drawer shell appears before its cart data. Do not read an empty shell.
        for _ in range(32):
            data = await drawer.evaluate(blinkit_dom.CART)
            if (data["items"] and data["total"] and data["bill"]) or re.search(
                r"(?:cart|basket).*empty|don't have any items|no items in", data["text"], re.I
            ):
                break
            await asyncio.sleep(0.25)
        if resolve_address and data["items"] and not data.get("address") and self.verified_address:
            await self.select_checkout_address(page)
            for _ in range(32):
                data = await drawer.evaluate(blinkit_dom.CART)
                if data.get("address") and data.get("total") and data.get("bill"):
                    break
                await asyncio.sleep(0.25)
        data["header_address"] = self.header_address
        if re.search(
            r"can't take your order|currently unavailable|store is closed|high demand", data["text"], re.I
        ):
            raise DomainError("Blinkit is not accepting this basket right now. Try again later.")
        cart_signature(data)  # Do not interpret missing selectors as an empty basket.
        return data

    async def select_checkout_address(self, page):
        """Apply only the address explicitly chosen at onboarding, never a default row."""
        rows = page.locator("div[class*='AddressList__AddressItemWrapper-']")
        if not await rows.first.is_visible():
            proceed = page.locator("div[class*='CheckoutStrip__CTAText']").get_by_text("Proceed", exact=True)
            if await proceed.count() != 1 or not await proceed.is_visible():
                raise DomainError("I couldn't confirm your delivery address. Please review your basket again.")
            await proceed.click()
        await rows.first.wait_for(state="visible")
        matches = []
        for index in range(await rows.count()):
            details = rows.nth(index).locator("div[class*='AddressList__AddressDetails-']").last
            if normalized(await details.inner_text()) == normalized(self.verified_address):
                matches.append(details)
        if len(matches) != 1:
            raise DomainError("Please reconnect Blinkit and choose your delivery address again.")
        await matches[0].click()
        await rows.first.wait_for(state="hidden")

    async def set_quantity(self, page, line, desired):
        # Existing items must remain removable even if search inventory stops returning them.
        before_cart = await self.read_cart(page)
        existing = [
            x
            for x in before_cart["items"]
            if normalized(x["name"]) == normalized(line.offer.name)
            and normalized(x["pack"]) == normalized(line.offer.pack_size)
        ]
        if len(existing) == 1:
            current = existing[0]["quantity"]
            for _ in range(abs(desired - current)):
                rows = page.locator("div.cart-modal-rn div[class*='DefaultProductCard__Container']")
                matches = []
                for i in range(await rows.count()):
                    row = rows.nth(i)
                    name = await row.locator("div[class*='ProductTitle']").inner_text()
                    pack = await row.locator("div[class*='ProductVariant']").inner_text()
                    if normalized(name) == normalized(line.offer.name) and normalized(pack) == normalized(
                        line.offer.pack_size
                    ):
                        matches.append(row)
                if len(matches) != 1:
                    raise DomainError("Blinkit's basket item changed. Inspect its cart before retrying.")
                row = matches[0]
                selector = (
                    "div[class*='AddToCart___StyledDiv2-']"
                    if desired > current
                    else "div[class*='AddToCart___StyledDiv-']"
                )
                expected = current + (1 if desired > current else -1)
                await click_and_settle(page, row.locator(selector))
                for _ in range(32):
                    current = await row.evaluate(blinkit_dom.QUANTITY) if await row.count() else 0
                    if current == expected:
                        break
                    await asyncio.sleep(0.25)
                if current != expected:
                    raise DomainError(
                        "Blinkit did not confirm the basket quantity. Inspect it before retrying."
                    )
            return
        if existing:
            raise DomainError("Blinkit's basket variants are ambiguous.")
        if desired == 0:
            return
        query = self.known.get(line.offer.product_id) or line.offer.name
        if not query:
            raise DomainError("Search this Blinkit item again before reviewing.")
        await self.navigate(page, query)
        card = page.locator("div[role='button'][id]").filter(has=page.locator("div[class*='line-clamp-2']"))
        await card.first.wait_for(state="visible")
        # Product IDs came from the visible DOM. Match as data, never interpolate into a CSS expression.
        matches = []
        for i in range(await card.count()):
            item = card.nth(i)
            if await item.get_attribute("id") == line.offer.product_id:
                matches.append(item)
        if len(matches) != 1:
            raise DomainError("Blinkit's product card changed. Search again.")
        card = matches[0]
        raw = await card.evaluate(blinkit_dom.CARD)
        parsed = parse_card(raw, line.offer.address_label)
        if (
            not parsed
            or normalized(parsed.name) != normalized(line.offer.name)
            or parsed.pack_size != line.offer.pack_size
        ):
            raise DomainError("Blinkit's product or pack size changed. Search again.")
        current = await card.evaluate(blinkit_dom.QUANTITY)
        if current is None:
            raise DomainError("Blinkit's quantity control could not be verified.")
        for _ in range(abs(desired - current)):
            before = current
            if current == 0 and desired > 0:
                button = card.get_by_text("ADD", exact=True)
            else:
                icon = ".icon-plus" if desired > current else ".icon-minus"
                button = card.locator(icon).locator("..")
            if await button.count() != 1:
                raise DomainError("Blinkit's add/remove control is ambiguous. No further changes were made.")
            await click_and_settle(page, button)
            for _ in range(32):
                current = await card.evaluate(blinkit_dom.QUANTITY)
                if current != before:
                    break
                await asyncio.sleep(0.25)
            expected = before + (1 if desired > before else -1)
            if current != expected:
                raise DomainError(
                    "Blinkit did not confirm the requested quantity. Check its basket before retrying."
                )

    async def review(self, session: SessionView) -> Review:
        if any(not x.offer.pack_size for x in session.lines):
            raise DomainError("Choose products with a verified pack size first.")
        desired = tuple(
            sorted(
                (normalized(x.offer.name), normalized(x.offer.pack_size), x.quantity) for x in session.lines
            )
        )
        if len({(n, p) for n, p, _ in desired}) != len(desired):
            raise DomainError("Two variants have indistinguishable names and packs. Choose one.")
        async with self.lock, self.browser_page() as (page, context):
            await self.navigate(page, "milk")
            before = await self.read_cart(page)
            required_address = self.verified_address or session.address_label
            address_before = before.get("address") or before.get("header_address") or ""
            # Empty carts expose only the selected location header. It must exactly match
            # onboarding; the full saved address is mandatory in the post-sync checkout bill.
            address_ok = normalized(required_address) in normalized(address_before)
            location_only = not before["items"] and normalized(
                before.get("header_address", "")
            ) == normalized(self.verified_location)
            if not address_ok and not location_only:
                raise DomainError(
                    "The store's full delivery address could not be verified. Select your saved address and reconnect."
                )
            try:
                self.guard.check(session.id, cart_signature(before))
            except CartConflict as conflict:
                conflict.cart = before
                conflict.previous_lines = self.previous_lines
                raise
            self.guard.before_write(desired)
            target_ids = {(x.offer.product_id, x.offer.sku_id) for x in session.lines}
            for line in self.previous_lines:
                if (line.offer.product_id, line.offer.sku_id) not in target_ids:
                    await self.set_quantity(page, line, 0)
            for line in session.lines:
                await self.set_quantity(page, line, line.quantity)
            after = await self.read_cart(page)
            actual = cart_signature(after)
            self.guard.accepted(actual)
            self.previous_lines = [x.model_copy(deep=True) for x in session.lines]
            # Persist session cookies/local storage after cart changes for subsequent worker contexts.
            await self.browser.save()
            if actual != desired:
                raise DomainError(
                    "The store's basket does not match the requested items and quantities. Review it in the store browser."
                )
            if not after.get("address") or normalized(required_address) not in normalized(after["address"]):
                raise DomainError("The store's delivery address changed during basket review.")
            return self.verified_review(session, after)

    def verified_review(self, session, after):
        total = paise(after.get("total"))
        if total is None:
            raise DomainError("The store's final bill could not be verified. No checkout was attempted.")
        bill = [
            {"label": str(x["label"]), "value": str(x["value"])}
            for x in after["bill"]
            if x.get("label") and x.get("value")
        ]
        subtotal = next(
            (paise(x["value"]) for x in bill if x["label"].casefold() in {"items total", "item total"}),
            None,
        )
        return Review(
            version=session.version,
            provider=self.provider,
            address_label=after["address"],
            item_subtotal_paise=subtotal,
            fees_paise=None,
            total_paise=total,
            payment_method="Pending — payment is not enabled",
            mode="live",
            bill_lines=bill,
        )

    async def revalidate(self, session: SessionView) -> Review:
        """Read-only freshness check: never repairs or changes a store cart."""
        desired = tuple(
            sorted(
                (normalized(x.offer.name), normalized(x.offer.pack_size), x.quantity) for x in session.lines
            )
        )
        async with self.lock, self.browser_page() as (page, context):
            await self.navigate(page, "milk")
            actual = await self.read_cart(page)
            # Retry read-only snapshots before declaring a real change. Never
            # let a transient empty snapshot erase the verified cart checkpoint.
            for _ in range(2):
                if cart_signature(actual) == desired:
                    break
                await asyncio.sleep(0.5)
                actual = await self.read_cart(page)
            if cart_signature(actual) != desired:
                raise DomainError("The store cart changed. Review the basket again before continuing.")
            self.guard.check(session.id, cart_signature(actual))
            required = self.verified_address or session.address_label
            if not actual.get("address") or normalized(required) not in normalized(actual["address"]):
                raise DomainError("The delivery address changed. Review it again before continuing.")
            return self.verified_review(session, actual)

    async def adopt_checkpoint(self, checkpoint, expected_address):
        """Explicitly approved recovery: verify the unchanged cart, then adopt without writes."""
        desired = tuple(
            sorted(
                (normalized(x.offer.name), normalized(x.offer.pack_size), x.quantity)
                for x in checkpoint.lines
            )
        )
        async with self.lock, self.browser_page() as (page, context):
            await self.navigate(page, "milk")
            actual = await self.read_cart(page)
            if cart_signature(actual) != desired or actual.get("address") != expected_address:
                raise DomainError(
                    "The store cart or address changed again. Send review for a fresh comparison; nothing was updated."
                )
            if normalized(self.verified_address or checkpoint.address_label) not in normalized(
                actual.get("address", "")
            ):
                raise DomainError("The store address could not be verified. Nothing was updated.")
            self.guard.owner = checkpoint.id
            self.guard.accepted(desired)
            self.previous_lines = [x.model_copy(deep=True) for x in checkpoint.lines]

    async def clear(self, session_id: str):
        async with self.lock, self.browser_page() as (page, context):
            await self.navigate(page, "milk")
            before = cart_signature(await self.read_cart(page))
            self.guard.check(session_id, before)
            if before and not self.previous_lines:
                raise DomainError(
                    "The store has a partially changed basket. Clear it in the store browser before continuing."
                )
            self.guard.before_write(())
            for line in self.previous_lines:
                await self.set_quantity(page, line, 0)
            after = cart_signature(await self.read_cart(page))
            self.guard.accepted(after)
            await self.browser.save()
            if after:
                raise DomainError("The store did not confirm an empty basket.")
            self.previous_lines = []
