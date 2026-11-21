# Connect EasyDial for live testing

Updated 2026-11-21. Current target: real product discovery → chosen basket → live provider basket/bill → **Checkout: Pending**. Payment and order placement are not called. There is no sample mode or sample catalogue in the application.

The code is ready for credentialed integration testing, not yet proven against your accounts. Tests use isolated provider responses; they do not establish live success. Blinkit's current website selectors and Swiggy's account-specific responses may need adjustment after login.

## 1. Local file and dependencies

All commands below run on your Mac. A blank local `backend/.env` has been created, is ignored by Git, and has owner-only permissions. Open it in your editor; do not paste credentials into chat or the iOS app. If setting up a fresh clone:

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

## 3. Swiggy Instamart: browser authorization, not a developer API key

Save the delivery address in your Swiggy account first. In your own Terminal:

```sh
cd /Users/prithidevghosh/Documents/ChatGPT/easydial/backend
uv run python tools/connect_swiggy.py
```

The helper opens Swiggy's authorization page, receives a localhost callback, and saves the access token privately into `.env`. Complete phone/OTP login on Swiggy's page. Return to Terminal and choose the numbered saved address. The helper fills:

```dotenv
SWIGGY_ACCESS_TOKEN="generated-by-login"
SWIGGY_ADDRESS_ID="selected-from-your-account"
LIVE_ADDRESS_LABEL="selected-address-text"
```

Do not invent the address ID. The helper can paginate saved addresses. It only authenticates and reads addresses; it does not modify baskets. Its OAuth state validation and local file handling are tested, but a real login is still pending.

Swiggy uses OAuth/PKCE and dynamic registration. The helper uses callback `http://localhost:8765/callback`; leave port 8765 available. If Swiggy rejects that redirect, request the exact callback's allowlisting from `builders@swiggy.in` as directed by [Swiggy authentication docs](https://mcp.swiggy.com/builders/docs/start/authenticate/). No email has been sent. If the session expires or is revoked, rerun this helper; automatic refresh is not implemented in this local build. Future multi-user onboarding needs per-user authorization.

## 4. Blinkit: save a signed-in browser session

There is no Blinkit API key used by this integration. It uses the browser session you authorize through Blinkit's own website.

```sh
cd /Users/prithidevghosh/Documents/ChatGPT/easydial/backend
uv run playwright install firefox
uv run python tools/connect_blinkit.py
```

In the Firefox window:

1. Log in using your Blinkit phone number and OTP.
2. Select your saved **full delivery address**, matching the physical address chosen for Swiggy. Do not rely on auto-detected location.
3. Return to Terminal and press Enter. Enter the full saved address text when asked, using the text Blinkit displays in its address/basket UI.
4. The helper saves the browser state and these `.env` fields:

```dotenv
BLINKIT_STORAGE_STATE="/Users/prithidevghosh/Documents/ChatGPT/easydial/backend/.state/blinkit.json"
BLINKIT_VERIFIED_LOCATION="actual-selected-header-text"
BLINKIT_VERIFIED_ADDRESS="actual-full-saved-address-text"
```

The `.state` folder is ignored by Git. Its contents are credentials; do not attach them to issues or chat. If Swiggy is not connected, also fill `LIVE_ADDRESS_LABEL` with your verified address. Re-run the helper after logout/expiry/address changes. Browser challenges must be completed normally; the worker does not bypass them.

If an empty Blinkit basket omits the full address, the worker checks the visible location header. If neither surface proves the configured full address, review stops before mutation. That requires adjusting the verified address integration against the authenticated UI; do not shorten the configured address merely to bypass this check.

## 5. Start or restart and reconnect

After credentials are saved, stop the existing EasyDial backend (Ctrl+C in its terminal) and start it again:

```sh
cd /Users/prithidevghosh/Documents/ChatGPT/easydial/backend
uv run uvicorn easydial.app:app --host 127.0.0.1 --port 8000
```

If the process started by Codex is still occupying port 8000, ask Codex to restart the EasyDial backend after you finish setup; no key needs to be shared. In the simulator, open EasyDial → gear → Connect and start a new session. Use `http://127.0.0.1:8000`. Configured indicators mean fields exist, not that the account's session is valid; a successful live search proves more.

A read-only search probe is available:

```sh
uv run python -m easydial.probe milk
```

The output can contain your address and live products; keep it local. Errors never replace live results with fixture products.

## 6. Test one real provider basket at a time

1. Start with an empty basket in the provider you are testing. EasyDial refuses to overwrite an unrelated or externally modified basket.
2. Search a specific milk brand and pack. Confirm the provider, price, pack and stock indication in the results.
3. Add an item, edit quantities, and add/replace/remove another item. These are draft edits in EasyDial until review; the UI explains this.
4. Tap **Review basket**. This is the actual provider-cart write: Instamart replaces the entire desired basket via `update_cart` and reads `get_cart`; Blinkit uses visible add/remove controls and reads its basket. It does not open payment.
5. Check the returned address, quantities and live bill against the provider app/site. Missing fields, quantity caps, mismatches, or changed addresses stop review rather than fabricate a total.
6. Tap **Checkout**. EasyDial shows **Pending**. There is no provider checkout/payment call, no COD selection, and no order number.
7. Edit again and review again to test replacement/quantity reconciliation. Every edit invalidates the old review. A review expires after five minutes.
8. To test the other provider, remove all draft items and tap **Sync empty basket**; after the provider confirms empty, select products from the other store. Alternatively clear the prior test cart in its own app and reconnect EasyDial for a new session. Each basket belongs to one provider.

After a backend restart, prior account-cart ownership cannot be established from memory: empty the store's test basket before starting again. If a network failure leaves a partial cart, review may stop and ask you to clear it manually. No automatic retry makes a purchase.

## Current limits

- Real OAuth, browser-session restore, search, basket synchronization and bill parsing still need authenticated verification. No live transaction success is claimed.
- Backend sessions/cart ownership are local and in memory. One operator per backend; no public deployment yet.
- Blinkit DOM selectors and Swiggy optional response fields can vary. Preserve a redacted description of failures and fix the adapter against actual evidence.
- Unknown Blinkit brand/formulation fields prevent automatic equivalence claims; item prices can still be displayed with provider attribution. Final payable totals come only from provider basket readback.
- No payment credentials are needed. Payment and order placement are intentionally outside this testing milestone.
