# Payment choice and request handoff — 2026-11-21

## Authorized boundary

The user authorized generating payment requests/links, but not authorizing a payment or placing an order. The later instruction requires showing the store's available payment choices rather than assuming UPI.

## Implemented journey

1. Review the basket and show the complete reconciled bill, including small-cart/minimum-order fee warnings.
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
