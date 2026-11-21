# WhatsApp-first EasyDial: feasibility and implementation gates

Researched 2026-11-21. Historical research snapshot: this document predates implementation. **Latest status:** the user subsequently authorized building; the single-user channel is implemented with local tests. Meta credentials and live WhatsApp/OpenAI verification are still pending. Read `WHATSAPP_SETUP.md` and `PROGRESS.md` for current evidence. User direction: pause app development and investigate a WhatsApp voice-note journey. This document proposes implementation; it does not authorize sending messages, registering business assets, or purchasing groceries.

## Verdict

Technically feasible: a dedicated EasyDial WhatsApp business chat can receive voice recordings, invoke our backend, and return choices, clarifications, basket changes and checkout summaries. Customers keep their normal WhatsApp app. The agent does not need access to their personal inbox or a WhatsApp Web browser session.

Production feasibility is conditional: Meta's AI-provider classification must be resolved for our India-focused shopping intermediary, customer grocery accounts need secure linking, and payment/provider confirmation remain unimplemented. A successful messaging test would establish transport only, not business-policy eligibility or a completed purchase.

## Recommended providers and responsibilities

| Stage | Recommendation | Existing vs new |
| --- | --- | --- |
| Receive voice/text and send responses | Meta WhatsApp Business Cloud API | New channel adapter and business setup |
| Fast optional transport spike | Twilio Sandbox for WhatsApp | Alternative test transport, not required alongside Meta |
| Receive events | Public HTTPS webhook in FastAPI; signature verification, durable queue | New narrow public surface; existing local API must not be exposed wholesale |
| Audio normalization | FFmpeg, bounded decoding to WAV or supported compressed format | New; support OGG/Opus recordings, validate MIME and decoded duration |
| Transcription | OpenAI gpt-4o-transcribe, existing configurable choice | Existing integration, key absent and real recordings unverified |
| Intent and conversation | Existing OpenAI master; configured gpt-4.1-mini | Reuse with channel-specific response rendering; evaluate language accuracy |
| Product search/cart/review | Existing Blinkit and Instamart Playwright workers | Reuse; logged-in live cart tests passed previously |
| Replies | Short text + optional OpenAI speech audio; choice buttons/list or numbered replies | New WhatsApp renderer; retain voice-only answer path |
| State | Durable database mapping verified channel sender to EasyDial customer/session | New persistence and per-customer store-account mapping |
| Payment | Future secure provider checkout/payment handoff and reconciliation | Not implemented; no promise that payment stays inside WhatsApp |

Wispr Flow is unnecessary for this route: WhatsApp already captures the recording; our server transcribes it. Voice notes are asynchronous messages, not full-duplex calls. Calling is a separate scope.

## Evidence and limitations

### Official API and setup

Meta documents creating a developer app with the WhatsApp use case, connecting a business portfolio and messaging account, generating a temporary test token, selecting test sender/recipient numbers and configuring webhooks. For production, complete the account-specific publishing/number requirements and replace temporary credentials. Do not assume an existing personal number can simply be attached without migration or an eligible coexistence flow. A dedicated business number is the least confusing recommendation. [Meta getting started](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started)

Twilio documents inbound-message webhooks and media support. Its Sandbox is useful before a production sender is registered, but participants must join it; membership expires after three days, branding is Twilio's, and it has testing/rate/template restrictions. It is not the public EasyDial product and does not bypass Meta policy. [Twilio API overview](https://www.twilio.com/docs/whatsapp/api), [Sandbox](https://www.twilio.com/docs/whatsapp/sandbox)

### Voice input/output

Audio media is supported, including OGG. Normalize incoming audio rather than assuming a WhatsApp recording passes our existing upload whitelist. The existing backend has its own 8 MB upload limit; set explicit channel byte/duration limits and test transcoding expansion. OpenAI's documented transcription file formats include WAV, MP3 and WebM. For outgoing native voice-note presentation, Meta's collection documents `audio.voice` with OGG/Opus. Plain audio replies can be used first. Do not rely on WhatsApp's optional on-device transcript being supplied to the webhook. [Twilio media](https://www.twilio.com/docs/whatsapp/guidance-whatsapp-media-messages), [OpenAI transcription](https://developers.openai.com/cookbook/examples/speech_transcription_methods), [Meta audio API collection](https://www.postman.com/meta/whatsapp-business-platform/request/goznbg0/send-audio-message-by-id)

### Conversation rules

User messages open/reset a 24-hour service window. Free-form replies can occur within it; outside it, approved templates are required. Automated service must have a clear escalation path, such as human support or a contact method. Respect opt-out and applicable opt-in requirements. The window is a messaging rule, not permission to charge the user. Do not describe all service messages as free: verify current country/category rates at launch. [WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy)

### AI-policy gate — unresolved for EasyDial

Meta's current developer guidance says general-purpose AI assistants are permitted only where Meta is legally required to permit that use. Its published exception markets do not establish an India exception. Indexed Business Solution Terms distinguish primary AI functionality from ancillary AI use, but directly opening the legal URL redirected to a blocked login page during this research; this is a material verification limitation. Do not interpret successful API onboarding as approval of our use case.

Our proposed scope is a concrete grocery shopping service, with AI used to understand and fulfill requests, rather than an open-ended chatbot. That is a product-design rationale, NOT a determination that Meta accepts a cross-store agent. Before public launch, obtain current terms/use-case confirmation from Meta or the chosen business solution provider, accurately describing cross-store browsing, ordering, and OpenAI processing. A Twilio account does not resolve this gate automatically. [Meta AI-provider guidance](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/ai-providers/), [Business Solution Terms](https://www.whatsapp.com/legal/business-solution-terms/)

## Proposed customer journey

1. User opens EasyDial's business chat and sends a voice note, e.g. a request for milk and bread in their preferred language.
2. Return a short acknowledgement; transcribe and extract quantities, units, brands, negations and corrections.
3. Clarify meaningful ambiguity. Do not silently choose a pack or infer delivery address from the WhatsApp number.
4. Search both stores for the linked account/address. Return a few relevant choices with brand, variant, pack, quantity and observed price. Compare equivalent products only; show delivery/handling fees separately until full bill review.
5. User responds by voice/text or selects a choice. Bind selection IDs to the current user and basket version; reject stale choices.
6. Accept additions, removals, replacements and quantity corrections. “Compare prices” is read-only. A bare “yes” must be interpreted against the actual pending question.
7. On review, sync the selected store basket and read back the complete provider bill and address. A revision invalidates the previous review.
8. Current milestone: show Checkout Pending honestly, with no order or payment. Future purchase stage requires a fresh explicit approval bound to exact items, address, fees and total; user authentication/payment may open a secure external flow.
9. Only report an order as placed/paid after provider evidence, with order ID and payment status. A sent WhatsApp message, API 200, or clicked button is not order confirmation.

Keep responses brief and in the user's language. Provide audio for important clarifications and final summaries, while keeping a readable bill. Do not send a dense catalogue or require users to learn commands. A familiar channel is a hypothesis for lower friction; validate it with intended older users.

## Backend design and required changes

Proposed path: WhatsApp event → authenticate webhook → durable event insert/deduplication → acknowledge → per-user queue → download/validate audio → transcribe → existing controller/master → provider workers → response outbox → WhatsApp reply/status tracking.

- Verify subscriptions and request signatures using the selected provider's documented mechanism. Treat media URLs and transcripts as untrusted input; fetch authenticated media through the provider flow, never arbitrary user-supplied URLs.
- Deduplicate message IDs before effects. Serialize each customer's turns; handle voice/text arriving during a slow search, duplicate webhooks and delivery receipts without double cart writes.
- Do not hold a webhook request open for transcription/browser work. Queue persistently and provide honest progress/failure replies.
- Persist customer mapping, draft versions, provider cart ownership, pending clarification, outgoing-message IDs, reviews and approval records. Current in-memory two-hour sessions are insufficient for asynchronous WhatsApp journeys and restarts.
- Keep commerce control deterministic. The LLM proposes structured intent; application code checks scope, ownership and versions. Never let a transcript directly execute browser code or authorize payment.
- The current saved Chrome profiles belong to one operator. They can support a single-user private proof of concept only. Never route arbitrary WhatsApp senders into these accounts. Initially allowlist the test user; production requires isolated customer accounts/profiles and secure login/address onboarding.
- Store OTP/password/payment authentication should use a secure account-linking or provider interaction, not credentials copied into the WhatsApp conversation. No supported Blinkit/Instamart OAuth integration has been established.
- A public HTTPS tunnel can forward the webhook-only adapter for a development trial; the machine and browser worker must remain awake. Production needs an always-on worker host. Do not assume short-lived serverless functions can own persistent Chrome profiles.
- Encrypt sensitive session material, redact logs, minimize audio retention and provide deletion controls. Do not commit recordings, phone numbers, addresses, tokens or browser profiles to this public repository.
- Store availability, automation blocks and authentication errors remain separate from “out of stock.” WhatsApp does not improve the underlying websites' automation reliability.

## What is verified versus proposed

| Capability | Evidence level |
| --- | --- |
| WhatsApp supports inbound media/webhooks and outbound messages | Official documentation; not tested with our account |
| Voice → multilingual interpretation | Existing OpenAI code; real end-to-end recording validation pending |
| Both providers search/edit/read live carts | Previous live worker and HTTP tests; see BROWSER_VERIFICATION.md |
| Exact-product cross-store comparison | Partial; grounded brand/formulation metadata still needs work |
| WhatsApp → EasyDial → both providers → reply | Not implemented/tested |
| Customer-specific store linking and durable state | Not implemented |
| Paid, confirmed order | Not implemented; order placement remains prohibited for current testing |
| Meta accepts EasyDial's particular India use case | Not established |

## Implementation checklist and acceptance gates

- [ ] Resolve account/use-case eligibility and select direct Meta or the optional Twilio spike. Document actual account requirements and pricing, not estimates represented as facts.
- [ ] Create a private test sender and secrets configuration. Suggested future names: WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN, WHATSAPP_VERIFY_TOKEN, META_APP_SECRET, plus OPENAI_API_KEY. These variables are proposals, not currently wired settings. Never publish values.
- [ ] Prove one allowed tester's real voice recording arrives and receives a reply. Record MIME/codec, processing latency and delivery evidence without storing personal payloads in Git.
- [ ] Add signature rejection, webhook dedupe, durable queue/outbox and isolated sender mapping. Verify duplicate/out-of-order events cannot duplicate mutations.
- [ ] Bind existing controller sessions and render product choices. Test voice correction, ambiguous “yes,” stale buttons, provider errors and mid-search edits.
- [ ] Test one real basket on each store through bill/address review and Pending. Clear only owned test baskets. No order placement.
- [ ] Test restart recovery, expired login, existing unrelated cart, service-window expiration and explicit stop/opt-out.
- [ ] Evaluate actual older-user journeys and identical Indian-language recordings against baseline transcription: item/brand/quantity/negation accuracy, clarification burden, task success and latency.
- [ ] Implement secure multi-user account linking before inviting arbitrary users.
- [ ] Separately design/verify payment and provider confirmation only after explicit purchase authorization; reconcile uncertain outcomes instead of retrying a payment blindly.

## Theme alignment

Build Creative remains a strong fit: a familiar personal channel serving a concrete accessibility need, demonstrated from recording to a useful live basket. Build Challenging remains possible only with measurable improvements on the SAME Indian-language recordings against a baseline; using WhatsApp plus transcription alone is not that evidence. Build Ambitious remains weaker without a named SaaS reference and corresponding shipped end-to-end product.

## Decision

Pause iOS feature development; retain its code and existing commerce backend. Recommend a WhatsApp-first, narrowly scoped grocery service using direct Meta Cloud API, subject to the explicit policy gate. Next implementation milestone should prove a real voice note through live cart review, not claim a paid order. No WhatsApp accounts, messages, deployments or runtime features were created during this research.
