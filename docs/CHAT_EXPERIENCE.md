# Short replies, progress and product photos

2026-11-21. WhatsApp replies use short everyday wording, retaining detailed charge tables and material payment restrictions. Intent instructions request brief clarifications in the user's language. Fixed messages are still English; full multilingual message localization is not yet implemented.

## Progress

Display a read/typing indicator while actively handling a message. If work lasts more than five seconds (plus a bounded indicator request), send at most one useful progress message using the current stage: listening, finding products, checking cart/fees, or preparing payment. Refresh quiet typing indicators while processing continues. Fast requests produce no extra text; completed/failed turns cancel the progress task.

Progress reservation persists before sending, so duplicate delivery/retries do not spam progress messages. An uncertain send is recorded and not repeated. A progress failure never changes basket behavior. Meta documents the indicator disappearing on response or after 25 seconds: [official typing-indicator API](https://developers.facebook.com/documentation/business-messaging/whatsapp/typing-indicators).

## Product images

Read each visible product card's image alongside its name, variant, availability and price. Retain only HTTPS URLs on allowlisted store image CDNs; omit missing/unrecognized images. Instamart's actual Diet Coke card exposed a public image on `instamart-media-assets.swiggy.com`; Blinkit's card reader captures the same visible `img` fields.

Send at most two photos per result batch with numbered captions including product, pack, price and “In stock”. Keep the full numbered text/menu for other matches. Photos are selected only from selectable verified in-stock results. No generated or substitute images. If Meta definitively rejects an image with HTTP 400, fall back to its caption; timeouts/uncertain delivery are never blindly retried.

Meta supports image messages with hosted image links or uploaded media IDs: [official image-message API](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/image-messages). Real phone rendering is still to be checked by the tester; transport payloads, image host validation and bounded progress behavior are tested locally.

## Delivery order (2026-11-21 fix)

Product messages precede the choice menu. Final replies in a turn form a durable outbox dependency chain: Meta delivered/read status releases the next message; HTTP acceptance and sent status do not. Early receipts are stored to close the webhook-versus-response race. Definite delivery failures release the chain without resending; uncertain results remain held. New valid customer turns cancel held older messages. No fixed-delay workaround is used. Missing receipts can hold the prompt, so individual numbered product captions remain actionable. No paid order or cart operation is triggered by a delivery receipt.
