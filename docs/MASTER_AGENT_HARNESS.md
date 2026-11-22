# Master agent north star and implementation plan

Updated 2026-11-22. Status: local implementation and verification; not released.

## North star

WhatsApp is the primary shopping experience we ask the customer to use in place of opening the store app. Success means the customer can obtain the information, choice, control and order visibility needed to shop confidently through our service. A correct intent label or successful tool call is not sufficient if the customer still cannot complete their goal.

A customer can express a shopping goal naturally, in Indian languages or mixed language. The master understands the goal and conversational references, chooses available tools, examines their results, and either completes the supported task or asks a useful clarification. Product questions, comparisons, follow-ups and purchasing are parts of one continuous experience rather than separate modes the customer must learn.

Tools define reliable capabilities; the model decides which capabilities to use and in what sequence. Customer phrases and individual examples must not become routing rules. Novel combinations of supported capabilities should work without another intent-specific branch. Unsupported capabilities and missing evidence must be explained honestly. Do not claim store-app parity until customer journeys demonstrate it.

### Customer-journey acceptance lens

- Discover: browse categories/brands, compare the right pack sizes and current prices, retain store preference, and distinguish uncertainty from missing inventory.
- Inspect: see the selected product's images, ingredients, nutrition, description and other store-provided details; ask follow-ups without repeating the name or losing the basket.
- Decide: understand tradeoffs using actual product evidence; return from questions to the same product/selection context and explicitly choose what to buy.
- Purchase: add/change/remove items, see a verified itemized bill and available payment options, then receive real order confirmation and tracking. Existing authorization and verification remain mandatory.
- Recover: preserve progress when store/proxy/tool failures happen, explain the actual problem usefully, and provide an actionable next step. Repeated generic retry prompts are not a complete recovery experience.
- Continue: resume a conversation, inspect a past purchase, or start a fresh basket without stale payment/cart state leaking into the new journey.

Use these journeys for product acceptance and regression testing. Instrument latency, tool failures and unresolved user goals so gaps are visible. The customer should not need to know internal tool names, workflow stages or which phrasing the developer anticipated.

### Capability gaps must remain visible

The present local implementation adds product exploration and flexible tool selection; it does not establish full store-app parity. Instamart rich product details remain unsupported, and its live exploration path still needs account-backed verification. Promotions/coupons, delivery-slot changes, returns/refunds, merchant support and cancellation guarantees are separate capabilities requiring explicit implementation and validation. Never imply those actions succeeded because the model can discuss them. Address them as product gaps, rather than by teaching the customer special phrases or inventing an answer.

## Invariants

- Existing search, selection, cart edits, cart clearing, review, reconciliation, payment handoff, confirmation, notifications, account management and customer isolation remain operational.
- Only explicit customer intent authorizes basket changes. Existing bill, address, stock, quantity, payment and purchase authorization checks remain authoritative.
- Questions and comparisons preserve the basket, current selections, checkout/payment preparation and order records.
- Merchant data and model tool arguments are untrusted. Backend validation controls provider access, product identity, tool scope and allowed actions.
- Live prices, availability, ingredients and image claims come from verified store observations. Missing data stays unknown. Image URLs are restricted to observed approved store media.
- No arbitrary code execution, arbitrary browser clicks or model-selected network URLs.
- Ordinary ordering can choose an existing shopping action in one model call. Extra calls are bounded and used when tools are needed to answer the request.
- Local Blinkit tests use no proxy. Tests do not submit real orders/payments or diagnostic customer messages.

## Architecture being built

1. Replace the fixed intent-only decision call with a bounded model-driven tool loop, retaining the existing context and transaction instructions.
2. Expose read-only catalogue search, product inspection and image delivery tools. Search supports connected Blinkit/Instamart workers. Blinkit product details/gallery use observed browser controls. Unsupported provider details must report their limitation.
3. Expose a validated handoff to the existing shopping/account execution flow. The model chooses the action; existing code enforces state and authorization. Existing button handlers remain intact.
4. Keep bounded product exploration context for follow-ups without replacing the active cart/menu or silently turning inspected products into selected products.
5. Return concise answers grounded in tool observations. Explicit requests to buy a researched product re-enter normal verified selection/review/payment.

## Ownership

- Primary agent: conversation integration, plan, live model checks, regression review and release boundary.
- Master-harness agent: bounded tool loop, capability schemas, grounded answers and action handoff.
- Product-tools agent: read-only provider search/details/gallery with identity checks.
- Regression agent: tool-loop behavior, isolation, invalid calls, budgets, failure paths and existing-flow protection.

## Acceptance criteria

- Previously unseen phrasing can combine tools to compare products/prices, inspect details/ingredients or show gallery images.
- Context resolves an unambiguous product reference; ambiguous brand, variant, store or ordinal references ask clarification.
- Questions during selection, review or payment leave all transactional state unchanged.
- Explicit purchase after exploration continues through the existing purchase flow; an informational request never triggers purchase actions.
- Product/tool errors produce honest explanations; unsupported details are not fabricated.
- Existing account, cart and payment regression suites pass, including onboarding-to-confirmation and subsequent fresh shopping.
- Live local browser verifies Blinkit detail expansion/gallery retrieval without cart mutations. Live model checks exercise multilingual understanding and tool choices, with controlled responses where necessary to avoid real transactions.
- Report separately: mocked journey tests, live browser evidence, live model evidence, AWS verification and remaining blockers. Passing one is not proof of another.

## Release boundary

The repeat-order optimization remains reverted. This work is a separate local change. The user authorized commit and push after proper testing; release through the existing CI gates only after final regression checks pass. An external proxy failure is a separate blocker and must not be hidden behind application-health success.

## Progress

- [x] Inspect current fixed-action interpreter and purchasing guards.
- [x] Verify Blinkit product detail expansion and multi-image gallery in local Chrome without proxy.
- [x] Record north star, invariants, ownership and acceptance criteria.
- [x] Implement master tool loop and existing-action handoff.
- [x] Implement verified read-only product tools.
- [x] Integrate bounded follow-up context without changing shopping state.
- [x] Run adversarial/tool-error and existing purchase regressions.
- [x] Run live model and read-only browser checks; document actual results.
- [x] Review release readiness and outstanding limitations (local build only; live AWS verification outstanding).

## Implemented behavior and evidence

The master uses model-selected function calls: research catalogue, inspect exact product, attach observed gallery images, ask a clarification, or hand a typed shopping/account action to the existing executor. Ordinary purchase discovery can return that action immediately. Tool use is capped at five model rounds, six calls and 100 seconds; these are ceilings, not deliberate waits.

Exploration context is separate from shopping choices and includes bounded product references and gallery assets already shown. Old prices are not treated as fresh quotes. Informational replies preserve selection, basket, review and payment state. Failed/cancelled execution restores exploration context and drops unsent image payloads, so later buttons cannot replay them.

After reading untrusted merchant data, the harness restricts action handoff to clarification or a clean new product search (no edits, replacements, store switch or clearing). Thus research can lead to the normal product-selection flow, but cannot directly mutate a cart/account or advance checkout in that same research turn. Direct customer transaction requests still use the existing guarded actions. This is a structural authorization boundary, not a phrase-matching rule.

Both Blinkit and Instamart exploration searches use temporary tabs and existing address guards; the primary checkout page stays open. Blinkit details validate product ID, name and pack before reading the expanded section and observed gallery. Instamart detailed-page extraction is not implemented; it reports unavailable rather than inventing ingredients. Ordinary purchasing search is unchanged.

Validation recorded locally:
- Live Blinkit product reader: actual ingredient section and 12 observed gallery URLs, first six at displayed main-image resolution; 3.83 seconds after initial search, no proxy/cart mutation.
- Live isolated Blinkit research search: 20 current offers in 1.75 seconds, primary page unchanged and temporary tab closed.
- Live model + Blinkit conversation: Hinglish ingredient/three-photo request returned actual listed ingredients and three gallery images in 9.98 seconds. Follow-up requesting unseen images returned three different images in 5.57 seconds. Shopping state stayed identical; no customer messages were sent. The final telemetry-enabled repeat also passed: 13.14 seconds for ingredients/three images and 5.97 seconds for three unseen images, with shopping state unchanged.
- Live configured gpt-4.1 with controlled catalogue: two-product Hinglish purchase preserved both products and quantities; Hindi price comparison used read-only results; scoped logout handed off correctly. Model unchanged.
- Ambiguous brand-versus-category interpretation is variable: some Red Bull/Maggi trials clarified before search; others searched first. This is not claimed as universally solved. Empty search evidence must not be converted into an out-of-stock claim.
- Existing onboarding-to-confirmation journey also passes through the real harness with controlled external responses, then fresh shopping; ordinary discovery uses one model call per turn.
- Final backend run including telemetry: 458 passed, 82 opt-in browser cases skipped. Ruff and whitespace checks passed. The separate Chrome-enabled run passed all 92 tests (82 browser cases plus 10 supporting unit checks) in 135 seconds.

AWS integration has not been tested for this new harness. No changes have been pushed or deployed. Production remains on the reverted pre-optimization behavior. Existing AWS sticky-proxy failure is separate from this local feature and remains a live-verification prerequisite.

### Final integration review

Added a generic clarification tool instead of brand-specific routing. Any empty/failed search now activates a factual result-based response, including partial-success comparisons: verified products/prices remain available, while missing results and unavailable stores are identified without unsupported stock claims. All-success answers still use model reasoning; there is no claim-by-claim proof of every generated sentence.

Final customer-context fix: after an informational discussion, bare ordinals go through the master with the latest discussion context rather than selecting an old shopping menu. Any model-proposed selection from the old menu is rejected while research context is active, including nonnumeric wording. Fresh ordering search restores numeric selection; existing versioned product buttons remain valid. Regression tests cover this transition and rollback of unsent image context on failed/cancelled execution.

Live configured-model evaluations with controlled catalogue responses passed Bengali, Tamil and Marathi comparisons, an explicitly negated checkout followed by an Instamart comparison, and clarification for an unknown-product ingredient request (0.92–2.87 seconds in those controlled runs). These are evidence of tested behavior, not a guarantee for every utterance.

The new provider reader checks passed 16 tests with Chrome enabled (nine browser checks and seven unit cases), in addition to the existing 68 browser regressions. Additional persisted checkout journeys cover research during review/payment, stale menu references, failures, cancellation, and customer isolation. Release status is recorded separately after the CI deployment finishes.

### Structured diagnostics

Privacy-safe JSON telemetry links incoming turns, model/tool calls, browser/provider stages, retries, order checks and outgoing replies with trace/span IDs and timings. Specific proxy verification reasons and sanitized exception code locations are included; raw customer content, credentials and payment URLs are excluded. See [OBSERVABILITY.md](OBSERVABILITY.md). Regression tests verify logging does not change results, exceptions, cancellation or payment retry behavior. A final regression caught and removed an unsafe logging-only provider attribute access before release.
