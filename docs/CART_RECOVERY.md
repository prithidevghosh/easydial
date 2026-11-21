# Recovering an existing cart without clearing it

Implemented 2026-11-21 for WhatsApp review. A legacy basket may exist at the store without a verified EasyDial ownership checkpoint. Persisting the requested draft does not prove that the live basket matches it.

## Customer journey

1. Send `review` or tap Review basket.
2. A cart ownership conflict produces a read-only comparison: items/pack counts currently at the store versus the requested basket. Neither basket is erased.
3. When every existing cart item matches a unique known product/pack, offer **Update cart**. The text explicitly describes changing the store basket to the requested quantities, including additions and removals. The user can instead edit their draft and review again.
4. The button is bound to a random proposal ID, basket version and five-minute expiry. A plain yes or model-generated intent cannot authorize adoption. The proposal is private durable conversation state.
5. Re-read the store cart and exact address under the provider lock. A changed cart/address rejects adoption without writes. Adopt the approved observed cart checkpoint, then run the existing guarded synchronization and bill readback.
6. Save the verified new checkpoint and show the full live fee table. Checkout/payment approval remains separate; no payment or order is initiated by recovery.

## Guardrails and limits

- A changed draft invalidates the old button through the version check. A newer comparison replaces the old proposal token. Successful adoption consumes the proposal.
- If another change occurs between adoption and sync, the regular cart/address checks still stop unsafe writes.
- Unknown products or ambiguous product/pack mappings are displayed but cannot be adopted or automatically removed. The comparison must be resolved with verified product records; arbitrary cart deletion is not a fallback.
- Wrong delivery addresses fail before the comparison is offered.
- Checkpoints survive restart. A crash during browser mutation still requires readback; database state is not proof of a completed external write.
- Empty-draft clearing and the paused iOS API retain their existing guards; this recovery UI targets nonempty WhatsApp baskets.
- Missing in-memory search queries now fall back to the saved product name. Existing exact product-ID and pack checks still apply; no substituted product is added silently.

## Verification

Tests exercise read-only preview, explicit adoption in a fresh worker, changed cart/address rejection, unmapped products, expired/wrong/replayed confirmation IDs, saved checkpoints and subsequent live-bill review. See PROGRESS.md for actual website observations and final validation count.
