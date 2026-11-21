# EasyDial

Voice-assisted grocery ordering for older adults and people who find commerce apps difficult to use.

**Phase: WhatsApp-first pilot. Updated: 2026-11-21.** Send voice notes to an EasyDial WhatsApp business chat; the Python backend searches Blinkit and Instamart and manages a reviewable basket. The channel implementation is locally tested; real WhatsApp delivery awaits Meta credentials and an OpenAI key. Checkout stops at Pending. Native iOS development is paused, with existing code retained.

Start with the [WhatsApp setup guide](docs/WHATSAPP_SETUP.md). Existing store-browser evidence and limitations are in the [verification ledger](docs/BROWSER_VERIFICATION.md).

Repository: [prithidevghosh/easydial](https://github.com/prithidevghosh/easydial) (public).

Connect accounts with [the exact credential guide](docs/CREDENTIALS.md).

Start with [plan.md](plan.md) for the detailed execution checklist and [the runbook](docs/RUNBOOK.md) to run the project. Research below records the evidence that informed the build.

## Research verdict

The concept is feasible as a bounded hackathon prototype. The full promise—any Indian language, reliable comparison across both providers, and completed orders—is not yet validated. Both providers remain in scope; no demo catalogue is shipped. Provider automation uses dedicated Chrome profiles for Blinkit and instamart.in. A measured language set and full dictation-to-paid-order validation remain outstanding. Payment remains deferred.

## Read in this order

1. [Goals and scope](docs/GOALS.md)
2. [Commerce integration audit](docs/COMMERCE_RESEARCH.md)
3. [Voice research](docs/VOICE_RESEARCH.md)
4. [Theme fit and evaluation plan](docs/THEMES_AND_EVALUATION.md)
5. [Proposed agent behaviour](docs/AGENT_DESIGN.md)
6. [Decision log and rationale](docs/DECISIONS.md)
7. [Progress, unknowns, and handoff](docs/PROGRESS.md)

The documents distinguish published capabilities, source-code observations, design recommendations, and untested assumptions. Decisions are provisional unless identified as user requirements. They record concise rationale and evidence, not private internal deliberation.
