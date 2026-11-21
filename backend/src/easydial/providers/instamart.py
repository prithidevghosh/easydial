"""Read-only official MCP adapter. Live cart/purchase tools are intentionally not exposed."""

import json
from decimal import Decimal, InvalidOperation

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..models import Offer, Provider, SearchResult


def paise(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace("₹", "").replace(",", "").strip())
        if not number.is_finite() or number < 0 or number > 1_000_000:
            return None
        return int((number * 100).quantize(Decimal(1)))
    except (InvalidOperation, ValueError):
        return None


def unpack(result) -> dict:
    if result.isError:
        raise ValueError("Provider rejected this request.")
    payload = result.structuredContent
    if not payload:
        for block in result.content:
            if getattr(block, "type", None) == "text":
                try:
                    payload = json.loads(block.text)
                    break
                except (ValueError, TypeError):
                    continue
    if not isinstance(payload, dict) or payload.get("success") is False or payload.get("error"):
        raise ValueError("Provider returned an error or unrecognized response.")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Provider returned an invalid product payload.")
    return data


def normalize_products(data: dict, query: str, address: str) -> SearchResult:
    if not isinstance(data.get("products"), list):
        raise ValueError("The product schema changed; results could not be verified.")
    offers = []
    for product in data["products"]:
        for variant in product.get("variations", []):
            spin, sku = variant.get("spinId"), variant.get("skuId")
            if not spin or not sku:
                continue
            price = variant.get("price") or {}
            available = variant.get("isInStockAndAvailable")
            cap = variant.get("maxQuantity")
            if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
                cap = 10
            offers.append(
                Offer(
                    id=f"live:instamart:{spin}:{sku}",
                    provider=Provider.INSTAMART,
                    product_id=str(spin),
                    sku_id=str(sku),
                    name=variant.get("displayName") or product.get("displayName") or "Unnamed product",
                    brand=variant.get("brandName") or product.get("brand") or None,
                    variant=variant.get("displayName") or product.get("displayName") or None,
                    pack_size=variant.get("quantityDescription") or None,
                    price_paise=paise(price.get("offerPrice")),
                    available=available if isinstance(available, bool) else None,
                    max_quantity=min(cap, 100),
                    mode="live",
                    address_label=address,
                )
            )
    return SearchResult(
        provider=Provider.INSTAMART,
        query=query,
        offers=offers,
        truncated=bool(data.get("nextOffset")),
        message="Live item prices; checkout fees not included.",
    )


class InstamartProvider:
    endpoint = "https://mcp.swiggy.com/im"

    def __init__(self, token: str, address_id: str):
        self.token, self.address_id = token, address_id

    async def call(self, name: str, arguments: dict):
        if name not in {"get_addresses", "search_products"}:
            raise ValueError("Only discovery tools are enabled.")
        async with streamablehttp_client(
            self.endpoint, headers={"Authorization": f"Bearer {self.token}"}, timeout=20
        ) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                if name not in {tool.name for tool in tools.tools}:
                    raise ValueError("This account does not expose the required tool.")
                return unpack(await client.call_tool(name, arguments))

    async def search(self, query: str, address: str) -> SearchResult:
        if not self.token or not self.address_id:
            return SearchResult(
                provider=Provider.INSTAMART,
                query=query,
                status="not_connected",
                message="Connect a Swiggy account and verified address first.",
            )
        data = await self.call("search_products", {"query": query, "addressId": self.address_id})
        return normalize_products(data, query, address)
