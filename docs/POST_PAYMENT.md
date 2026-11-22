# Order confirmation and payment reconciliation

Implemented 2026-11-21; updated 2026-11-22 for automatic confirmation and active-basket cleanup. Supersedes earlier generic confirmation-page checks.

## Customer journey

1. Review the actual items, charges (including small-cart fees), merchant total and address.
2. Confirm the total, then choose an eligible payment method. Instamart: cash on delivery. Blinkit: cash on delivery or merchant UPI QR.
3. Cash: a separate **Place order** button explicitly authorizes submission. Capture existing history IDs, re-open/revalidate checkout, persist a once-only reservation, then click the final merchant control once.
4. QR: capture history before generation, retain a durable payment request, show the existing expiring QR/UPI link and **Check status**. Approve payment in the UPI app. A typed/voice payment claim or Check status initiates verification. We do not assume a WhatsApp payment webhook for arbitrary merchant QR images.
5. Blinkit starts a durable read-only watcher five seconds after the payment menu is sent. Sending its QR or submitting cash starts a fresh five-second window. Each watcher makes at most three reads, with three seconds between completed reads. A menu-only observation is not a payment attempt and cannot report payment failure. Manual status checks can discover later confirmations after automatic checks end.
6. Confirm only a new matching merchant order. QR additionally needs merchant evidence of paid status. Show items, merchant order ID, amount, cash-due/payment-verified status and ETA only when exposed by the store.
7. On verified confirmation, atomically subtract the purchased quantities from the active EasyDial basket, create a new draft, retain the original receipt, and queue the status notification once. Newer drafts and merchant carts are not cleared. Restart recovers confirmations persisted before this transaction.
8. No result, unreadable history, ambiguous matches or timeout means **pending**, not failed. Show **Check status**, retain the record and do not resubmit or ask the user to pay again. Explicit matching cancelled/failed orders are reported separately; a cancellation does not prove a refund.

## Matching and source of truth

Private SQLite `order_checks` is keyed by sender and EasyDial order ID. It stores the immutable approved view, baseline history IDs, method, creation time, result and read count. Reading status uses this saved snapshot, not a later mutable basket. Restart preserves attempts; unknown legacy attempts without a baseline cannot be retrospectively treated as confirmed.

Require provider, new history ID, recent merchant timestamp, exact payable, matching address, all quantities and item identity. Blinkit uses exact normalized names/packs; Instamart history omits pack descriptions, so require exact catalog ID (the ID part of the selected SKU), name and quantity. Missing or conflicting identity stays pending. Multiple matches stay pending. Future timestamps are rejected; Blinkit minute precision permits a 60-second lower tolerance. Instamart's Unix-millisecond timestamps are converted to seconds. Baseline IDs exclude pre-existing orders even if their timestamps are close.

Readers navigate the authenticated websites and observe their own responses; they do not replay private APIs. Blinkit uses the account orders page and the detail links supplied in its history cards. Instamart uses its account button and the history response. Only necessary fields are retained; raw responses, phone numbers, payment instruments and delivery-agent details are not logged or committed.

## Verification evidence

- Read-only live history inspection parsed ten Blinkit and ten Instamart records. Corrected Instamart timestamp units and verified the corrected timestamps live.
- Blinkit historic cancelled orders can still say Paid Online, so payment text alone is insufficient. Historic card payments remain outside our selected QR/cash policy.
- Regression coverage: baseline exclusion, wrong items/address/amount/provider/method/time, missing/unpaid orders, cancellation, ambiguous matches, blocked browser, three-check exhaustion, late confirmation after restart, sender isolation, status buttons, and once-only cash submission after timeout.
- No order was placed and no payment authorized during these checks. A fresh, user-authorized cash order and a genuinely paid Blinkit QR have **not** been exercised end to end. Treat their final merchant layouts/statuses as needing live acceptance testing; unknown layouts remain pending.

## Operational limits / next work

This is still the single-worker, single-connected-account pilot. No unattended post-confirmation delivery/refund tracker, production multi-account sessions, is implemented here. Completed purchases now move out of the active basket while preserving receipts. A fresh production flow must verify catalog-ID stability across Instamart variants and complete addresses; inconsistent merchant fields should not be loosened just to obtain success. Existing browser challenge handling remains manual, with no bypass. Automatic retry of the final order/payment mutation is prohibited.

## Acceptance checklist

- [x] Durable baseline before a new attempt; explicit final cash approval.
- [x] Once-only cash click, including timeout/restart protection.
- [x] Status button after QR creation and pending verification.
- [x] Payment claims trigger verification only; English replies.
- [x] Bounded polling and pending on absence/uncertainty.
- [x] Live read-only history readers, synthetic confirmation/failure tests.
- [ ] User-authorized real cash order confirmed against history.
- [ ] User-paid real Blinkit QR confirmed against history.
- [ ] Production session isolation and ongoing delivery/refund tracking.
