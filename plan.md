# EasyDial — implementation plan and verification ledger

Updated: 2026-11-21. Owner: current agent and founder. Repository: https://github.com/prithidevghosh/easydial (public). Status: **build authorized; implementation underway**.

This is the primary execution document. Read it together with `docs/PROGRESS.md` at the beginning of every session. Update the checkboxes and evidence before ending a session. A written feature, successful compilation, sample response, and verified live journey are four different levels of completion.

## Latest direction — browser automation and eventual paid-order completion

User now requires both Blinkit and Instamart through browser automation, with dictation → product selection → editable basket → live checkout → paid and provider-confirmed order as the final product goal. Swiggy MCP is no longer the chosen integration. The user's production-access experience motivates the change; do not generalize it into an independently verified claim about every MCP account.

For this work session the user explicitly allows current-location use and forbids placing an order. Test login/search/cart/checkout preparation only. Payment code requires real observed payment surfaces, explicit per-basket approval and provider order/payment evidence; neither a button click nor a pending screen constitutes success.

- [x] Inspect actual Blinkit DOM and fix selectors based on evidence.
- [x] Inspect Instamart browser login, location, search, variants, cart and checkout.
- [x] Replace Swiggy MCP runtime/configuration with a browser worker.
- [x] Retain isolated account browser sessions and show login/challenge/service blockers accurately.
- [x] Test real add/increase/decrease/replace/remove/review for each reachable provider.
- [x] Test checkout boundary without placing an order; document every untested stage.
- [x] Add actual-page regression fixtures and repeat affected tests.
- [ ] Implement and verify user-approved payment and provider-confirmed order in a later authorized purchase session.

No stealth modifications, CAPTCHA bypasses, proxy rotation, or retries around explicit automation blocks. A normal browser session can reduce repeated login churn but cannot guarantee that sites accept automation.

Verified browser/API evidence: both providers passed search → cart → full address/live bill → local Pending → clear. See `docs/BROWSER_VERIFICATION.md`. No order or payment was attempted. 30 local tests pass with optional Chrome DOM checks.

## Previous release override — historical 2026-11-21 milestone

The user has removed demo mode from scope and requested live Blinkit and Swiggy flows through basket review, with Checkout showing Pending. This supersedes all earlier sample-mode milestones below; those entries are historical evidence only.

- [x] Remove sample catalogue/mode/completion from runtime; fixtures remain test-only.
- [x] Fix button contrast and refine home layout; explicit primary/secondary styles.
- [x] Local credential helpers and exact setup guide (`docs/CREDENTIALS.md`).
- [x] Instamart cart write + readback + address/quantity/bill checks.
- [x] Blinkit visible cart controls + readback + address/quantity/bill checks.
- [x] Existing-cart protection, account locks, stale-review invalidation and no payment tools.
- [x] Checkout becomes local Pending; never calls provider checkout.
- [ ] Authenticate both accounts and validate actual search/cart responses.
- [ ] Validate Blinkit DOM selectors/address surfaces against the logged-in website.
- [ ] Test live additions, replacements, removals and quantities on each provider.
- [ ] Verify displayed live totals against both provider apps.
- [ ] Test OpenAI microphone and multilingual corrections with real recordings.

Current implementation evidence is contract/unit tests and simulator checks, not authenticated provider success. An empty local credentials file is prepared and ignored by Git. Payment is outside this milestone; the broader eventual purchase goals remain future work.

## 1. Product contract

Build a native iOS app that makes buying groceries approachable for older adults and people who struggle with commerce interfaces. Users speak naturally, hear useful choices, correct themselves, compare matching products across Blinkit and Instamart, and explicitly approve a reviewed order.

The product must be able to answer these questions without losing state:

- What does the person actually want, including brand, formulation, size, quantity and budget?
- Are they asking to search, compare, add, remove, replace, or change a quantity?
- Is an apparent correction replacing an earlier value rather than adding to it?
- Are the options equivalent, in stock, and valid for the same delivery address?
- Is the displayed comparison an item subtotal or a final payable total?
- Has the user approved the exact current basket, delivery address and payment method?
- Did the provider confirm an order, or did we only open payment/click a button?

Latest verification: 30 local backend/DOM tests and Ruff pass; both provider HTTP journeys validated live through Pending and cleanup; live-only disconnected-home simulator test passes. Earlier sample-journey evidence below is historical and has been superseded. Checked implementation items below do not imply authenticated live validation.

### Non-negotiable goals

- [ ] G1: One approachable iOS entry point with large voice controls and spoken replies.
- [ ] G2: OpenAI master understands multilingual intent and preserves corrections.
- [ ] G3: Separate provider workers obtain grounded product information.
- [ ] G4: Exact-match comparison distinguishes item price from payable total.
- [ ] G5: Edits preserve unrelated basket items and invalidate outdated approval.
- [ ] G6: No purchase without explicit approval of the latest summary.
- [ ] G7: Confirmed order state requires provider evidence.
- [ ] G8: Same-recording evaluation demonstrates measured improvement over a baseline.
- [ ] G9: Every work session leaves a reproducible handoff.

These boxes are final outcome checks, not scaffold checks. Do not mark G2 complete after merely writing a prompt, or G7 after a simulated order.

## 2. Scope and assumptions

### Accepted direction

- Native SwiftUI iOS app; Python backend.
- OpenAI master; explicit Instamart and Blinkit workers.
- Dedicated persistent Chrome/Playwright integrations for Blinkit and Instamart.in (supersedes the earlier Swiggy MCP plan).
- Direct microphone input, initially tap to start/stop.
- OpenAI transcription and speech output initially; benchmark Sarvam before deciding final speech stack.
- Both providers stay in scope. Blinkit is not discarded because upstream needs work.
- Item-price comparison is technically feasible. Swiggy policy interpretation remains unresolved, not declared prohibited.
- Telephone/PSTN calling later.

### Bounded first release

- One test user and one verified delivery address per session.
- Hindi/Hinglish and one participant-selected regional language for measured claims.
- Small grocery baskets; one provider per checkout.
- Search, compare, select, edit, review, approve, payment handoff and verified status.
- One-time connection/address setup may require assistance; recurring ordering should minimize typing.

### Exclusions until after first verified journey

Cross-provider split orders, silent substitutions, subscription reordering, automatic spending, loyalty/coupon optimization, post-order edits, automatic cancellation, multi-household production launch, full-duplex calls, and universal accuracy promises.

## 3. Architecture

`SwiftUI microphone/text UI → authenticated FastAPI service → OpenAI structured intent → deterministic basket controller → provider workers → normalized evidence → grounded response → speech playback`

The OpenAI master reasons about the user's request. It must not receive a general-purpose tool capable of arbitrary checkout. Application code validates intent, resolves identifiers against returned offers, manages versions and controls mutations.

### Components

| Component | Responsibility | Evidence required |
| --- | --- | --- |
| iOS app | Recording, transcript, choices, basket, review, payment handoff | Simulator build + real interaction + later physical-device audio test |
| API | Session boundaries, typed endpoints, errors, audio size limits | API tests and HTTP smoke test |
| Master | Structured intent and clarification using dialogue context | Schema validation + actual model traces with secrets removed |
| Basket controller | Atomic edits, versioning, stale-state rejection, reviews | Behavioural tests including adversarial order sequences |
| Instamart worker | MCP connection, schemas, product normalization, cart/payment reconciliation | Actual signed-in tool outputs |
| Blinkit worker | Isolated browser, explicit location, DOM extraction, verified cart/payment operations | Live browser evidence at chosen address |
| Speech | Transcription and spoken feedback | Same-recording and listening tests |
| Evaluation | Paired baseline/enhanced scoring | Reproducible report with failures and per-language counts |

### Proposed repository layout

`backend/` contains the service, provider adapters, domain logic and tests. `ios/` contains the SwiftUI project and build definition. `docs/` contains research, decisions, progress and runbook. `evals/` contains synthetic cases and later consented-data manifests; private audio is excluded from git. Temporary browser profiles and credentials must never be committed.

## 4. Evidence vocabulary and data modes

- **Sample:** bundled synthetic catalogue, prices and delivery details. Prominent label in API and app. Sample checkout never creates a real order.
- **Implemented:** code exists and is reachable, but live provider behaviour may be unverified.
- **Contract tested:** adapter parsed representative fixtures or fake MCP responses; not live proof.
- **Live verified:** actual authenticated provider call succeeded at a specified time/address.
- **Blocked externally:** credential, account interaction or permission clarification is missing; continue independent work.

No silent fallback from live data to sample data. Provider errors are unavailable/unknown, never “out of stock.” A worker returning an empty list must distinguish a successful empty result from extraction failure.

## 5. Milestone 0 — establish an executable foundation

- [x] Confirm implementation authorization from the latest user message.
- [x] Check workspace and local Xcode/Python availability.
- [x] Create this detailed plan before implementation.
- [x] Create backend package, locked dependencies and ignored local secrets/state.
- [x] Create native iOS project with microphone/local-network descriptions.
- [x] Establish health/config/session endpoints and run instructions.
- [x] Document missing credentials without exposing secrets.

**Exit check:** a new agent can install dependencies, start the service and build the app using the runbook. No previously existing user files were overwritten. **Goal check:** infrastructure supports G1/G2/G3/G9 without pretending to deliver live groceries.

## 6. Milestone 1 — commerce discovery and normalized offers

### Shared contract

- [ ] Product record contains provider, source mode, product/SKU IDs, name, brand, pack size, quantity, price in integer paise, stock state, address context and timestamp.
- [x] Unknown size/price/stock remains unknown; unknown fields cannot form exact-match comparisons.
- [x] Provider timeouts/errors are isolated; a failure on one does not invent data on the other.
- [x] Independent provider reads can run concurrently; Blinkit browser calls serialize.
- [x] Search results cannot mutate an external cart or trigger a purchase.

### Instamart

- [ ] Connect to official streamable HTTP MCP using server-side user credentials.
- [ ] Discover actual tools and save a redacted schema snapshot.
- [x] Normalize `structuredContent`; inspect error flags before parsing text fallbacks.
- [x] Map variant IDs, brands, quantity descriptions, availability and offer prices.
- [x] Handle pagination deliberately; disclose any result cap.
- [ ] Validate the selected saved address and guest-location semantics.
- [ ] Run a read-only live milk/bread search and compare against provider UI.

### Blinkit

- [ ] Reuse insights from pinned upstream source; retain license if copying code.
- [ ] Isolate browser context/profile per test user and serialize calls.
- [ ] Support user-driven phone/OTP setup without storing OTP in chat history.
- [ ] Explicitly set and verify address; never infer customer address from server IP.
- [ ] Extract structured name, brand, variant, pack size, displayed price and stock evidence.
- [x] Handle missing selectors as failure rather than returning fabricated success.
- [ ] Run live discovery at selected address and compare with visible cards.
- [ ] Record selector/access failures; do not bypass site access controls.

**Exit check:** at least one genuine normalized search per provider. Code/fixture tests alone do not pass this milestone. **Goal check:** G3/G4; does each price actually correspond to the product and delivery context?

## 7. Milestone 2 — basket controller and comparison

- [x] Maintain an intended basket separate from provider carts.
- [x] Resolve offered IDs against server-side results, never trust client/model prices.
- [x] Require positive bounded quantities; distinguish pack count from weight/volume.
- [x] Support add, set quantity, remove and replacement atomically.
- [x] Preserve unrelated lines on every edit.
- [x] Increment basket version on meaningful changes.
- [x] Reject stale client/model versions and discard stale search completion.
- [ ] Invalidate reviews/approval when basket, address, provider or payment changes.
- [x] Compare only identical known brand/formulation/pack-size/quantity combinations.
- [x] Label subtotal comparisons and unknown fees explicitly.
- [x] Do not mutate real carts to answer a price question.

**Test cases:** add milk then bread; set milk to three; remove bread; replace brand; unknown offer ID; negative/zero/excessive quantity; duplicate request; stale version; change address; cheaper different-size item; one failed provider. **Goal check:** G4/G5/G6; did a spoken edit do exactly what was intended, once?

## 8. Milestone 3 — OpenAI master and language handling

- [x] Use structured outputs with bounded intents/actions and schema validation.
- [x] Pass current basket, recent turns and available options as context.
- [ ] Keep original transcript and reply language distinct from normalized catalogue queries.
- [ ] Clarify missing brand/size or ambiguous references when necessary.
- [ ] Interpret addition, replacement, quantity correction, removal, comparison and stop distinctly.
- [x] Never infer purchase authorization from a model-only boolean.
- [x] Treat provider text as untrusted content, not instruction.
- [ ] Model errors/refusals/timeouts produce a recoverable message with unchanged basket.
- [ ] Validate actual Hindi/Hinglish and regional-language requests with configured API access.

**Representative requests:** “do packet… nahi teen”; “Amul nahi Mother Dairy”; “same size but cheaper”; “bread mat add karna”; “remove the milk” when two variants exist. **Goal check:** G2/G5; natural language intelligence should improve basket correctness, not just fluent replies.

## 9. Milestone 4 — native iOS journey

- [x] Home screen with clear EasyDial identity, large microphone and sample/live status.
- [ ] Permission denial has recovery instructions; recording starts only after permission.
- [ ] Tap start/stop; obvious listening/processing state; no background recording.
- [ ] Upload short audio to backend, show transcript, let user retry or type.
- [x] Offer cards show provider, exact variant, price basis and stock.
- [x] Basket has accessible quantity/remove controls and spoken-edit route.
- [ ] Review screen shows total basis, address, provider and payment choice.
- [ ] Spoken output identifies AI-generated voice and has replay/stop controls.
- [ ] Accessibility labels, Dynamic Type, readable contrast and >=44pt targets.
- [ ] Handle offline/API failure, processing cancellation and foreground return.
- [ ] Simulator compile and UI interaction checks.
- [ ] Physical-device microphone/playback/local-network check.

**Goal check:** G1; can an intended user progress without reading developer configuration or navigating dense menus? Developer settings belong in a separate screen. Sample data must remain conspicuously labeled.

## 10. Milestone 5 — cart synchronization and payment

This milestone follows live discovery and requires a user-authorized test basket for actual purchase. No default endpoint should expose unchecked live spending.

- [ ] Inspect existing provider cart and explain reconciliation before overwriting it.
- [ ] Instamart: submit full desired basket and re-read; reconcile removed/capped items.
- [ ] Blinkit: verify every add/decrement against cart readback; do not accept click logs as state.
- [ ] Fetch complete bill and available payment methods.
- [ ] Present final current summary with delivery address and attribution.
- [ ] Bind explicit approval to basket version, total, address and payment method.
- [ ] Revalidate after approval; require renewed approval for material change.
- [ ] Use transaction locks and persist operation IDs before calling purchase tools.
- [ ] Resolve uncertain outcomes before retrying; prevent duplicate placement.
- [ ] UPI: return supported app intent/QR and track terminal payment state.
- [ ] Confirm only successful payments not already auto-confirmed.
- [ ] Verify order ID/status; handle multi-store partial success without repeating successful orders.
- [ ] Distinguish pending, failed, confirmed and unknown states in speech/UI.
- [ ] Do not claim cancellation or post-order changes the provider cannot perform.

**Goal check:** G6/G7; is the approved order exactly the placed order, and what evidence proves placement?

## 11. Milestone 6 — evaluation and user validation

- [ ] Choose participant(s), regional language and consent/retention approach.
- [ ] Add synthetic text cases for controller regression, clearly separate from speech benchmarks.
- [ ] Collect held-out recordings, native-speaker labels and speaker splits.
- [ ] Run baseline and enhanced harness on identical files and fixed model versions.
- [ ] Measure brand/variant/quantity accuracy, exact basket match and correction success.
- [ ] Report clarification burden, task completion and latency median/p95.
- [ ] Compare Sarvam with OpenAI using the same downstream controller.
- [ ] Test speech output intelligibility and real payment handoff with a target user.
- [ ] Document failures and language boundaries honestly.
- [ ] Prepare a Build Challenging demo; Creative remains a valid alternate narrative.

**Goal check:** G8; is improvement measured on identical held-out examples, rather than inferred from vendor marketing or selected successes?

## 12. Reliability and privacy checks

Credentials stay server-side and are excluded from logs/git. Sessions require unguessable credentials and expiry. Restrict public exposure until authentication, rate limits and token storage are suitable. Development tokens/local storage are not production security. Audio uploads have size/duration limits and are discarded after processing by default. Browser profiles and account tokens are isolated. Never collect UPI PINs. Returning private addresses requires the correct session. Production access is a separate gate from local development.

## 13. External dependencies and fallback policy

| Dependency | What is needed | Independent work while unavailable |
| --- | --- | --- |
| OpenAI | API key and model access | Controller, app, sample journey and adapter tests |
| Swiggy | User authorization/address; callback approval where required | MCP transport and schema/normalization tests |
| Blinkit | User browser login and serviceable address | Parser, profile isolation and read-only adapter |
| Price comparison policy | Clarification of exact consumer use case | Comparison logic on synthetic data; single-provider live path |
| Actual purchase | Explicit basket/amount/address/payment approval | Review states and failure simulations |
| Participant/audio | Consent and recordings | Evaluation protocol and synthetic intent cases |

Do not ask the user to paste secrets into chat. Provide a local environment-file setup path. Do not send outreach without instruction. Missing credentials do not justify stopping independent implementation.

## 14. Verification ladder

1. Domain/unit tests: basket invariants, exact comparison, stale approval, amount handling.
2. Adapter tests: structured/text MCP errors, missing stock/size, provider isolation.
3. API tests: session ownership, invalid input, stale version, missing credentials, safe sample checkout.
4. HTTP smoke: real local process creates a session, searches sample data, edits, reviews and completes a sample journey.
5. iOS build: simulator SDK, no signing required; warnings reviewed.
6. iOS interaction: app talks to service; UI reflects actual API state, including errors.
7. Credentialed tests: speech, live search/cart and authorized purchase, recorded separately.
8. Target-user and paired speech evaluation.

Passing a lower rung does not imply a higher rung passed. Re-run affected checks after changes; avoid repeating unchanged checks without a reason.

## 15. Session checkpoint template

At each milestone and before stopping record:

- Files/features changed and which goal they serve.
- Exact commands/checks run and their result.
- Sample versus live evidence.
- Unresolved risks and external inputs.
- Next concrete task and startup instructions.
- Whether plan scope changed and why.

Update `docs/DECISIONS.md` for architecture/scope changes and `docs/PROGRESS.md` for actual status. Never leave “research only” as the current state after implementation authorization.

## 16. Definition of done

The project is complete only when an intended user can speak a supported-language request, choose a correctly matched live option, revise the basket, approve the current total/address/payment method, complete payment and receive a verified order result; comparison behaviour and supported providers are represented honestly; the evaluation and runbook are reproducible. A useful sample build is an intermediate deliverable, not completion of the product goal.

## Execution ledger

| Date | Checkpoint | Result | Next |
| --- | --- | --- | --- |
| 2026-11-21 | Build authorization and environment | Xcode 26.5, Swift 6.3.2, Python 3.11 and uv available; no OpenAI key in process environment | Foundation, typed controller and provider discovery |
| 2026-11-21 | Backend foundation | 13 tests pass, read-only provider adapters, structured OpenAI intent/audio endpoints; no credentials used | Live auth and provider smoke tests pending |
| 2026-11-21 | iOS initial journey | Revised sample UI journey and final simulator build passed | Physical-device audio and target-user testing pending |
| 2026-11-21 | GitHub | Public repository; research, plan, backend, iOS, CI and handoff commits pushed | Remote backend and iOS CI passed (run 44871504663); credentialed validation next |

| 2026-11-21 | Live-only request | Removed runtime samples; real provider cart review and Pending checkout implemented; 26 contract/unit tests pass | Authenticate and verify both providers |
| 2026-11-21 | Visual correction | Full-width white-on-blue primary actions; refined home, composer and supporting text; simulator checks | Credentialed basket screenshots and device usability |
| 2026-11-21 | Remote verification | GitHub run 44872856799 passes backend and iOS build at 06ef361 | Account login is the remaining live-validation prerequisite |
