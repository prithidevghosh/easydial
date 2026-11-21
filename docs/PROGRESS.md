# Progress and handoff

Updated: 2026-11-21. Public repository: https://github.com/prithidevghosh/easydial.

## Current scope

User rejected the button styling and then explicitly removed the entire demo flow. Current milestone is live Blinkit and Swiggy discovery/cart review, stopping at **Checkout: Pending**. Payment and order placement are outside this milestone. This supersedes earlier sample/read-only progress. Read `plan.md`, particularly its current release override, and `docs/CREDENTIALS.md` before continuing.

## Implemented in this session

- White primary labels on deep blue, full-width 56-point actions, consistent soft secondary buttons, refined home spacing/type, custom text composer and explicit slate supporting text.
- Runtime sample provider removed; sample mode rejected. Home displays connection status and real address when configured. Fixtures exist only under tests.
- Live provider searches; no fallback products on errors. Unknown stock/prices remain unknown.
- Swiggy get_cart → update_cart → get_cart; validates full requested quantities, stock, selected address and bill totals. Only discovery/cart tools allowlisted; checkout, payment and order tools blocked.
- Blinkit manual-authenticated Firefox worker, visible card parsing, quantity reconciliation through scoped add/remove controls, full-address verification, and basket/bill readback. Unknown DOM/quantity/total stops review; no forced clicks or payment buttons.
- Cart ownership checks preserve pre-existing/external baskets. Draft edits sync on Review basket. Sync empty basket supports removing all items and changing provider. Locks serialize account mutations; reviews expire/invalidate after edits.
- Checkout is a local Pending state and never a purchase. Duplicate taps cannot submit an order.
- Local Swiggy PKCE login/address helper and Blinkit manual-login helper; secrets stay in ignored owner-only `.env`/`.state` files.
- Exact credential/setup instructions and live checklist in CREDENTIALS.md; updated runbook, decisions and plan.

## Actual verification

- Ruff passes across src, tests and setup tools.
- 26 backend tests pass: provider-contract journey, review invalidation, authentication isolation, atomic edits, preserved replacements, absent credentials, no runtime demo, live cart contract/readback, fees, wrong address, caps, inconsistent totals, existing-cart protection, blocked payment tools, clear-owned-cart and provider-switch rules, OAuth callback state and private env-file handling.
- iOS simulator build and disconnected live home/settings UI test pass after fixing accessibility assertions to match SwiftUI's combined labels.
- Earlier in this session, the primary button contrast was inspected in a basket screenshot and the then-existing sample journey passed. That runtime sample flow was subsequently deleted.
- Firefox DOM readers executed against a local HTML fixture: excludes struck-through MRP, reads quantity, rejects missing quantity. This does NOT verify actual Blinkit selectors.
- Latest code changes are being committed/pushed with CI; record the successful run after it finishes.

## Not yet verified / required external input

No OpenAI key, Swiggy login, or Blinkit session was available during implementation. No authenticated search/cart mutation or payment occurred. The newly written login helpers have local validation, not successful account login evidence. Real provider response shapes/DOM can require fixes after authentication. Do not tell the user the live pipeline has passed until each provider's real search, quantity edit, replacement/removal, address and bill have been verified.

The user should follow CREDENTIALS.md locally, then ask the agent to restart the backend. Do not request secrets in chat. A blank ignored backend/.env exists with live mode; preserve any credentials the user adds. Firefox is installed for Playwright.

## Next actions

1. User configures OpenAI and runs both login helpers, selecting the same physical address. Verify each read-only search separately.
2. Against a deliberately empty real provider basket, validate review write/readback, replace, increase/decrease, remove, empty sync, then Pending. Keep payment blocked.
3. Fix actual Blinkit selectors/address surfaces or Swiggy schema differences from redacted evidence; do not infer success from no exception.
4. Capture live test evidence without credentials or identifiable addresses. Add regression fixtures for actual schema variants.
5. Test recorded multilingual voice, correction handling, spoken output and physical-device accessibility. Same-recording benchmark still outstanding.
6. Durable cart/session records, production user auth and per-user onboarding remain future work. Backend is single-operator loopback only; restart loses ownership, so empty the provider test cart before a fresh session.

Research docs retain dated findings. The Swiggy comparison use-case interpretation remains unresolved; both providers are enabled for the user's requested local testing, not represented as production approved. Telephone calls remain deferred.
