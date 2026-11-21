# Commerce integration audit

Research date: 2026-11-21. This is documentation review and static source inspection, **not an authenticated end-to-end test**. No customer account was connected, cart changed, payment initiated, or order placed.

## Verdict

| Requirement | Swiggy Instamart | Unofficial Blinkit repository |
| --- | --- | --- |
| Location-specific products/prices | Documented structured search | DOM search implemented; live reliability unknown |
| Pack size and variant matching | Variant fields documented | Search output lacks dedicated variant/pack-size fields |
| Stock information | Product and variant availability fields | Inferred from visible ADD cards; incomplete evidence |
| Add/remove/replace before checkout | Whole-cart replacement supports this | Increment/decrement operations implemented; replacement needs orchestration |
| Full payable amount | Cart bill breakdown documented | Cart text scraping implemented; selector dependent |
| Payment/order placement | Documented payment lifecycle | Checkout/payment clicks implemented; confirmation verification missing |
| Tracking/history | Dedicated tools documented | History tool exists; no dedicated tracking tool exposed |
| Multiple EasyDial users | Per-user delegated OAuth documented | One global context/session path; unsafe to share unchanged |
| Fit without changes | Strong candidate, subject to access validation | Does not align fully as-is |

## Swiggy: actual interface

Official endpoint: `https://mcp.swiggy.com/im`, using streamable HTTP. The reference lists 16 tools:

- Discovery: `create_address`, `delete_address`, `get_addresses`, `search_products`, `your_go_to_items`.
- Cart: `clear_cart`, `get_cart`, `update_cart`.
- Payment: `get_payment_options`, `check_payment_status`, `confirm_order`.
- Order: `checkout`.
- Tracking: `get_delivery_status`, `get_orders`, `track_order`.
- Support: `report_error`.

The actual session's `tools/list` is authoritative; documented tools may not all be available to a particular account. [Tool reference](https://mcp.swiggy.com/builders/docs/reference/instamart/), [framework connection guide](https://mcp.swiggy.com/builders/docs/start/developer/build-an-agent/).

### Search and cart semantics

`search_products` accepts a query with a saved address ID, or guest coordinates. It returns product/variant identifiers, brand, quantity description, MRP/offer price, availability, and optional quantity caps/ETA. Use variant-level `spinId` and `skuId`; product-family IDs are insufficient. Check pagination and never invent IDs. [Search contract](https://mcp.swiggy.com/builders/docs/reference/instamart/search_products/).

`update_cart` **replaces the entire cart**. EasyDial must read the existing cart, reconcile the intended change, submit the complete desired basket, then read back the result. Returned removed-out-of-stock and reduced-quantity entries require user-facing reconciliation. A spoken “add bread” must not silently delete milk already present. [Update contract](https://mcp.swiggy.com/builders/docs/reference/instamart/update_cart/).

`get_cart` includes item variants/quantities, selected address, bill line items, payable total, and possible availability/address warnings. Search prices are preliminary; checkout should use the refreshed basket total. Do not assume every optional field exists. [Cart contract](https://mcp.swiggy.com/builders/docs/reference/instamart/get_cart/).

### Payment is a workflow

Read live payment options. Depending on eligibility these can include UPI app intent/QR, COD, or Swiggy Money. Do not promise COD or silently choose a method. The documented UPI flow uses returned app identifiers rather than asking for a VPA. [Payment options](https://mcp.swiggy.com/builders/docs/reference/instamart/get_payment_options/).

After a final summary and explicit user approval, call `checkout`. Multi-store carts may produce multiple orders and partial success. Do not retry the entire basket after partial success. There is no cancellation tool in the documented Instamart set; the checkout reference directs users to support. [Checkout contract](https://mcp.swiggy.com/builders/docs/reference/instamart/checkout/).

For pending UPI, use the returned transaction identifier and polling cadence. Inspect terminal/success/confirmed flags. A widget may already finalize the order; a custom headless client may need to do so. [Status contract](https://mcp.swiggy.com/builders/docs/reference/instamart/check_payment_status/). Call `confirm_order` only for successful payment that has not already been confirmed. [Confirmation contract](https://mcp.swiggy.com/builders/docs/reference/instamart/confirm_order/).

### Access and limitations

Local prototyping is advertised as free and without prior approval; production requires application. This is not proof that every callback, account, location, or payment path will work in EasyDial. Real commerce is involved; do not infer a free-order sandbox. [Developer access](https://mcp.swiggy.com/builders/developers/).

OAuth 2.1/PKCE and dynamic client registration are documented. Callback URLs have exact-match requirements; localhost has an HTTP exception. Phone/OTP entry belongs in Swiggy's authorization UI, not in our agent transcript. Scopes do not separate read from write, so EasyDial must enforce its own tool permissions. [Authentication](https://mcp.swiggy.com/builders/docs/start/authenticate/).

For an app serving customers, use a separate delegated session per user. A founder's token is not a substitute for customer authorization. [Delegated authentication](https://mcp.swiggy.com/builders/docs/start/enterprise/delegated-auth/).

**Comparison permission remains unresolved.** Swiggy's ground rules restrict competitive intelligence/benchmarking and brand-obscuring aggregation. User-directed shopping comparisons are not explicitly resolved by that wording. Obtain clarification for this exact use case before relying on cross-provider comparison. Keep provider attribution visible. [Published ground rules](https://mcp.swiggy.com/builders/access/).

No documented atomic cross-provider checkout, provider-neutral basket, reservation of stock, or post-order basket editing was established. Coupon optimization is also not established by the listed tools. These are app-side or unsupported capabilities, not things the master agent can assume.

## Blinkit: implementation inspection

Repository: [hereisSwapnil/blinkit-mcp](https://github.com/hereisSwapnil/blinkit-mcp). Inspected commit: `a39547118f4decd04a77ac16316fa09efdb6237a`. Downloaded public source into a temporary research directory; no dependencies installed and no source executed.

### What exists

Python 3.12+, FastMCP, Playwright Firefox, and dotenv. Default transport is stdio; `SERVE_HTTPS=true` actually selects SSE, not TLS configuration. This is browser automation wrapped in MCP, not a sanctioned Blinkit API. [Project configuration](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/pyproject.toml), [entry point](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/main.py).

The server registers 15 tools: `check_login`, `login`, `enter_otp`, `set_location`, `search`, `add_to_cart`, `remove_from_cart`, `check_cart`, `checkout`, `get_addresses`, `select_address`, `proceed_to_pay`, `select_payment_method`, `pay_now`, `get_order_history`. Most return captured print output. One global context uses `~/.blinkit_mcp/cookies/auth.json`; no per-user isolation or request serialization is evident. QR output is saved/opened on the server machine, unsuitable unchanged for remote mobile clients. [Server code](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/server.py).

### Findings that matter to EasyDial

1. **Search is incomplete for comparison.** It selects cards containing ADD and ₹, extracts a DOM ID/name/first rupee-bearing line, and can return an unknown ID. No dedicated pack-size, variant, explicit stock, or ETA fields are emitted. Products without ADD may disappear; an empty result is not proof of no stock. [Search code, lines 96–148](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/order/services/search.py#L96).
2. **Results are bounded.** The ordering facade defaults to ten results, despite the service default being twenty; no exposed search pagination. Remembered product IDs/query associations only live in memory. [Facade](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/order/blinkit_order.py).
3. **Location can be wrong.** Browser startup obtains host-IP location with a Noida fallback. A hosted server's location is not the customer's address. Explicitly select and verify the user's delivery address before quoting. [Authentication/browser code](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/auth/service.py#L19), [IP helper](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/utils/geo.py).
4. **Cart edits need verification.** Increment/decrement uses UI selectors and delays. Removal logs decrement attempts without enforcing the click helper's success. Cart extraction is text, with a raw-text fallback truncated to 300 characters when item selectors fail. Quantity calls need positive-integer validation and readback. [Cart implementation](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/order/services/cart.py).
5. **Payment is not verified completion.** The code prefers COD automatically, otherwise generates UPI QR. `click_pay_now` clicks a button and returns; it does not poll for payment success or require an order ID. The existing flow needs explicit user payment selection and a verified completion step. [Checkout implementation](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/order/services/checkout.py).
6. **History is implemented, tracking is not established.** History scrapes order summaries/details; do not treat that as a reliable payment reconciliation or live delivery tracking contract. [History implementation](https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/order/services/history.py).
7. **Tests do not establish readiness.** The inspected test directory contains an interactive CLI and tool-listing helper, not an automated regression suite. Open contributions are not capabilities on main; cross-location availability PR #2 was still open when checked. [Tests](https://github.com/hereisSwapnil/blinkit-mcp/tree/a39547118f4decd04a77ac16316fa09efdb6237a/test), [PR #2](https://github.com/hereisSwapnil/blinkit-mcp/pull/2).

Selector changes, site blocking, timeouts, and session expiry remain runtime risks. An MIT code license does not establish Blinkit approval for automated access; official permission/access arrangements were not established in this research.

## Proposed validation gate before implementation commitments

| Check | Evidence needed | Current status |
| --- | --- | --- |
| Swiggy connection | OAuth callback succeeds and actual tool schemas saved with secrets removed | Not run |
| Swiggy discovery | Correct address, pack sizes, prices, stock and pagination in a live response | Not run |
| Cart revisions | Add, remove, replace, quantity change, pre-existing cart preserved as intended | Not run |
| Payment | Available methods, pending/success/failure, reliable order ID, no duplicate placement | Not run |
| Blinkit discovery | Source outputs match actual product/variant/location | Static limitations found; live test not run |
| Blinkit confirmation | Definitive order identifier and status, rather than click logs | Missing in inspected final-payment method |
| Comparison permission | Explicit answer on attributed consumer comparisons | Unresolved |
| Public demo access | Production/callback approval if required for chosen hosting | Unresolved |

An authenticated smoke test is the next research step when account access is available. Any purchase test requires a separately approved concrete basket. Neither provider can yet be described as “tested end to end.”
