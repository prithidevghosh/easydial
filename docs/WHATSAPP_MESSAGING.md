# EasyDial WhatsApp messaging system

## Design direction

Calm, clear and recognisable as a helpful grocery business. The customer should immediately see what happened, what it costs, and what to do next. Short English replies remain the default even when requests arrive in another language.

WhatsApp controls the font family, font size, bubble colour and button appearance. EasyDial uses native single-asterisk bold, ordinary sentence case, whitespace, image captions and interactive headers/footers. It does not imitate custom fonts with mathematical Unicode, put normal messages in code blocks, or send screenshots of text. Monospace is reserved for the bill table.

Purposeful icons: 👋 welcome, 🎙️ listening, 🛒 basket, 🧾 bill, 📍 delivery address, 🔐 consent, ✅ verified connection/order, 📦 delivered, ⚠️ an actual charge or verification concern. No emoji on every line; icons never replace the explanatory words. A check mark must not appear before an action has actually succeeded.

## Layout rules

1. **Normal conversation:** one or two short sentences, without a repeated greeting, slogan or signature. Ask one clear question. Preserve explicit clarification for unclear speech.
2. **Progress:** keep the existing delayed single update per turn and quiet typing indicators. No extra messages to decorate the flow; no invented percentages or promises of success.
3. **Products:** product image where a verified image URL is available; bold numbered product name; pack and store on their own line; bold unit price; availability; requested quantity and its item total when greater than one. No per-card call to choose before the other options arrive.
4. **Choices:** exactly one final list prompt. Product names are the list row titles; pack/store/price are the description. Mixed image/text cards retain the original numbered order. Keep the existing handset-delivery dependencies.
5. **Basket:** one item per block, quantity × name, pack and line total, then clearly labelled *Items subtotal*. Delivery and fees are explicitly deferred to review. The final action is Review basket, with a short invitation to make changes by voice/text.
6. **Review:** full item list, narrow 30-column monospace cost table, charge warnings, and delivery address. The payable amount comes from the existing verified bill; formatting never reconciles or invents a charge. The next-action card offers payment only when the existing bill guard allows it.
7. **Payment:** clear store and amount, native method list, and an explicit final Place order action for cash. The exact merchant QR is sent unchanged as an image with its amount, expiry reminder and UPI approval instruction. Never fall back to text claiming a missing payment QR exists.
8. **Order status:** one outcome heading, order ID, total, payment state and current ETA where valid. Delivered orders do not show a stale arrival ETA or ask for cash again. Unconfirmed/stale states do not claim payment or order success and retain their recovery actions.
9. **Onboarding:** a warm welcome; readable consent paragraphs; distinct phone and verification prompts; separated full saved addresses; a connected confirmation with delivery address and a simple shopping invitation. Consent wording/version, opaque button IDs and login state transitions remain unchanged.
10. **Recovery:** clear native action headings where an existing button is available. Do not invent buttons for an unsupported action or change retry/approval semantics through copy.

## Representative messages

These are copy previews, not screenshots from a live WhatsApp handset. Single asterisks below are WhatsApp's bold syntax.

### Welcome · native button card

Header: Welcome to EasyDial 👋

Send a voice note. We’ll help with the groceries.

First, choose a store to connect.

Buttons: Blinkit · Instamart

### Product image caption

```text
*1. Coca-Cola Diet Coke*
330 ml · Blinkit

*₹50.00 per pack* · In stock
4 packs · *₹200.00* for your quantity
```

After all product cards:

Header: Your product options

Choose a product below, or reply with its number.

Footer: Delivery and fees are checked at review.

List button: Choose a product

### Basket

```text
🛒 *Your Blinkit basket*

4 × Coca-Cola Diet Coke
330 ml · *₹200.00*

*Items subtotal · ₹200.00*
Delivery and fees are checked at review.
```

Next message: “Review prices and delivery fees, or tell me what you’d like to change.”

Button: Review basket

### Bill layout

The charge section is sent inside WhatsApp triple backticks:

```text
Charge               Amount
──────────────────────────────
Items total             ₹50.00
Delivery charge         ₹35.00
Handling charge         ₹13.00
Small cart charge       ₹25.00
Total payable          ₹123.00
```

Followed by the real small-cart warning and full delivery address. If verified, the final card says: “Your total is *₹123.00*. Check the bill above, then choose how to pay.”

### QR caption

```text
*UPI payment · Blinkit*
*Amount due · ₹123.00*

Please complete your payment using the QR above.
It expires in a few minutes. Approve only in your UPI app.
```

### Confirmed order

```text
✅ *Order confirmed*

Order ID: example-order-id
*Total · ₹123.00*
Payment verified.
Arriving in 8 minutes

• 1 × Example product · 188 g
```

## Implementation and boundaries

- `backend/src/easydial/whatsapp/presentation.py`: shared card, caption, basket, receipt and progress presentation. Pure formatting; no commerce or consent decisions.
- `conversation.py`: delegates shopping views to the presentation module; retains existing business rules and action IDs.
- `onboarding.py`: applies layout to existing onboarding messages; consent words are unchanged.
- `order_tracking.py`: formats verified result states without changing polling or matching.
- `transport.py`: adds missing action-card headings on a copied payload, disables automatic text link previews, and formats payment QR captions. The durable payload is never mutated.
- Long baskets/receipts split without losing items, with balanced monospace fences and the action prompt last. Product names/pack labels are bounded and stripped of injected formatting; prices and quantities are never truncated away.
- Existing image rejection fallback, QR no-fallback, uncertain-send behavior and delivery ordering remain intact.

## Verification

Regression coverage checks mixed card ordering, one final selection prompt, unit price vs requested quantity total, caption/list/button limits, merchant formatting injection, long receipts, complete charge disclosure, blocked-bill actions, unchanged consent words/tokens, payload immutability, image fallback and QR bytes. The full backend suite is run before committing.

No live shopping, login, payment or outbound customer messages are required to validate this presentation change. Live appearance on iOS/Android WhatsApp should be checked after the new release is deployed. Pushing code is not evidence that the AWS worker has been updated.

Reference: [Meta interactive message structure and limits](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/types/InteractiveObject/).
