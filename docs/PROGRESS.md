# Cart recovery implemented — 2026-11-21

Added WhatsApp review recovery for a nonempty store cart without a matching checkpoint. It shows current store items beside the requested basket, then offers a versioned, expiring Update cart button only for uniquely matched existing products. Confirmation rechecks cart/address before adopting; normal guarded synchronization and fee review follow. No automatic clearing or payment. Unknown/ambiguous products remain protected. Added saved-product-name search fallback after restart, retaining exact ID/pack verification. See `CART_RECOVERY.md`.

Validation: 65 tests passed including DOM checks; Ruff passed. Live headless read-only inspection found Instamart quantity 2 of the saved milk pack; the requested draft has quantity 3 plus quantity 2 of the banana pack. The recovery proposal mapped the existing milk uniquely, verified the configured address, and a read-only search found the missing banana with the exact saved product ID/pack. No quantity was changed. The user still needs to confirm the concrete comparison in WhatsApp before their basket is changed.

---

# Explicit database-backed order drafts — 2026-11-21

Added sender-scoped `order_drafts` and event-scoped `order_revisions` to the private SQLite database. Every turn now reads its basket/bill/preparation from the order table before handling review or checkout. Draft, revision, conversation, event outcome and responses commit together; older versions and duplicate event completions are protected. Existing snapshots migrate at startup without changing baskets. See `ORDER_PERSISTENCE.md`. This does not enable multi-user store sessions or payments.

Validation: 60 tests pass, including Chrome DOM checks; Ruff and diff checks pass. Private pre-migration database backup created, service restarted, and one existing draft migrated with an exact snapshot match. Health is ok; headless remains enabled and payments remain disabled.

---

# Local headless mode enabled — 2026-11-21

User requested actual headless operation. The private backend `.env` still had the default headed setting; set `BROWSER_HEADLESS=true`, verified no queued/processing requests, gracefully stopped the service and restarted it. Both provider workers now resolve headless=True with channel=chrome; local health returned ok. Browser contexts launch lazily on the next shopping request. No cart mutation or order was performed. Private `.env` remains untracked.

---

# Additive basket correction — 2026-11-21

The store-switch error was triggered by a misleading mixed-store product menu, not an explicit request to switch. The existing draft contained three Instamart milk packs; Diet Coke had not been added after the rejected selection. Earlier advice to empty the basket was inappropriate for the user's additive request.

WhatsApp now limits selectable additions to the current basket store while retaining both stores' results for price comparison. Existing mixed-store menus recover by showing compatible choices without removing items or silently substituting a product. Product additions preserve existing lines; the combined basket asks whether to modify or review. Intent instructions explicitly distinguish additions from replacements. Provider safety guards remain unchanged. No private customer state or live cart was edited during this fix.

---

# Store-switch diagnosis — 2026-11-21

User encountered the old “Sync an empty basket” error. Read-only inspection found draft version 6 still contains quantity 3 from Instamart, with `synced_provider=instamart` and no legacy cart checkpoint. Selecting Blinkit correctly hits the single-store guard. Replaced the technical error with store-specific instructions: remove all draft items, review to verify/clear the previous store cart, then choose the new store. Empty drafts get a shorter review-first message. No customer state or store cart was changed. The legacy externally changed/unknown cart protection still applies; if review refuses the existing cart, the user must clear it directly in Instamart, then review again.

---

# Latest implementation — payment inspection and fee transparency

2026-11-21: inspected both logged-in payment-choice pages without initiating payment or placing an order. See `PAYMENT_INSPECTION.md` for evidence. Blinkit test basket was cleared; existing Instamart basket was preserved. WhatsApp now shows a detailed monospace charge table, warns about small-cart/minimum-order fees, and requires a reconciled fresh bill before recording preparation-only approval. Payment handoff remains disabled.

Headless Chrome is configurable and authenticated read-only checks passed locally for both stores; Linux deployment and headless cart mutations are not yet verified. Verified cart checkpoints now persist after successful reviews, with live guards still enforced after restart.

Runtime: WhatsApp service restarted on port 8001; local and existing public HTTPS tunnel `/health` both returned `status=ok`, `payments=false`.

Validation: **54 tests passed**, including Chrome DOM regressions (`EASYDIAL_DOM_TESTS=1`); Ruff and diff whitespace checks passed. No paid-order success is claimed.

Operational caveat: the pre-upgrade Instamart draft requests quantity 3 of the 1 ltr x 2 pack, while the inspected live basket contains quantity 2. There is no historical ownership checkpoint in this legacy state. The attempted read-only reconciliation correctly refused to adopt it; no database checkpoint was created and no cart was changed. The user must reconcile this existing basket deliberately before a fresh sync (e.g. clear it in Instamart, then ask EasyDial to review the desired draft). Do not silently overwrite it or erase the database. Subsequent successfully reviewed baskets get durable checkpoints automatically.

User reports WhatsApp works and Meta/OpenAI settings are configured privately; older setup status below is historical. The end goal remains user-authorized payment and provider-confirmed order evidence. Neither is implemented yet.

---

# Latest research update — deployment and payment

2026-11-21: user reports WhatsApp working. Headless/payment research is recorded in `DEPLOYMENT_AND_PAYMENTS.md`. Browser workers still use `headless=False`; no production deployment or authenticated headless check performed. Recommended payment design is provider-owned UPI with user authorization and durable provider confirmation, conditional on observing transferable website payment artifacts. No cart/payment/order actions or runtime changes were made for this research. Current no-purchase/Pending boundary remains.

---

# Latest operational update — 2026-11-21

User completed Meta app creation and filled private `.env`. Presence checks now pass for Meta/OpenAI settings, FFmpeg and store address. A read-only Meta API request verified sender access. OpenAI API calls and real inbound/outbound WhatsApp delivery are still unverified.

WhatsApp service restarted on loopback port 8001; older port-8000 API stopped to avoid shared Chrome-profile conflicts. A temporary Cloudflare HTTPS tunnel is running. Public GET webhook challenge passed, and unsigned POST returned 403. The user must save the callback URL/verify token in Meta and subscribe to `messages`, then initiate the chat from the allowlisted tester. No message, cart mutation, payment or order was triggered during this setup check. Temporary tunnel URLs are runtime details and change on restart.

---

# Progress and handoff

Updated 2026-11-21. Public repo: https://github.com/prithidevghosh/easydial.

## Current direction

WhatsApp-first EasyDial is now the requested direction; iOS feature development is paused. Research recommends Meta Cloud API with OpenAI dictation/intent and the existing **two Playwright provider workers**, Blinkit and https://instamart.in/. The single-user WhatsApp channel is implemented and locally tested, but Meta/OpenAI credentials and actual message delivery remain unverified. See `WHATSAPP_SETUP.md` for setup and `WHATSAPP_FEASIBILITY.md` for remaining policy/production gates. Swiggy MCP has been removed at the user's request. Final goal is a paid, provider-confirmed order; this session explicitly forbids placing any order. Current working boundary is verified live cart/checkout preparation → local Checkout Pending.

Read `plan.md`, `docs/BROWSER_VERIFICATION.md` and `docs/CREDENTIALS.md` before continuing. Earlier MCP/Firefox and fixture-only milestones remain in Git history; they are superseded.

## WhatsApp implementation (latest)

- Separate webhook-only FastAPI service on loopback port 8001; signed POST validation, GET challenge and one configured test sender/account.
- Durable SQLite inbox/session/outbox, duplicate rejection, serialized turns, uncertain-send and interrupted-work handling; no blind mutation retries.
- Authenticated media retrieval, bounded FFmpeg OGG/Opus normalization, OpenAI transcription and structured grocery intent. No fake transcription/catalogue fallback.
- Product lists, numeric/spoken choice interpretation, multi-item continuation, draft changes, complete bill/address summaries and local Pending; existing provider workers reused.
- Private credential helper and readiness/status commands. No secrets printed or committed.
- 43 local tests pass in total, including 13 WhatsApp tests and five Chrome DOM regressions. Ruff passes. WhatsApp coverage includes real OGG/Opus decoder checks.
- Not yet live-tested: Meta transport, actual OpenAI voice/intent, WhatsApp-to-store journey. User requested assisted Meta registration; user is signed in; browser is at first-time Meta developer registration, awaiting their terms/contact/About-you steps. No Meta app or business account created by the agent yet.
- Fixed responses are currently English; outbound speech, broader language evaluation, multi-user account linking and payment remain outstanding.

## Completed and tested — previous browser milestone

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

0. Help the user finish Meta sign-in/app setup, save secrets with `tools/connect_whatsapp.py`, then start the isolated webhook service and configure the HTTPS callback using `WHATSAPP_SETUP.md`. Verify actual voice-note transport and live cart review without purchase. Public-launch eligibility remains unresolved.

1. Add OpenAI API key locally, then verify actual dictation and correction journeys. Do not confuse successful typed commerce tests with voice success.
2. Improve grounded product metadata and conservative exact-match comparison; current browser cards lack complete brand/formulation metadata.
3. Broaden live coverage to more products and variants, stock/quantity changes, expired sessions, external edits and failure recovery.
4. Persist per-user session/cart ownership and secure profile isolation before multi-user deployment. The current service is single-operator and loopback-only.
5. Implement payment as a separate explicitly approved operation with durable approval snapshot, current total/address revalidation and provider order/payment reconciliation. Do not enable or test a purchase without a later user instruction allowing it.
6. Benchmark the same Indian-language recordings against the baseline and harness for the Voice AI for India theme.

## Operational reminders

The WhatsApp-only local server has been smoke-tested on port 8001 and reports `setup_required` while Meta credentials are absent. It must be restarted after filling `.env`. The older API on port 8000 must be stopped before live WhatsApp commerce uses the saved profiles. No public tunnel is running yet.

Only one process owns each Chrome profile. Stop the backend before opening setup helpers. `.env` and `.state` contain private credentials/address data. Never print or commit them. Use the live smoke tool only with empty test carts and explicit cart-edit authorization. A restart loses in-memory ownership; do not automatically adopt an existing basket.

Both sites can change DOM or return temporary failures. An error is not evidence of out-of-stock. A payment button or Pending UI is not proof of a paid order. No bot-detection bypass was added.
