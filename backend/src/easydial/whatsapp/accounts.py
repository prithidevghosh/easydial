"""Customer-scoped connections. Never import the operator's pilot profiles."""

import hashlib
import hmac
import secrets
import time
from pathlib import Path

from ..controller import DomainError
from ..models import Provider
from ..providers import ProviderWorkers
from .conversation import ShoppingChat

CONSENT_VERSION = "2026-11-21-v1"
CONSENT_TEXT = (
    "Allow EasyDial to use the OTP you send here to log into your {store} account, "
    "and securely save and reuse your session cookies to search, manage your basket and help with orders? "
    "We encrypt login messages and saved sessions. Your OTP is not sent to the shopping AI "
    "and is removed from our processing queue after use. It may remain in your WhatsApp chat. "
    "You can disconnect and withdraw consent anytime. Payments and orders still require your confirmation."
)


class Accounts:
    def __init__(self, db, box, directory):
        self.db, self.box, self.root = db, box, Path(directory).resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS onboarding (
                sender TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS store_connections (
                sender TEXT NOT NULL, provider TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(sender,provider));
            CREATE TABLE IF NOT EXISTS consent_events (
                id INTEGER PRIMARY KEY, sender TEXT NOT NULL, provider TEXT NOT NULL,
                version TEXT NOT NULL, text TEXT NOT NULL, event_id TEXT NOT NULL,
                action TEXT NOT NULL, created_at REAL NOT NULL,
                UNIQUE(sender,provider,event_id,action));
            CREATE TRIGGER IF NOT EXISTS consent_no_update BEFORE UPDATE ON consent_events
                BEGIN SELECT RAISE(ABORT, 'Consent evidence is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS consent_no_delete BEFORE DELETE ON consent_events
                BEGIN SELECT RAISE(ABORT, 'Consent evidence is append-only'); END;
            CREATE TABLE IF NOT EXISTS merchant_accounts (
                account_hash TEXT PRIMARY KEY, sender TEXT NOT NULL, provider TEXT NOT NULL);
        """)

    def state(self, sender):
        row = self.db.execute("SELECT state FROM onboarding WHERE sender=?", (sender,)).fetchone()
        return self.box.open(row[0]) if row else None

    def put(self, sender, state):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO onboarding VALUES (?,?)", (sender, self.box.seal(state)))

    def begin(self, sender, stage="store", **values):
        state = dict(stage=stage, nonce=secrets.token_hex(8), expires=time.time() + 600, tries=0, **values)
        self.put(sender, state)
        return state

    def consent(self, sender, provider, event_id, action):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO consent_events(sender,provider,version,text,event_id,action,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    sender,
                    provider,
                    CONSENT_VERSION,
                    CONSENT_TEXT.format(store=provider.title()),
                    event_id,
                    action,
                    time.time(),
                ),
            )

    def consented(self, sender, provider):
        row = self.db.execute(
            "SELECT action,version FROM consent_events WHERE sender=? AND provider=? ORDER BY id DESC LIMIT 1",
            (sender, provider),
        ).fetchone()
        return bool(row and row["action"] == "accepted" and row["version"] == CONSENT_VERSION)

    def connections(self, sender):
        return {
            r["provider"]: self.box.open(r["data"])
            for r in self.db.execute("SELECT * FROM store_connections WHERE sender=?", (sender,))
        }

    def save_connection(self, sender, provider, data):
        if not self.consented(sender, provider):
            raise DomainError("Please give permission before connecting this store.")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO store_connections VALUES (?,?,?)",
                (sender, provider, self.box.seal(data)),
            )

    def account_key(self, provider, phone):
        return hmac.new(self.box.identity_key, f"{provider}:{phone}".encode(), hashlib.sha256).hexdigest()

    def claim_account(self, sender, provider, phone, *, reserve=True):
        digest = self.account_key(provider, phone)
        row = self.db.execute(
            "SELECT sender FROM merchant_accounts WHERE account_hash=?", (digest,)
        ).fetchone()
        if row and row[0] != sender:
            raise DomainError(
                "This store account is already connected to another EasyDial chat. Use a different account or contact support."
            )
        if not reserve:
            return
        with self.db:
            self.db.execute(
                "DELETE FROM merchant_accounts WHERE sender=? AND provider=? AND account_hash!=?",
                (sender, provider, digest),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO merchant_accounts VALUES (?,?,?)", (digest, sender, provider)
            )

    def session_box(self, sender, provider):
        return self.box.derive(f"merchant-session:{sender}:{provider}")

    def session_path(self, sender, provider):
        if provider not in {"blinkit", "instamart"}:
            raise ValueError("Unknown provider")
        identity = hmac.new(self.box.identity_key, sender.encode(), hashlib.sha256).hexdigest()
        return str(self.root / identity / f"{provider}.enc")

    def forget(self, sender, provider):
        Path(self.session_path(sender, provider)).unlink(missing_ok=True)
        with self.db:
            self.db.execute("DELETE FROM store_connections WHERE sender=? AND provider=?", (sender, provider))
            self.db.execute("DELETE FROM merchant_accounts WHERE sender=? AND provider=?", (sender, provider))


class CustomerChats:
    """Separate worker objects and profiles; at most one customer's browsers open at a time."""

    def __init__(self, settings, accounts, master, payments):
        self.settings, self.accounts, self.master, self.payments = settings, accounts, master, payments
        self.chats = {}
        self.active = None

    async def close_customer(self, sender, *, save=True):
        chat = self.chats.pop(sender, None)
        if chat:
            try:
                if save:
                    for worker in chat.workers.workers:
                        if worker.provider.value in self.accounts.connections(sender):
                            await worker.browser.save()
            finally:
                await chat.workers.close()
        if self.active == sender:
            self.active = None

    async def get(self, sender):
        if self.active and self.active != sender:
            await self.close_customer(self.active)
        connections = self.accounts.connections(sender)
        # A revoked or out-of-date consent can never authorize browser reuse.
        connections = {p: c for p, c in connections.items() if self.accounts.consented(sender, p)}
        if not connections:
            raise DomainError("Connect a store first. Send Connect to get started.")
        if sender not in self.chats:
            config = self.settings.model_copy(
                update={
                    "blinkit_browser_profile": "",
                    "instamart_browser_profile": "",
                    "blinkit_storage_state": "",
                    "blinkit_verified_location": "",
                    "blinkit_verified_address": "",
                    "instamart_verified_location": "",
                    "instamart_verified_address": "",
                    "live_address_label": next(iter(connections.values()))["address"],
                }
            )
            workers = ProviderWorkers(config)
            workers.allowed_providers = {Provider(p) for p in connections}
            for worker in workers.workers:
                data = connections.get(worker.provider.value)
                if data:
                    worker.verified_location = data["location"]
                    worker.verified_address = data["address"]
                    worker.browser.storage_state = self.accounts.session_path(sender, worker.provider.value)
                    worker.browser.secret_box = self.accounts.session_box(sender, worker.provider.value)
            chat = ShoppingChat(config, workers, self.master)
            chat.payments = self.payments
            self.chats[sender] = chat
        self.active = sender
        return self.chats[sender]

    async def close(self):
        for sender in list(self.chats):
            await self.close_customer(sender)
