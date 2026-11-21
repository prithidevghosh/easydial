# Progress and handoff

Updated: 2026-11-21.

## Current state

Initial desk research and static code audit complete. **Implementation has not started.** Authenticated integration feasibility and speech quality are still unproven. Do not translate “documented” into “tested.”

## Completed

- Captured user requirements and deferred calling scope.
- Reviewed official Swiggy Instamart tool contracts, authentication, delegated sessions, access and published operating boundaries.
- Inspected Blinkit source at commit `a39547118f4decd04a77ac16316fa09efdb6237a`: server, browser authentication, search, cart, checkout, facade, history, configuration and test helpers; checked issue/PR state.
- Reviewed official Wispr language/developer/MCP pages, OpenAI transcription/VAD docs, and Sarvam speech model docs.
- Assessed the supplied hackathon themes and drafted an evaluation protocol.
- Documented a proposed controller/agent design for revisions, comparisons and checkout.
- Created the Markdown documentation set indexed by the root README.

## Not done

- No user interview, recording collection, speech benchmark, live MCP authentication or provider search.
- No cart mutation, payment, order or integration deployment.
- No official confirmation of comparison use-case permission, Blinkit approval, or hackathon eligibility.
- No dependencies installed, app scaffolding, source modifications to Blinkit, or application code written.
- No measured performance/cost results.

## Highest-priority next steps

1. Confirm the hackathon name/rules, deadline and whether a hosted demo is required. Current theme analysis relies on the user's supplied wording.
2. Pick one real target participant, a serviceable city/address and initial languages. Record actual pain points before finalizing the UI.
3. Clarify with Swiggy whether an attributed, user-directed Instamart/Blinkit comparison is acceptable. No message has been sent. Any outreach needs the user's explicit instruction.
4. When the user is ready to connect a test account, validate localhost OAuth, `tools/list`, address retrieval and read-only discovery. Capture redacted evidence.
5. Inspect live Blinkit search outputs at the intended address before committing it to the demo. Validate pack-size extraction, price meaning and availability interpretation.
6. Collect a small consented speech pilot and execute the paired evaluation when implementation/evaluation is authorized.
7. Only after a build instruction, implement the narrow journey and validate cart edits. A purchase test requires an explicitly approved basket/amount/address.

## Open questions

| Question | Why it matters | Current fallback |
| --- | --- | --- |
| Is comparison permitted? | Core cross-provider feature depends on it | Single-provider journey; synthetic catalogue for speech evaluation |
| Does the chosen Swiggy account expose all tools? | Actual capability may differ from docs | Discover schema at connection time |
| Can hosted callback/production access be obtained in time? | Local prototype permission does not prove public-demo permission | Local demo if rules permit |
| Does Blinkit still work at the chosen location? | Static code cannot prove DOM compatibility | Clearly unavailable/experimental provider |
| Which language pair and participants? | Determines meaningful speech/TTS validation | Hindi/Hinglish plus one participant-selected regional language |
| What is the payment experience for the target user? | QR/UPI may reintroduce friction | Use available methods only; test onboarding and handoff |
| Is actual order placement required for judging? | Determines transactional demo boundary | State clearly whether journey ends at preview or confirmed order |

## Evidence and reproducibility

Research date is 2026-11-21; model IDs and online documentation can change. Pin versions and recheck sources before implementation. Swiggy is a remote service, so runtime tool schemas need a dated snapshot later. Blinkit source links are pinned to the inspected commit.

Temporary Blinkit source: `/private/tmp/easydial-research/hereisSwapnil-blinkit-mcp-a395471`. It may be deleted by the OS; the durable report uses GitHub permalinks. No secrets were downloaded. Existing `.codex/` contents were left untouched.

Use the OpenAI Docs skill for future OpenAI API verification. No other implementation skill was needed for this research phase.

## Maintenance convention

After each work session update this file with completed work, actual verification, blockers and next actions. Add decisions to DECISIONS.md with status and evidence. Keep intended goals separate from achieved results. If implementation is authorized later, record the authorization and new phase here before building.
