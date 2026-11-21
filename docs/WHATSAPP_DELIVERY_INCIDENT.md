# WhatsApp reply failure — 2026-11-21

After the operator replaced the access token, the server was restarted to reload backend/.env. Local and public health both pass. Incoming events exist in the customer queue; outgoing replies are rejected by Meta.

Verified without retaining credentials:
- Token debugger: valid USER token for the expected EasyDial app.
- whatsapp_business_management and whatsapp_business_messaging: granted.
- Test Messaging Account review status: APPROVED.
- Test phone status: CONNECTED; phone lookup succeeds.
- Plain text sends return HTTP 403 / Meta 131005 (Access denied).
- The same failure occurs on configured Graph v27.0 and dashboard example v25.0.

This is not a merchant browser block or a stopped server. Exact authorization failure remains unresolved; valid token and granted scope names alone do not establish send permission. Next isolate using the dashboard's own test send with the same test account and approved recipient, and inspect the account selection in the token authorization flow. Do not repeatedly regenerate tokens without checking account assignment. Do not replay uncertain historical outbox entries blindly.

Onboarding live OTP verification remains pending until outgoing delivery is restored. No merchant order was placed during these checks.

Follow-up: the operator reproduced the same permission error using Meta API Setup's own Send message. Business Suite shows the owner with Full access to the Test Messaging Account, one linked connected test WhatsApp number, high quality, and the account-structure migration notice. Inspect the separate WhatsApp account assignment next. Meta's current Access Tokens Guide explicitly distinguishes grants on Messaging accounts and WhatsApp accounts (Business Suite does not cascade system-user grants).

## Current status

Later incoming customer turns received delivered/read replies and completed Blinkit OTP onboarding. The 131005 incident is no longer blocking the live pilot. The precise Meta-side permission correction was not independently established; do not claim that a system-user token was proven to fix it. Do not retain or publish debugger URLs containing tokens.

## 2026-11-21 22:00 IST — Expired public tunnel DNS

Symptom: Meta's outgoing test send worked; user-originated WhatsApp texts produced no response. Backend health was 200, last inbound persisted event was 21:26:59 IST, and the latest outbound messages were sent/read. Configured temporary Cloudflare hostname no longer resolved, despite cloudflared still running. Restarting only the backend cannot fix a dead public hostname.

Restarted the quick tunnel, updated WHATSAPP_PUBLIC_URL privately, restarted backend, and verified public health plus signed empty webhook POST both return 200. Entered the new callback URL in Meta Configuration; messages remained subscribed. Meta clears its masked verify token on URL edits, so asked the operator to enter the existing WHATSAPP_VERIFY_TOKEN and save, then send a real WhatsApp message. At this checkpoint, Meta save and end-to-end incoming message delivery are still pending user action.

Production requirement: stable HTTPS hostname / named tunnel or deployed backend. Quick-tunnel hostnames are temporary and may need replacement; a live local health endpoint alone does not prove Meta can reach the webhook. Never put verify tokens, app secrets or access tokens into incident notes or chat.
