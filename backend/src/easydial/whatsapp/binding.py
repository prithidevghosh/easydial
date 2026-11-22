"""Bind private state to its WhatsApp identity and encryption key, not host paths."""
import hashlib
import json


def fingerprint(settings, *, legacy=False, sender=None):
    if sender is None:
        # Multi-user state belongs to the business account and encryption key.
        # Single-user state also belongs to the one shared profile's owner.
        sender = "" if settings.whatsapp_multiuser else next(iter(settings.allowed_senders), "")
    values = [settings.whatsapp_phone_number_id, sender]
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
        # Migrate both earlier formats, including a legacy owner moved to a numbered slot.
        if not settings.whatsapp_multiuser or not any(
            row[0] == fingerprint(settings, sender=sender, legacy=with_paths)
            for sender in settings.allowed_senders.union([""])
            for with_paths in (False, True)
        ):
            raise RuntimeError("WhatsApp account/address configuration changed; use a new private database")
    with db:
        db.execute("INSERT OR REPLACE INTO binding VALUES (1,?)", (expected,))
