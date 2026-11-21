> Historical pre-implementation audit. Customer onboarding and isolation have since been implemented as a controlled pilot; see [CUSTOMER_ONBOARDING.md](CUSTOMER_ONBOARDING.md) for current capabilities and remaining verification/deployment gates.

# Onboarding and AWS readiness audit

Date: 2026-11-21. Source inspection only; no onboarding, cloud deployment, merchant login, cart modification or purchase performed.

## Verdict

The current application is a single-operator WhatsApp commerce pilot. It is not ready for public multi-user onboarding or an unchanged production AWS deployment. A single-user AWS pilot is feasible after Linux packaging, durable storage, secrets, HTTPS and merchant validation on the target host.

## Evidence

- `backend/src/easydial/settings.py`: one `whatsapp_allowed_sender`, one browser profile and verified address per store.
- `backend/src/easydial/whatsapp/app.py`: rejects other senders; a single worker owns the database; database binding includes sender/profile configuration; one global ProviderWorkers instance.
- `backend/tools/connect_instamart.py` and `connect_blinkit.py`: operator-driven browser login and address selection, not customer chat onboarding.
- `backend/src/easydial/providers/browser.py`: persistent local browser profiles, configurable headed/headless Chrome; no per-customer session resolver.
- Customer-keyed conversation/draft records exist, but this does not isolate merchant accounts or browser contexts.
- No Dockerfile, AWS infrastructure configuration, or deployment pipeline was found. Previous deployment research contains historical sections and must not be treated as current implementation status.

## Requested customer journey (not implemented)

1. Click an EasyDial WhatsApp link or scan a QR encoding that link; explicitly send the prefilled Start message. Opening/scanning alone is not a received message or authenticated store connection.
2. Welcome, explain account access, choose Connect Blinkit / Connect Instamart / connect the other later.
3. Ask for the chosen store's phone number; do not assume it equals the WhatsApp sender number. User confirms permission to sign in.
4. Open that customer's isolated merchant browser. Request merchant OTP through its actual login UI. Route the reply to a bounded login state machine, not the shopping LLM. Add expiry, retry/resend limits and cancellation. Prevent OTP persistence in ordinary inbound payloads, conversation history, error logs or analytics; discard it immediately after use. OTP chat login still needs validation against each actual website flow. Offer a private browser recovery flow when a challenge cannot be completed through chat; do not bypass it.
5. Verify successful login before marking connected. Persist the customer's encrypted session/profile, bound to customer + provider. Sessions can expire or be revoked: implement reconnect, logout and deletion. Do not promise permanent login.
6. Read saved merchant addresses for that account. Present a numbered/list selection, apply it in that browser, read back the full selected address and save the merchant address identity. If none exists, offer add-address setup. Cross-store delivery must resolve to the same physical address through explicit selection.
7. Resume ordinary shopping with only the connected stores. Payment/order attempts, carts, history, media and locks must stay within that customer/provider account boundary.

## Implementation sequence and release gates

- [ ] Add customer/store connection records and a session resolver; never reuse the pilot browser profiles for arbitrary new senders.
- [ ] Add deterministic connect/phone/OTP/address/reconnect states and secret-redacted ingress handling before enabling OTP replies.
- [ ] Add join URL/QR and welcome/store-selection messages using a properly configured EasyDial WhatsApp business number. Current test-number public-launch readiness has not been verified.
- [ ] Validate login, invalid/expired OTP, resend limits, challenge recovery, revoked sessions and address selection for both stores.
- [ ] Prove two distinct customers cannot see each other's addresses, products, carts, order history or QR artifacts, including concurrent work and restarts.
- [ ] Build a pinned Linux runtime with Chrome/Playwright dependencies and audio tooling; verify configured browser channel exists.
- [ ] Start with one always-on EC2 pilot instance, encrypted persistent disk, process supervision, stable HTTPS webhook, managed secrets and redacted monitoring/backups. This is a proposed architecture, not provisioned infrastructure.
- [ ] Validate store login and read/cart/payment journeys from the actual AWS IP. Local headless success or headed recovery does not prove cloud acceptance; blocks remain a deployment risk.
- [ ] For public multi-user deployment, introduce PostgreSQL for customer/order state and an appropriate durable job queue/account lock system; Redis may assist locks/queueing but is not the sole record of orders/payments. Maintain once-only submission and uncertain-result recovery across crashes.
- [ ] Verify paid-order confirmation on a future explicitly authorized live order. The prior cancelled order's exact reconciliation rejection remains unproven; do not mark this gate passed from unit tests.
- [ ] Add health/queue monitoring, token rotation, rate limits, retention/deletion and a deployment rollback procedure before public launch.

## References

- WhatsApp click-to-chat format: https://faq.whatsapp.com/5913398998672934
- AWS ECS storage alternatives if later containerized: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_data_volumes.html

Do not remove the current sender restriction until account isolation is implemented and tested. No production-readiness claim is established by this audit.
