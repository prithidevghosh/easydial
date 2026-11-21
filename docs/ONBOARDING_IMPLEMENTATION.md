# Customer onboarding implementation plan

2026-11-21. Authorized scope: shared WhatsApp join QR, explicit recorded consent BEFORE store login/OTP, customer-isolated sessions, saved address choice and normal shopping. No real OTP request, new login or purchase without the customer's explicit onboarding action.

## Invariants

- Existing single-user pilot remains available; multi-user mode is explicit and must never fall back to pilot profiles.
- Customer identity comes only from the signed Meta webhook sender; all connection/session/address/payment records are scoped to that identity.
- Consent is versioned, immutable evidence containing exact text, customer, provider, inbound event ID and timestamps. Declining causes no browser/login action. Reconnection requires current consent. Withdrawal closes sessions and removes stored authentication.
- Phone and OTP messages are intercepted before transcription/LLM/shopping. Sensitive inbound payloads are encrypted before durable queueing; OTPs never enter conversation state or logs, and are erased after processing. Expired/interrupted login never replays OTP submission automatically.
- Store sessions are encrypted at rest; decrypted browser state exists in memory only. A new customer starts without any other customer's cookies. Keys stay outside Git/database. Authentication expiry is handled as reconnect, not shared-account fallback.
- One browser context per customer/store. Existing single-process worker serializes turns; do not enable multiple process replicas without distributed ownership.
- Address options come from the authenticated store UI, are bound to the onboarding attempt, and require explicit selection. No default address is silently selected.
- Login challenges, unknown UI, ambiguous addresses or failed verification stop with a clear recovery action. No challenge bypass.
- Shared QR encodes only the public EasyDial WhatsApp number and Start text, never identity, cookies, OTPs or tokens. Scanning opens a chat; sending Start starts onboarding.

## Steps and verification

1. [x] Implement encrypted connection/consent storage and bounded deterministic state machine (choose store → consent → phone → OTP → address → ready).
2. [x] Implement isolated provider/session resolver, no global profile inheritance, protected session writes and logout/reconnect lifecycle.
3. [ ] Inspect unsigned store login pages without requesting OTP; implement observed login controls and conservative address UI adapter. Record which selectors are observed versus still requiring authenticated validation.
4. [x] Integrate signed webhook routing and runtime: onboarding before master agent, encrypted ingress, per-customer shopping chat/workers and provider availability.
5. [x] Add shared link + QR routes/generator with deployment number validation; do not advertise the Meta test number as public production onboarding.
6. [x] Test consent denial/acceptance, OTP expiry/retries/restarts/redaction, stale/cross-user actions, separate sessions/addresses/carts/payment scope, join URL/QR and pilot regressions.
7. [ ] Document configuration, customer journey, consent security limits, live test steps and remaining deployment gates; commit/push.

## Acceptance boundaries

Automated regression tests establish isolation and state transitions, not live merchant login success. Live OTP requests require consent from the test customer. Do not enable public ingress or claim complete live validation before two customer accounts have been exercised independently. Do not migrate existing pilot cookies into a new customer's connection.

## Implementation checkpoint

Code is running in isolated multi-user pilot mode with the existing test number. 177 tests passed including offline Chrome tests. Consent-before-login, encrypted ingress/session storage, customer provider resolution and shared QR are implemented. Observed merchant phone-entry and address-list controls; customer OTP/address completion is currently being tested live. See CUSTOMER_ONBOARDING.md for configuration and explicit production gaps. No customer cookie was migrated from the old pilot.

Latest checkpoint: Blinkit onboarding reached shopping in a real WhatsApp customer session. Location-render timing, nested/duplicate address parsing, restart prompts and phone-specific throttling have regression coverage. Instamart full live onboarding and deployment acceptance remain outstanding. Current test result: 177 standard tests and 19 opt-in Chrome tests passed separately. Changes are being committed in onboarding/isolation and sequential-shopping groups.
