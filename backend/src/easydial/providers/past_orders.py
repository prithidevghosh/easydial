"""Bounded, read-only Blinkit history discovery through normal browser navigation."""

import asyncio
import logging
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from ..controller import DomainError
from .browser import goto_with_retry
from .history_cache import HistoryCache
from .order_history import blinkit_placed_at, order_status, parse_blinkit

LIMIT = 10
CONCURRENCY = 3
log = logging.getLogger(__name__)


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


async def read_past_orders(worker, start=None, end=None, on_batch=None):
    started = time.monotonic()
    cache_hits = detail_reads = 0
    async with worker.lock, worker.browser_page() as (_, context):
        page = await context.new_page()
        try:
            def predicate(r):
                return urlsplit(r.url).path == '/v1/layout/order_history'
            async with page.expect_response(predicate, timeout=20000) as response:
                await goto_with_retry(page, 'https://blinkit.com/account/orders', wait_until='domcontentloaded')
            cards, _ = history_page(await (await response.value).json())
            unique = {str(c['identity']['id']): c for c in cards}
            issues = []
            if cards:
                title = cards[0]['items'][0]['data'].get('title', {}).get('text', '')
                if title:
                    await page.get_by_text(title, exact=True).first.wait_for(timeout=20000)
            # Repeat orders are deliberately limited to the first history response.
            # Never scroll or follow pagination, even when no product matches.
            selected = list(unique.values())[:LIMIT]
            cache = HistoryCache(worker)
            cache.prune(selected)
            orders = []
            async def read_one(card):
                nonlocal cache_hits, detail_reads
                # These still count toward the first 10, but were not completed purchases.
                title = card['items'][0]['data'].get('title', {}).get('text', '')
                if order_status(title) in {'cancelled', 'failed'}:
                    return None, None
                placed = card_date(card)
                if not in_period(placed, start, end):
                    return None, None
                params = parse_qs(urlsplit(card.get('click_action', {}).get('blinkit_deeplink', {}).get('url', '')).query)
                cart, order = params.get('cart_id', [''])[0], params.get('order_id', [''])[0]
                if not cart.isdigit() or not order.isdigit():
                    return None, 'unreadable_history_link'
                cached = cache.get(card)
                if cached:
                    cache_hits += 1
                    return cached, None
                detail_reads += 1
                page = await context.new_page()
                try:
                    await goto_with_retry(page, f'https://blinkit.com/account/orders/{cart}/{order}', wait_until='domcontentloaded')
                    try:
                        await page.get_by_text('Order details', exact=True).wait_for(timeout=20000)
                    except Exception:
                        return None, 'past_order_not_loaded'
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
                        return None, 'incomplete_past_order'
                    parsed['placed_at'] = placed or parsed['placed_at']
                    if card['items'][0]['data'].get('title', {}).get('text', '').casefold() == 'return completed':
                        parsed['status'] = 'returned'
                    if parsed['placed_at'] is None:
                        return None, 'unknown_order_date'
                    elif in_period(parsed['placed_at'], start, end):
                        cache.put(card, parsed)
                        return parsed, None
                    return None, None
                except Exception:
                    return None, 'past_order_not_loaded'
                finally:
                    await page.close()

            offset = 0
            while offset < len(selected):
                # A cached prefix needs no browser work and can be matched in one call.
                end_offset = offset
                while end_offset < len(selected) and cache.get(selected[end_offset]):
                    end_offset += 1
                end_offset = max(end_offset, min(offset + CONCURRENCY, len(selected)))
                tasks = [asyncio.create_task(read_one(card)) for card in selected[offset:end_offset]]
                try:
                    batch = []
                    for index, task in enumerate(tasks):
                        parsed, issue = await task
                        scanned = offset + index + 1
                        if issue:
                            # A matching newer prefix wins; an unreadable newer
                            # order must never be skipped for an older match.
                            if batch and on_batch and await on_batch({'orders': batch, 'scanned_count': scanned - 1, 'issues': []}):
                                return {'orders': orders + batch, 'scanned_count': scanned - 1, 'issues': []}
                            issues.append(issue)
                            break
                        if parsed:
                            batch.append(parsed)
                        if on_batch and batch and (index == len(tasks) - 1 or not tasks[index + 1].done()):
                            if await on_batch({'orders': batch, 'scanned_count': scanned, 'issues': []}):
                                return {'orders': orders + batch, 'scanned_count': scanned, 'issues': []}
                            orders.extend(batch)
                            batch = []
                    orders.extend(batch)
                finally:
                    # Stop speculative older reads immediately on a match or cancellation.
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                offset = end_offset
                if on_batch and issues:
                    break
            return {'orders': orders, 'scanned_count': offset, 'issues': sorted(set(issues))}
        finally:
            await page.close()
            log.info('Past-order scan: seconds=%.2f detail_reads=%s cache_hits=%s', time.monotonic() - started, detail_reads, cache_hits)
