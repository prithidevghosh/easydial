"""Durable, once-only request generation; no charge authorization or order submission."""

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
        self.inbox = inbox
        self.public_url = public_url.rstrip("/")
        self.inbox.db.execute("""CREATE TABLE IF NOT EXISTS payment_requests (
            id TEXT PRIMARY KEY, sender TEXT NOT NULL, order_id TEXT NOT NULL,
            bill_fingerprint TEXT NOT NULL, status TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
            artifact TEXT, created_at INTEGER NOT NULL, expires_at INTEGER)""")
        with self.inbox.db:
            self.inbox.db.execute("UPDATE payment_requests SET status='unknown' WHERE status='creating'")

    def existing(self, sender, order_id):
        return self.inbox.db.execute(
            "SELECT * FROM payment_requests WHERE sender=? AND order_id=? ORDER BY created_at DESC LIMIT 1",
            (sender, order_id),
        ).fetchone()

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
