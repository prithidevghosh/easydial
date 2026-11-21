"""Official MCP discovery and basket review. No order/payment tools are exposed."""

import asyncio
import json
from decimal import Decimal, InvalidOperation

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..controller import DomainError
from ..models import Offer, Provider, Review, SearchResult, SessionView
from .cart_guard import CartGuard


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
        self.lock = asyncio.Lock()
        self.guard = CartGuard()

    async def call(self, name: str, arguments: dict):
        if name not in {"get_addresses", "search_products", "get_cart", "update_cart"}:
            raise ValueError(
                "Only discovery and basket tools are enabled. Payment and order tools are blocked."
            )
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

    async def review(self, session: SessionView) -> Review:
        if not self.token or not self.address_id:
            raise DomainError("Connect Swiggy and select a delivery address first.", 503)
        desired = tuple(sorted((x.offer.product_id, x.offer.sku_id, x.quantity) for x in session.lines))
        async with self.lock:
            before = await self.call("get_cart", {})
            self.guard.check(session.id, cart_signature(before))
            self.guard.before_write(desired)
            await self.call(
                "update_cart",
                {
                    "items": [{"spinId": p, "skuId": s, "quantity": q} for p, s, q in desired],
                    "selectedAddressId": self.address_id,
                },
            )
            after = await self.call("get_cart", {})
            actual = cart_signature(after)
            # Remember the provider readback even when stock changes prevent review.
            self.guard.accepted(actual)
            if actual != desired:
                raise DomainError(
                    "Swiggy changed quantities or removed an item. Check availability and edit your basket before reviewing again."
                )
            if after.get("addressWarning") or after.get("unserviceableItems") or after.get("cartWarning"):
                raise DomainError(
                    "Swiggy reported a delivery or basket issue. Check the selected address and items in Instamart."
                )
            details = after.get("selectedAddressDetails") or {}
            if details.get("id") != self.address_id:
                raise DomainError("Swiggy's basket delivery address does not match your selected address.")
            if any(x.get("isInStockAndAvailable") is not True for x in after.get("items", [])):
                raise DomainError("Swiggy could not confirm availability for every basket item.")
            total = paise(after.get("cartTotalAmount"))
            bill = after.get("billBreakdown") or {}
            payable = paise((bill.get("toPay") or {}).get("value"))
            if total is None or (payable is not None and payable != total):
                raise DomainError("Swiggy's payable total could not be verified.")
            bill_lines = [
                {"label": str(x["label"]), "value": str(x["value"])}
                for x in bill.get("lineItems", [])
                if "label" in x and "value" in x
            ]
            subtotal = next(
                (
                    paise(x["value"])
                    for x in bill_lines
                    if x["label"].strip().casefold() in {"item total", "item subtotal", "items total"}
                ),
                None,
            )
            return Review(
                version=session.version,
                provider=Provider.INSTAMART,
                address_label=details.get("address") or session.address_label,
                item_subtotal_paise=subtotal,
                fees_paise=None,
                total_paise=total,
                payment_method="Pending — payment is not enabled",
                mode="live",
                bill_lines=bill_lines,
                cart_reference=after.get("cartId"),
            )

    async def clear(self, session_id: str):
        async with self.lock:
            before = await self.call("get_cart", {})
            self.guard.check(session_id, cart_signature(before))
            self.guard.before_write(())
            await self.call("update_cart", {"items": [], "selectedAddressId": self.address_id})
            after = cart_signature(await self.call("get_cart", {}))
            self.guard.accepted(after)
            if after:
                raise DomainError("Swiggy did not confirm an empty basket.")


def cart_signature(data: dict) -> tuple:
    items = data.get("items")
    if items is None and data.get("cartAbsent") is True:
        return ()
    if not isinstance(items, list):
        raise ValueError("Unrecognized cart response")
    result = []
    for item in items:
        p, s, q = item.get("spinId"), item.get("skuId"), item.get("quantity")
        if not p or not s or isinstance(q, bool) or not isinstance(q, int) or q < 1:
            raise ValueError("Unrecognized cart item")
        result.append((str(p), str(s), q))
    if len({(p, s) for p, s, _ in result}) != len(result):
        raise ValueError("Duplicate SKU rows cannot be reconciled safely")
    return tuple(sorted(result))
