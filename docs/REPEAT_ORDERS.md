# Repeat a past Blinkit purchase

Added 2026-11-22. Intent interpretation accepts Indian languages and mixed-language requests. The feature is Blinkit-only; it does not change Instamart ordering.

The browser opens the authenticated order list, observes its normal history response, and reads only its first page, capped at 10 unique records. It never scrolls or follows pagination, even when no match is found. Cancelled and failed orders count toward that limit but are not repeatable purchases. A requested date range filters these first-page records; it never extends the search to older orders. Return-completed cards use a different date field, covered by a regression.

The semantic selector receives only historical product names, packs, quantities and dates. It selects validated item keys; ambiguous variants require clarification. Explicit new quantities override historical counts. Relative dates use the current Indian calendar date.

There is no native repeat button on the inspected Blinkit detail page. The feature searches the current catalogue and requires the same product name and pack, verified availability, current price and sufficient quantity. It stages all additions before changing the local basket, preserves existing items, and continues through the existing bill review and payment controls. It never places an order automatically.

A complete history read with no match explicitly explains the first-page / up-to-10 limit. An incomplete read reports that history could not be checked, rather than asserting that a product was never purchased. A historical match that cannot be verified in today's catalogue is reported separately.

## Earlier verification (before the first-page restriction)

- Local headed Chrome used a direct connection, with `browser.proxy is None` asserted. No local application server was started.
- Live history: 20 records, including three cancelled records and 17 completed/returned purchases; no older order detail opened.
- Live catalogue: historical Diet Coke 330 ml resolved at the current price and quantity two was added to an isolated local draft. No merchant cart mutation or purchase.
- Live model checks: Hindi, Hinglish, Bengali, Tamil, Telugu, Kannada, Marathi, Gujarati and English preserved repeat intent, quantity two and the previous calendar month.
- Regressions cover the first-page 10-record cap, date filtering, returned/cancelled cards, unknown selection IDs, ambiguity, absence, changed packs, current prices, additive quantities and rejection of Instamart history requests.

Production continues to use its configured sticky proxy. The automatic confirmation lifecycle is documented in POST_PAYMENT.md.
- AWS candidate validation used the existing pinned proxy: 20 history records read, current catalogue search passed, proxy IP unchanged. A copied production database removed the two confirmed lines, preserved the receipt and queued one notification; the diagnostic sent no WhatsApp messages and did not write the production database.
- Added a Chrome regression with delayed history/detail rendering after the AWS test exposed premature scrolling. Existing search (two products, quantity two each), logout and status intents also passed live model checks.

## First-page restriction — 2026-11-22

A fresh local Chrome check without a proxy observed 10 unique orders in the first history response and an available next page. Per user instruction, repeat lookup and background history warm-up now read only that first page, with a hard cap of 10. Matching still stops at the newest qualifying purchase. A missing product is explicitly limited to this scope; neither date filters nor a no-match result authorize older-page reads. Regression coverage asserts no scroll, no second history request, the cap, and unchanged cancellation/cache behavior. No shopping, cart or payment code changed for this restriction.
