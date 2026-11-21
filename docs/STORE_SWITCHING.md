# Explicit store switching

## Root cause

The retained transcript correctly requested moving the product from Instamart to Blinkit. ChatIntent had no provider target. Search queried both providers, then filtered all choices to the current basket's store. Separately, model clarification promised Blinkit. The resulting cards and promise contradicted each other.

## Contract

- `target_provider` scopes provider discovery and visible choices. Never silently substitute another store when the target is unavailable.
- `switch_store` is explicit user intent, separate from comparing prices or adding an unrelated product. Provider context is supplied to the master.
- Search replies describe harness results, not unexecuted model promises.
- A one-product move stages source lines and destination in private conversational state. Search preserves the source basket and asks for a replacement choice. Follow-up searches during a pending move retain its destination unless another store is explicitly requested.
- Selecting a destination checks that the staged source still matches, refreshes stale prices, validates the destination draft, then clears the synced source cart using existing ownership checks. Only confirmed clearing commits the new draft and clears the old checkpoint. Target cart synchronization remains the next Review basket action.
- Pending store moves block review/checkout/payment of the old store. No fallback to source offers, even when destination search fails.
- Existing order/payment attempts block switching. Unknown/externally modified source carts fail closed. Multi-item moves currently clarify rather than implicitly removing unrelated products.

## Verification

Execution regressions cover destination-only calls/cards, source unchanged before selection, source clear then new-store review, unavailable target, failed clear, review/checkout during a pending move, and payment-attempt protection. The real model passed all 27 intent cases including the reported transcript, a Hinglish store move and a comparison-only request. No live cart clearing or placement was performed as part of these checks.
