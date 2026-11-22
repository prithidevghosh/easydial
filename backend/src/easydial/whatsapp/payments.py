"""Durable, once-only request generation; no charge authorization or order submission."""

import asyncio
import hashlib
import json
import secrets
import time
from html import escape
from urllib.parse import urlsplit

from ..billing import fingerprint
from ..controller import DomainError


class PaymentRequests:
    def __init__(self, inbox, public_url):
        from .order_tracking import OrderMonitor

        self.monitor = OrderMonitor(inbox)
        self.inbox = inbox
        self.public_url = public_url.rstrip("/")
        self.inbox.db.execute("""CREATE TABLE IF NOT EXISTS payment_requests (
            id TEXT PRIMARY KEY, sender TEXT NOT NULL, order_id TEXT NOT NULL,
            bill_fingerprint TEXT NOT NULL, status TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
            artifact TEXT, created_at INTEGER NOT NULL, expires_at INTEGER)""")
        self.inbox.db.execute("""CREATE TABLE IF NOT EXISTS cash_orders (
            sender TEXT NOT NULL, order_id TEXT NOT NULL, status TEXT NOT NULL,
            fingerprint TEXT NOT NULL, merchant_order_id TEXT, created_at INTEGER,
            PRIMARY KEY(sender,order_id))""")
        self.inbox.db.execute("""CREATE TABLE IF NOT EXISTS automatic_order_checks (
            sender TEXT NOT NULL, order_id TEXT NOT NULL, due_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(sender,order_id))""")
        self.inbox.db.execute("""CREATE TABLE IF NOT EXISTS retried_order_attempts (
            id TEXT PRIMARY KEY, sender TEXT NOT NULL, order_id TEXT NOT NULL,
            payload TEXT NOT NULL, confirmed_at INTEGER NOT NULL)""")
        with self.inbox.db:
            self.inbox.db.execute("UPDATE cash_orders SET status='unknown' WHERE status='submitting'")
            self.inbox.db.execute("UPDATE payment_requests SET status='unknown' WHERE status='creating'")
            # Recover confirmations that were persisted before basket cleanup or notification.
            for row in self.inbox.db.execute("""SELECT o.sender,o.order_id,c.state FROM order_checks o
                    JOIN customers c ON c.sender=o.sender LEFT JOIN completed_orders d
                    ON d.sender=o.sender AND d.order_id=o.order_id
                    WHERE o.status='confirmed' AND d.order_id IS NULL""").fetchall():
                if json.loads(row["state"])["view"]["id"] == row["order_id"]:
                    self.inbox.db.execute("""INSERT INTO automatic_order_checks(sender,order_id,due_at) VALUES (?,?,?)
                        ON CONFLICT(sender,order_id) DO UPDATE SET done=0,due_at=excluded.due_at""",
                        (row["sender"], row["order_id"], time.time()))

    def proxy_recovery_allowed(self, sender):
        """Permit a new network journey only outside an active merchant checkout.

        Inspect durable evidence across all drafts: QR expiry, local revocation and
        exhausted polling do not establish that a merchant payment cannot settle.
        An explicitly ended old UPI journey may remain unresolved without pinning
        a different shopping draft forever. Its payment outcome is never changed.
        Unknown or unreadable evidence keeps the existing journey pinned.
        """
        from ..models import SessionView

        try:
            state = self.inbox.load(sender)
            if not isinstance(state, dict):
                return False
            view = SessionView.model_validate(state["view"])
            if view.phase == "checkout_pending" or view.payment_preparation:
                return False
            if any(state.get(key) for key in ("payment_menu", "cash_confirmation", "retry_confirmation")):
                return False
            attempts = {}
            for row in self.inbox.db.execute("SELECT * FROM order_checks WHERE sender=?", (sender,)).fetchall():
                snapshot = SessionView.model_validate_json(row["snapshot"])
                if snapshot.review is None:
                    return False
                if snapshot.review.provider.value != "blinkit":
                    attempts[row["order_id"]] = (False, False)
                    continue
                result = json.loads(row["result"] or "{}")
                terminal = (
                    row["status"] in {"confirmed", "failed"}
                    and result.get("status") == row["status"]
                    and bool(result.get("order", {}).get("order_id"))
                    and result.get("order", {}).get("status") in (
                        {"confirmed", "preparing", "on_way", "delivered"}
                        if row["status"] == "confirmed" else {"cancelled", "failed"}
                    )
                )
                ended = False
                if row["order_id"] != view.id and row["method"] == "upi" and row["status"] == "not_found":
                    requests = self.inbox.db.execute(
                        "SELECT status FROM payment_requests WHERE sender=? AND order_id=?",
                        (sender, row["order_id"]),
                    ).fetchall()
                    cash = self.inbox.db.execute(
                        "SELECT 1 FROM cash_orders WHERE sender=? AND order_id=?",
                        (sender, row["order_id"]),
                    ).fetchone()
                    checks = self.inbox.db.execute(
                        "SELECT done FROM automatic_order_checks WHERE sender=? AND order_id=?",
                        (sender, row["order_id"]),
                    ).fetchone()
                    # stop_checkout revokes local artifacts and stops monitoring.
                    # This ends the network journey, not the merchant payment.
                    ended = bool(requests and all(item["status"] == "revoked" for item in requests)
                                 and cash is None and checks and checks["done"] == 1)
                attempts[row["order_id"]] = (True, terminal or ended)
                if not (terminal or ended) and not (
                    row["method"] == "observe" and row["status"] in {"prepared", "pending", "not_found"}
                ):
                    return False
            for table in ("payment_requests", "cash_orders"):
                for row in self.inbox.db.execute(
                    f"SELECT order_id FROM {table} WHERE sender=?", (sender,)
                ).fetchall():
                    evidence = attempts.get(row["order_id"])
                    # Missing monitor evidence also means the provider/outcome is unknown.
                    if evidence is None or (evidence[0] and not evidence[1]):
                        return False
            return True
        except Exception:
            return False

    def completion(self, sender, state):
        from ..models import SessionView
        from .order_completion import completed_state

        row = self.monitor.get(sender, state["view"]["id"])
        if not row or row["status"] != "confirmed":
            return state, None
        result = json.loads(row["result"] or "{}")
        if not result.get("order", {}).get("order_id"):
            return state, None
        purchased = SessionView.model_validate_json(row["snapshot"])
        return completed_state(state, purchased), {
            "order_id": purchased.id, "merchant_order_id": result["order"]["order_id"],
        }

    async def watch_menu(self, sender, view, workers):
        if not self.existing(sender, view.id):
            await self.monitor.capture(sender, view, "observe", workers)
            self.monitor.activate(sender, view.id)

    def schedule_check(self, sender, order_id, *, reset=False):
        with self.inbox.db:
            self.inbox.db.execute(
                "INSERT OR IGNORE INTO automatic_order_checks(sender,order_id,due_at) VALUES (?,?,?)",
                (sender, order_id, time.time() + 5),
            )
            if reset:
                self.inbox.db.execute("UPDATE automatic_order_checks SET attempts=0,done=0,due_at=? WHERE sender=? AND order_id=?",
                                      (time.time() + 5, sender, order_id))

    def revoke_customer(self, sender):
        """Expire EasyDial-hosted artifacts; never claim to cancel merchant requests/orders."""
        with self.inbox.db:
            self.inbox.db.execute("UPDATE automatic_order_checks SET done=1 WHERE sender=?", (sender,))
            self.inbox.db.execute(
                "UPDATE payment_requests SET status='revoked',artifact=NULL,expires_at=0 WHERE sender=?",
                (sender,),
            )

    def existing(self, sender, order_id):
        cash = self.inbox.db.execute(
            "SELECT * FROM cash_orders WHERE sender=? AND order_id=?", (sender, order_id)
        ).fetchone()
        if cash:
            return cash
        return self.inbox.db.execute(
            "SELECT * FROM payment_requests WHERE sender=? AND order_id=? ORDER BY created_at DESC LIMIT 1",
            (sender, order_id),
        ).fetchone()

    def stop_checkout(self, sender, order_id):
        """Stop local QR access/checks; this does not revoke a merchant payment."""
        with self.inbox.db:
            self.inbox.db.execute(
                "UPDATE payment_requests SET status='revoked',artifact=NULL,expires_at=0 WHERE sender=? AND order_id=?",
                (sender, order_id),
            )
            self.inbox.db.execute(
                "UPDATE automatic_order_checks SET done=1 WHERE sender=? AND order_id=?", (sender, order_id),
            )

    def release_for_confirmed_retry(self, sender, order_id):
        """Archive an unresolved attempt only after explicit retry confirmation."""
        db = self.inbox.db
        with db:
            row = self.monitor.get(sender, order_id)
            if not row or row["status"] != "not_found":
                raise DomainError("This attempt is not ready to retry. Check its latest status.")
            payload = {}
            for table in ("order_checks", "cash_orders", "payment_requests", "automatic_order_checks"):
                payload[table] = [dict(r) for r in db.execute(
                    f"SELECT * FROM {table} WHERE sender=? AND order_id=?", (sender, order_id)
                ).fetchall()]
            encoded = self.inbox.secret_box.seal(payload) if self.inbox.secret_box else json.dumps(payload)
            db.execute("INSERT INTO retried_order_attempts VALUES (?,?,?,?,?)",
                       (secrets.token_hex(16), sender, order_id, encoded, int(time.time())))
            for table in payload:
                db.execute(f"DELETE FROM {table} WHERE sender=? AND order_id=?", (sender, order_id))

    async def create(self, sender, view, review, workers):
        parsed = urlsplit(self.public_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise DomainError("Payment-link hosting is not configured yet.")
        previous = self.existing(sender, view.id)
        if previous:
            raise DomainError(
                "A payment request already exists. Check its status in the store before creating another."
            )
        await self.monitor.capture(sender, view, "upi", workers)
        request_id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        with self.inbox.db:
            self.inbox.db.execute(
                "INSERT INTO payment_requests VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    sender,
                    view.id,
                    fingerprint(view, review),
                    "creating",
                    hashlib.sha256(token.encode()).hexdigest(),
                    None,
                    int(time.time()),
                    None,
                ),
            )
        try:
            self.monitor.activate(sender, view.id)
            artifact = await workers.generate_upi(view, review)
            artifact["provider"] = review.provider.value
            # Snapshot the complete approved bill without the customer's address or account details.
            artifact["bill_lines"] = review.bill_lines
            expiry = int(time.time()) + artifact.pop("ttl_seconds")
            with self.inbox.db:
                self.inbox.db.execute(
                    "UPDATE payment_requests SET status='ready',artifact=?,expires_at=? WHERE id=?",
                    (json.dumps(artifact), expiry, request_id),
                )
            return {
                "id": request_id,
                "url": f"{self.public_url}/pay/{token}",
                "expires_at": expiry,
                "total_paise": review.total_paise,
            }
        except BaseException:
            with self.inbox.db:
                self.inbox.db.execute(
                    "UPDATE payment_requests SET status='unknown' WHERE id=?", (request_id,)
                )
            raise

    async def place_cash(self, sender, view, review, workers):
        if self.existing(sender, view.id):
            raise DomainError("An order or payment attempt already exists. I won’t submit it twice.")
        await self.monitor.capture(sender, view, "cash", workers)
        reserved = False

        def reserve():
            nonlocal reserved
            with self.inbox.db:
                self.inbox.db.execute(
                    "INSERT INTO cash_orders VALUES (?,?,?,?,?,?)",
                    (sender, view.id, "submitting", fingerprint(view, review), None, int(time.time())),
                )
            self.monitor.activate(sender, view.id)
            reserved = True
            self.schedule_check(sender, view.id, reset=True)

        try:
            result = await workers.submit_cash(view, review, reserve)
            if not reserved:
                raise DomainError("The order was not submitted.")
        except BaseException as error:
            if reserved:
                with self.inbox.db:
                    self.inbox.db.execute(
                        "UPDATE cash_orders SET status='unknown' WHERE sender=? AND order_id=?",
                        (sender, view.id),
                    )
                if not isinstance(error, Exception):
                    raise
            else:
                raise
        await asyncio.sleep(5)
        result = await self.monitor.check(sender, view.id, workers)
        with self.inbox.db:
            self.inbox.db.execute(
                "UPDATE cash_orders SET status=?,merchant_order_id=? WHERE sender=? AND order_id=?",
                (result["status"], result.get("order", {}).get("order_id"), sender, view.id),
            )
        return result

    def qr_artifact(self, sender, request_id):
        row = self.inbox.db.execute(
            "SELECT * FROM payment_requests WHERE id=? AND sender=?", (request_id, sender)
        ).fetchone()
        if not row or row["status"] != "ready" or not row["expires_at"] or row["expires_at"] <= time.time():
            raise DomainError(
                "This payment QR has expired or is unavailable. Check order status before paying again."
            )
        attempt = self.monitor.get(sender, row["order_id"])
        if attempt and attempt["status"] in {"confirmed", "failed"}:
            raise DomainError("This payment attempt has already finished. Check your order status.")
        return json.loads(row["artifact"]) | {"expires_at": row["expires_at"]}

    def lookup(self, token):
        if not 20 <= len(token) <= 100:
            return None
        row = self.inbox.db.execute(
            "SELECT * FROM payment_requests WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)
        ).fetchone()
        if not row or row["status"] != "ready" or not row["expires_at"] or row["expires_at"] <= time.time():
            return None
        return row


def page_html(row):
    data = json.loads(row["artifact"])
    amount = f"₹{data['total_paise'] / 100:.2f}"
    bill = "".join(
        f"<tr><td>{escape(x['label'])}</td><td>{escape(x['value'])}</td></tr>" for x in data["bill_lines"]
    )
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<title>EasyDial · {amount}</title><style>
body{{background:#f5f7f4;color:#20382e;font:17px system-ui;margin:0;padding:32px 20px}}
main{{max-width:420px;margin:auto;background:white;border-radius:24px;padding:28px}}
h1{{font-size:32px;margin:8px 0}}p{{line-height:1.5;color:#52645b}}img{{display:block;width:256px;max-width:100%;margin:24px auto}}
a{{display:block;background:#245c44;color:white;text-align:center;padding:16px;border-radius:16px;text-decoration:none;font-weight:600}}
table{{width:100%;font-size:15px;border-collapse:collapse}}td{{padding:8px 0;border-bottom:1px solid #e8eee8}}td:last-child{{text-align:right}}
small{{display:block;margin-top:20px;color:#52645b;line-height:1.5}}
</style><main><p>EasyDial · {escape(data["provider"].title())}</p><h1>{amount}</h1>
<table aria-label="Store bill">{bill}</table>
<img src="{escape(data["qr_data"], quote=True)}" alt="Store-issued UPI payment QR">
<a href="{escape(data["upi_uri"], quote=True)}">Open UPI app</a>
<p>Or scan this QR using your UPI app. It expires shortly.</p>
<small>You approve payment only inside your UPI app. This page does not confirm payment or an order.</small></main></html>'''
