# Database-backed order drafts

Implemented 2026-11-21. These are shopping drafts/preparation records, not placed merchant orders. Payment remains disabled.

## Source of truth

The current single-worker pilot uses the existing private SQLite database configured by `WHATSAPP_DATABASE`. No external Redis/Mongo account is required. The worker already queues inbound messages durably and loads customer state for each turn; the order itself now has explicit database records.

- `order_drafts`: composite key `(sender, order_id)`, draft version, phase, JSON snapshot, last inbound event ID and update time. Snapshot includes basket lines, provider, delivery context, reviewed bill and preparation approval.
- `order_revisions`: one immutable snapshot per finished inbound event, with sender, order ID, version, event outcome and time. Version can remain unchanged for a read or rejected action; event ID identifies the revision uniquely.
- `customers`: conversation state and a compatibility copy of the view. `Inbox.load()` replaces that view with `order_drafts` before the runtime handles a request. Missing order records fail closed; conversation copies are not a fallback for checkout.
- `events` and `outbox`: durable incoming work and outgoing responses, as before.

## Write/read sequence

1. Queue and deduplicate the inbound WhatsApp event.
2. Load the sender's conversation and authoritative order draft from the database.
3. Interpret the request against those stored basket lines; additions preserve existing products.
4. On review, synchronize the permitted store basket and read the real bill. On amount approval, recheck the live cart/address/charges without mutation. Stored prices alone never authorize payment.
5. Atomically commit the draft, revision, conversation, event result and outgoing replies. Send replies only after commit.

An older draft version cannot replace a newer one. Finishing the same event again does not add duplicate revisions or replies. A transaction failure rolls back the entire database change. Existing customer snapshots migrate idempotently on startup; migration never touches store carts or changes item quantities.

## Operational limits

This remains a single-worker, single-connected-customer pilot with a database file lock and configured sender allowlist. Sender-scoped database records do not provide independent Blinkit/Instamart authentication for additional users. Browser changes cannot share a database transaction; uncertain writes still require live reconciliation, never blind replay.

Persist the database and browser profiles on a private durable volume when deploying. Revision snapshots contain personal shopping/address data, so retain them under the same access controls as the existing private database; a production retention/deletion policy remains to be implemented. Redis could later hold disposable caching/queue coordination, but should not replace the durable order record. Any database migration must preserve transactional writes, sender ownership, revisions and idempotency.

## Verification

Regression tests cover sender isolation, authoritative draft reads over stale conversation copies, restart persistence, legacy migration, rejection of older versions, duplicate completion and atomic rollback of draft/revision/reply writes. Existing WhatsApp approval tests exercise checkout through database-loaded state; live merchant bills are still revalidated.
