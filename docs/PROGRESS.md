# Unclear speech and previous-basket recovery — 2026-11-21

Investigation of the reported 17:14–17:15 IST responses found an unclear transcript interpreted as removal, followed by a correctly captured request to put the same order back. User confirmed they intended agreement/proceeding after the bill, not deletion. The last nonempty draft was one Mandarin Orange 3-piece pack. No audio replay was possible because original recordings are not retained; do not claim the ASR itself has been fixed or benchmarked.

Added NEXT_STEP context to intent interpretation without replaying old conversation instructions. A separate structured removal-intent check now rejects proposed deletion on unclear/garbled, negated or move/proceed language. This reduces the observed failure but is still model-based, not a guarantee. Bare numbers after a completed selection deterministically clarify add versus replace instead of guessing.

Added restore intent with previous-basket context. Nonempty draft snapshots are retained only after successful basket changes; legacy empty drafts retrieve the most recent nonempty revision scoped to the same customer and order. Context retrieval alone never restores items. Explicit restore rechecks exact products/quantities at the provider, requires confirmation for changed prices, and clears old bill/payment preparation. Pending payment requests still prevent mutation. No customer draft or store cart was changed during diagnostic tests.

Validation: 95 automated tests passed, 7 DOM skipped; final real-model text eval 48/48 across 24 cases twice. Earlier expanded run failed 2 cases; reports and limitations are retained in `INTENT_EVALUATION.md`. Added tests cover deletion verification, restoring known items, changed-price confirmation, failed refresh preserving state, scoped historical context and ambiguous numeric selection.

---

# Automatic stale-selection recovery — 2026-11-21

The selected option was correctly understood, but the controller's ten-minute offer expiry surfaced as “Search again”. The WhatsApp harness now refreshes an expired selected offer automatically using only its provider and saved product name. It requires one exact product ID/SKU/pack/address/mode match; no lookalike or alternate pack is substituted. An unchanged price completes the original add/replacement with its intended quantity. A changed price leaves the basket untouched and presents the updated price for explicit versioned selection, retaining the pending replacement target. Unavailable stock, insufficient quantity, ambiguous identity or failed checks preserve the basket. Freshness enforcement remains in the controller.

Validation: 88 tests passed, 7 DOM skipped; Ruff/diff checks pass. Added stale correction success, changed-price approval, unavailable/failed/quantity-limited preservation and selected-provider/exact-pack regressions. Recovery is bounded to one provider search and does not retry a payment or mutate the store cart. Customer-facing progress says “Checking the latest price…”. Read-only live verification of the exact user sentence resolved select+replacement; Instamart refreshed the exact Mandarin Orange SKU / 3 Pieces pack, available at the unchanged ₹112 price. No customer draft or store cart was changed by this diagnostic.

---

# Persistent selection corrections and English replies — 2026-11-21

Root cause: selecting one result removed same-query alternatives from active choices and pruned their offers. A correctly transcribed “not first, choose second” then reached the model without numbered options. New `recent_selection` context preserves exact original menu order, selected line/offer and alternative offers across persistence. Explicit corrections select a known alternative with `replaces_line_id`, preserve the selected quantity and replace rather than add. New searches supersede this context. Old versioned buttons, price freshness, single-store and payment guards still apply. Narrow legacy recovery reconstructs the old deterministic top-two menu only for one result provider/query and one selected line; ambiguous histories are not reconstructed.

Customer prose is instructed to be English regardless of input language. Removed inherited user-language clarification instructions and the Hindi fallback; unexpected Indic-script clarification uses a short English fallback. Product names remain store-sourced.

Validation: 82 automated tests pass, 7 DOM skipped; Ruff/diff checks pass. Expanded real-model development suite: 42/42 across 21 cases twice, including negated ordinal correction, Devanagari, additive second choice, ambiguous bare number and second choice before selection. The exact user wording against a read-only copy of the actual saved context selected Mandarin Orange and targeted the current Nagpur Orange line for replacement; no customer basket or provider cart was changed by this check. Cached prices still expire normally, requiring a fresh search if the old menu is stale.

---

# Delivery-confirmed WhatsApp ordering — 2026-11-21

Sequential HTTP sends did not preserve customer-visible order: Meta accepted the second product image but delivered the later choice prompt first. New reply batches have durable `outbox.after_id` dependencies. Each message waits for its predecessor's delivered/read receipt, rather than HTTP acceptance or sent status. Definitive delivery failure releases the next message; uncertain sends remain held. Early status callbacks are persisted before remote IDs are attached; receipts do not regress and dependencies survive restart. Fresh valid customer turns supersede held-back older replies so stale choice menus do not appear later. No blind retries or arbitrary sleeps.

Validation: 78 tests passed, 7 DOM tests skipped, Ruff passed. Four new delivery regressions cover slow images, early/reordered receipts, restart, supersession and failure/uncertainty. Live phone rendering still needs a fresh user search after restart. If Meta does not provide delivery receipts, dependent messages wait; product captions remain individually actionable and a new user message supersedes waiting replies. This is receipt-based sequencing, not a guarantee about every WhatsApp client's rendering internals.

---

# Compound intent and real-model eval — 2026-11-21

Fixed explicit edits plus new-product discovery as a single WhatsApp turn; retained product selection, cart ownership and payment guards. A locally retained Thums Up transcript preserved the intended request, identifying an intent/execution issue rather than an ASR failure for that example. Added opt-in synthetic real-model evaluator and compound execution regressions. Baseline mini 9/16; revised mini 30/32; revised gpt-4.1 32/32 across 16 cases twice. Configured gpt-4.1 as the master; dictation unchanged. Standard suite 74 passed, 7 DOM skipped. Read `INTENT_EVALUATION.md` for scoring, limitations and Sarvam comparison requirements. These are development-set text evals, not a held-out audio benchmark or full live-store journey.

---

# Product-choice message UX — 2026-11-21

Per the user’s correction, product cards come first and the choice menu comes last. Each card includes its selection number/instruction; products with photos are not repeated in a second full text list. Removed premature “Adding to your basket” wording during search. Store diagnostics remain in structured private search results; customer output shows only a brief incomplete-comparison note or retry guidance. No customer browser troubleshooting instructions are emitted by search. Meta may deliver media out of order despite sequential sends, so each card is independently actionable.

Validation: 71 tests passed, 7 DOM tests skipped; Ruff passes. Tests cover prompt-last ordering, deduplication, complete menu choices, and sanitized partial/total search failures. No live store changes made.

---

# Hindi new-product intent fix — 2026-11-21

User reported “That item is no longer in your basket” for “ek dahi ka packet order krna h”. The current draft was empty; the error originates from an edit against a missing line. Failed request text/intent was not retained, so the original model output cannot be reconstructed. The interpreter previously received the full SessionView, including stale “Clear the cart now” conversation history. Removed that historical input and supply only authoritative basket line IDs/products/quantities, reviewed-bill presence and displayed choices. Added explicit Hindi/Hinglish discovery examples and one bounded reinterpretation for nonexistent edit/replacement targets; repeated invalid output asks for clarification without mutation.

Validation: 70 tests passed, 7 browser DOM tests skipped (no DOM changes); Ruff/diff checks pass. Real OpenAI interpretation of the exact user sentence and a Devanagari equivalent both returned search, quantity 1, no replacement and no edits. This verifies text intent only, not the original voice transcription or store results. No live cart changes or WhatsApp messages were sent by the diagnostic test.

---

# Payment choices, handoff and chat UX — 2026-11-21

Reviewed upstream Blinkit MCP payment/auth source at `a39547118f4decd04a77ac16316fa09efdb6237a`; see `BLINKIT_MCP_PAYMENT_REVIEW.md`. Its QR flow corroborates our observed UI, but its fixed delay, automatic cash preference and lack of payment-result verification do not resolve our remaining issues. WhatsApp service restarted; local and public HTTPS health return ok, payment-link hosting is configured, and an invalid payment token returns 410. Payment authorization remains disabled.

User authorized payment-request generation only and explicitly requested payment-method choice. Implemented live payment-category discovery, versioned WhatsApp menu, Blinkit UPI QR decoding/amount checks and expiring HTTPS presentation links. Cards/wallets/banks hand off to the store checkout; COD does not place an order. Attempts are durable and no uncertain request is automatically regenerated. Instamart PhonePe account-linking blocks independent gateway-link verification; do not claim this is solved. See `PAYMENT_HANDOFF.md`.

Added shorter messages, quiet typing indicators and at most one delayed stage update. Product cards carry image URLs; WhatsApp shows up to two photos and text/menu choices, with definite-failure text fallback. See `CHAT_EXPERIENCE.md`.

Validation: 75 tests pass (including Chrome DOM tests); Ruff passes. Live menus were verified on both stores, and public product images were observed on both. A real Blinkit ₹103 QR was generated and decoded without payment approval. A fresh headless retry was blocked by Blinkit; headed automated generation then caught a QR loading placeholder and recorded an unknown attempt. Fixed the image-read race with a regression test; do not claim the revised complete automated live handoff has passed. The temporary test request belongs to a separate audit order and does not lock the customer’s actual draft. No unknown request was automatically regenerated.

---

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
