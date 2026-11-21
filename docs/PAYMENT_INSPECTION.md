# Payment inspection and implementation — 2026-11-21

## Authorization and result

Inspected the logged-in websites up to payment choices. No Pay button, Generate QR button, payment-on-delivery submission or payment request was triggered. No order was placed. The temporary Blinkit test basket was removed afterwards. The user's existing Instamart basket was preserved.

## Observed live evidence

Blinkit: one Heritage Daily Health Toned Milk 500 ml pack. Opening Proceed To Pay reached `/checkout`, with payment choices in a Zomato zpaykit iframe. UPI exposes “Scan QR to pay” and “Generate QR”. QR creation was not attempted. Cards, wallets, netbanking, cash and pay later were also listed; listing is not proof that every method is usable. This particular cart displayed a cash-on-delivery restriction below ₹50; that is not a small-cart fee waiver threshold.

| Blinkit charge | Amount |
| --- | ---: |
| Items total | ₹30.00 |
| Delivery charge | ₹35.00 |
| Handling charge | ₹13.00 |
| Small cart charge | ₹25.00 |
| Total payable | ₹103.00 |

Instamart: the existing basket reached `/payment` using Proceed to Pay. Choices included cards, wallets, netbanking and Pay on Delivery. Expanding wallets showed PhonePe (Wallet/UPI/Cards), Amazon Pay and MobiKwik. No wallet was selected and no Pay control was clicked. A transferable payment URL or UPI intent is therefore **not verified**.

| Instamart charge | Amount |
| --- | ---: |
| Item Total | ₹332.00 |
| Handling Fee | ₹12.00 |
| Delivery Partner Fee | ₹0.00 (FREE) |
| To Pay | ₹344.00 |

Struck-out earlier prices were ignored. Fixed the DOM parser to retain FREE delivery rows instead of omitting them. These prices and methods are inspection snapshots, not guarantees for future carts, addresses or accounts.

## Implemented behavior

- WhatsApp review includes every parsed current charge in a monospace table, plus the payable total and delivery address. WhatsApp does not render ordinary Markdown tables reliably.
- Small-cart, small-order and minimum-order fee labels produce an explicit warning when charged. Never invent a waiver threshold or suggest buying more without verified evidence.
- Approval is withheld if the detailed bill cannot be parsed and reconciled to the total. Unknown charges remain visibly unverified.
- “Approve amount” re-reads the actual cart and address without repairing or mutating them. Changes to items, address, subtotal, fee lines or total invalidate the previous bill. A changed bill requires another approval.
- Reviews expire after five minutes. Basket edits clear prior approval. A failed recheck revokes any earlier preparation approval.
- Approval is a private, durable **preparation-only** record. It does not create a payment request or authorize a charge. The response explicitly states Pending and that payment handoff remains disabled.
- Successful cart reviews persist ownership checkpoints in the private WhatsApp database. On restart, the worker restores expected ownership and still compares the live basket before acting. Unknown/external baskets remain protected. An interrupted write is not assumed to have succeeded.
- Headless mode is configurable with `BROWSER_HEADLESS=true`; `BROWSER_CHANNEL=chrome` retains Chrome. Default remains headed. Both saved profiles passed authenticated read-only address/search/cart checks in headless Chrome on this Mac. Linux hosting, deployment IP behavior and headless cart mutations remain unverified.

## Next implementation gates

1. Deploy a pinned browser/FFmpeg runtime with persistent private profiles/database and stable HTTPS; onboard accounts on that host. Verify headed recovery, headless reads and authorized cart mutation tests.
2. With a specific approved basket and amount, inspect creation of a Blinkit QR or Instamart PhonePe payment attempt. Establish whether it can be handed to the customer's phone without exporting browser cookies or private session URLs. Never invent a merchant payment link.
3. Build a durable payment-attempt state machine: preparing, awaiting customer authorization, checking provider, confirmed, failed, expired, or unknown. Block duplicate attempts and reconcile uncertainty before retrying.
4. Revalidate a fresh fee table immediately before initiating an authorized attempt. Re-approve changed fees/totals. User enters UPI PIN or bank/card authorization only in their payment provider.
5. Confirm success from the merchant's actual order record and payment status. Capture merchant order ID, total and fulfillment status; a WhatsApp reply, screenshot or gateway redirect is insufficient evidence alone.
6. Run a real paid-order test only with explicit purchase authorization. Current no-order instruction remains in effect.

## Verification scope

Local tests cover observed fee tables, FREE and discount rows, unknown bills, fee changes requiring new approval, stale actions, approval revocation, read-only revalidation and restart ownership protection. Live payment completion is deliberately not claimed. See PROGRESS.md for final test results and runtime state.
