# Run EasyDial locally

**Active channel: WhatsApp.** Follow [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md). Start with `uv --directory backend run python -m easydial.whatsapp`; expose only its webhook server on port 8001. The iOS/local API instructions below are retained for development and must not run concurrently against the same store profiles during live WhatsApp tests.

Updated 2026-11-21. Application is live-only. Start with [credential setup](CREDENTIALS.md).

## Backend

```sh
cd backend
uv sync --locked
uv run uvicorn easydial.app:app --host 127.0.0.1 --port 8000
```

Without credentials, the home screen shows disconnected stores. No sample products or simulated purchases are available. `/health` reports `mode=live` and `live_checkout=false`: the latter means payment/order submission is disabled, not that discovery uses samples. `/config` reports configured connection fields; authentication is only verified by provider calls. `/docs` has the API schema.

`.env` is server-side, ignored by Git. Restart after configuration changes. This is a loopback-only, single-operator development service with expiring bearer sessions and in-memory carts. Do not expose it as a public backend.

Provider workers use installed Google Chrome with dedicated private profiles. Close setup/audit browsers before starting the backend; a profile cannot be owned by two processes. Do not use the legacy Swiggy MCP helper.

## Native iOS

Open `ios/EasyDial.xcodeproj`, choose an iPhone simulator, and Run. The generated project is committed. Use `xcodegen generate` from `ios` only after changing `project.yml`.

```sh
xcodebuild -project ios/EasyDial.xcodeproj -scheme EasyDial \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath ios/.build build CODE_SIGNING_ALLOWED=NO
```

The app defaults to `http://127.0.0.1:8000`; the gear opens connection settings. A physical phone needs signing and the Mac's reachable LAN hostname on a trusted network; localhost on the phone is the phone itself. Device audio, VoiceOver and target-user testing remain outstanding.

## Live journey and boundary

Search → select matching pack → draft basket edits → **Review basket** synchronizes the real provider cart and reads its live bill → **Checkout** shows Pending. No provider payment or checkout tool is callable. Read CREDENTIALS.md for empty-cart prerequisites and the full verification checklist. All results and review values come from configured providers. Failure is displayed, never replaced with fake products.

OpenAI is optional for literal typed search; microphone/intent/voice responses require its key. Audio is capped at 60 seconds by the app; temporary recordings are deleted after upload/cancel, uploads are size bounded and closed server-side.

## Verification

```sh
uv --directory backend run ruff check src tests tools
uv --directory backend run pytest -q
# Also run observed-DOM regressions locally with Chrome installed:
EASYDIAL_DOM_TESTS=1 uv --directory backend run pytest -q
```

Provider fixtures exist only under `backend/tests` and are not wired to the application. They validate contracts/guards without contacting user accounts. They do not prove live integration success.

The current simulator test checks the disconnected live home and connection settings, against a local backend with no credentials. It does not mutate actual accounts:

```sh
xcodebuild -project ios/EasyDial.xcodeproj -scheme EasyDial \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  -derivedDataPath ios/.build test CODE_SIGNING_ALLOWED=NO
```

Run authenticated checks manually using the CREDENTIALS.md checklist. Do not run the disconnected-state UI test against a credentialed backend.

## Troubleshooting

Connection error: start the backend and check its address/port. Voice unavailable: configure OpenAI and restart. Provider errors: rerun login or validate the selected address; unavailable is not out-of-stock. Review error: inspect the real basket; third-party changes are never silently discarded. Lost local basket after restart: expected until durable sessions are added.

## WhatsApp is silent after restart

Run `uv --directory backend run python tools/whatsapp_status.py`. Local health alone is insufficient: check public tunnel health and last inbound age. A running cloudflared process can retain a dead quick-tunnel hostname. If public DNS/health fails, recreate the tunnel, update private WHATSAPP_PUBLIC_URL, restart the server, and save the new `/webhooks/whatsapp` callback in Meta's WhatsApp Configuration using the existing verify token. Keep the messages subscription enabled. Verify the public endpoint and saved Meta callback, then ask the user to send a fresh message and inspect delivered/read receipts. Do not replay old order/payment messages to test connectivity. Temporary access-token validity is a separate check; do not print tokens in diagnostics.
