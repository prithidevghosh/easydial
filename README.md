# Easy Dial

Easy Dial is a WhatsApp voice assistant for quick commerce. A shopper describes what they want, picks products from a connected store, reviews the itemized bill and approves checkout. The project grew out of the friction of ordering groceries at home, and supports other goods available in the store catalog.

**Start with [AGENTS.md](AGENTS.md)** for the unattended, credential-free walkthrough, the live setup with your own Meta app, and step-by-step shopper journeys. The [pitch deck](docs/assets/Easy-Dial.pdf) and the full-length `Easy-Dial-demo.mp4` accompany the submission ZIP.

The Blinkit pilot completed a paid order verified against store history. Instamart login can be intermittent. The earlier iOS client is included as an optional touch point. The planning and research documents below record earlier stages of the project.

## Read in this order

1. [Goals and scope](docs/GOALS.md)
2. [Commerce integration audit](docs/COMMERCE_RESEARCH.md)
3. [Voice research](docs/VOICE_RESEARCH.md)
4. [Theme fit and evaluation plan](docs/THEMES_AND_EVALUATION.md)
5. [Proposed agent behaviour](docs/AGENT_DESIGN.md)
6. [Decision log and rationale](docs/DECISIONS.md)
7. [Progress, unknowns, and handoff](docs/PROGRESS.md)

The documents distinguish published capabilities, source-code observations, design recommendations, and untested assumptions. Decisions are provisional unless identified as user requirements. They record concise rationale and evidence, not private internal deliberation.

## AWS pilot

Deployment state, credentials location, fixed webhook, backup/cutover procedure and current merchant-access blockers are tracked in [AWS deployment handoff](docs/AWS_DEPLOYMENT.md). This workshop account lasts 72 hours; a running backend is not proof of merchant ordering availability.
