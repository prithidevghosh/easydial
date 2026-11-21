"""Conservative read-only Blinkit worker. Uses a manually authenticated browser state.

Selectors are based on the inspected public upstream implementation. Live validation
is required; parsing failure is never interpreted as out-of-stock or success.
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

from ..models import Offer, Provider, SearchResult
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
    price = paise(prices[0]) if len(prices) == 1 else None
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
        available=True if re.search(r"\bADD\b", text) else None,
        mode="live",
        address_label=address,
    )


class BlinkitProvider:
    def __init__(self, storage_state: str, verified_location: str):
        self.storage_state = storage_state
        self.verified_location = verified_location
        self.lock = asyncio.Lock()

    async def search(self, query: str, address: str) -> SearchResult:
        if not self.storage_state or not Path(self.storage_state).is_file() or not self.verified_location:
            return SearchResult(
                provider=Provider.BLINKIT,
                query=query,
                status="not_connected",
                message="Connect Blinkit and verify the delivery location first.",
            )
        async with self.lock:
            async with async_playwright() as pw:
                browser = await pw.firefox.launch(headless=True)
                try:
                    context = await browser.new_context(storage_state=self.storage_state)
                    page = await context.new_page()
                    await page.goto(
                        f"https://blinkit.com/s/?q={quote(query)}",
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    # Location must appear in the delivery header, not somewhere in product text.
                    header = page.locator("div[class*='LocationBar__Subtitle']").first
                    location = await header.inner_text(timeout=8000)
                    if self.verified_location.casefold() not in location.casefold():
                        raise ValueError("Blinkit's delivery location could not be verified.")
                    cards = page.locator("div[role='button']").filter(has_text="₹").filter(has_text="ADD")
                    await cards.first.wait_for(state="visible", timeout=10000)
                    count = await cards.count()
                    offers = []
                    for i in range(min(count, 20)):
                        card = cards.nth(i)
                        name = card.locator("div[class*='line-clamp-2']").first
                        raw = {
                            "id": await card.get_attribute("id"),
                            "name": await name.inner_text(),
                            "text": await card.inner_text(),
                        }
                        parsed = parse_card(raw, address)
                        if parsed:
                            offers.append(parsed)
                    if not offers:
                        raise ValueError("Blinkit cards could not be verified; availability is unknown.")
                    return SearchResult(
                        provider=Provider.BLINKIT,
                        query=query,
                        offers=offers,
                        truncated=count > 20,
                        message="Live browser results. Unknown brand/variant or ambiguous prices need verification.",
                    )
                finally:
                    await browser.close()
