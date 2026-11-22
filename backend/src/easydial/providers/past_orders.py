"""Bounded, read-only Blinkit history discovery through normal browser navigation."""

import asyncio
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from ..controller import DomainError
from ..observability import emit, traced
from .browser import goto_with_retry
from .order_history import blinkit_placed_at, order_status, parse_blinkit

LIMIT = 20


def history_page(payload):
    if not payload.get('is_success'):
        raise DomainError('Blinkit order history could not be loaded. Please try again.')
    response = payload.get('response', {})
    if not isinstance(response.get('snippets'), list):
        raise DomainError('Blinkit order history could not be read. Please try again.')
    return ([s['data'] for s in response['snippets'] if s.get('data', {}).get('identity')],
            bool(response.get('pagination', {}).get('next_url')))


def card_date(card):
    head = card['items'][0]['data']
    stamp = (head.get('subtitle') or head.get('left_underlined_subtitle') or {}).get('text', '')
    current = datetime.now(ZoneInfo('Asia/Kolkata'))
    placed = blinkit_placed_at(stamp, current=current)
    if placed and placed > current.timestamp() + 60 and not re.search(r'\b20\d{2}\b', stamp):
        placed = blinkit_placed_at(stamp, current=current.replace(year=current.year - 1))
    return placed


def in_period(placed, start, end):
    if placed is None:
        return True  # Read details before deciding; never silently exclude an unreadable date.
    day = datetime.fromtimestamp(placed, ZoneInfo('Asia/Kolkata')).date()
    return (start is None or day >= start) and (end is None or day <= end)


@traced('history.past_orders')
async def read_past_orders(worker, start=None, end=None):
    async with worker.lock, worker.browser_page() as (_, context):
        page = await context.new_page()
        try:
            def predicate(r):
                return urlsplit(r.url).path == '/v1/layout/order_history'
            async with page.expect_response(predicate, timeout=20000) as response:
                await goto_with_retry(page, 'https://blinkit.com/account/orders', wait_until='domcontentloaded')
            cards, more = history_page(await (await response.value).json())
            unique = {str(c['identity']['id']): c for c in cards}
            issues = []
            if cards:
                title = cards[0]['items'][0]['data'].get('title', {}).get('text', '')
                if title:
                    await page.get_by_text(title, exact=True).first.wait_for(timeout=20000)
            # Pagination is driven by the visible browser, never by replaying a private API.
            for _ in range(3):
                if len(unique) >= LIMIT or not more:
                    break
                before = len(unique)
                try:
                    async with page.expect_response(predicate, timeout=10000) as response:
                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    cards, more = history_page(await (await response.value).json())
                    unique.update((str(c['identity']['id']), c) for c in cards)
                except Exception:
                    issues.append('history_pagination_unavailable')
                    break
                if len(unique) == before:
                    issues.append('history_pagination_stalled')
                    break
            selected = list(unique.values())[:LIMIT]
            orders = []
            for card in selected:
                # These still count toward the latest 20, but were not completed purchases.
                title = card['items'][0]['data'].get('title', {}).get('text', '')
                if order_status(title) in {'cancelled', 'failed'}:
                    continue
                placed = card_date(card)
                if not in_period(placed, start, end):
                    continue
                params = parse_qs(urlsplit(card.get('click_action', {}).get('blinkit_deeplink', {}).get('url', '')).query)
                cart, order = params.get('cart_id', [''])[0], params.get('order_id', [''])[0]
                if not cart.isdigit() or not order.isdigit():
                    issues.append('unreadable_history_link')
                    continue
                await goto_with_retry(page, f'https://blinkit.com/account/orders/{cart}/{order}', wait_until='domcontentloaded')
                try:
                    await page.get_by_text('Order details', exact=True).wait_for(timeout=20000)
                except Exception:
                    issues.append('past_order_not_loaded')
                    continue
                deadline = time.monotonic() + 8
                parsed = None
                while time.monotonic() < deadline:
                    try:
                        parsed = parse_blinkit(card, await page.locator('body').inner_text(timeout=2000))
                        if parsed and parsed['items'] and parsed['order_id']:
                            break
                    except (KeyError, TypeError, ValueError, IndexError):
                        parsed = None
                    await asyncio.sleep(.25)
                if not parsed or not parsed['items'] or not parsed['order_id']:
                    issues.append('incomplete_past_order')
                    continue
                parsed['placed_at'] = placed or parsed['placed_at']
                if card['items'][0]['data'].get('title', {}).get('text', '').casefold() == 'return completed':
                    parsed['status'] = 'returned'
                if parsed['placed_at'] is None:
                    issues.append('unknown_order_date')
                elif in_period(parsed['placed_at'], start, end):
                    orders.append(parsed)
            emit('history.past_orders_observed', provider='blinkit', order_count=len(orders),
                 count=len(selected), status='partial' if issues else 'ok')
            return {'orders': orders, 'scanned_count': len(selected), 'issues': sorted(set(issues))}
        finally:
            await page.close()
