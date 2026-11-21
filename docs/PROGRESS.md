# Onboarding and AWS audit — 2026-11-21

Inspected live source structure for requested public onboarding. Current service accepts one WhatsApp sender, binds one database to those profiles and constructs one shared set of store workers. Chat-based store login/OTP and saved-address selection are absent; current login is manual via local setup tools. No deployment packaging/infrastructure found. Added ONBOARDING_DEPLOYMENT_READINESS.md with evidence, proposed customer journey and release gates. No code/runtime change, cloud provisioning, login or purchase during this audit.

---

# Order confirmation investigation — 2026-11-21

User reported an order visibly arriving while the WhatsApp monitor exhausted its checks. The saved UPI attempt contains seven reads and only a pending result: no parsed order or rejection reason was retained. User then manually cancelled the order and requested no live check; no merchant navigation, order submission, or payment was performed for this investigation. The earlier failure's exact rejected field cannot be recovered from that record or the screenshot (which omits payment/date/address details).

Hardened Blinkit parsing to read the status immediately below Order summary, as shown in the supplied screenshot, rather than relying exclusively on the history-card title. Cancellation/failure takes precedence over an older arriving status. All identity, baseline, timestamp, amount, basket, address and paid-state checks remain mandatory. Added private field-name-only rejection diagnostics, parse issue codes and exception class (no exception message/personal values), so the next unmatched order is diagnosable. These diagnostics are not sent to WhatsApp. Validation: 149 tests passed, 10 opt-in DOM tests skipped; Ruff passed. Server restarted with these changes.

Regression coverage includes the visible ₹123 receipt structure with synthetic/redacted order details, current detail status overriding an unknown card status, cancellation precedence, immediate stop after first verified paid match and safe pending diagnostics. This is a hardening fix, not evidence that the exact live failure has been reproduced/resolved. Next validation: a future user-authorized purchase must confirm on first matching history read; if pending, inspect the new reason fields before changing matching rules. Do not use the cancelled order to claim prior payment success or initiate a replacement purchase.

---

# Inline WhatsApp payment QR — 2026-11-21

Blinkit UPI now queues a payment_qr artifact reference instead of a payment webpage link. The sender-scoped, unexpired verified merchant PNG is uploaded through Meta media and sent as an image with amount and "Please complete your payment using the QR above." Check status follows through the existing delivery dependency. No redrawing or payment regeneration. Expired/finished artifacts are not sent; QR rejection never falls back to a caption implying the image arrived. Existing legacy payment pages remain compatible but are no longer advertised in the new reply.

At the user's request, queued the same still-valid existing merchant QR as an image plus a dependent status button; no new payment request created and no payment authorized. Meta marked both the live QR image and subsequent status button delivered. Validation: 143 standard tests passed (10 opt-in DOM skipped); Ruff passed. Tests cover exact PNG upload, image media ID, expiry, ownership and no misleading caption fallback.

---

# Blinkit discounted subtotal parsing — 2026-11-21

The reported ₹123 bill was correct. Live inspection found the Items total amount column contained a struck-out ₹55 and current ₹50, while its label column included a Saved ₹5 badge. The old reader captured `₹55 ₹50` and marked subtotal unverified. Updated visible-DOM bill extraction to scope to the amount column, exclude struck/hidden nodes (including ancestor styling), and accept one unambiguous amount or explicit free/waived text. Multiple current amounts remain unverified; no subtraction-based inferred subtotal or relaxed payment guard.

Read-only live revalidation returned ₹50 + ₹35 delivery + ₹13 handling + ₹25 small-cart = ₹123, reconciled=true and payment choice allowed=true. No cart changes, order placement or payment. Validation: 141 standard tests passed (10 opt-in DOM skipped); all 10 DOM tests passed separately, including the exact discounted subtotal structure, savings badge, hidden amounts, free fee and ambiguous values. Ruff passed.

---

# Basket contents and store answer — 2026-11-21

The retained request asked both what was in the cart and whether it belonged to Instamart or Blinkit. The intent correctly returned basket, but the fixed basket formatter omitted provider. Summaries now derive the store from the actual selected offers and include it in the heading; removed the duplicate modification question from the item summary while retaining the Review basket action. Added the exact question to model evals and an execution regression. Validation: 141 standard tests passed (8 opt-in DOM skipped), 28/28 real-model intent evals passed, Ruff passed. No live cart mutation.

---

# Explicit store-switch repair — 2026-11-21

Inspected the retained conversation: the voice request correctly transcribed removal from Instamart and adding the same product on Blinkit. This was a harness error, not ASR: provider preference was absent from structured intent, choices were locked to the existing basket store, and free-form model clarification promised an unexecuted switch.

Added target_provider and explicit switch_store intent, included current provider in model context, restricted provider calls/results to the requested store, and removed unchecked model promises from search replies. Stage a one-product store move without deleting the source basket; selecting the destination validates the new draft and clears the previously synced source cart before committing the switch. Pending moves block review/checkout of the old store. Unavailable destination never falls back. Basket changes, failed clearing and existing payment attempts remain guarded. Multi-item moves currently ask for clarification instead of silently deleting other lines.

Validation: 139 standard tests passed (8 opt-in DOM tests skipped), followed by all 6 focused store-switch tests after adding destination-retention coverage; Ruff passed. Real-model gpt-4.1 evaluation: 27/27 passed, including the exact reported sentence, Hinglish move and compare-without-moving. Source/destination execution tests are synthetic; no live customer cart was changed for verification. See STORE_SWITCHING.md.

---

# WhatsApp ingress outage recovery — 2026-11-21

Local server was healthy and prior replies were read, but no new inbound events arrived. The configured temporary Cloudflare hostname no longer resolved despite a running cloudflared process. Recreated the tunnel, updated private WHATSAPP_PUBLIC_URL, restarted the server, and verified/saved the new callback in the existing Meta dashboard. Public health and webhook challenge both returned HTTP 200; access token was valid at inspection. Added local/public health and last-inbound-age checks to the safe status tool. No order/payment events replayed. Fresh user test arrived, its reply was marked read, and the user confirmed replies work again.

---

# Durable post-payment verification — 2026-11-21

Built sender/order-scoped history baselines and immutable approved snapshots. Cash submission now checks merchant history automatically; QR links include Check status, and payment-completion claims trigger read-only verification. Poll at most ten times, three seconds apart, within a 30-second read budget. Missing/ambiguous/unpaid/unreadable history remains pending with a button; never retry submission or infer payment from a message. Match new IDs, timestamp, provider, amount, address and items. Instamart uses exact catalog IDs because its history omits pack text. Old status buttons remain usable for the same order after review expiry.

Validation: 134 standard tests passed (8 opt-in DOM tests skipped), then all 8 DOM tests passed separately; Ruff passed. Live read-only history parsed ten records from each store; fixed Instamart millisecond timestamps. No order or payment submitted. Final real cash/paid-QR acceptance remains outstanding. See POST_PAYMENT.md for evidence, matching rules, tests and limitations.

---

# Restricted payments and explicit COD submission — 2026-11-21

Implemented Instamart pay on delivery only; Blinkit pay on delivery when eligible plus UPI QR. Filter current menus and reject historical disallowed selections. Cash selection verifies the merchant screen, then requires a separate versioned, review-bound, five-minute Place order button with amount and provider. Only that explicit action reaches submission. Private cash_orders table reserves customer/order before final click, prevents duplicate submissions and retains unknown attempts after timeout/restart. Successful replies require merchant confirmation text and order identifier; an unverified click is never reported as a confirmed order.

Corrected Blinkit accordion handling following user feedback: close Cards if expanded, open Cash/UPI idempotently, wait for content. The initial Cash failure also had explicit below-₹50 store restriction. Above-threshold basket enabled Cash; final Pay Now control is outside the iframe. Added independent BLINKIT_BROWSER_HEADLESS override; local pilot uses visible Chrome, Instamart retains configured mode. No challenge bypass.

Live evidence: actual filtered Instamart menu contained only cash, cash preflight verified ₹156. Actual Blinkit menu contained cash and UPI QR, cash preflight verified ₹133. Generated a real Blinkit QR and decoded matching ₹133/INR/reference with 300-second expiry. All temporary Blinkit cart edits cleared and verified. No final cash click, paid transaction, or real order placement. Final confirmation/order-status parsing remains unverified against a real order; QR payment outcome is not automatically verified.

Validation: 118 standard tests passed (8 opt-in DOM skipped); all 8 Chrome DOM tests passed separately, including card closure, idempotent UPI expansion and disabled cash rejection. Submission tests cover separate final confirmation, stale button rejection, once-only submission, preflight failure, uncertainty and restart recovery. See PAYMENT_HANDOFF.md for the current policy and remaining limits.

---

# Blinkit headed comparison — 2026-11-21

User requested an explicit non-headless comparison. With the same configured saved Blinkit profile and browser channel, headless inspection was blocked; headless=False loaded the homepage (normal Blinkit title, no challenge text), passed browser.check_page, and navigated successfully to milk search. No cart modification or payment inspection in this comparison. This supports a browser-mode difference but does not identify the detection mechanism or establish reliable payment access. Production headless setting unchanged.

---

# Confirmed bill to live payment options — 2026-11-21

Implemented the requested Instamart journey: disclose listed charges versus merchant payable, accept confirmation of the payable amount, then independently verify that exact amount on the merchant payment screen before returning live methods. Do not invent rounding or mark arithmetic reconciled. Unknown charges/conflicting totals remain blocked; cart/address/fingerprint validation remains. Approval state is recorded only after successful method/amount inspection.

Live end-to-end harness test on a private in-memory copy of the current draft reached Cards, Wallets, Netbanking, Pay on delivery at ₹156. No customer draft persisted, no WhatsApp message sent, no method chosen and no payment/order submitted. Method subpages inspected read-only; inputs documented in PAYMENT_HANDOFF.md. Blinkit headless recheck blocked; headed follow-up requested by user.

Validation: 114 tests passed, 7 DOM skipped; Ruff passed. New tests cover disclosed discrepancy, unknown charge rejection, payment-screen amount match/mismatch/missing amounts and no approval on failed menu retrieval.

---

# Instamart bill discrepancy investigation — 2026-11-21

Read-only inspection of the authenticated website and its own cart response confirms the mismatch is upstream of WhatsApp formatting. Visible current fees are ₹85 + ₹12 + ₹23.60 + ₹35.70, with ₹156 To Pay. Cart response billBreakupDetails.subTotal and subtotalWithoutDiscounts are 156.3; bill.toPay is 156.0. No explicit rounding field was found. Handling fee has a struck-out prior price; the DOM correctly excludes it. No inferred adjustment added, no cart mutation or payment operation performed. Do not claim the discrepancy resolved.

Latest two dependent outbox rows (98/99) were sent and marked read. Sent payloads are redacted, so receipt evidence alone cannot establish their exact content. Server restarted and operational after inspection. Blocked-bill Change basket/Save for later actions from prior commit remain installed.

---

# Blocked-bill next actions — 2026-11-21

Added Change basket and Save for later WhatsApp buttons after a persistent bill mismatch. Both are version/review-bound, deterministic, and perform no provider or payment operation. Change basket prompts for additions/removals/changes by text or voice; Save for later confirms the existing saved draft without pausing or clearing it. No manual bill retry is delegated to the user. Checkout progress now says Checking your final total rather than prematurely claiming to find payment options. Payment stays blocked while charges differ. Existing delivery-receipt ordering keeps the buttons after the bill.

Validation: 108 tests passed, 7 DOM skipped; Ruff passed. Tests cover blocked-bill buttons, both handlers, unchanged draft and stale review rejection. The store’s ₹0.30 mismatch remains unresolved; these buttons improve recovery choices without fabricating a payable amount.

---

# Automatic bill recovery — 2026-11-21

Supersedes the manual Recheck total UX below. User requires the agent to handle bill verification itself. Review, checkout, and payment-method selection now retry an inconsistent bill with up to two additional read-only provider revalidations. Never repeat cart synchronization or invent rounding. Checkout refreshes expired bills automatically and presents fresh terms for approval. Any changed or inconsistent bill revokes payment preparation and cannot generate a payment request. Progress can say Double-checking the store’s total using the existing throttled progress mechanism.

Persistent inconsistency produces a short explanation that the store total cannot be verified and the basket is saved; no technical reconciliation error or manual recheck button. This is bounded in-turn recovery, not background polling. The previously observed ₹0.30 discrepancy is not proven resolved. No live cart/payment mutation during this change.

Validation: 106 tests passed, 7 DOM skipped, Ruff passed. Added initial-review recovery and checkout transient/persistent mismatch coverage, preserving basket and blocking payment without resynchronization.

---

# Actionable cart review — 2026-11-21

Fixed contradictory review UX: a bill whose components fail reconciliation now ends with a versioned Recheck total button and an invitation to change items; it does not invite payment. Reconciled bills retain Choose payment. Simplified mismatch wording. Regression verifies the reported ₹85 + ₹12 + ₹23.60 + ₹35.70 versus ₹156 case stays blocked; never invent a rounding adjustment.

Read-only headless inspection confirmed the persisted Instamart website cart has one Britannia Milk Bikis Biscuits 500 g. The same fee discrepancy persists; no rounding text was observed. Review already synchronizes and reads back the website cart before presenting the bill. Mobile-app account/cart synchronization is unverified, so do not promise that confirmation will populate it. No cart writes or payment actions during this inspection. Server restarted after inspection.

Validation: 103 tests passed, 7 DOM skipped; Ruff and diff checks passed. Actual mobile-app synchronization and missing ₹0.30 explanation remain unresolved.

---

# Ask to repeat unclear speech — 2026-11-21

User explicitly requested no guessed action when speech or intent is unclear. Added structured unclear_request output with a deterministic clarification override before action validation/execution. All proposed searches, edits, selections, review, checkout, restoration and payment actions are discarded when flagged. Rejected deletion checks now also ask to repeat instead of suggesting checkout or removal. Reply: "I couldn't understand that clearly. Could you say it again?" Basket remains unchanged. Detecting uncertainty remains model-dependent; ASR is unchanged.

Validation: 102 tests passed, 7 DOM skipped; Ruff passed. Real-model text eval 24/24, including the reported garbled transcript returning unclear_request=true and the exact repeat prompt. No live cart or payment actions performed. Report: evals/intent-repeat-request.json.

---

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

## 2026-11-21 — Blinkit onboarding location timing fix

The fresh Blinkit page renders Login before its location input. The onboarding adapter previously checked location visibility once immediately after DOMContentLoaded, skipped the delayed gate, and timed out. A simple wait for either Login or location was insufficient in a live check.

StoreLogin.open_blinkit_login now observes both gate and phone-entry state in a bounded loop. It fills a temporary Koramangala Bengaluru locality, selects the visible result, waits for the gate to close, and opens Login. Repeated actions are limited to opening the login dialog; phone/OTP submissions are outside this loop. Temporary locality is never saved as a verified delivery address. Unexpected onboarding failures log provider, stage and exception class only, never sensitive exception text.

Verified the patched helper on a fresh live Blinkit context through phone entry without submitting a phone, requesting OTP, or placing an order. Added Chrome regressions for delayed rendering, an already location-ready page, and Login rendering before the location gate. Full customer OTP/address selection still requires the user's next WhatsApp attempt. Meta delivery is now reaching onboarding; prior 131005 incident is no longer the active reported blocker.

### Blinkit saved-address selection correction

Customer completed OTP successfully but address selection failed. The reader matched both AddressDetailsWrapper and AddressDetails and chose the longest text (including Home), then matched that combined rendered text against DOM descendant text. This produced the duplicated Home label and failed selection. Read only the innermost address text; retain its row index; re-read and uniquely validate address identity before selecting the freshly resolved row. Check exact innermost text again before clicking. Never use a substring to choose between similar addresses. Added a Chrome regression covering nested labels, overlapping address strings, successful session-save invocation, and rejection of an address changed since presentation. That regression and 16 onboarding tests pass. The failed login context was already closed by existing error handling, so the customer must reconnect once to test live selection; no OTP was replayed.

### Login rate limit scoped to merchant phone

Changed the three-attempt/15-minute guard from WhatsApp sender to a keyed HMAC of provider plus normalized Indian login phone. A different phone or store gets its own budget; the same phone cannot evade the limit by using another WhatsApp chat. Counter rows never contain the raw login phone. Existing rows without a phone hash are retained but cannot be attributed and are excluded from the new account-specific limit; this is a one-time migration reset, not a deletion of history. The reply shows actual remaining rounded-up minutes. Attempts are still reserved before external work to avoid replaying uncertain OTP submissions; pre-submit failure accounting is a remaining refinement. Seventeen onboarding tests pass, including cross-phone, cross-store, cross-chat, phone normalization, legacy migration, and remaining-time assertions.

### Preserve pre-login prompts across server restarts

Previously all onboarding stages except store/ready were reset to store on startup, including the phone prompt after recorded consent. This caused a phone reply after maintenance to receive a repeated text-only store prompt. Preserve consent and phone stages (including their existing expiry); reset only stages that require a lost browser challenge. Store-stage invalid replies now return Blinkit/Instamart buttons with an explanation that store selection comes first. Never infer a store or start login from a bare number without the required selection/consent. Nineteen onboarding tests pass, including phone-prompt restart recovery and action-button recovery.

### Duplicate saved-address entries no longer block onboarding

The remaining failure came from hashing only street-address text and rejecting the whole list when duplicate hashes existed. Replaced that identity with a hash of the ordered visible address-list snapshot plus row index for both providers. Each displayed choice remains distinct even with identical labels and street text. A changed or reordered snapshot fails validation before clicking; exact street text is rechecked immediately before selection. Instamart now also uses the verified row index rather than substring matching.

Read-only verification using the existing operator Blinkit inspection profile returned 14 entries, 14 unique choices, and one duplicate street-address occurrence. No address selection, OTP request, cart action or order occurred during that live inspection. All 38 onboarding and Chrome DOM tests pass, including identical entries on both providers, choosing the second duplicate, and rejecting a reordered list. Customer OTP-to-address confirmation still awaits live retry. Earlier claims were too broad: prior tests lacked the actual duplicate-address shape, and the exception handler discarded the authenticated context.

## 2026-11-21 — Sequential multi-product requests and search relevance

Incident evidence: saved shopping transcript was exactly "Order four Diet Cokes and one popcorn." Saved results contained Diet Coke (error: selected-location mismatch) and popcorn (success including unrelated recommendation cards). The original voice transcription did preserve both items. A live interpreter check also returned Diet Coke quantity 4 and popcorn quantity 1 for English and Hinglish examples.

Changes:
- Search one requested item at a time. Persist remaining RequestedProduct objects, including quantity and replacement IDs, in the customer state. After selecting the first item, search the next at the selected store. Do not mix choices from different requested items into one menu.
- Show the full request summary, then current-item options. Failed current items expose Retry item / Skip item buttons and block automatic advancement. Preserve failed item quantity in user-visible text and model context.
- Filter browser search cards conservatively against query terms and pack text, retaining brand/variant terms (Diet Coke cannot become Sprite Zero or regular Coke; popcorn cannot become Crax puffs). Small alias mapping supports dahi/curd and Coke plural. This may omit legitimate matches with unfamiliar synonyms; it must not invent substitutions.
- Blinkit's homepage can briefly show a placeholder/default locality while restoring saved state. Wait up to five seconds for the expected location; continue rejecting a persistent mismatch.

Validation: 177 standard tests pass (19 opt-in DOM tests skipped in that run), Ruff passes. New tests cover unrelated cards, variants, partial failure, retry/skip flow, one-item-at-a-time search, and quantity preservation across selection (4 then 1). Existing payment invalidation and atomic failure tests remain passing. No customer basket was changed by the repair, no OTP requested, and no order/payment submitted. Customer-selected Crax remains until the user requests a change; never silently replace an already-selected item.
