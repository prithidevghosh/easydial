# Progress and handoff

Updated: 2026-11-21.

## Current state

Build authorized by the user after the research phase. Native SwiftUI iOS app and FastAPI backend implemented, with a verified synthetic shopping journey. Repository is public: https://github.com/prithidevghosh/easydial. Live commerce and multilingual voice quality remain unverified. Read `plan.md` before continuing.

## Completed

- Research and source audit recorded in COMMERCE_RESEARCH.md and VOICE_RESEARCH.md; themes and evaluation protocol documented.
- Extensive staged implementation plan, product goals, decisions, and verification gates committed.
- OpenAI structured intent interface with quantity/replacement targets, transcription and speech routes. Missing credentials produce explicit unavailable responses.
- Separate sample, Instamart MCP, and custom Blinkit Playwright discovery adapters. Live failures never fall back silently to samples.
- Session isolation, versioned atomic basket edits, equivalent-product item-price comparison, expiring review, approval invalidation, and idempotent sample confirmation.
- Native microphone recording, text fallback, grouped provider choices, basket sheet, review, and clearly simulated completion.
- Soothing blue/white visual design, generous touch targets, compact voice control after search, and custom app icon.
- Reproducible project generation, local runbook, dependency lock, and GitHub CI configuration.

## Verification actually performed

- `uv run ruff check src tests`: passed.
- `uv run pytest -q`: 13 passed. Covers sample journey, session isolation, stale state, basket edit atomicity, replacement preservation, provider failure isolation, unknown prices/stock, review expiry, and disabled live checkout.
- Native iOS simulator build: succeeded with Xcode 26.5.
- iPhone 17 simulator UI test: sample search → add → basket → review → explicit sample confirmation → no-real-order result, passed after design refinement.
- Simulator screenshots inspected for layout; actual older-user usability, VoiceOver and physical-device microphone testing are outstanding.
- GitHub Actions run 44871504663 at commit `3a54310`: backend checks and iOS simulator build both passed. https://github.com/prithidevghosh/easydial/actions/runs/44871504663

## Outstanding work and blockers

1. Configure OpenAI credentials locally using `backend/.env`, then test actual recorded audio and intent correction; no credential is present in this repository. Language-grounded spoken responses need further work and evaluation; most deterministic responses currently use English.
2. Authenticate a real Swiggy test account, capture actual tool schemas, verify delivery address and read-only product discovery. OAuth onboarding and token refresh are not implemented.
3. Authenticate Blinkit with a local browser profile, validate current selectors and product fields at the same address. Custom Playwright code exists but has not run against an authenticated account.
4. Resolve published Swiggy comparison-use interpretation before enabling live comparison. No outreach has been sent. Both providers remain in scope.
5. Implement actual provider cart synchronization and verified totals, payment handoff, durable idempotency and order status. Live checkout is deliberately disabled until these gates pass.
6. Add durable sessions, production authentication, rate limits and deployment configuration before hosting a public service.
7. Collect consented same-recording language evaluation and compare the baseline with EasyDial. No measured improvement is claimed yet.
8. Confirm hackathon identity/deadline/rules, target user, address and regional language. Theme 2 remains the best fit if evaluation demonstrates improvement; Theme 1 also fits the useful journey.

## How to resume

Follow RUNBOOK.md for local startup. Default sample mode needs no secrets; OpenAI requests need an API key on the backend. Use a simulator with the backend at `http://127.0.0.1:8000`. The backend is an in-memory local development service; restarting loses sessions. See IOS_DESIGN.md for design rationale and DECISIONS.md for accepted technical choices.

Do not equate compiled code, mocked tests, or sample purchases with a verified live capability. Preserve unrelated basket items, invalidate review after every edit, distinguish unavailable from out-of-stock, and never report an order without provider evidence. Telephone calls remain deferred.

## Maintenance convention

Each session updates this file and the plan ledger with completed work, actual evidence, blockers and next actions. Record decisions and concise rationale, not private internal deliberation. Make focused commits and push to the public repository. Keep credentials, recordings, addresses and authenticated browser state out of git.
