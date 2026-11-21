# Decisions and rationale

Updated: 2026-11-21. Implementation was authorized after the initial research. This log records conclusions, alternatives and evidence, not private internal reasoning. Later decisions supersede earlier phase restrictions.

| ID | Decision or recommendation | Status | Rationale / revisit condition |
| --- | --- | --- | --- |
| D01 | Research only; no app build | Historical; superseded by D14 | Initial research restriction ended with explicit build authorization |
| D02 | Preserve OpenAI master and two provider roles | User requirement; design proposed | Provider-specific behaviour can be isolated while master owns dialogue |
| D03 | Instamart-first demonstrable journey | Recommended | Stronger documented contract; verify actual account tools and callback |
| D04 | Blinkit is experimental, not a reliable drop-in checkout backend | Research conclusion | Static gaps in search schema, session isolation and payment verification; revisit after fixes and live testing |
| D05 | Cross-provider comparison is conditional | Research conclusion | Technical normalization and Swiggy use-case permission both unresolved |
| D06 | Direct audio capture with an API speech provider | Recommended | Better control over recordings, evaluation and app UX than external dictation |
| D07 | Wispr is optional manual input, not a core embedded dependency | Recommended | No suitable official embedding API verified; revisit if Wispr supplies one |
| D08 | Compare OpenAI and Sarvam before selecting speech stack | Recommended | India-specific accuracy cannot be established from feature lists |
| D09 | Test a bounded language set first | Recommended | Avoid unsupported claims about every Indian language/accent |
| D10 | Prefer Challenging with measured improvements; Creative otherwise | Recommended | Theme 2 explicitly requires same-recording comparisons |
| D11 | Use deterministic basket versioning and checkout gates | Recommended | A fluent agent can still act on stale state or duplicate a purchase |
| D12 | Calling remains deferred | User requirement | Microphone journey and commerce access should be validated first |
| D13 | Maintain separate Markdown handoff documents | User requirement; completed initial set | Future work should update progress and the decision log |
| D14 | Begin building, using extensive plan.md as the execution checklist | User authorized 2026-11-21 | Maintain milestone acceptance checks and actual verification evidence |
| D15 | Native SwiftUI iOS app and Python/FastAPI backend | Accepted build direction | Matches iOS target and existing Blinkit Python ecosystem |
| D16 | Both provider workers remain in scope | Accepted clarification | Fix/rebuild Blinkit gaps; comparison is technically feasible, policy interpretation remains unresolved |
| D17 | Explicit sample commerce mode while live access is unavailable | Implemented development boundary | Enables UI/controller verification without fabricated live claims; no live spending endpoint |
| D18 | Configurable OpenAI baseline models | Implemented, API calls unverified | gpt-4.1-mini, gpt-4o-transcribe, gpt-4o-mini-tts are documented baseline candidates; actual account/Indic performance requires testing |
| D19 | Public GitHub repository with focused commits | User requirement | https://github.com/prithidevghosh/easydial; secrets and local agent/browser state excluded |
| D20 | Premium, soothing, low-friction iOS design | User requirement | Large voice control, deliberate spacing/type and progressive interaction; validate against actual users |

## Alternatives considered

- Building both provider checkouts immediately: larger demo risk before Blinkit and access conditions are established.
- Using Wispr as the speech backend: no verified official embedding interface, with language-selection friction.
- Starting with full-duplex telephone calls: adds telephony, onboarding and payment-handoff complexity before the core ordering behaviour is proven.
- Positioning as SaaS: does not match the current consumer problem or the supplied third-theme criteria.
- Using only prompts to manage checkout: does not provide reliable stale-result rejection, locking or state reconciliation.

Supporting evidence is linked alongside claims in [commerce research](COMMERCE_RESEARCH.md) and [voice research](VOICE_RESEARCH.md). Evaluation design and architectural choices are EasyDial proposals, not claims that a vendor implements them.


## D21 — Live-only flow, pending payment boundary (accepted, 2026-11-21)

User explicitly requested complete removal of demo flow and live Blinkit/Instamart baskets up to checkout. Supersedes D17's sample-mode choice and the earlier read-only implementation boundary. Production rejects sample mode; synthetic data lives only in isolated tests. Review synchronizes the chosen provider cart, validates its readback and displays its bill. Checkout is a local Pending state, never a provider checkout or payment call. Both providers are enabled for the requested local testing; published policy interpretation remains an unresolved research question rather than a disabled-provider switch.

## D22 — Account cart protection and local login helpers (accepted implementation, 2026-11-21)

An unrelated/external cart is never replaced silently. This single-operator build requires an empty initial provider cart, remembers ownership/expected readback in memory, and stops on mismatch. Draft edits sync only on explicit Review basket. Swiggy login uses a local OAuth/PKCE helper; Blinkit login uses a manually authenticated Firefox context. Secrets stay in ignored local files. Real account validation remains pending.

## D23 — Explicit primary-action contrast (implemented, 2026-11-21)

User screenshot showed dark inherited text on the dark button. PrimaryActionStyle owns white label contrast, full width, a minimum 56-point height and an 18-point corner radius. Soft secondary controls share a lighter treatment; the home screen removes redundant introductory assistant content and clarifies disconnected stores. Supporting text has an explicit slate colour.


## 2026-11-21 — both providers use visible browser automation

User decision: replace Swiggy MCP and eventually support dictation through a paid, provider-confirmed order. This session explicitly prohibits order placement. Runtime removes MCP and OAuth setup; both stores use dedicated persistent headed Chrome profiles, serialized mutations, cart ownership guards and provider readback.

Website correction: `https://www.swiggy.com/instamart` showed a temporary-glitch page, but the user supplied `https://instamart.in/`, which loaded successfully and allowed logged-in search/cart. Do not attribute the old route's failure to an established bot block or conclude Instamart is unavailable.

Empty cart address gate: neither store exposes the full checkout address when the cart is empty. Require the exact selected homepage location captured during onboarding before draft cart writes, then require the provider-specific full saved address and final bill after sync. Any mismatch prevents a successful review; no payment follows automatically.

Readback timing: item rows appear before bills; quantity widgets can update optimistically before the store saves the change. Wait for complete bill data and completed browser network activity before navigating away from cart edits. Never retry an uncertain write blindly; reconcile the provider cart first.

Reuse normal logged-in sessions instead of relaunching headless Firefox for every request. This reduces login churn; it is not a guarantee against future bot checks. No stealth, challenge bypass, fingerprint spoofing or proxy rotation is added.
