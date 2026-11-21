# Blinkit MCP payment reference review

Reviewed 2026-11-21 at upstream commit `a39547118f4decd04a77ac16316fa09efdb6237a`. Read source only; did not install or execute upstream code.

## Finding

Useful reference for the payment UI, but not a complete fix for EasyDial's current blockers. Its checkout service enters `#payment_widget`, opens UPI, clicks Generate QR and extracts a base64 image from `div[class*='QrImageWrapper'] img`. This corroborates the route observed in our live headed inspection. It does not provide an independent Blinkit payment-link API.

Source: https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/order/services/checkout.py

## Comparison and decisions

| Concern | Upstream implementation | EasyDial decision |
| --- | --- | --- |
| Payment choice | Automatically selects cash when enabled, otherwise UPI QR | Keep explicit user choice from actual visible store categories. Never silently select COD. |
| QR readiness | Sleeps two seconds, waits for visible image, reads its source | Keep our image-complete/data-PNG readiness check and delayed-placeholder regression test. Visibility alone caused the observed extraction failure. |
| QR validity | Returns image bytes without decoding amount/currency or checking expiry | Decode and verify exact approved total, INR, transaction reference and observed expiry before publishing. |
| Payment handoff | Saves QR locally and opens it on the desktop | Present the store-generated artifact through an expiring private HTTPS page for WhatsApp. Full revised live flow remains unverified. |
| Final action | Exposes `pay_now`, with a fallback that can click Place Order | Do not adopt this action under the current no-order/no-payment-approval boundary. |
| Headless | Launches ordinary Playwright Firefox with configurable headless and saved storage state | No evidence this resolves our Chrome headless access-denied challenge. Changing browser would require separate login and verification; it is not a proven fix. |
| Payment/order result | Clicking a button reports the click; no settlement reconciliation in checkout service | Never treat a click or QR generation as a confirmed paid order. |

Browser source: https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/auth/service.py

MCP tools: https://github.com/hereisSwapnil/blinkit-mcp/blob/a39547118f4decd04a77ac16316fa09efdb6237a/src/server.py

The README advertises saved/new UPI-ID selection and lists `get_upi_ids` / `select_upi_id`; these tools are absent from the inspected server implementation. Base decisions on source and observed UI, not feature copy.

## Remaining verification

Our live headed inspection generated and decoded a real QR without approving payment. The subsequent automated run encountered an image placeholder; the readiness fix passes a browser regression test, but the complete revised live request-to-WhatsApp-link path has not passed yet. Fresh headless access was still denied. Preserve unknown attempts rather than regenerating them automatically, and do not claim production-ready payment or paid-order confirmation. See `PAYMENT_HANDOFF.md` for handoff state and limitations.
