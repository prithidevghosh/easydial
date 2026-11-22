"""Read product information through verified visible store pages, without cart actions."""

from urllib.parse import urlsplit

from ..controller import DomainError
from ..models import Provider, SearchResult, now
from ..observability import emit, error_fields, traced
from . import blinkit_dom, instamart_dom
from .blinkit import normalized, parse_card
from .images import product_image_url

MAX_IMAGES = 20
MAX_FULL_IMAGES = 6
MAX_DETAILS = 16000


def detail_text(body: str) -> str:
    """Keep only the expanded product section, never account/header/recommendations."""
    lines = [line.strip() for line in body.splitlines()]
    try:
        start = lines.index('Product Details') + 1
        end = lines.index('View less details', start)
    except ValueError:
        return ''
    return '\n'.join(lines[start:end]).strip()


def image_asset(url: str) -> str:
    # CDN resizing prefixes differ between thumbnail and main display; identify
    # only the observed asset path, without constructing an unobserved image URL.
    path = urlsplit(url).path
    for prefix in ('/da/', '/assets/'):
        if prefix in path:
            return path[path.index(prefix):]
    return path


def base_details(offer):
    return {
        'status': 'unavailable', 'provider': offer.provider.value,
        'product_id': offer.product_id, 'name': offer.name, 'pack_size': offer.pack_size,
        'source_url': None, 'details_text': '', 'details_truncated': False,
        'images': [], 'image_count': 0, 'images_truncated': False,
        'retrieved_at': now().isoformat(),
        'message': 'Detailed product information is not available from this store integration yet.',
    }


@traced('product.catalogue_read')
async def read_catalogue(worker, query, address):
    """Explore in a separate tab while retaining the primary checkout/payment page."""
    from .instamart import parse_card as parse_instamart_card

    async with worker.lock, worker.browser_page() as (_, context):
        page = await context.new_page()
        try:
            await worker.navigate(page, query)
            if worker.provider == Provider.BLINKIT:
                cards = page.locator("div[role='button'][id]").filter(has_text="₹")
                await worker.wait_for_products(page, cards, query)
                limit, parser, script = 20, parse_card, blinkit_dom.CARD
            elif worker.provider == Provider.INSTAMART:
                cards = page.get_by_test_id('item-collection-card-full')
                await cards.first.wait_for(state='visible')
                limit, parser, script = 30, parse_instamart_card, instamart_dom.CARD
            else:
                raise DomainError('This store is not supported for product exploration.')
            count = await cards.count()
            offers, seen = [], set()
            for index in range(min(count, limit)):
                card = cards.nth(index)
                raw = await card.evaluate(script)
                if worker.provider == Provider.BLINKIT:
                    raw['quantity'] = await card.evaluate(blinkit_dom.QUANTITY)
                offer = parser(raw, address)
                if offer and offer.id not in seen:
                    seen.add(offer.id)
                    offers.append(offer)
                    worker.known[offer.product_id] = query
            emit('catalogue.observed', provider=getattr(worker, 'provider', None), offer_count=len(offers), status='ok')
            return SearchResult(provider=worker.provider, query=query, offers=offers, truncated=count > limit,
                                message='Current store prices and availability; final fees are checked at basket review.')
        finally:
            await page.close()


@traced('product.details_read')
async def read_product_details(worker, offer, include_images=True):
    result = base_details(offer)
    if worker.provider != offer.provider:
        raise DomainError('The selected product belongs to a different store.')
    if offer.provider != Provider.BLINKIT:
        emit('product.details_unavailable', provider=offer.provider.value, error_code='unsupported_product_details')
        # Instamart has a different product layout. Never apply Blinkit selectors
        # to it or claim that an absent integration means absent ingredients.
        return result
    if not offer.product_id.isdigit() or not offer.pack_size:
        raise DomainError('Please search for this product again before opening its details.')
    async with worker.lock, worker.browser_page() as (_, context):
        page = await context.new_page()
        try:
            await worker.navigate(page, offer.name)
            card = page.locator(f'div[role="button"][id="{offer.product_id}"]')
            await worker.wait_for_products(page, card, offer.name)
            if await card.count() != 1:
                raise DomainError('I could not verify the exact product. Please search for it again.')
            raw = await card.evaluate(blinkit_dom.CARD)
            raw['quantity'] = await card.evaluate(blinkit_dom.QUANTITY)
            current = parse_card(raw, offer.address_label)
            if not current or (
                current.product_id != offer.product_id
                or normalized(current.name) != normalized(offer.name)
                or normalized(current.pack_size or '') != normalized(offer.pack_size)
            ):
                emit('product.identity_failed', provider=getattr(worker, 'provider', None), error_code='product_identity_changed')
                raise DomainError('The product or pack changed. Please search again to choose the exact item.')
            # Only the verified product title is clickable here. Never the card's
            # ADD/quantity control or any checkout/cart action.
            await card.locator("div[class*='line-clamp-2']").first.click()
            await page.wait_for_url(f'**/prid/{offer.product_id}', timeout=15000)
            location = urlsplit(page.url)
            if location.scheme != 'https' or location.hostname != 'blinkit.com' or not location.path.endswith('/prid/' + offer.product_id):
                emit('product.identity_failed', provider=getattr(worker, 'provider', None), error_code='unexpected_product_navigation')
                raise DomainError('The store opened an unexpected product page.')
            title = page.locator("[class*='ProductInfoCard__BreadcrumbProductName']")
            await title.wait_for(state='visible', timeout=10000)
            if normalized(await title.inner_text()) != normalized(offer.name):
                emit('product.identity_failed', provider=getattr(worker, 'provider', None), error_code='product_title_mismatch')
                raise DomainError('I could not verify the selected product on its details page.')
            more = page.get_by_text('View more details', exact=True)
            if await more.is_visible():
                await more.click()
                await page.get_by_text('View less details', exact=True).wait_for(state='visible', timeout=8000)
            text = detail_text(await page.locator('body').inner_text())
            result.update(
                status='ok', source_url=page.url, details_text=text[:MAX_DETAILS],
                details_truncated=len(text) > MAX_DETAILS,
                price_paise=current.price_paise, available=current.available,
                retrieved_at=now().isoformat(),
                message=('Information shown on the current store product page. Missing fields are unknown.'
                         if text else 'The store did not expose readable product details; ingredients and other missing fields are unknown.'),
            )
            if include_images:
                thumbnails = page.locator("img[class*='ProductCarousel__CarouselImage-']")
                count = await thumbnails.count()
                images, seen = [], set()
                for index in range(min(count, MAX_IMAGES)):
                    thumbnail = thumbnails.nth(index)
                    source = product_image_url(await thumbnail.get_attribute('src'))
                    if not source:
                        continue
                    asset = image_asset(source)
                    if asset in seen:
                        continue
                    if index < MAX_FULL_IMAGES:
                        await thumbnail.click()
                        main = page.locator("[class*='ProductCarousel__ImageContainer'] img").first
                        # The displayed main image can update before its network load
                        # completes. Read its observed URL, never rewrite CDN sizing.
                        try:
                            await page.wait_for_function(
                                "asset => { const img = document.querySelector(\"[class*='ProductCarousel__ImageContainer'] img\"); "
                                "return img && (img.currentSrc || img.src).includes(asset); }",
                                arg=asset, timeout=3000,
                            )
                            main_source = product_image_url(await main.evaluate('img => img.currentSrc || img.src'))
                            if main_source and image_asset(main_source) == asset:
                                source = main_source
                        except Exception as error:
                            emit('product.image_preview_unavailable', provider=getattr(worker, 'provider', None),
                                 error_code='gallery_preview_not_ready', count=index + 1, **error_fields(error))
                            # The observed thumbnail is still a valid store image.
                            pass
                    seen.add(asset)
                    images.append(source)
                result.update(images=images, image_count=count, images_truncated=count > len(images))
            emit('product.details_observed', provider=getattr(worker, 'provider', None), status=result['status'], image_count=len(result['images']))
            return result
        finally:
            await page.close()
