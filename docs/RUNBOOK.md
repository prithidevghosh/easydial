# Run EasyDial locally

## What currently works

Native iOS UI backed by FastAPI; synthetic two-store grocery search; exact-match item-price comparison; adding, removing, replacing and changing quantities; versioned review; explicitly simulated checkout. Model/speech API code and read-only live provider adapters exist, but credentialed verification is outstanding. No actual purchase endpoint is enabled.

## Backend

Requires Python 3.11+ and uv.

```sh
cd backend
uv sync --locked
cp .env.example .env
uv run uvicorn easydial.app:app --host 127.0.0.1 --port 8000
```

The default sample mode needs no credentials. Visit `http://127.0.0.1:8000/docs` for the API schema. The local server issues short-lived bearer session tokens; session state is in memory and disappears on restart. This is a single-operator development service, not a publicly deployable authentication system. Bind to loopback for simulator use. Do not expose it to the internet.

To enable real voice/intent, set `OPENAI_API_KEY` in `backend/.env` locally and restart the backend. Never put the key in iOS code or chat. Current configurable baseline models are `gpt-4.1-mini`, `gpt-4o-transcribe`, and `gpt-4o-mini-tts`. Model access and language performance must be checked against the account. Missing credentials produce a clear unavailable message; sample search shortcuts do not pretend to use an LLM.

## iOS

Requires Xcode. The generated project is committed; XcodeGen is only needed when changing `ios/project.yml`.

```sh
cd ios
xcodegen generate
xcodebuild -project EasyDial.xcodeproj -scheme EasyDial \
  -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 17' \
  -derivedDataPath .build build CODE_SIGNING_ALLOWED=NO
```

Open `ios/EasyDial.xcodeproj` in Xcode, choose an iPhone simulator, and Run. The app defaults to `http://127.0.0.1:8000`. The Settings screen allows changing the backend URL. A physical iPhone needs signing and the Mac's reachable local hostname on the same trusted network; localhost on an iPhone refers to the phone itself. Physical-device testing is outstanding. HTTPS is required for a future hosted service.

## Sample walkthrough

1. Start backend, run app, confirm sample notice.
2. Tap Milk. Both stores return explicitly synthetic options.
3. Add Amul milk from Blinkit, search Bread, add the Blinkit bread.
4. Open basket; increase milk to three. Remove/replace as desired.
5. Review sample basket, then complete and confirm the sample journey.
6. The app states that no real order was placed.

Free-form understanding and microphone requests require OpenAI access. Without it, the text field searches literal product names only; the app's sample shortcuts and basket controls remain usable. Audio recordings are capped at 60 seconds by the app and removed from its temporary directory after upload/cancellation. The backend bounds uploaded size and closes upload files after processing.

## Tests

```sh
cd backend
uv run ruff check src tests
uv run pytest -q
uv run python -m easydial.probe milk
```

The probe only searches and clearly reports sample/live mode. With the sample backend running on port 8000, run the iOS journey test:

```sh
cd ios
xcodebuild -project EasyDial.xcodeproj -scheme EasyDial \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  -derivedDataPath .build test CODE_SIGNING_ALLOWED=NO
```

## Live discovery prerequisites

Live mode currently supports read-only discovery, not purchases. Set `COMMERCE_MODE=live`, `LIVE_ADDRESS_LABEL`, and the relevant account configuration. The operator must verify that this label corresponds to the provider's selected address; UI onboarding/address verification is not implemented yet.

- Swiggy: obtain a per-user token using the official OAuth flow, set `SWIGGY_ACCESS_TOKEN` and `SWIGGY_ADDRESS_ID`. The adapter checks actual tool availability. In-app OAuth/refresh and redacted schema capture remain to be built.
- Blinkit: provide an authenticated Playwright storage-state file outside git, configure `BLINKIT_STORAGE_STATE` and `BLINKIT_VERIFIED_LOCATION`, install Firefox with `uv run playwright install firefox`. The worker checks the visible location header and never uses host-IP geolocation. User-facing login/onboarding is outstanding. Selectors are unverified against the current site and fail conservatively.
- `ENABLE_LIVE_COMPARISON` defaults false while the exact comparison use-case interpretation remains unresolved. Sample comparisons are always available. Enabling this switch does not establish permission or runtime reliability.

Keep real account data and browser profiles out of issues/screenshots/commits. Do not paste access tokens into diagnostic outputs. A public source repository does not imply a public backend deployment.

## Troubleshooting

Connection error: start the backend, verify `/health`, check app Settings and the port. On simulator, use loopback. On device, use the Mac's LAN hostname and allow the local-network prompt.

Stale basket: app refreshes on HTTP 409; repeat the intended edit against the updated state.

Provider unavailable: inspect configuration and account access; do not assume out-of-stock. Live adapters never silently replace failed responses with sample data.

Voice unavailable: configure the API key server-side, restart, and start a new app session. Microphone denial can be changed in iOS Settings; literal text search remains available.

Lost basket after backend restart: expected in this development build. Persistent user sessions and durable checkout records are planned before live purchasing.
