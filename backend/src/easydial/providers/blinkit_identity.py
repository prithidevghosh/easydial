"""Bind differing merchant labels only through an observed, exact-product detail page."""

import re
from urllib.parse import urlsplit

from ..controller import DomainError
from ..models import Provider
from ..observability import emit


def _normalized(value):
    return re.sub(r"\s+", "", value or "").casefold()


def offer_cart_name(offer):
    return (getattr(offer, "cart_name", None) or offer.name) if getattr(offer, "provider", None) == Provider.BLINKIT else offer.name


def lines_signature(lines):
    return tuple(sorted((_normalized(offer_cart_name(x.offer)), _normalized(x.offer.pack_size), x.quantity) for x in lines))


async def _read_cart_name(worker, page, offer):
    """Read exact-ID PDP without touching the caller's cart or payment page."""
    from . import blinkit_dom
    from .blinkit import parse_card
    from .browser import BrowserSession

    if not offer.product_id.isdigit() or not offer.pack_size:
        raise DomainError("The store product identity could not be verified.")
    tab = await page.context.new_page()
    try:
        query = worker.known.get(offer.product_id) or offer.name
        await worker.navigate(tab, query)
        card = tab.locator(f'div[role="button"][id="{offer.product_id}"]')
        await worker.wait_for_products(tab, card, query)
        if await card.count() != 1:
            raise DomainError("The store product identity could not be verified.")
        current = parse_card(await card.evaluate(blinkit_dom.CARD), offer.address_label)
        if not current or current.product_id != offer.product_id or _normalized(current.name) != _normalized(offer.name) or _normalized(current.pack_size) != _normalized(offer.pack_size):
            raise DomainError("The store product or pack changed. Search again before updating it.")
        await card.locator("div[class*='line-clamp-2']").click()
        await tab.wait_for_url(f"**/prid/{offer.product_id}", timeout=10000)
        url = urlsplit(tab.url)
        if url.scheme != "https" or url.hostname != "blinkit.com" or not url.path.endswith(f"/prid/{offer.product_id}"):
            raise DomainError("The store opened an unexpected product page.")
        title = tab.locator("[class*='ProductInfoCard__BreadcrumbProductName']")
        missing = tab.get_by_text("The page you are looking for can’t be found.", exact=True)
        await title.or_(missing).first.wait_for(state="visible", timeout=10000)
        if await missing.is_visible():
            # The merchant can navigate to the correct product route but render a
            # transient not-found screen. Retry that observed URL exactly once.
            emit("blinkit.cart_identity_retry", provider=worker.provider, error_code="product_page_not_found")
            await tab.reload(wait_until="domcontentloaded", timeout=15000)
            await BrowserSession.check_page(tab)
            url = urlsplit(tab.url)
            if url.scheme != "https" or url.hostname != "blinkit.com" or not url.path.endswith(f"/prid/{offer.product_id}"):
                raise DomainError("The store opened an unexpected product page.")
            await title.or_(missing).first.wait_for(state="visible", timeout=10000)
            if await missing.is_visible():
                raise DomainError("Blinkit's product details could not be verified. Your basket is saved; please retry shortly.")
        # Observed PDP purchase panel, not recommendations or a different pack selector.
        pack = tab.locator("[class*='ProductDesktopBffEnabled__ProductWrapperRightSection'] .tw-flex-col.tw-gap-1\\.5 > .tw-text-300.tw-font-semibold")
        if await title.count() != 1 or await pack.count() != 1 or not await pack.is_visible() or _normalized(await pack.inner_text()) != _normalized(offer.pack_size):
            raise DomainError("The store product pack could not be verified.")
        name = (await title.inner_text()).strip()
        if not name:
            raise DomainError("The store product label could not be verified.")
        return name
    finally:
        await tab.close()


async def resolve_cart_names(worker, page, lines, cart):
    """Resolve unique aliases and translate only proven names in existing ownership."""
    if worker.provider != Provider.BLINKIT or not cart.get("items") or page is None:
        return
    groups = {}
    for line in lines:
        offer = line.offer
        if not getattr(offer, "product_id", None) or not getattr(offer, "sku_id", None):
            continue  # No identity evidence: retain strict original-label comparisons.
        groups.setdefault((offer.product_id, offer.sku_id, _normalized(offer.pack_size)), []).append(offer)
    rows = cart["items"]
    known_keys = {(_normalized(offer_cart_name(offer)), key[2]) for key, offers in groups.items() for offer in offers}
    unresolved_rows = [row for row in rows if (_normalized(row.get("name")), _normalized(row.get("pack"))) not in known_keys]
    updates = {}
    for key, offers in groups.items():
        offer = offers[0]
        pack = key[2]
        cached = {x.cart_name for x in offers if getattr(x, "cart_name", None)}
        if len(cached) > 1:
            raise DomainError("The store product labels are ambiguous. Review the basket again.")
        name = next(iter(cached), offer.name)
        exact = [x for x in rows if _normalized(x.get("name")) == _normalized(name) and _normalized(x.get("pack")) == pack]
        if len(exact) > 1:
            raise DomainError("The store's basket variants are ambiguous.")
        if not exact and not cached and any(_normalized(x.get("pack")) == pack for x in unresolved_rows):
            name = await _read_cart_name(worker, page, offer)
            exact = [x for x in rows if _normalized(x.get("name")) == _normalized(name) and _normalized(x.get("pack")) == pack]
            if len(exact) > 1:
                raise DomainError("The store's basket variants are ambiguous.")
            if not exact:
                continue
        if exact and _normalized(name) != _normalized(offer.name):
            updates[key] = name
    # Never collapse separate product identities onto the same visible cart row.
    targets = {}
    for key, offers in groups.items():
        name = updates.get(key, offer_cart_name(offers[0]))
        target = (_normalized(name), key[2])
        if target in targets and targets[target] != key:
            raise DomainError("Two store variants have indistinguishable cart labels and packs.")
        targets[target] = key
    translation = {}
    for key, name in updates.items():
        for offer in groups[key]:
            old = (_normalized(offer.name), key[2])
            new = (_normalized(name), key[2])
            if old in translation and translation[old] != new:
                raise DomainError("The store product labels are ambiguous.")
            translation[old] = new
    def translated(signature):
        result = tuple(sorted((*translation.get((name, pack), (name, pack)), qty) for name, pack, qty in signature))
        if len({(name, pack) for name, pack, _ in result}) != len(result):
            raise DomainError("The store cart ownership is ambiguous.")
        return result
    expected = translated(worker.guard.expected)
    attempted = translated(worker.guard.attempted) if worker.guard.attempted is not None else None
    for key, name in updates.items():
        for offer in groups[key]:
            offer.cart_name = name
        emit("blinkit.cart_identity_verified", provider=worker.provider, status="ok")
    worker.guard.expected, worker.guard.attempted = expected, attempted
