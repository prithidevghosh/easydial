# Current payment policy — 2026-11-21

This section supersedes the earlier broad-method policy below.

- Instamart: pay on delivery only.
- Blinkit: pay on delivery when enabled by the store, and UPI QR only.
- Old card/wallet/netbanking/pay-later button payloads are rejected server-side.
- Choosing cash is not an order. The bot checks the live cash screen and presents a separate versioned, expiring **Place order** button stating provider, payable amount and cash-at-delivery terms. Only that button authorizes the final merchant submission.
- Final submission revalidates cart, address, bill fingerprint and cash control. Cash attempts are reserved in private SQLite before clicking, with a unique customer/order key. Timeouts/crashes become unknown, never automatically retried. Confirmation is reported only with explicit merchant success text and an order identifier; otherwise status remains unknown.
- QR selection explicitly opens the UPI accordion and generates the provider QR. The amount/currency/reference/expiry are verified before the expiring link is returned. UPI PIN and approval stay in the user's app. Merchant confirmation after actual UPI payment remains unverified; do not claim a paid order from QR creation.

## Live controls verified

Instamart: Pay on Delivery → Cash/Pay on Delivery (selection only) → separate `pm_cod_pay_btn` labeled **Pay ₹156 with Cash**. The displayed payment amount is checked against the approved amount. No final click during testing.

Blinkit: Cards is an accordion and may start expanded. Close it if expanded, open the desired Cash/UPI accordion only if not already expanded, and wait for its contents. Cash final **Pay Now** lives in the parent page outside the payment iframe, and is returned only after Cash displays its exact-change message. No final click during testing.

Blinkit showed a real restriction: cash unavailable for orders below ₹50 in the test account. Above-threshold test basket enabled Cash. This threshold is observed information, not hardcoded eligibility; read the current store controls/restriction each time. Temporary test baskets were cleared. Actual QR generation returned a verified ₹133 amount and five-minute expiry without payment approval.

The pilot now uses `BLINKIT_BROWSER_HEADLESS=false` independently of the general browser setting. Headless Blinkit was blocked; visible Chrome worked. This is not a challenge bypass or evidence of headless production readiness.

## Validation boundary

No real order was placed. Submission, duplicate prevention and timeout/restart recovery are tested with fakes; real provider controls are verified only up to the irreversible click. The post-order DOM success detector still needs validation on an explicitly authorized real order. No automatic payment completion/status polling is claimed. The existing single-user store-session pilot limitation remains.

---

# Payment choice and request handoff — 2026-11-21

## Authorized boundary

The user authorized generating payment requests/links, but not authorizing a payment or placing an order. The later instruction requires showing the store's available payment choices rather than assuming UPI.

## Implemented journey

1. Review the basket and show the complete observed bill, including small-cart/minimum-order fee warnings. For Instamart, disclose any difference between listed charges and the merchant payable amount without naming it rounding. A readable bill can proceed to method selection only after confirming the merchant payable; the payment screen must independently display exactly that amount. Unknown charges or conflicting merchant totals remain blocked.
2. **Choose payment** rechecks the cart/address/bill and reads the real store payment screen. Present the visible categories in WhatsApp; do not invent absent UPI/card/wallet options.
3. The user's versioned selection chooses the next step:
   - **Blinkit UPI:** recheck again, open UPI and click only **Generate QR**. Read the provider's visible QR, decode it, require UPI/INR, a merchant/payment reference and an exact amount match. Read the displayed expiry.
   - **Cards, wallets, netbanking, pay later:** send the verified store checkout URL. This is a secure store handoff requiring the same account/login; it is NOT a newly generated transferable gateway session, and EasyDial does not collect credentials or click Pay. The chosen category may need selecting again in that browser.
   - **Cash / Pay on delivery:** show any observed restriction; otherwise explain that it requires order placement, which remains disabled.
4. UPI generates an expiring, opaque EasyDial HTTPS link showing the provider-issued QR, the bill and an **Open UPI app** link. Opening the page does not create another request or authorize payment. User authentication/approval belongs entirely to their UPI app.

## Live evidence

Blinkit exposed Wallets, cards, Netbanking, UPI, Cash and Pay Later. A temporary one-item milk basket had ₹30 items + ₹35 delivery + ₹13 handling + ₹25 small-cart fee = ₹103. Clicking Generate QR produced a visible PNG QR in the Zomato payment iframe and a five-minute countdown. Decoding yielded a `upi://pay` URI with INR amount 103 and merchant/payment reference fields. No Pay Now control, PIN or payment approval was used. The first test QR was allowed to expire before further testing.

Instamart's current account exposed Cards, Wallets, Netbanking and Pay on Delivery. Selecting PhonePe under wallets required account linking; no transferable PhonePe payment artifact was verified. A narrow mobile viewport showed the same categories. Do not claim an Instamart UPI QR generator is implemented. The wallet option hands off to `https://instamart.in/payment` for the user to continue securely.

Blinkit initially blocked the headless inspection with a human-verification challenge. User authorized a temporary headed recovery browser; the page subsequently became accessible. No challenge bypass was implemented. Headless production configuration remains enabled. A fresh headless payment check was blocked again after manual access succeeded; do NOT claim reliable headless payment generation for Blinkit. A headed automated QR attempt exposed an initially visible loading image; it was marked unknown and not automatically retried. The implementation now waits for the actual loaded PNG, verified by a delayed-image DOM regression. The interactive live audit generated and decoded a real QR, but the revised full automated request-to-public-link path still needs a successful live verification.

## Durable and security controls

`payment_requests` stores sender/order ownership, bill fingerprint, attempt status, token hash, artifact, timestamps and expiry. Reservation commits before clicking Generate QR. Crashes/timeouts become unknown; the code does not automatically retry or create another request. One existing attempt blocks another attempt and basket mutations for that draft pending manual reconciliation. This conservative pilot restriction remains even after link expiry: expiry alone is not proof that an order was never paid. Automated merchant payment/order-status reconciliation and cancellation remain unimplemented.

The public `/pay/{token}` page uses an unguessable token stored as a hash, five-minute-or-shorter provider expiry, no-store/no-referrer/noindex headers and restrictive CSP. It shows no customer address, phone number, saved card or browser credentials. No scripts, automatic redirects, Pay API or public generation endpoint. Invalid/expired tokens return 410. Access logging remains disabled.

Set private `WHATSAPP_PUBLIC_URL` to the HTTPS origin of the WhatsApp service. The development Cloudflare tunnel is temporary; deployment needs a stable HTTPS origin. This origin is a presentation wrapper around a verified store-issued QR, not EasyDial acting as a merchant or inventing a payment destination.

## Remaining limits

- Payment methods vary by account/cart/location. Only current visible options are offered.
- Link creation is not evidence of a paid or confirmed merchant order. Store order-status verification remains outstanding.
- UPI app handling from an iPhone/WhatsApp browser varies; the page also displays the original QR for scan/import. Real phone opening and paid-order completion have not been tested.
- A card/wallet/bank checkout URL does not transfer the automation browser's authenticated session. The customer must sign in to the same store account and check the final bill.
- Expired/unknown attempts require deliberate operator/provider reconciliation; do not delete attempt records to force a retry.


## 2026-11-21 — Confirmed amount to live method menu

Read-only live evidence: Instamart Payment Options and each inspected method page show `1 item. Total: ₹156`. Website cart and its own response report listed components/subTotal ₹156.30 and toPay ₹156.00. The UI now discloses both without manufacturing a rounding/discount line. `can_choose_payment` permits this fully readable Instamart bill to reach method inspection; the arithmetic reconciliation flag remains false. `open_options` requires an exact independent payment-screen amount match before returning a menu. Missing or different amounts fail without recording payment preparation. Revalidation of items, quantities, address and bill fingerprint remains mandatory. Payment authorization/order submission remain separate work.

Live Instamart options and inputs:

| Option | Observed screen | User input / next step |
| --- | --- | --- |
| Cards | Saved card and Add New Card | New card: number, expiry MM/YY, CVV, name, optional nickname; issuer authentication on provider screen. Never collect these in WhatsApp. |
| Wallets | PhonePe (Wallet/UPI/Cards), Amazon Pay, MobiKwik | Choose provider. PhonePe/MobiKwik show Link Account. Complete account login/linking and any OTP in provider UI; approve in provider/app as required. No OTP was requested during inspection. |
| Netbanking | Popular and full bank list | Choose bank, then authenticate and approve on bank-controlled page. No bank credentials entered during inspection. |
| Pay on Delivery | Cash/Pay on Delivery, pay cash at delivery | Explicit order-placement confirmation required; no online credentials. Do not promise doorstep UPI from this cash-only detail screen. |

No standalone Instamart UPI category appeared; PhonePe is listed under wallets with UPI in its label. No transferable payment link for that route has been verified.

Blinkit reinspection was blocked by its browser verification. Previously observed: UPI, Cards, Wallets, Netbanking, Cash, Pay Later. UPI QR requires approval in the customer's UPI app, not a PIN in WhatsApp. Other routes require respective provider authentication; current wallet/pay-later brands and eligibility are unverified. Do not present historical categories as fresh live verification.
