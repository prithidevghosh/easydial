# Semantic product discovery

## Goal and decision

Product relevance must reflect the customer's meaning rather than exact word containment. Both merchant browser adapters now return structurally valid, deduplicated catalogue candidates without a lexical filter. ProviderWorkers sends one bounded candidate set across connected stores to an OpenAI structured-output ranker using OPENAI_TEXT_MODEL. Exact SKU refresh for a selected item remains deterministic and does not need relevance ranking.

## Contract

- Model input: search request and catalogue ID, store, product name, brand, variant, pack and observed price. No customer phone, address, session cookies, OTP or conversation history.
- Model output: ordered existing offer IDs only (maximum 20). No generated product records, prices, images or availability.
- Code removes unavailable/unpriced candidates before ranking and rejects unknown or repeated IDs. Returned IDs resolve to the original Offer objects. Ordering is preserved within each provider's choices.
- Model understands Indian-language synonyms, spelling and plurals; it must preserve explicit brand, dietary, flavour and pack requirements. Category confusions such as milk biscuits versus milk and cookie ice cream versus cookies are excluded.
- Alternatives violating explicit constraints are omitted, never silently substituted. The customer must still select an exact returned product. Ranking cannot mutate carts or place orders.
- API errors, missing credentials, malformed/refused output or timeout yield a matching-unavailable result with the existing retry actions. There is no silent lexical fallback or unfiltered recommendation fallback.
- Maximum model wait: 25 seconds, client timeout 20 seconds, no client retries. Store browser reads retain their existing independent timeouts and candidate caps (20 Blinkit, 30 Instamart).

## Validation

Mocked boundary tests cover semantic queries reaching the model without a lexical prefilter, original-object/price preservation, no address leakage, unknown/duplicate IDs, unavailable candidates, missing credentials and whole-pipeline failure without unfiltered offers. Existing fixture journeys inject an explicit test ranker; they do not call the model.

Live model checks passed for cookie vs cookie ice cream/puffs, dahi vs milk/flavoured yogurt, Diet Coke vs regular Coke/Sprite Zero, aloo vs tomato/potato chips, and 1 litre vs 1000 ml/500 ml. These five cases are limited evidence, not a claim of universal accuracy.

Read-only live Blinkit search for cookie passed through extraction and model ranking, returning Hide & Seek cookie variants with real prices. No cart changes or purchases occurred. Semantic discovery still depends on the merchant returning suitable candidates within the page cap; query rewriting and searching beyond that cap are not part of this change.
