# Live browser verification — 2026-11-21

## Scope and result

Both Blinkit and **https://instamart.in/** were inspected in normal headed Chrome after the user completed phone login and selected the same physical delivery address. Both reached **Proceed to Pay** with full address, exact pack, quantities and live bill. No payment button was clicked and no order was placed, as explicitly instructed.

Both backend provider adapters and the HTTP API used by iOS were exercised against the live accounts. All test additions were removed and empty carts verified. This is evidence for a bounded live checkout-preparation journey, not production reliability, voice accuracy or paid-order completion.

## Live evidence

| Check | Blinkit | Instamart.in |
|---|---|---|
| Authenticated session restored after Chrome restart | Passed | Passed |
| Search and exact product/pack extraction | Passed | Passed |
| Initial cart empty verification | Passed | Passed |
| Add Heritage Daily Health Toned Milk, 500 ml | Passed | Passed; selected single pack in variant modal |
| Increase quantity 1 → 2 and re-read bill | ₹60 item subtotal, ₹133 total | ₹60 item subtotal, ₹131 total |
| Replace with full-cream milk, quantity 1 | ₹40 item subtotal, ₹113 total | ₹40 item subtotal, ₹111 total |
| Remove owned test products / verify empty | Passed | Passed |
| Read full saved checkout address | Passed | Passed |
| Website pre-payment boundary | Proceed To Pay | Proceed to Pay |
| HTTP search → add → review → local Pending → clear | Passed, ₹103 total | Passed, ₹101 total |
| Actual payment / order confirmation | Not attempted | Not attempted |

Prices above are observations for the selected address and time, not fixed catalogue expectations. The initial guest/detected locality had different prices and fees; only the user-selected address was used for the final checks. Address text, phone numbers, cookies and OTPs are deliberately excluded from this document.

### API smoke commands actually executed

From `backend`, with the configured local server running and test carts empty:

```sh
uv run python tools/check_live_cart.py --provider blinkit --confirm-cart-edits
uv run python tools/check_live_cart.py --provider instamart --confirm-cart-edits
```

Each reported live search/add/review → `checkout_pending`, then confirmed cleanup. These commands modify real carts but do not place orders. They require explicit opt-in and an exact product/pack match. Do not run them against a basket containing personal shopping.

## Defects found and fixed against observed UI

### Blinkit

- Homepage cards differ from search cards. Search has `div[role=button][id]`, but no LocationBar. Verify the exact selected location on the homepage before opening search.
- The empty cart button is disabled. Read the actual `CartButton__Button`, not its enclosing Container; missing drawer HTML never means empty.
- The drawer shell appears before items and bill; wait for its actual contents.
- Logged-in cart rows nest `DefaultProductCard__Container` inside `CartProduct__Container`. Reading both duplicates each item. Read the inner product rows only.
- Search uses icon-plus/minus controls; the drawer uses styled controls and a direct quantity text node. Both are supported.
- Existing items are edited/removed directly in the cart. Removal must not depend on a product still appearing in search.
- Explicit pack DOM handles `1 ltr`; struck MRP is excluded. Search duplicates are deduplicated by product identity.
- Page-wide network-idle waits were unreliable because analytics requests remained open. Wait for first-party request completion initiated by the UI action, then verify visible provider readback. No private API request is replayed.

### Instamart

- The old `www.swiggy.com/instamart` route returned HTTP 202 with a temporary-glitch page. Its cause was not established. The user supplied `instamart.in`, which returned HTTP 200 and worked. Do not describe the old route as proof of a bot block.
- Search requires login. Actual selectors use `item-collection-card-full`, `buttonpair-add` and product links. Pack and price are in the link's parent, not inside the anchor.
- Add can open a variant modal. Match all `variants-name` text exactly, so `500 ml` cannot silently become `500 ml x 4`. Confirm is scoped only to that customization widget.
- Quantity controls contain an animated next number as well as current count. Read only `add_buttons_center`, not all row text.
- Empty-cart screen says “Your cart is getting lonely”; this is an explicitly observed empty state.
- Bill shows struck old prices alongside current prices. Exclude struck values and require the bill's To Pay to match the footer payable total.
- Wait for saves before navigation. Immediate reload after a quantity click could abort a pending update; a visual animation alone is insufficient evidence.

## Configuration and lifecycle

Both authenticated dedicated profiles and each provider's exact selected header/full address were saved in ignored `backend/.state/site-audit` and `backend/.env`. The user selected the addresses; automation did not choose among saved addresses. `LIVE_ADDRESS_LABEL=Home` is the common app label, while each provider retains its own full address formatting privately.

The backend owns one persistent headed Chrome context per provider and serializes operations per account. Stop it before using a setup helper against the same profile. Chrome must be installed. Cart ownership and app sessions remain in memory; after a backend restart, clear any leftover test basket manually before starting a new session.

Empty carts do not expose full checkout addresses. Before initial draft writes, match the selected homepage location exactly to onboarding. After sync, require the full saved address and bill before returning a successful review.

## Automated checks and remaining limits

30 local tests pass with `EASYDIAL_DOM_TESTS=1 uv run pytest -q`, including five opt-in Chrome regressions using redacted structural HTML (no provider network). Normal CI runs the contract tests and skips those Chrome-only cases. Ruff passes. Fixture tests are not live evidence; the separate live runs above are.

Remaining work:

- OpenAI key is not configured; dictation, spoken corrections and multilingual benchmark are not live-verified.
- Browser offers do not yet extract complete brand/formulation metadata. Automatic exact-match comparison stays conservative; do not infer product equivalence from a generic “milk” query or compare different packs.
- Payment, payment-status reconciliation, order IDs, receipts and confirmed paid-order state are not implemented or tested. Local Pending is never an order.
- Test more products, cities, stock changes, quantity caps, expired login, variants, reloads and external cart edits. Single-product tests do not establish universal site coverage.
- No guarantee against future automation blocks. No stealth, CAPTCHA bypass, fingerprint spoofing, proxy rotation or retry loop around an explicit block is implemented.

## Blinkit India proxy journeys

Set `BLINKIT_PROXY_USERNAME` (base Webshare username or its `-in-rotate` form) and
`BLINKIT_PROXY_PASSWORD` in the backend's private `.env`. Both must be provided;
leaving both empty preserves direct browsing. Instamart remains independent.

Blinkit uses `http://p.webshare.io:80` with `<base>-in-<numeric-session-id>` at
Chrome launch. `ipv4.webshare.io` reports the exit IP; that IP is not a directly
connectable proxy endpoint. The rotating suffix is deliberately replaced with a
sticky session so page assets and subsequent actions use the same session.

A journey here is the connected Blinkit session, from login through shopping and
checkout. The session ID and first exit IP survive browser restarts/customer
switches in a private sidecar next to the browser state (encrypted for customer
sessions), or inside the operator profile. Credentials are never stored there.
Explicit reconnect starts a new journey. Existing basket resets do not rotate IP.

Before each browser `open()` returns to a provider operation, a separate page
checks the exit IP through the same browser proxy. A changed IP or failed check
closes the browser and stops the operation, without falling back to a direct
connection. This is an operation-boundary check, not a guarantee that Webshare
cannot change the IP during an in-flight request. Configure Webshare's dashboard
Sticky duration to cover the journey (supported range: 1 minute to 24 hours).
An expired/lost sticky IP requires reconnecting, not automatic rotation.

Run `uv run python tools/check_blinkit_proxy.py` from `backend` for an isolated,
headed, read-only check of the proxy, Blinkit phone-login UI, repeated IP checks,
and browser restart continuity. It never submits a phone number or OTP.

References: https://apidocs.webshare.io/proxy-connection and
https://help.webshare.io/en/articles/16310718-endpoint-generator-rotating-residential

Local verification (2026-11-22): the first live proxy probe failed before Blinkit;
a subsequent isolated run passed the proxy check, Blinkit HTTP 200, visible phone
login form, repeat-IP check, and persisted-session browser restart. OTP submission,
authenticated shopping/checkout and cloud-host acceptance were not tested in this
check. Backend regression suite: 283 passed, 40 skipped; changed-file Ruff checks
passed. Proxy availability can still fail; callers stop on that failure.
