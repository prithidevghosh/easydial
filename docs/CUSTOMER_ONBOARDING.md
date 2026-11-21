# EasyDial customer onboarding

Implemented 2026-11-21. This is a controlled, single-process multi-customer pilot, not an AWS production release.

## Customer journey

1. Scan the shared QR or open the WhatsApp link; send the prefilled **Start** message.
2. Choose **Blinkit** or **Instamart**.
3. Read the store-specific permission notice. Only **I agree** (button or that exact typed phrase) accepts. **Not now** declines; ordinary “yes”, a phone number or an OTP is not consent.
4. Enter the store account's Indian mobile number. EasyDial requests an OTP only after consent is recorded. Reconnection starts a fresh store session; it does not silently reuse a prior account.
5. Type the OTP in WhatsApp. Audio is not transcribed during onboarding. Never send credentials in a developer/support chat.
6. Choose a numbered saved address read from the authenticated store UI. A second store requires explicit confirmation that both addresses represent the same physical destination.
7. Send normal shopping messages. Only connected stores are queried. **Connect** starts another connection; **Disconnect blinkit** or **Disconnect instamart** withdraws permission and removes that store's saved login. Existing merchant orders are not cancelled.

If there are no saved addresses, add one in the merchant app and reconnect. Address choices are the saved addresses exposed by the website's current selector; no address is silently selected. The Blinkit entry locality is temporary, used to expose login; it is replaced by the selected saved address before shopping is enabled. Full cart delivery-address verification remains mandatory before a purchase.

## Shared QR

The same QR works for every admitted test customer. It contains only `https://wa.me/<business-number>?text=Start`, not a customer ID or authentication secret. Opening it does not send a message or grant consent.

- `GET /join` redirects to the configured WhatsApp number.
- `GET /join/qr.png` returns its PNG QR.
- Pilot asset: `docs/assets/easydial-join-pilot.png`.
- `WHATSAPP_BUSINESS_NUMBER` is the public telephone number, NOT `WHATSAPP_PHONE_NUMBER_ID`.
- The current asset uses Meta's existing test number at the user's request. Public reachability/recipient admission is still governed by Meta's test setup. A shareable QR does not turn that number into a production WhatsApp account.

## Setup

From the repository root:

```sh
uv --directory backend sync
uv --directory backend run python tools/setup_customer_onboarding.py --number <international-digits>
uv --directory backend run python -m easydial.whatsapp
```

The setup helper preserves the original pilot settings in private `.state/onboarding-rollback.json`, generates the session-encryption key once, configures a separate `.state/whatsapp/customers.sqlite3`, and creates the QR. It never copies operator browser cookies into customer sessions. Keep the key in a secret manager when deploying, never in Git, logs or the database. Losing or changing it makes encrypted records unreadable. Key rotation/re-encryption tooling is not implemented.

To restore the original pilot, stop the service and restore the two settings in that private rollback file; restart. Do not point multi-user mode at the old plaintext pilot database. Do not run both modes on the same webhook/process/merchant profiles.

## Consent evidence

`consent_events` records sender identity from a signature-verified Meta webhook, provider, exact displayed consent text, version, inbound event ID, action (accepted/declined/withdrawn), and server timestamp. SQLite triggers reject updates/deletes; withdrawal appends evidence. The audit is application-level append-only, not cryptographic non-repudiation or protection from a database administrator. An upgraded consent version requires acceptance again before session reuse.

`CONSENT_TEXT` and `CONSENT_VERSION` in `whatsapp/accounts.py` are the source of truth. They explain OTP login, encrypted session reuse for shopping, exclusion of OTPs from the shopping AI, withdrawal, and the fact that OTP messages may remain in the user's WhatsApp chat.

## Isolation and storage boundaries

- Signed webhook sender is the tenant identity. A message cannot choose another customer's tenant key.
- All inbound message bodies in multi-user mode are encrypted before insertion into the durable queue, including messages that arrive before the OTP state is entered. Bodies are replaced with `{}` after processing. They are never logged. Onboarding runs before media download, transcription, model context, cart restore or shopping.
- Onboarding state, phone during its bounded challenge, saved address metadata, and store browser state use authenticated encryption. OTPs are not copied into state, conversation history or model inputs.
- One independent browser context per customer/store. No persistent operator Chrome profile is inherited. Cookies/local storage/IndexedDB are encrypted on disk; Playwright receives decrypted state in memory. Session files use opaque customer paths and restrictive filesystem permissions.
- Shopping worker objects are resolved per customer; disconnected stores are rejected at the provider boundary. Switching customers closes/saves the preceding customer's browsers. Up to four pending login contexts are allowed and expire after ten minutes.
- A verified merchant phone/account cannot be connected to two EasyDial customers simultaneously. Merely typing someone else's phone number does not reserve it; successful OTP verification is required.
- Drafts, payment requests, order checks and QR artifacts retain their existing sender-scoped keys. The single-process queue serializes turns. This is not distributed locking or horizontal autoscaling.
- Ordinary shopping records and sender metadata still live in the private SQLite database. Encrypt the deployment disk/backups and restrict access. Encryption of login credentials is not a claim that every historical shopping field is separately encrypted.

## Recovery and limitations

- Ten-minute setup expiry, three OTP attempts and three OTP requests per customer per fifteen minutes; users explicitly restart expired login. No automatic OTP resend.
- A process restart resets unfinished setup to store choice. It does not replay the merchant OTP submission. Ready connections survive via encrypted browser state.
- Challenges, ambiguous/changed address layouts and unknown login UI stop without cart/payment action. No CAPTCHA or bot-detection bypass exists.
- Store sessions can expire. Reconnection is required; there is no permanent-login guarantee.
- Connection changes are blocked while a basket/order attempt exists. Withdrawal is always allowed, but removes automated access needed to inspect existing orders; users may need to check those directly in the merchant app. A full post-order/reset and reconnect recovery UX remains a separate release gate.
- Disconnect removes EasyDial's stored login, not the merchant account or a server-side session revocation on every device. Existing consent audit entries are retained.
- No public production number, Linux/AWS browser validation, multi-instance queue, key rotation, remote human challenge recovery, or automatic add-new-address UI is included in this pilot.

## Verification

Regression suite with local browser tests: 177 passed at implementation checkpoint. Covers consent gating/evidence/withdrawal, encrypted ingress and redaction, expired/restarted/wrong OTPs, cross-user buttons, distinct account/address/browser cookies, connected-provider filtering, same-account sharing prevention and decoding the shared QR.

Observed live without requesting OTP: Instamart phone/login controls; Blinkit location-first login controls in headed Chrome; authenticated saved-address selector structures in both operator profiles (read only, no address/cart changes). OTP submission and new-customer address-selection completion require the user's live test; do not infer success from the structural fixtures. The user is testing Instamart through WhatsApp. No developer purchase was made.

## Latest verification checkpoint

Blinkit customer OTP, saved-address selection and subsequent shopping have now been exercised through WhatsApp. Follow-up fixes handle delayed location UI, duplicate saved-address entries, store/phone-specific rate limits and preserving pre-login prompts across restarts. Current complete test coverage: 177 standard tests plus 19 opt-in Chrome DOM tests passed separately. Instamart customer OTP completion, multiple simultaneous customers and AWS release gates remain separate live acceptance work. See PROGRESS.md for incident evidence and the sequential product-selection change.

## Logout

Send `logout`, `log out`, `sign out`, or `please log me out`. Explicit voice commands (after transcription) and common Hinglish forms such as `logout kar do` are supported. Negated commands and questions do not trigger logout.

Logout applies to both connected stores for that WhatsApp customer. It records consent withdrawal, deletes their encrypted merchant session files and account connections/claims, cancels any in-progress OTP login, and closes their browser contexts without saving cookies. It clears the shopping draft, product choices, cart checkpoint and payment approval/menu by creating a fresh conversation session. EasyDial-hosted payment artifacts are revoked; old order-attempt records remain for audit and duplicate-order protection. Already-placed orders are not cancelled, and an already-issued merchant QR cannot be remotely revoked by deleting EasyDial's copy.

The next shopping request or stale payment button is intercepted by onboarding. Select a store, give consent and complete phone/OTP/address setup again before shopping. Other customers' sessions and the shared encryption root key are unaffected. No real pilot customer is logged out as a deployment test.

### Saved login status

Customers can ask “Am I logged in?” or “Is any login session saved?” in text or voice after onboarding. EasyDial answers from that customer's consented connections and encrypted session files before shopping interpretation. This reports saved credentials, not a guarantee that the merchant has not expired them. The query does not change onboarding, baskets, payments or consent. During phone/OTP setup, audio remains gated to avoid sending authentication details to transcription.
