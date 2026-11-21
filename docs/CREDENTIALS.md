# Connect EasyDial for live testing

Updated 2026-11-21. Current target: real product discovery → chosen basket → live provider basket/bill → **Checkout: Pending**. Payment and order placement are not called. There is no sample mode or sample catalogue in the application.

Both provider websites have been inspected after login through the pre-payment cart. Backend adapter verification is recorded separately in BROWSER_VERIFICATION.md; unit tests alone do not establish live success.

## 1. Local file and dependencies

All commands below run on your Mac. A local `backend/.env` has been created, is ignored by Git, and has owner-only permissions. Open it in your editor; do not paste credentials into chat or the iOS app. If setting up a fresh clone:

```sh
cd /Users/prithidevghosh/Documents/ChatGPT/easydial/backend
uv sync --locked
# Only if .env does not already exist:
cp -n .env.example .env
chmod 600 .env
```

Do not replace a filled `.env` with the example. The helpers preserve unrelated values. `COMMERCE_MODE=live` is required. No comparison toggle is required in this build; both configured providers are searched. The research note about Swiggy's published use-case restrictions remains unresolved; this is not a claim of production approval.

## 2. OpenAI: voice and intent

1. Open [OpenAI API keys](https://platform.openai.com/api-keys), select or create an EasyDial project, and create a secret key. Project keys are managed from the project's API Keys settings. See [official project/key instructions](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform).
2. Ensure the API account has usable billing/credits in [Billing](https://platform.openai.com/settings/organization/billing/overview). A ChatGPT subscription is separate from API usage.
3. Put the key only in `backend/.env`:

```dotenv
OPENAI_API_KEY="your-secret-key"
OPENAI_TEXT_MODEL=gpt-4.1-mini
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-transcribe
OPENAI_SPEECH_MODEL=gpt-4o-mini-tts
OPENAI_VOICE=coral
```

The configured models must be available to your API project. These are configurable baseline choices, not a claim about the latest model. A key is required for microphone requests and free-form corrections. Literal product searches and on-screen basket controls can test commerce without OpenAI.

## 3. Store accounts — normal Chrome login, no MCP credentials

Use **https://blinkit.com/** and **https://instamart.in/**. The legacy `www.swiggy.com/instamart` route returned a temporary-glitch page during testing; `instamart.in` worked. The MCP dependency, OAuth token configuration and old Swiggy helper have been removed.

For this Mac, the authenticated profiles and selected addresses from the live audit are already saved privately in `backend/.env` and `backend/.state/site-audit`. Do not copy these into Git, issues, screenshots or another user's account. There is no need to supply provider API keys.

For a new setup, install Google Chrome, stop the backend, then run:

```sh
cd /Users/prithidevghosh/Documents/ChatGPT/easydial/backend
uv run python tools/connect_blinkit.py
uv run python tools/connect_instamart.py
```

Complete phone/OTP login directly in each Chrome window. Select the **same physical delivery address** in both stores. Return to the terminal and follow the local prompts for the exact full saved address. The helpers preserve unrelated `.env` settings and close Chrome before returning. Full addresses may be formatted differently by the two stores; store each provider's own exact text.

Relevant fields (filled by setup; never commit filled values):

```dotenv
BLINKIT_BROWSER_PROFILE=/absolute/path/to/private/blinkit-profile
BLINKIT_VERIFIED_LOCATION=exact selected homepage location text
BLINKIT_VERIFIED_ADDRESS=exact full Blinkit checkout address
INSTAMART_BROWSER_PROFILE=/absolute/path/to/private/instamart-profile
INSTAMART_VERIFIED_LOCATION=exact selected homepage address-line text
INSTAMART_VERIFIED_ADDRESS=exact full Instamart checkout address
LIVE_ADDRESS_LABEL=Home
```

`BLINKIT_STORAGE_STATE` remains a compatibility option for older setup files. New setup uses persistent Chrome profiles. Only one process may own a profile: close setup/audit windows before starting the backend. The backend opens visible Chrome windows as needed, reuses them, and closes them on shutdown. Reconnect after logout or an intended address change. Challenges require normal manual completion; no bypass is implemented.

## 4. Test the app through checkout

1. Keep both provider carts empty before starting a fresh EasyDial session. Existing or externally modified baskets are rejected, never silently replaced.
2. Start the backend with `uv run uvicorn easydial.app:app --host 127.0.0.1 --port 8000` from `backend`.
3. Open the iOS simulator app. Connection settings should show configured providers. This indicator confirms local configuration, not permanent login validity.
4. Use typed search first (no OpenAI key needed), select an exact product/pack, and add it to the draft.
5. Tap **Review basket**. This mutates the real store cart and reads back its items, quantities, selected address and payable bill.
6. Change quantity, review again, replace a product, and review again. Removing all draft items and reviewing clears only the cart owned by that session.
7. Compare the reviewed bill with the provider's browser. Fees and available products vary by location and time.
8. **Checkout** still shows **Pending** locally. It does not click provider payment controls or place an order.

See [BROWSER_VERIFICATION.md](BROWSER_VERIFICATION.md) for the evidence ledger. The final product goal includes payment and provider-confirmed orders, but those stages remain unimplemented/unverified and are forbidden during this no-order test session.

## 5. Boundaries

- This is a single-operator local backend, not multi-user production infrastructure.
- Authentication profiles and full addresses are private. `.state` and `.env` are ignored.
- The homepage location must exactly match onboarding. Empty carts do not expose a full checkout address; after adding items, full-address and bill checks are mandatory before review succeeds.
- Browser selectors can change. Unknown state produces an error, never sample stock or a fabricated total.
- Restarting the backend loses in-memory cart ownership. Clear your test cart in the provider before starting a new session.
- Voice/Indian-language quality needs a configured OpenAI key and recordings; the browser audit alone does not validate speech.
