# Blinkit proxy journey recovery

A saved Blinkit login and a Webshare network journey have separate lifetimes. Recovery preserves encrypted authentication and cart/payment records; it never invokes onboarding or replays a merchant action.

## Recovery boundary

Only an observed proxy IP mismatch before a merchant action can start recovery. The customer-scoped browser owner must explicitly permit it. Missing/invalid policy, persistent browser profiles, login browsers and active checkout contexts fail closed.

The policy reads durable customer state and all payment/order attempts on each decision. Current payment preparation/menu, cash or retry confirmation, an in-memory payment checkpoint, uncertain submissions and unresolved active attempts prevent rotation, including after restart. An expired or locally revoked QR alone does not establish merchant failure.

A historical UPI network journey may end without declaring the payment failed only when it belongs to another draft, its monitor reports `not_found`, all its local artifacts were explicitly revoked, automatic checking was stopped, and no cash attempt exists. Those historical rows remain unchanged and existing once-only submission guards remain authoritative. This prevents explicitly stopped historical journeys from permanently blocking new shopping. Later settlement remains possible; this rule never reports an unpaid or failed merchant outcome.

## Browser behavior

There are at most three verification probes per open, including the original mismatch. Recovery copies current cookies/storage into an isolated candidate context and verifies a fresh India proxy. It rechecks eligibility before adopting that context. Success preserves login and publishes the verified pin; subsequent calls reuse that IP. Existing address/cart verification still runs before merchant actions.

Failure or cancellation closes only the candidate and retains the original context, login and pin. Protected checkout IP mismatch also leaves the existing page/context intact. No order, QR or payment action is repeated by recovery.

Encrypted state and pin publication is synchronous and rolls back on a write error. The two files are individually persisted; this is not a multi-file crash transaction. Either proxy pin remains independent of the reusable authentication state after a crash.

## Verification

Coverage includes encrypted login preservation, bounded retries, same-IP reuse, candidate failure/cancellation, persistence/restart, customer isolation, in-flight payment capture, payment appearing during verification, active/uncertain/revoked checkout protection, historical stopped journeys and unchanged draft/payment evidence. Live validation uses a disposable copy of the saved AWS session; direct WhatsApp validation is recorded separately from automated tests. Local browser checks use no proxy.

Release verification: 514 backend tests passed; 92 Chrome-enabled checks passed; Ruff and whitespace checks passed. An isolated AWS candidate with an intentionally stale pin recovered using copied saved authentication, returned 20 offers in each of two searches, and retained the same verified session/IP for the second request. Production session files remained unchanged in that diagnostic. Direct WhatsApp tests follow deployment.
