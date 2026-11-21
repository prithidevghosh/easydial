"""Instamart.in browser search, variant selection, cart edits and verified bill readback."""

import asyncio
from urllib.parse import quote

from ..controller import DomainError
from ..models import Offer, Provider, SearchResult
from . import instamart_dom
from .blinkit import BlinkitProvider, cart_signature, normalized
from .browser import click_and_settle
from .money import paise


class InstamartProvider(BlinkitProvider):
    provider = Provider.INSTAMART

    def __init__(self, browser_profile: str = "", verified_address: str = "", verified_location: str = ""):
        super().__init__("", verified_location, verified_address, browser_profile)

    async def navigate(self, page, query):
        await page.goto("https://instamart.in/", wait_until="domcontentloaded", timeout=25000)
        location = await page.get_by_test_id("address-bar").get_by_test_id("address-line").inner_text()
        if normalized(self.verified_location) != normalized(location):
            raise DomainError("Instamart's delivery location changed. Select your intended address.")
        self.header_address = location
        await page.goto(
            f"https://instamart.in/search?custom_back=true&query={quote(query)}",
            wait_until="domcontentloaded",
            timeout=25000,
        )
        await self.browser.check_page(page)

    async def search(self, query: str, address: str) -> SearchResult:
        if not self.browser.configured or not self.verified_location:
            return SearchResult(
                provider=Provider.INSTAMART,
                query=query,
                status="not_connected",
                message="Connect Instamart in Chrome and select your delivery address first.",
            )
        async with self.lock:
            page, _ = await self.browser.open()
            await self.navigate(page, query)
            cards = page.get_by_test_id("item-collection-card-full")
            await cards.first.wait_for(state="visible")
            offers = []
            for i in range(min(await cards.count(), 30)):
                offer = parse_card(await cards.nth(i).evaluate(instamart_dom.CARD), address)
                if offer and offer.id not in {x.id for x in offers}:
                    offers.append(offer)
                    self.known[offer.product_id] = query
            if not offers:
                raise DomainError("Instamart's product cards could not be verified.")
            return SearchResult(
                provider=Provider.INSTAMART,
                query=query,
                offers=offers,
                truncated=await cards.count() > 30,
                message="Live Instamart item prices. Final fees require basket review.",
            )

    async def read_cart(self, page):
        go_cart = page.get_by_text("Go to Cart", exact=True)
        if not page.url.endswith("/cart") and await go_cart.is_visible():
            await go_cart.click()
            await page.wait_for_url("**/cart", timeout=15000)
        else:
            await page.goto("https://instamart.in/cart", wait_until="domcontentloaded", timeout=25000)
        for _ in range(32):
            data = await page.locator("body").evaluate(instamart_dom.CART)
            if "Your cart is getting lonely" in data["text"]:
                data["text"] = "Your cart is empty"
            payable = next((paise(x["value"]) for x in data["bill"] if x["label"] == "To Pay"), None)
            if "cart is empty" in data["text"].casefold() or (
                data["items"] and payable is not None and payable == paise(data["total"])
            ):
                break
            await asyncio.sleep(0.25)
        await self.browser.check_page(page)
        cart_signature(data)
        data["header_address"] = self.header_address
        if data["items"]:
            payable = next((paise(x["value"]) for x in data["bill"] if x["label"] == "To Pay"), None)
            if payable is None or payable != paise(data["total"]):
                raise DomainError("Instamart's bill and payable amount do not match yet. Review again.")
        return data

    async def set_quantity(self, page, line, desired):
        before = await self.read_cart(page)
        matching = [
            x
            for x in before["items"]
            if normalized(x["name"]) == normalized(line.offer.name)
            and normalized(x["pack"]) == normalized(line.offer.pack_size)
        ]
        current = matching[0]["quantity"] if len(matching) == 1 else 0
        if len(matching) > 1:
            raise DomainError("Instamart has ambiguous product variants in its cart.")
        if current == 0 and desired > 0:
            query = self.known.get(line.offer.product_id)
            if not query:
                raise DomainError("Search this Instamart item again before reviewing.")
            await self.navigate(page, query)
            cards = page.get_by_test_id("item-collection-card-full")
            await cards.first.wait_for(state="visible")
            matching_cards = []
            for i in range(await cards.count()):
                card = cards.nth(i)
                raw = await card.evaluate(instamart_dom.CARD)
                offer = parse_card(raw, line.offer.address_label)
                if (
                    offer
                    and offer.product_id == line.offer.product_id
                    and offer.pack_size == line.offer.pack_size
                ):
                    matching_cards.append(card)
            if not matching_cards:
                raise DomainError("Instamart's product or pack changed. Search again.")
            await click_and_settle(page, matching_cards[0].get_by_test_id("buttonpair-add"))
            modal = page.get_by_test_id("InstamartItemCustomizationWidget")
            # Addition may open pack selection. Wait for an observed outcome, never click a generic Confirm.
            for _ in range(32):
                if await modal.is_visible() or await page.get_by_text("Go to Cart", exact=True).is_visible():
                    break
                await asyncio.sleep(0.25)
            if await modal.is_visible():
                variants = modal.get_by_test_id("variants-container")
                matches = []
                for i in range(await variants.count()):
                    variant = variants.nth(i)
                    pack = " ".join(await variant.get_by_test_id("variants-name").all_text_contents())
                    if normalized(pack) == normalized(line.offer.pack_size):
                        matches.append(variant)
                if len(matches) != 1:
                    raise DomainError(
                        "Choose the exact Instamart pack in its browser; no variant was guessed."
                    )
                await click_and_settle(page, matches[0].get_by_test_id("add_buttons_center"))
                await modal.get_by_test_id("InstamartItemCustomizationWidget-cta").click()
                await modal.wait_for(state="hidden")
                await page.get_by_text("Go to Cart", exact=True).wait_for(state="visible")
            before = await self.read_cart(page)
            matching = [
                x
                for x in before["items"]
                if normalized(x["name"]) == normalized(line.offer.name)
                and normalized(x["pack"]) == normalized(line.offer.pack_size)
            ]
            if len(matching) != 1 or matching[0]["quantity"] != 1:
                raise DomainError("Instamart did not confirm the exact pack addition. Inspect its cart.")
            current = 1
        for _ in range(abs(desired - current)):
            rows = page.get_by_test_id("item-list-available-items-container").get_by_role("listitem")
            matches = []
            for i in range(await rows.count()):
                row = rows.nth(i)
                if normalized(await row.get_by_test_id("cart-item-name").inner_text()) == normalized(
                    line.offer.name
                ) and normalized(await row.get_by_test_id("cart-item-quantity").inner_text()) == normalized(
                    line.offer.pack_size
                ):
                    matches.append(row)
            if len(matches) != 1:
                raise DomainError("Instamart's basket item changed. No further changes made.")
            expected = current + (1 if desired > current else -1)
            await click_and_settle(
                page,
                matches[0].get_by_test_id("add_buttons_plus" if desired > current else "add_buttons_minus"),
            )
            # Reloaded provider readback prevents treating optimistic animation as success.
            after = await self.read_cart(page)
            found = [
                x["quantity"]
                for x in after["items"]
                if normalized(x["name"]) == normalized(line.offer.name)
                and normalized(x["pack"]) == normalized(line.offer.pack_size)
            ]
            current = found[0] if len(found) == 1 else 0
            if current != expected:
                raise DomainError(
                    "Instamart did not confirm the requested quantity. Inspect its cart before retrying."
                )


def parse_card(raw: dict, address: str) -> Offer | None:
    path, name, pack = raw.get("path"), raw.get("name"), raw.get("pack")
    if not path or not path.startswith("/p/") or not name or not pack:
        return None
    product_id = path.rsplit("-", 1)[-1]
    return Offer(
        id=f"live:instamart:{product_id}:{pack}",
        provider=Provider.INSTAMART,
        product_id=path,
        sku_id=f"{product_id}:{pack}",
        name=name,
        pack_size=pack,
        price_paise=paise(raw.get("price")),
        available=False if "SOLD OUT" in raw.get("text", "") else (True if raw.get("add") else None),
        mode="live",
        address_label=address,
    )
