import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

from ..controller import DomainError
from ..models import Offer, Provider, Review, SearchResult, SessionView
from . import blinkit_dom
from .cart_guard import CartGuard
from .instamart import paise


def parse_card(raw: dict, address: str) -> Offer | None:
    product_id = raw.get("id")
    name = (raw.get("name") or "").strip()
    if not product_id or not name:
        return None
    text = raw.get("text", "")
    sizes = re.findall(r"\b(?:\d+\s*[x×]\s*)?\d+(?:\.\d+)?\s*(?:kg|g|ml|l|pcs|pieces)\b", text, re.IGNORECASE)
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
        pack_size=sizes[0].lower() if len(set(sizes)) == 1 else None,
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
    def __init__(self, storage_state: str, verified_location: str, verified_address: str = ""):
        self.storage_state, self.verified_location = storage_state, verified_location
        self.verified_address = verified_address
        self.lock = asyncio.Lock()
        self.guard = CartGuard()
        self.known: dict[str, str] = {}
        self.previous_lines = []

    def connected(self):
        return bool(self.storage_state and Path(self.storage_state).is_file() and self.verified_location)

    @asynccontextmanager
    async def browser_page(self):
        if not self.connected():
            raise DomainError("Connect Blinkit and verify your delivery address first.", 503)
        async with async_playwright() as pw:
            browser = await pw.firefox.launch(headless=True)
            try:
                context = await browser.new_context(storage_state=self.storage_state)
                page = await context.new_page()
                page.set_default_timeout(8000)
                yield page, context
            finally:
                await browser.close()

    async def navigate(self, page, query):
        await page.goto(
            f"https://blinkit.com/s/?q={quote(query)}", wait_until="domcontentloaded", timeout=20000
        )
        location = await page.locator("div[class*='LocationBar__Subtitle']").first.inner_text()
        if normalized(self.verified_location) not in normalized(location):
            raise DomainError(
                "Blinkit's selected delivery location changed. Reconnect with the intended address."
            )

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
                if parsed:
                    offers.append(parsed)
                    self.known[parsed.product_id] = query
            if not offers:
                raise ValueError("Blinkit cards could not be verified")
            return SearchResult(
                provider=Provider.BLINKIT,
                query=query,
                offers=offers,
                truncated=count > 20,
                message="Live Blinkit prices; final bill is checked during basket review.",
            )

    async def read_cart(self, page):
        drawer = page.locator(
            "div[class*='CartDrawer'], div[class*='CartSidebar'], div.cart-modal-rn, div[class*='CartWrapper__CartContainer']"
        ).first
        if not await drawer.is_visible():
            button = page.locator(
                "div[class*='CartButton__Button'], div[class*='CartButton__Container'], a[href='/cart']"
            ).first
            await button.click()
        await drawer.wait_for(state="visible")
        data = await drawer.evaluate(blinkit_dom.CART)
        data["header_address"] = await page.locator("div[class*='LocationBar__Subtitle']").first.inner_text()
        if re.search(
            r"can't take your order|currently unavailable|store is closed|high demand", data["text"], re.I
        ):
            raise DomainError("Blinkit is not accepting this basket right now. Try again later.")
        cart_signature(data)  # Do not interpret missing selectors as an empty basket.
        return data

    async def set_quantity(self, page, line, desired):
        query = self.known.get(line.offer.product_id)
        if not query:
            raise DomainError("Search this Blinkit item again before reviewing.")
        await self.navigate(page, query)
        card = page.locator("div[role='button'][id]").filter(has=page.locator("div[class*='line-clamp-2']"))
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
            await button.click()
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
            raise DomainError("Choose Blinkit products with a verified pack size first.")
        desired = tuple(
            sorted(
                (normalized(x.offer.name), normalized(x.offer.pack_size), x.quantity) for x in session.lines
            )
        )
        if len({(n, p) for n, p, _ in desired}) != len(desired):
            raise DomainError("Two Blinkit variants have indistinguishable names and packs. Choose one.")
        async with self.lock, self.browser_page() as (page, context):
            await self.navigate(page, "milk")
            before = await self.read_cart(page)
            self.guard.check(session.id, cart_signature(before))
            # Verify the full selected address before any mutation, not just the locality header.
            required_address = self.verified_address or session.address_label
            address_before = before.get("address") or before.get("header_address") or ""
            if normalized(required_address) not in normalized(address_before):
                raise DomainError(
                    "Blinkit's full delivery address could not be verified. Select your saved address and reconnect."
                )
            self.guard.before_write(desired)
            target_ids = {x.offer.product_id for x in session.lines}
            for line in self.previous_lines:
                if line.offer.product_id not in target_ids:
                    await self.set_quantity(page, line, 0)
            for line in session.lines:
                await self.set_quantity(page, line, line.quantity)
            after = await self.read_cart(page)
            actual = cart_signature(after)
            self.guard.accepted(actual)
            self.previous_lines = [x.model_copy(deep=True) for x in session.lines]
            # Persist session cookies/local storage after cart changes for subsequent worker contexts.
            await context.storage_state(path=self.storage_state)
            if actual != desired:
                raise DomainError(
                    "Blinkit's basket does not match the requested items and quantities. Review it in Blinkit."
                )
            if not after.get("address") or normalized(required_address) not in normalized(after["address"]):
                raise DomainError("Blinkit's delivery address changed during basket review.")
            total = paise(after.get("total"))
            if total is None:
                raise DomainError("Blinkit's final bill could not be verified. No checkout was attempted.")
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
                provider=Provider.BLINKIT,
                address_label=after["address"],
                item_subtotal_paise=subtotal,
                fees_paise=None,
                total_paise=total,
                payment_method="Pending — payment is not enabled",
                mode="live",
                bill_lines=bill,
            )

    async def clear(self, session_id: str):
        async with self.lock, self.browser_page() as (page, context):
            await self.navigate(page, "milk")
            before = cart_signature(await self.read_cart(page))
            self.guard.check(session_id, before)
            if before and not self.previous_lines:
                raise DomainError(
                    "Blinkit has a partially changed basket. Clear it in Blinkit before continuing."
                )
            self.guard.before_write(())
            for line in self.previous_lines:
                await self.set_quantity(page, line, 0)
            after = cart_signature(await self.read_cart(page))
            self.guard.accepted(after)
            await context.storage_state(path=self.storage_state)
            if after:
                raise DomainError("Blinkit did not confirm an empty basket.")
            self.previous_lines = []
