# Headless deployment and payment research

Updated 2026-11-21. Scope: source-code inspection and primary-source research. No store cart was changed, payment page advanced, payment session created, order submitted or deployment configuration changed during this research. User reports the WhatsApp integration is working; this is user-reported evidence, not independent verification of the complete paid-order journey.

## Current implementation update

The subsequent authorized inspection and implementation are recorded in [PAYMENT_INSPECTION.md](PAYMENT_INSPECTION.md). Configurable headless Chrome and local authenticated read-only checks now pass for both stores. WhatsApp fee tables, explicit fee warnings, live amount revalidation and private cart checkpoints are implemented. Payment initiation remains disabled. The original research below describes the state before this inspection.

## Headless verdict

Playwright can run headless on a Linux host/container. Our current `providers/browser.py` explicitly sets `channel="chrome", headless=False` for both persistent-profile and storage-state paths. A configurable `headless=True` is technically straightforward, but it has NOT been implemented or tested against either authenticated store in the deployment environment. Do not equate Playwright support with either site's acceptance of automation.

Official Chrome uses the newer headless implementation; Playwright's default Chromium headless shell is a different runtime. Preserve the browser channel initially to reduce variables, explicitly install Chrome if using `channel="chrome"`, and validate the selected binary on the intended Linux image. Pin matching Playwright/package/browser versions. [Playwright browsers](https://playwright.dev/python/docs/browsers), [Docker deployment](https://playwright.dev/python/docs/docker)

### Recommended deployment design

- Always-on Linux VM or container host for FastAPI, the durable worker, FFmpeg and browser processes. A single-host pilot is adequate initially; arbitrary public users are not supported by the current account model.
- Stable HTTPS hostname for Meta's webhook; replace the temporary Cloudflare development URL.
- Persistent protected volume for the private SQLite state and browser profiles. Run one worker per database/profile; do not autoscale instances against the same filesystem/profile. Current file locks do not implement distributed ownership.
- Store secrets outside the image/repository; encrypt private volumes and backups, restrict operator access and add retention/deletion before a production launch.
- Authenticate on the target host through a private, access-controlled browser onboarding session, then test headless reuse. Do not assume copying the raw macOS Chrome profile to Linux will preserve cookies or login. Exported Playwright state is sensitive and may not capture all site authentication/device state. [Authentication guidance](https://playwright.dev/python/docs/auth)
- Provide a private headed-browser recovery path for expired login, OTP and site challenges. On a headless Linux host, headed Chrome can use a virtual display; this is a deployment option, not a way to bypass challenges. No public remote-debugging or unauthenticated remote-desktop port.
- Set explicit viewport/timezone/locale as appropriate to the user, and use the verified selected store address. Server IP/geolocation is not the customer's delivery location.
- Monitor worker availability, queue age, authentication failures and provider errors without logging personal payloads. Treat access blocks as a stop requiring attention, not an invitation to rotate identities or evade detection.

The existing durable inbox/outbox does not make browser writes transactionally durable: provider cart ownership remains in memory. Before production payment, persist and reconcile cart ownership and payment attempts. Ordinary short-lived serverless handlers are unsuitable for owning this long-lived browser session; a webhook frontend could enqueue to a separate persistent worker.

### Headless acceptance gates

- [x] Add configurable browser headless/channel settings without silently changing the working local mode.
- [ ] Build a pinned Linux runtime with browser dependencies and FFmpeg.
- [ ] Manually onboard the intended store accounts on that host.
- [ ] Verify both stores' authenticated address/search reads, then authorized add/edit/review/cleanup tests.
- [ ] Verify restart recovery, expired login, network interruption, stock change and unrelated-cart protection.
- [ ] Test from the actual deployment IP; record any challenges without bypassing them.
- [ ] Validate resource usage and serialize per-account writes before considering multiple users/workers.

## What payment research establishes

| Source | Established capability | What it does NOT establish |
| --- | --- | --- |
| Blinkit terms | Checkout may offer cards, net banking, UPI, COD and other supported methods | Exact availability for this account/cart; a transferable payment URL/QR; browser selectors |
| Swiggy terms | Platform accepts online payment methods and payment at delivery where offered | Exact Instamart.in desktop/mobile checkout behavior for this account |
| Official Swiggy MCP checkout docs | An authorized integration can return UPI payment-pending references/intent/bridge information; wallet/COD can complete order placement immediately | Access for our account or equivalent browser-exposed links; we are NOT switching back to MCP |
| Existing browser ledger | Both carts previously reached a Proceed to Pay control | Payment-screen contents, side effects of that control, completed payment or order status |

Sources: [Blinkit terms](https://blinkit.com/terms), [Swiggy terms](https://www.swiggy.com/terms-and-conditions), [Swiggy checkout reference](https://mcp.swiggy.com/builders/docs/reference/instamart/checkout/), `BROWSER_VERIFICATION.md`.

## Recommended payment model

The provider remains the merchant/payment recipient. EasyDial prepares and reviews the basket, asks for explicit approval, and hands the user into the provider's payment flow. The user authorizes payment on their own phone. Do not collect a UPI PIN, card CVV or banking OTP in WhatsApp or in the agent. [NPCI UPI guidance](https://www.npci.org.in/what-we-do/upi/faqs)

Preferred UX, subject to observing actual provider support:

1. User requests checkout. Re-read exact products, quantities, full address, discounts, fees and total from the provider.
2. Show the complete payable bill and selected payment method. Obtain explicit approval tied to a durable basket-version/amount/address snapshot. A price/address/item change invalidates approval. A generic conversational “yes” is insufficient unless bound to this exact pending question.
3. Enter the provider's supported payment flow once. Record any order/payment reference immediately. Some actions may create an unpaid order or debit a wallet instantly; inspect and classify the actual UI before automating them.
4. If the website exposes a legitimate order-linked hosted payment URL or mobile UPI intent, provide a short-lived HTTPS handoff in WhatsApp. Preserve the provider-issued reference and amount; do not synthesize a payee or a generic UPI transfer request.
5. User opens the link, checks payee/amount and authorizes in their UPI/bank app. Test WhatsApp's in-app browser and external app opening on actual iPhone and Android devices.
6. Reconcile payment AND order on the provider's authenticated confirmation/order-history surface. Record order ID, paid status and relevant receipt/reference. Only then send a confirmed/paid message in WhatsApp.

### Handoff uncertainty is a core implementation gate

A cloud browser's cookies are not automatically available in the user's phone browser. A copied checkout-page URL may require login, lead to a different cart, or be bound to the original session. Do not call any arbitrary URL a payment link until its cross-device behavior has been tested for that provider.

If only a provider-issued dynamic QR exists, evaluate whether it can be delivered intact and whether the user's UPI app can import it from the same phone. This is less accessible than one-tap payment and cannot be assumed to work universally. Never generate a QR from a guessed merchant VPA: it may transfer money without paying the grocery order.

If neither supported transferable intent/link nor usable QR is available, use a normal provider app/site checkout handoff and have the user log in there, or pursue an authorized provider integration. A seamless one-tap cross-device checkout remains unproven for both browser adapters.

Creating a Razorpay/other gateway account for EasyDial is not a shortcut: a payment collected by EasyDial would not automatically settle the Blinkit/Instamart order. That would introduce a different merchant/settlement/refund model. No payment-gateway key is currently needed for the recommended provider-owned checkout model.

### UPI changes matter

Do not base the product on asking for a UPI ID and sending a generic collect request. Razorpay's current integration guidance describes migration away from UPI Collect effective 28 February 2026, with specified exceptions including iOS mobile app/mobile web. Prefer supported intent/QR flows and validate the actual provider/device path; this source is payment-infrastructure guidance, not proof of either store's implementation. [Razorpay migration guidance](https://github.com/razorpay/markdown-docs/blob/master/announcements/upi-collect-migration/standard-integration.md), [UPI Intent](https://razorpay.com/docs/payments/payment-methods/upi/upi-intent/).

### COD and wallets are not harmless test modes

COD may avoid an immediate online payment but still places a real order and creates a delivery/payment obligation. It is not a demonstration of a paid order. A funded wallet can debit and place an order immediately. Do not select either to test selectors without explicit authorization for the specific purchase. Availability remains account/order dependent.

## Required implementation state and reconciliation

Proposed state progression:

`draft → reviewed → approval_recorded → payment_initializing → awaiting_user_payment → payment_verifying → confirmed_paid`

Additional independent outcomes: `expired`, `payment_failed`, `order_failed`, `cancelled`, `payment_unknown`, `paid_order_unconfirmed`, `refund_pending`. If an unpaid order is created early, retain its ID without calling it a successful paid order. COD would require a separate `confirmed_unpaid_cod` state.

Persist the approval, basket digest, amount, address reference, chosen method, attempt ID, provider order/transaction references and expiration BEFORE risky actions. Use one active attempt per approved basket. Existing event deduplication alone cannot prevent duplicate charges across crashes.

On timeout, browser close or ambiguous response: reconcile provider state; never immediately repeat checkout, regenerate payment or create another order. Distinguish payment success from fulfillment acceptance. A screenshot from the user, an app-return URL, a WhatsApp delivery receipt or the model's confidence is not sufficient proof of payment/order completion.

We do not receive Blinkit/Swiggy payment-gateway webhooks merely by operating a WhatsApp webhook. Without an authorized provider status API, bounded authenticated browser readback is the current potential reconciliation path and still needs inspection/testing. Do not reverse-engineer/replay private payment APIs as a shortcut.

## Next concrete research and build gates

- [ ] Inspect both providers' actual payment-choice screens for the logged-in account in a controlled session; record redacted UI evidence, button semantics, supported methods and whether entry creates a pending order. Current no-purchase instruction remains in force.
- [ ] Establish whether a provider-issued link/intent/QR survives a handoff to the same user's phone. Stop if an action could submit an order or debit funds without authorization.
- [ ] Inspect confirmation/history/status surfaces and cancellation/expiry behavior before writing payment automation.
- [ ] Implement durable approvals, payment-attempt records, stale-bill rejection and uncertain-outcome recovery with mocked tests first.
- [ ] Test the handoff on actual iPhone/Android WhatsApp; ensure no session secrets are exposed in chat/logs.
- [ ] Only after the user authorizes an exact basket and amount, perform one low-value real purchase and verify receipt/payment/order evidence. This research session does NOT provide that authorization.

## Decision

Headless hosting is feasible at the browser/runtime level; cloud store reliability must be demonstrated. Recommend a persistent Linux browser worker and provider-owned user-authorized UPI payment handoff, conditional on actual website support. Keep the current live channel and local Pending boundary unchanged until payment surfaces, approval and reconciliation are implemented and verified.
