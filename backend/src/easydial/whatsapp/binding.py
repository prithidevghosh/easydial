"""Bind private state to its WhatsApp identity and encryption key, not host paths."""
import hashlib
import json


def fingerprint(settings, *, legacy=False):
    values = [settings.whatsapp_phone_number_id, settings.whatsapp_allowed_sender]
    if legacy or not settings.whatsapp_multiuser:
        values += [settings.live_address_label, settings.blinkit_browser_profile,
                   settings.instamart_browser_profile]
    if settings.whatsapp_multiuser:
        values += ["multiuser-v1", hashlib.sha256(settings.customer_session_key.encode()).hexdigest()]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def bind(db, settings):
    expected = fingerprint(settings)
    row = db.execute("SELECT fingerprint FROM binding WHERE id=1").fetchone()
    if row and row[0] != expected:
        if not settings.whatsapp_multiuser or row[0] != fingerprint(settings, legacy=True):
            raise RuntimeError("WhatsApp account/address configuration changed; use a new private database")
    with db:
        db.execute("INSERT OR REPLACE INTO binding VALUES (1,?)", (expected,))
