"""Account-encrypted, bounded cache of terminal historical item details only."""

import hashlib
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)
MAX_AGE = 7 * 24 * 3600


def fingerprint(card):
    head = card['items'][0]['data']
    value = [card['identity']['id'], head.get('title'), head.get('subtitle'),
             head.get('left_underlined_subtitle'), card.get('click_action')]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class HistoryCache:
    def __init__(self, worker):
        browser = getattr(worker, 'browser', None)
        self.path = Path(str(browser.storage_state) + '.history') if browser and browser.storage_state and browser.secret_box else None
        self.box = browser.secret_box.derive('past-order-items-v1') if self.path else None
        self.entries = {}
        if self.path and self.path.is_file():
            try:
                value = self.box.open(self.path.read_text())
                if value.get('version') == 1 and isinstance(value.get('entries'), dict):
                    self.entries = value['entries']
            except Exception:
                log.warning('Past-order cache unreadable; fetching fresh history')

    def prune(self, cards):
        allowed = {str(card['identity']['id']) for card in cards}
        self.entries = {key: entry for key, entry in self.entries.items() if key in allowed}
        self.save()

    def get(self, card):
        entry = self.entries.get(str(card['identity']['id']))
        try:
            if not entry or entry.get('fingerprint') != fingerprint(card) or not 0 <= time.time() - entry.get('saved_at', 0) < MAX_AGE:
                return None
            order = entry['order']
            valid = (
                str(order['history_id']) == str(card['identity']['id']) and order['order_id']
                and order['status'] in {'delivered', 'returned'}
                and isinstance(order['placed_at'], (int, float)) and order['items']
                and all(isinstance(item['name'], str) and item['name'] and isinstance(item['pack'], str) and item['pack']
                        and type(item['quantity']) is int and item['quantity'] > 0 for item in order['items'])
            )
            return order if valid else None
        except (KeyError, TypeError, AttributeError):
            return None

    def put(self, card, order):
        if order['status'] not in {'delivered', 'returned'}:
            return
        # No delivery address, payment data, or historical prices are persisted.
        self.entries[str(card['identity']['id'])] = {
            'fingerprint': fingerprint(card), 'saved_at': time.time(),
            'order': {key: order[key] for key in ('order_id', 'history_id', 'status', 'placed_at', 'items')},
        }
        self.save()

    def save(self):
        if self.path:
            try:
                self.box.write(self.path, {'version': 1, 'entries': self.entries})
            except OSError:
                log.warning('Past-order cache could not be saved; live results remain usable')
