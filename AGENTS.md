# Easy Dial · run and review the WhatsApp project

**Live URL (project entry)** · [Easy Dial on GitHub](https://github.com/prithidevghosh/easydial)

**WhatsApp pilot** · [Open chat](https://d1m5hsnftwd3t2.cloudfront.net/join) · [Scan the QR](https://d1m5hsnftwd3t2.cloudfront.net/join/qr.png) · [Service health](https://d1m5hsnftwd3t2.cloudfront.net/health)

**Slides** · [Read the five-slide pitch (PDF)](docs/assets/Easy-Dial.pdf)

**Demo video** · Open `Easy-Dial-demo.mp4` in the submission ZIP · [Original recording on Google Drive](https://drive.google.com/file/d/13iKtx8FTVAl3IKH2GYtvb--xWaUzasKT/view?usp=sharing)

Easy Dial lets someone order from a quick commerce store by sending a voice note in WhatsApp. Try a mixed shopping list, change your mind about a product, review the actual store fees and choose how to pay. User-facing replies are in English. The hosted pilot uses a Meta **test** number. A tester must verify their WhatsApp number in the owner's Meta recipient list to receive replies. Scanning the QR alone does not register a tester. The hosted URL depends on the workshop AWS account remaining active.

**For independent judging, use your own Meta app and follow steps 1–7.** WhatsApp is the product entry point. The QR and join link are two ways into the same chat. The older iOS project is a separate client and is not needed to run the submission. AWS credentials are not required.

## Start here if reviewing the ZIP without accounts

Extract the archive and open a shell in the directory containing this file. Commands below start from that root. Skip the Git clone and `cd easydial` lines in step 1 when using an extracted ZIP. The required live environment is documented in step 4. [backend/.env.example](backend/.env.example) also lists optional settings and defaults.

**Keyless guided mock mode.** Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (tested with 0.10.12). The project needs Python 3.11+ and uses embedded SQLite. Install the pinned dependencies and run:

```bash
uv python install 3.11
uv --directory backend sync --locked --python 3.11
uv --directory backend run --locked python -m tools.review_walkthrough
```

Press Enter at each shopper turn, or append `--auto` for an unattended run. The command drives the WhatsApp backend's real onboarding, cart, fee review, payment selection and order-history confirmation logic. Replies come from the application. The transport, model, catalogue, merchant login and payment are fixtures. This guided text journey runs locally and uses no API keys. It does not open a real WhatsApp chat or transcribe audio. For those touch points use the live path below.

**Test identity and seed data.** There is one end-user role, the shopper. The mock starts with a new temporary database on each run, seeds its fixture catalogue automatically, and supplies the synthetic merchant phone `9999999999`, OTP `123456` and saved address option `1`. In live mode each reviewer uses their own verified WhatsApp and consenting merchant login. SQLite creates its schema on first boot, and products and addresses come from the store. There is no separate seed command, admin login or web frontend. The WhatsApp worker starts with the server in step 5.

**Legacy iOS touch point (optional).** The submission's supported shopping flow is WhatsApp. To inspect the earlier native client, use macOS with Xcode 15+ and an installed iOS 17+ simulator. From the root run:

```bash
uv --directory backend sync --locked
uv --directory backend run --locked uvicorn easydial.app:app --host 127.0.0.1 --port 8000
```

In another terminal, run `open ios/EasyDial.xcodeproj`. Select the **EasyDial** scheme and an iPhone simulator, then press **Run**. Open the app's connection settings and use `http://127.0.0.1:8000`. For live speech and product search this earlier API uses the OpenAI credentials and single-operator merchant profiles described in [the credential guide](docs/CREDENTIALS.md). The iOS client does not implement the WhatsApp onboarding and payment journey.

**What’s next.** Priorities are more reliable Instamart sign-in and recorded benchmarks for multilingual voice requests and corrections. The paid order in the existing slides is from the Blinkit pilot.

## 1. Prepare a clean server

Use a Linux **x86-64** host with Docker Engine, Git, curl and OpenSSL, internet access and enough memory for Chrome (the hosted pilot has 8 GB). Commands below assume Bash and access to the Docker daemon. The application image supplies Python, pinned backend dependencies, FFmpeg, Google Chrome and Xvfb. You do not need MongoDB, Redis, an existing browser profile or a developer's home address.

On a fresh Ubuntu server, one installation path is:

```bash
sudo apt-get update
sudo apt-get install -y docker.io git curl openssl
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Reconnect your login shell if you just joined the Docker group. On a host that already has Docker, start here:

```bash
docker info >/dev/null
git clone https://github.com/prithidevghosh/easydial.git
cd easydial
docker build --platform linux/amd64 -f deploy/container/Dockerfile -t easydial-judge:local .
```

Use one worker per database. For independent reviewers, use one deployment and Meta callback per Meta app. Alternatively, multiple verified testers can share one deployment using separate WhatsApp and merchant accounts. Each sender gets an isolated basket, consent record and store session. One Meta app's callback cannot point to several independent servers at once. On a shared Docker host give each deployment its own name, free port and private config directory:

```bash
export RUN_ID=easydial-judge-1
export PORT=18001
export CONFIG_DIR="$HOME/.config/$RUN_ID"
install -d -m 700 "$CONFIG_DIR"
```

Keep this shell for the remaining commands. Pick a unique RUN_ID and port if another reviewer is already running here.

## 2. Get credentials from your own accounts

1. Sign in to the [Meta App Dashboard](https://developers.facebook.com/apps/). Register as a developer if prompted. Choose **Create App → Connect with customers through WhatsApp**. Select or create a business portfolio and finish creating the app.
2. In the app open **Use cases → Customize → Connect on WhatsApp → Quickstart → Start using the API**. Some dashboard layouts show **WhatsApp → API Setup**. Select or create a WhatsApp Business Account and select the provided test number under **From**.
3. Copy two different fields: the visible **From phone number** (for the join QR) and the numeric **Phone Number ID** (for API calls). The WhatsApp Business Account ID, or WABA ID, is a third identifier. Keep it handy for subscription troubleshooting.
4. Under **To**, add the reviewer's personal WhatsApp number and complete Meta's verification. Generate the access token for the same app and WhatsApp account. Use **Send message** in API Setup and confirm the template arrives. This tests outbound delivery only. The short-lived test token is sufficient for a review. An extended deployment needs a token with access to the same WhatsApp assets and the `whatsapp_business_messaging` permission. Management operations also require `whatsapp_business_management`.
5. In **App settings → Basic → App secret → Show**, copy the app secret. Easy Dial uses it to verify Meta's webhook signatures. You also generate a separate webhook verify token in step 4.
6. Obtain an [OpenAI project API key](https://platform.openai.com/api-keys) with billing and access to the configured text and transcription models (defaults: `gpt-4.1` and `gpt-4o-transcribe`). Get a real support contact for the pilot. For live store verification you also need a consenting tester with a merchant phone login, access to its OTP, and a saved address served by the chosen store in India.

Meta's [official setup walkthrough](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started) covers account creation and the dashboard test message.

## 3. Give Meta a public HTTPS URL

If your host already has a public TLS reverse proxy, forward the origin to `127.0.0.1:$PORT` and use its HTTPS origin below. Otherwise, for a short review on **Linux**, run a Cloudflare Quick Tunnel:

```bash
docker run -d --name "$RUN_ID-tunnel" --network host \
  cloudflare/cloudflared:latest tunnel --no-autoupdate --url "http://127.0.0.1:$PORT"
docker logs "$RUN_ID-tunnel" 2>&1 | grep trycloudflare.com
```

It can take a few seconds for the URL to appear. Read the **generated HTTPS origin** from the logs. Keep the tunnel running. A 502 before step 5 is expected. On macOS with Docker Desktop, run the host's `cloudflared tunnel --url "http://127.0.0.1:$PORT"` instead of the host-network container. Quick Tunnel URLs change when the tunnel is recreated. [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)

## 4. Create the private environment file

Create a new file outside the checkout. This command **refuses to overwrite an existing file** so rerunning the guide cannot rotate the encryption key for existing logins. For an existing deployment, edit the original file.

```bash
(
  set -e -C
  umask 077
  cat > "$CONFIG_DIR/runtime.env" <<ENV
WHATSAPP_MULTIUSER=true
WHATSAPP_PHONE_NUMBER_ID=REPLACE_WITH_META_PHONE_NUMBER_ID
WHATSAPP_BUSINESS_NUMBER=REPLACE_WITH_META_FROM_NUMBER
WHATSAPP_ACCESS_TOKEN=REPLACE_WITH_META_ACCESS_TOKEN
META_APP_SECRET=REPLACE_WITH_META_APP_SECRET
WHATSAPP_VERIFY_TOKEN=$(openssl rand -hex 32)
CUSTOMER_SESSION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
WHATSAPP_SUPPORT_CONTACT=REPLACE_WITH_SUPPORT_EMAIL
OPENAI_API_KEY=REPLACE_WITH_OPENAI_PROJECT_KEY
WHATSAPP_PUBLIC_URL=REPLACE_WITH_HTTPS_ORIGIN
WHATSAPP_ALLOWED_SENDER_1=REPLACE_WITH_VERIFIED_TESTER_NUMBER
WHATSAPP_DATABASE=/data/whatsapp.sqlite3
CUSTOMER_SESSION_DIRECTORY=/data/customers
BROWSER_CHANNEL=chrome
BROWSER_HEADLESS=true
BLINKIT_BROWSER_HEADLESS=false
ENV
)
```

Edit `$CONFIG_DIR/runtime.env` in your private editor. Replace every `REPLACE_WITH_...` value. Use raw values without quotes in Docker's env file. Both visible WhatsApp numbers use country code plus digits, without `+`, spaces or dashes. The public URL is the HTTPS origin without a path or trailing slash. Keep the generated padded session key with the data volume across restarts. Never commit this file.

The numbered sender is the **customer receiving replies**. Add `WHATSAPP_ALLOWED_SENDER_2` through `_5` for other verified testers. In multi-user mode a populated numbered slot turns on sender filtering. The old `WHATSAPP_ALLOWED_SENDER`, if present, counts toward five distinct numbers. Keep each tester in both the Easy Dial list and Meta's test recipient list. Store phone numbers and OTPs are supplied during onboarding. Store addresses, cookies and local browser-profile paths do not belong in this file.

## 5. Start the service and verify it

From the repository root:

```bash
docker volume create "$RUN_ID-data"
docker run --rm --platform linux/amd64 --user 0:0 \
  -v "$RUN_ID-data:/data" --entrypoint chown easydial-judge:local 10001:10001 /data
docker run -d --platform linux/amd64 --name "$RUN_ID" --restart unless-stopped \
  --shm-size=1g --env-file "$CONFIG_DIR/runtime.env" \
  -v "$RUN_ID-data:/data" -p "127.0.0.1:$PORT:8001" easydial-judge:local
export PUBLIC_URL="$(sed -n 's/^WHATSAPP_PUBLIC_URL=//p' "$CONFIG_DIR/runtime.env")"
curl -fsS --retry 15 --retry-all-errors --retry-delay 1 "http://127.0.0.1:$PORT/health"
curl -fsS --retry 8 --retry-all-errors --retry-delay 1 "$PUBLIC_URL/health"
curl -sS -D - -o /dev/null "$PUBLIC_URL/join"
```

Both health responses should say `"status":"ok"`. The join request should return **307** with `Location: https://wa.me/YOUR_BUSINESS_NUMBER?text=Start`. Open `$PUBLIC_URL/join/qr.png` to get **your own** QR. The QR bundled with the repository belongs to the hosted pilot. Health verifies configuration presence and startup. Actual credentials and store access are checked on use.

## 6. Connect the WhatsApp webhook

1. In **WhatsApp → Configuration → Webhooks** (or **Use cases → Customize → Configuration**), set the callback URL to **your HTTPS origin followed by `/webhooks/whatsapp`**.
2. Enter the exact generated `WHATSAPP_VERIFY_TOKEN` from your private file, then **Verify and save**. Subscribe the WhatsApp Business Account object to the **messages** field. This covers inbound messages and outbound delivery statuses.
3. In API Setup confirm the app is connected to the intended WABA. For manually provisioned accounts, check the Graph API's `GET /<WABA_ID>/subscribed_apps` with a management-authorized token. If your app is absent, subscribe it with `POST /<WABA_ID>/subscribed_apps` using that app's authorized token.
4. From the verified tester's phone, scan the QR or open `$PUBLIC_URL/join`. Tap **Send** on the prefilled **Start**. A store picker in that chat verifies the full inbound-webhook and outbound-reply path.

## 7. Walk through the product as a shopper

| Action in WhatsApp | What to look for |
|---|---|
| Send **Start**, then choose **Blinkit** for the reference journey. | Easy Dial asks for explicit permission to use the OTP for login and save the session. Decline to test the exit, or select **I agree** to continue. |
| Enter the store account's Indian mobile number, then its OTP in the setup conversation. Choose one of your saved delivery addresses. | The store sends the OTP. Easy Dial confirms the selected address and connects this customer's session. OTPs may remain in the tester's own chat. |
| Record “Chaar Diet Coke aur ek popcorn chahiye.” Text also works. | Easy Dial transcribes the request, searches the connected store and offers real products with prices and images when available. Choose a match for each item. Availability and pack sizes depend on the store and address. |
| Ask to change the quantity or choose another option, then tap **Review basket**. | The revised basket and store bill show the item total, fees (including small-cart fees when applied), delivery address and final payable amount. Reviewing can update the actual merchant cart. |
| Choose a payment method. | Blinkit can offer pay on delivery or a UPI QR when available. Instamart's supported checkout method is pay on delivery. The WhatsApp QR is a real payment request. |
| To review without spending, stop at the bill or payment selection. For an intended purchase, explicitly confirm **Place order** for cash or approve UPI in your own payment app. | Cash confirmation submits a real order. Easy Dial checks merchant order history for the matching order and sends its status. **Check status** requests a fresh lookup. If the result is uncertain, inspect the store's own order history before deciding to retry. |
| Send **Clear basket** to clear a draft, or **Logout** to disconnect. | Logout removes that customer's saved store access. A subsequent shopping request requires a store login. Placed orders remain with the merchant. |

The same flow accepts other goods in the store catalog. For example, try AA batteries or shampoo. Product search and checkout require live merchant availability. Instamart login has intermittently failed before or after OTP. A merchant challenge, unavailable address, or login cooldown may prevent live completion on a particular server. Use the store's supported verification flow. Record the last verified stage when reviewing.

## Troubleshooting, restart and checks without accounts

| Symptom | Check |
|---|---|
| Health says `setup_required` or container keeps restarting | Check the required values, numeric IDs, session key and volume permissions. Use `docker logs --tail 80 "$RUN_ID"`. One process can own a WhatsApp database at a time. |
| Meta rejects callback verification | Visit the public health URL, confirm the full callback path, and compare the verify token character for character. The app secret is used separately to verify signatures on POST requests. |
| Dashboard template works, but “Start” gets no reply | Check the **messages** subscription, WABA app subscription, receiving Phone Number ID, tester's two allowlists and token expiry. Look for inbound and delivery events. Meta's dashboard test alone does not exercise the webhook. |
| Join link opens the wrong chat | Check `WHATSAPP_BUSINESS_NUMBER` (visible From number). Scan the QR served by your own instance. |
| Token rotated or tunnel origin changed | Update the private env file. For a new origin also update Meta's callback. Recreate the **container** using the same database volume, session key and Meta account. |
| Text works but voice fails | Verify funded OpenAI project access to the transcription model. FFmpeg is included in the image. |
| Login works but address or catalog is empty | Verify a saved, serviceable address in that merchant account. Use a different tester's independent account to test isolation. |

For queue and delivery counts without revealing messages, from the clone run:

```bash
docker exec -i "$RUN_ID" python - < backend/tools/whatsapp_status.py
```

For a normal restart, run `docker restart "$RUN_ID"`. After **editing** the env file, stop the worker and remove its container (`docker stop "$RUN_ID" && docker rm "$RUN_ID"`), then repeat step 5 using the same volume. A simple restart retains the old container environment.

**Offline acceptance tests.** From the repository root, use [uv](https://docs.astral.sh/uv/getting-started/installation/) with Python 3.11+ and FFmpeg on the host. These tests exercise signed webhook handling, isolated consent/login, conversational edits and checkout through confirmation using controlled external services. No real OTP, order or payment is involved.

```bash
uv --directory backend run --locked pytest -q tests/test_whatsapp.py tests/test_onboarding.py tests/test_whatsapp_journey.py
```

For the full backend suite, omit the three filenames. For a container browser check after step 5:

```bash
docker exec -i "$RUN_ID" python - < deploy/container/smoke.py
```

The expected output confirms both headed Chrome on Xvfb and headless Chrome. For a longer-lived AWS deployment see [the deployment guide](docs/AWS_DEPLOYMENT.md). The Docker path above is sufficient for judging.

### Fresh-clone verification

On 22 November 2026 we built the Linux/amd64 image from a clean public clone at `077e3ad`, booted it with a new data volume and disposable test settings, and reached it over a public HTTPS tunnel. We decoded its QR to the configured test chat, verified the webhook challenge, checked that a signed event was accepted and an unsigned event rejected, and launched Chrome in both modes. The full backend suite on the clean checkout reported 514 passed and 82 skipped. The onboarding-to-order test uses controlled external services. A judge using new accounts must still verify Meta delivery, complete the merchant OTP and check availability at their own address. The reference paid order shown in the slides came from the earlier live pilot.
