# Progress and handoff

Updated 2026-11-21. Public repo: https://github.com/prithidevghosh/easydial.

## Current direction

Native iOS EasyDial with OpenAI dictation/intent and **two Playwright provider workers**, Blinkit and https://instamart.in/. Swiggy MCP has been removed at the user's request. Final goal is a paid, provider-confirmed order; this session explicitly forbids placing any order. Current working boundary is verified live cart/checkout preparation → local Checkout Pending.

Read `plan.md`, `docs/BROWSER_VERIFICATION.md` and `docs/CREDENTIALS.md` before continuing. Earlier MCP/Firefox and fixture-only milestones remain in Git history; they are superseded.

## Completed and tested

- Retained premium SwiftUI UI and live-only app; no demo catalogue or fake order flow.
- Replaced MCP runtime, OAuth helper and dependency with Instamart.in browser automation.
- Added reusable dedicated headed Chrome contexts, serialized account access, shutdown cleanup and manual-login helpers.
- Fixed Blinkit navigation, disabled empty-cart button, duplicate nested cart rows, asynchronous bill loading, quantity parsing, litre packs, MRP exclusion and existing-cart edits/removal.
- Implemented Instamart real search, exact pack selection in variants, quantity changes, replacement, cart/address/bill parsing and footer total consistency.
- Preserved existing-cart protection, versioned draft edits, full-address verification after sync, unknown-state errors and no payment submission.
- User completed login and selected matching physical addresses. Private settings/profiles saved locally; no credentials or address text committed.
- Both live workers passed add, increase quantity, replace, full bill/address readback and cleanup.
- Both fresh HTTP API journeys passed search → add → review → Checkout Pending → remove → verify empty, after reopening Chrome with saved profiles.
- Test carts left empty. No order placed; no payment attempted.
- 30 local tests including five Chrome DOM regressions and Ruff pass. Backend is running on localhost:8000 with both stores configured.

## Key files

- `backend/src/easydial/providers/browser.py`: profile lifecycle, explicit blockers, UI-action request completion without reading/replaying private API payloads.
- `blinkit.py`: Blinkit search/cart controls plus shared cart-review orchestration and ownership checks.
- `instamart.py`: Instamart.in overrides for navigation, search, variants, cart quantities and readback; reuses review/clear orchestration.
- `blinkit_dom.py`, `instamart_dom.py`: observed DOM readers. Keep them grounded in actual pages.
- `backend/tools/connect_blinkit.py`, `connect_instamart.py`: local manual login/profile setup.
- `backend/tools/check_live_cart.py`: opt-in live API smoke/cleanup; never places an order.
- `backend/tests/test_dom.py`: opt-in redacted Chrome DOM regressions; no network.

## Next work

1. Add OpenAI API key locally, then verify actual dictation and correction journeys. Do not confuse successful typed commerce tests with voice success.
2. Improve grounded product metadata and conservative exact-match comparison; current browser cards lack complete brand/formulation metadata.
3. Broaden live coverage to more products and variants, stock/quantity changes, expired sessions, external edits and failure recovery.
4. Persist per-user session/cart ownership and secure profile isolation before multi-user deployment. The current service is single-operator and loopback-only.
5. Implement payment as a separate explicitly approved operation with durable approval snapshot, current total/address revalidation and provider order/payment reconciliation. Do not enable or test a purchase without a later user instruction allowing it.
6. Benchmark the same Indian-language recordings against the baseline and harness for the Voice AI for India theme.

## Operational reminders

Only one process owns each Chrome profile. Stop the backend before opening setup helpers. `.env` and `.state` contain private credentials/address data. Never print or commit them. Use the live smoke tool only with empty test carts and explicit cart-edit authorization. A restart loses in-memory ownership; do not automatically adopt an existing basket.

Both sites can change DOM or return temporary failures. An error is not evidence of out-of-stock. A payment button or Pending UI is not proof of a paid order. No bot-detection bypass was added.
