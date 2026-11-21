# Decisions and rationale

Updated: 2026-11-21. User requirements are binding for this phase; recommendations below await product decisions and validation. This log records conclusions, alternatives and evidence, not private internal reasoning.

| ID | Decision or recommendation | Status | Rationale / revisit condition |
| --- | --- | --- | --- |
| D01 | Research only; no app build | User requirement | Implementation begins only after a later user instruction |
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

## Alternatives considered

- Building both provider checkouts immediately: larger demo risk before Blinkit and access conditions are established.
- Using Wispr as the speech backend: no verified official embedding interface, with language-selection friction.
- Starting with full-duplex telephone calls: adds telephony, onboarding and payment-handoff complexity before the core ordering behaviour is proven.
- Positioning as SaaS: does not match the current consumer problem or the supplied third-theme criteria.
- Using only prompts to manage checkout: does not provide reliable stale-result rejection, locking or state reconciliation.

Supporting evidence is linked alongside claims in [commerce research](COMMERCE_RESEARCH.md) and [voice research](VOICE_RESEARCH.md). Evaluation design and architectural choices are EasyDial proposals, not claims that a vendor implements them.
