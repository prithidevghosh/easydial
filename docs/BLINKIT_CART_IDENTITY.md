# Blinkit cart identity verification

## Incident (22 November 2026)

AWS logs showed a post-write `CartConflict` at 06:51:22 UTC, a cart-clear ownership failure at 06:53:35, and pre-write conflicts at 07:03–07:04. Local merchant inspection reproduced the discrepancy: catalogue product 68482 included `(Godihittu)`, while its exact-ID product detail page and cart used the shorter name. Exact name comparisons therefore rejected the same 1 kg product.

## Recovery

Keep the catalogue display name. When an unmatched cart row has the same pack, follow the verified catalogue card to its exact product-ID page in a temporary tab. Verify its URL, visible primary pack and breadcrumb title, then persist that exact alternate name in `Offer.cart_name`. No fuzzy matching or removal of parenthetical phrases. Unknown products, pack changes, duplicate aliases and quantity/ownership changes remain blocked.

Use this evidence consistently for review, reconciliation, quantity controls, payment freshness, checkpoint restoration, clearing and order-history confirmation. Ownership quantities are preserved when labels are translated; alias/signature updates are atomic. Existing matching carts require no extra detail-page read.

An observed exact-ID product page may render Blinkit's explicit not-found screen. Reload that same observed URL once; never repeat ADD or payment actions. Persistent failure does not create an alias.

The repeated local replacement tests also exposed HTTP 429 from the merchant search endpoint, rendering a blank catalogue. Read-only probes succeeded before throttling. This is distinct from cart identity; no speculative catalogue retry changes are included.

## Validation

Regression coverage includes legacy checkpoints, partial first writes, replacement, restart, clearing, payment/order identity, alias collisions, changed packs/quantities, wrong product URLs, not-found retry limits and temporary-tab cleanup. Local live testing uses no proxy and never submits an order/payment.

The paused Instamart implementation is excluded from this change. Linux-emulation timing failures in two pre-existing tests were reproduced against deployed baseline 077e3ad; their assertions were not weakened.

Release-candidate verification: 526 backend tests passed (89 optional browser tests skipped); 111 tests passed with Chrome enabled. The 19 identity tests also passed in Linux Chrome (within a 26-test run that included seven subsequently removed speculative search-reload tests). The final committed source was checked independently of the paused Instamart edits.

Live candidate: 1 atta + 3 Diet Cokes reviewed at ₹228; replacing atta with the exact Daawat rice product retained 3 Diet Cokes and reviewed at ₹263. Both unchanged-cart checks passed. An additional payment-options probe encountered merchant HTTP 429; no payment or order was submitted.

After a quiet period, a fresh browser restored the verified rice checkpoint, revalidated ₹263, cleared both product lines and verified an empty cart. All local test browsers were closed.
