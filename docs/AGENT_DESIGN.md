> Current implementation note (2026-11-21): both commerce workers use Playwright (Blinkit and instamart.in); MCP is superseded. The backend validates typed cart journeys through local Pending. Voice and paid-order stages below remain unverified/proposed. See BROWSER_VERIFICATION.md.

# Proposed agent behaviour

Updated: 2026-11-21. Design proposal only; no implementation. This preserves the requested OpenAI master plus two provider agents while defining the controls needed around them.

## Roles

- **Master:** conversation, language preference, ambiguity resolution, canonical basket, comparison explanation, and approval state.
- **Instamart worker:** only Instamart tools and normalized provider evidence.
- **Blinkit worker:** only isolated Blinkit tools and normalized provider evidence.
- **Application controller:** deterministic validation, session isolation, basket versions, tool permissions and checkout locking. These responsibilities cannot rest on a prompt alone.

Workers should return bounded structured results to the master rather than independently talking to the user or choosing what to buy. Search workers may run concurrently across providers once comparison use is cleared. Calls within the Blinkit browser must be serialized; concurrent searches share mutable page state. Mutations are serialized per user/provider.

## Basket and offer records

An intended item should retain the original phrase, normalized product, brand constraint, variant, requested amount/unit, pack count, substitution policy, and clarification status. Keep requested volume/weight separate from the count of saleable packs: two 500 ml packs are not two litres.

A provider offer should retain provider/product/SKU IDs, display name, pack size, pack count, item price, quantity cap, availability (`available`, `unavailable`, `unknown`), address context, retrieval timestamp, and evidence reference. Unknown fields remain unknown.

A basket stores a monotonically increasing version, provider choice, selected address, offers, quantities, bill breakdown, total, payment method and approval snapshot. External carts are separate from the user's intended basket.

## Proposed state flow

`LISTENING → INTERPRETING → CLARIFYING or SEARCHING → OPTIONS → EDITING → REVIEW → AWAITING_CONFIRMATION → CHECKOUT → PAYMENT_PENDING or ORDER_CONFIRMED`

Every stage can enter a recoverable error state. Model output suggests transitions; application validation authorizes them.

1. A correction during search increments the version; discard stale search results.
2. An edit after review invalidates old prices and approval.
3. “Yes” approves only the most recent unambiguous summary, not a previous version.
4. Before placement, re-read provider state and compare items, amount and address with the approved snapshot. Material changes require a fresh summary.
5. On timeout after a purchase call, reconcile status/history before retrying. Persist operation and provider IDs; an app lock alone does not establish provider idempotency.
6. Once payment starts, do not silently revise the in-flight order. Resolve that payment/order state and explain what can still change.

## Required interpretations

| User request | Expected behaviour |
| --- | --- |
| “Add bread too” | Preserve existing requested items and add a clarified bread variant |
| “Make that three” | Change the referenced item's quantity; clarify if reference is ambiguous |
| “Replace Amul with Mother Dairy” | Search replacement and remove the old selection only as part of verified reconciliation |
| “Which is cheaper?” | Answer comparison; do not mutate either cart merely because the question was asked |
| “Same size but cheaper” | Restrict comparison to equivalent size/variant or explicitly disclose differences |
| “Don't order yet” | Clear approval and stop before purchase |
| “Remove the milk” with multiple milk items | Ask which item rather than guessing |
| New address | Invalidate location-dependent stock, prices, ETA and review |

## Comparison correctness

Compare identical products first: brand, formulation, pack size and count. Normalize unit prices only where meaningful. Alternative brands or sizes are substitutions, not exact matches. Different products being cheapest does not make them equivalent.

Separate item subtotal from final payable total, including returned delivery/handling/small-cart fees and applied discounts. Never claim the cheapest total when one provider's fees are unknown. Do not assume household memberships or coupons transfer across platforms.

Do not fill both real carts merely to produce a comparison without explaining that action. A pre-existing provider cart may contain the user's own items. There is no established read-only quote endpoint for a hypothetical basket. Preliminary item comparison and final chosen-provider cart total should be distinct. If exact two-cart comparison is later approved and implemented, design explicit reconciliation of existing carts; blind snapshot restoration can overwrite concurrent user changes.

A provider outage means “could not verify,” not “out of stock.” Do not recommend a partial basket as cheapest without disclosing missing items. Initially prefer one provider per basket; split ordering adds separate fees and partial failure cases.

## Voice and payment boundaries

The app can reduce browsing friction but cannot assume onboarding and payment disappear. Swiggy authorization uses its own UI; UPI approval remains with the user. A QR code on the same phone may be awkward. Present available payment methods accessibly, keep OTPs/tokens out of model logs, and never ask for a payment PIN.

Treat catalogue descriptions and tool text as data, not instructions that can alter checkout controls. Do not retain sensitive recordings by default merely to make debugging convenient. Evaluation recordings should have a stated consent/retention plan.

## Failure scenarios to verify later

Stale search after a correction; ambiguous units; pack-size mismatch; stock removal or quantity cap; cart modified in the provider app; changed address; token expiry; tool error returned as text; browser selector failure; duplicate click/retry; payment failure; successful payment without confirmation; multi-store partial success; and user interruption immediately before checkout.
