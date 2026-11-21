import asyncio
import json
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..billing import breakdown, fingerprint, table
from ..controller import DomainError, Session, exact_comparison
from ..master import INSTRUCTIONS
from ..models import EditRequest, IntentEdit, Offer, RequestedProduct, SessionView, now
from ..providers.cart_guard import CartConflict
from ..providers.images import product_image_url
from . import reconciliation


class ChatIntent(BaseModel):
    action: Literal["search", "edit", "select", "review", "checkout", "basket", "clarify", "stop", "payment"]
    queries: list[RequestedProduct]
    edits: list[IntentEdit]
    offer_id: str | None
    quantity: int | None = Field(ge=1, le=100)
    clarification: str


def text_message(text):
    return {"type": "text", "text": {"body": text}}


def money(paise):
    return "not verified" if paise is None else f"₹{paise / 100:.2f}"


def basket_text(session):
    if not session.view.lines:
        return "Your basket is empty."
    return (
        "Your basket:\n"
        + "\n".join(
            f"• {line.quantity} × {line.offer.name} ({line.offer.pack_size or 'pack unspecified'}) — "
            f"{money(line.subtotal)}"
            for line in session.view.lines
        )
        + "\nWant to change anything?"
    )


class ShoppingChat:
    def __init__(self, settings, workers, master):
        self.settings, self.workers, self.master = settings, workers, master

    def initial(self):
        return {
            "view": SessionView(
                id=uuid4().hex, mode="live", address_label=self.settings.live_address_label
            ).model_dump(mode="json"),
            "offers": {},
            "choices": [],
            "paused": False,
            "last_timestamp": 0,
        }

    async def interpret(self, text, session, state):
        client = self.master.require_client()
        result = await client.responses.parse(
            model=self.settings.openai_text_model,
            instructions=INSTRUCTIONS
            + """
This is the WhatsApp channel. Additional actions: select, basket, checkout.
Select only a specific offer from DISPLAYED_CHOICES when the user unambiguously chooses it;
use offer_id exactly, and quantity null to preserve the suggested pack count. Understand spoken
option numbers in Indian languages. Never select an arbitrary cheapest/first match when ambiguous.
Basket means show the draft. Review means sync the store cart and read its bill (no purchase).
Checkout ONLY means an explicit request to continue AFTER a reviewed bill; it is local Pending,
never a purchase. A bare 'yes' is ambiguous: clarify, never infer a checkout or product selection.
Search/comparison does not select or mutate products. Preserve brand, formulation and pack size.
Adding a different product keeps every existing basket line. Do not infer replacement or store
switching from a request for another product. Only explicit replacement sets replaces_line_id.
The selectable options for an existing basket belong to its store; other-store prices are comparison only.
Stop pauses this chat; do not interpret 'don't place an order yet, compare milk' as opt-out.
Return empty queries/edits unless needed, null offer_id/quantity unless selecting.
Use short, everyday language: one short sentence where possible. Avoid technical words and repeated disclaimers.
Clarifications must be brief and in the user's language. Never claim any completed action or price
in clarification. Shopping only: redirect unrelated questions to groceries.
""",
            input=json.dumps(
                {
                    "request": text,
                    "basket": session.view.model_dump(mode="json"),
                    "DISPLAYED_CHOICES": [
                        {"number": i + 1, **session.offers[id].model_dump(mode="json")}
                        for i, id in enumerate(state["choices"])
                        if id in session.offers
                    ],
                },
                ensure_ascii=False,
            ),
            text_format=ChatIntent,
            store=False,
        )
        if result.output_parsed is None:
            raise DomainError("I couldn't understand that. Please say it another way.", 422)
        return result.output_parsed

    async def handle(self, text, state, action_id=None, progress=None, customer=None):
        session = Session(
            view=SessionView.model_validate(state["view"]),
            offers={id: Offer.model_validate(value) for id, value in state["offers"].items()},
        )
        selected_method = None
        normalized = text.strip().casefold()
        if state["paused"]:
            if normalized not in {"start", "resume", "hi", "hello"}:
                return state, []
            state["paused"] = False
            return state, [
                text_message("Welcome back. Send a voice note or tell me what groceries you need.")
            ]
        kwargs = {"queries": [], "edits": [], "offer_id": None, "quantity": None, "clarification": ""}
        if not action_id and state.get("payment_menu"):
            menu = state["payment_menu"]
            chosen = next(
                (item for item in menu["methods"] if normalized in {item["id"], item["title"].casefold()}),
                None,
            )
            if chosen:
                action_id = f"payment:{session.view.version}:{menu['id']}_{chosen['id']}"
        if action_id:
            parts = action_id.split(":", 2)
            if len(parts) != 3 or parts[1] != str(session.view.version):
                raise DomainError("That choice is old. Ask to see your basket or search again.")
            if parts[0] == "pick":
                intent = ChatIntent(action="select", **{**kwargs, "offer_id": parts[2]})
            elif parts[0] == "payment":
                menu = state.get("payment_menu")
                if (
                    not menu
                    or not session.view.review
                    or menu["review_id"] != session.view.review.id
                    or not parts[2].startswith(menu["id"] + "_")
                    or now() - datetime.fromisoformat(menu["created_at"]) > timedelta(minutes=5)
                ):
                    raise DomainError("These payment options expired. Review your basket again.")
                method_id = parts[2][len(menu["id"]) + 1 :]
                selected_method = next((x for x in menu["methods"] if x["id"] == method_id), None)
                if selected_method is None:
                    raise DomainError("Choose one of the payment options shown.")
                intent = ChatIntent(action="payment", **kwargs)
            elif parts[0] == "reconcile":
                pending = state.get("cart_reconciliation")
                if (
                    not pending
                    or not pending.get("checkpoint")
                    or pending["id"] != parts[2]
                    or pending["version"] != session.view.version
                    or now() - datetime.fromisoformat(pending["created_at"]) > timedelta(minutes=5)
                ):
                    raise DomainError("This cart comparison expired. Send review for a fresh comparison.")
                # Only this explicit versioned button authorizes adopting the observed cart.
                state["cart_reconciliation"] = None
                await self.workers.adopt_checkpoint(pending["checkpoint"], pending["address"])
                state["cart_checkpoint"] = pending["checkpoint"]
                intent = ChatIntent(action="review", **kwargs)
            elif parts[0] in {"review", "basket", "checkout"}:
                if parts[0] == "checkout" and (not session.view.review or parts[2] != session.view.review.id):
                    raise DomainError("Please review the current bill first.")
                intent = ChatIntent(action=parts[0], **kwargs)
            else:
                raise DomainError("Please use the latest choices.")
        elif normalized in {"hi", "hello", "start", "help"}:
            return state, [
                text_message(
                    "Hi, I’m EasyDial, your AI grocery helper. What do you need? Type or send a voice note. "
                    f"Help: {self.settings.whatsapp_support_contact}. STOP to pause."
                )
            ]
        elif normalized in {"stop", "unsubscribe"}:
            intent = ChatIntent(action="stop", **kwargs)
        elif normalized in {"basket", "review", "checkout"}:
            intent = ChatIntent(action=normalized, **kwargs)
        elif normalized.isascii() and normalized.isdigit() and 1 <= int(normalized) <= len(state["choices"]):
            intent = ChatIntent(
                action="select", **{**kwargs, "offer_id": state["choices"][int(normalized) - 1]}
            )
        else:
            intent = await self.interpret(text, session, state)
        if (
            customer
            and getattr(self, "payments", None)
            and intent.action in {"edit", "select", "review", "checkout", "payment"}
            and self.payments.existing(customer, session.view.id)
        ):
            raise DomainError(
                "A payment request is already open. Check it in the store before changing your basket or starting another."
            )
        if progress:
            progress.update(
                {
                    "search": "Finding matching products…",
                    "review": "Checking your cart and fees…",
                    "checkout": "Finding payment options…",
                    "payment": "Preparing your payment option…",
                }.get(intent.action, "Updating your basket…")
            )
        session.say(text, "user")
        replies = []
        if intent.action == "search":
            if not 1 <= len(intent.queries) <= 3 or not session.view.address_label:
                raise DomainError("Please connect your store address and request up to three products.")
            if any(
                q.replaces_line_id and q.replaces_line_id not in {x.id for x in session.view.lines}
                for q in intent.queries
            ):
                raise DomainError("Which basket item should I replace?")
            batches = await asyncio.gather(
                *(self.workers.search(q.query, session.view.address_label) for q in intent.queries)
            )
            results = []
            choices = []
            basket_store = session.view.synced_provider or (
                session.view.lines[0].offer.provider if session.view.lines else None
            )
            for query, batch in zip(intent.queries, batches):
                for result in batch:
                    for offer in result.offers:
                        offer.suggested_quantity = query.quantity
                        offer.replaces_line_id = query.replaces_line_id
                        session.offers[offer.id] = offer
                    results.append(result)
                    # Give each provider/query space instead of allowing the first store to fill the list.
                    choices.extend(
                        o.id
                        for o in result.offers[:2]
                        if o.available
                        and o.price_paise is not None
                        and (basket_store is None or o.provider == basket_store)
                    )
            state["choices"] = list(dict.fromkeys(choices))[:10]
            session.view.results = results
            session.view.comparison = exact_comparison([o for r in results for o in r.offers])
            session.changed()
            if basket_store:
                replies.append(text_message(f"Adding to your {basket_store.value.title()} basket."))
            if state["choices"]:
                replies.extend(self.choice_messages(session, state["choices"]))
                if session.view.comparison:
                    replies.append(text_message(session.view.comparison))
            else:
                replies.append(
                    text_message(
                        f"No selectable products were returned{(' from ' + basket_store.value.title()) if basket_store else ''}. "
                        "Your basket is unchanged. Try a brand or pack size."
                    )
                )
            failures = [
                f"{r.provider.value.title()}: {r.message or 'could not check; stock unknown'}"
                for r in results
                if r.status != "ok"
            ]
            if failures:
                replies.append(text_message("\n".join(failures)))
            if intent.clarification:
                replies.append(text_message(intent.clarification))
        elif intent.action == "select":
            if intent.offer_id not in state["choices"]:
                raise DomainError("Please select one of the current products, or search again.")
            offer = session.offers[intent.offer_id]
            basket_store = session.view.synced_provider or (
                session.view.lines[0].offer.provider if session.view.lines else None
            )
            # Recover old mixed-store menus without deleting items or silently substituting a product.
            if basket_store and offer.provider != basket_store:
                state["choices"] = [
                    id for id in state["choices"] if session.offers[id].provider == basket_store
                ]
                session.changed()
                replies = [
                    text_message(
                        f"Your existing basket is kept in {basket_store.value.title()}. "
                        "That option was from another store. Choose a matching product below to add to your basket."
                        if state["choices"]
                        else f"Your existing basket is kept in {basket_store.value.title()}. "
                        "Please search again for an option from that store."
                    )
                ]
                if state["choices"]:
                    replies.extend(self.choice_messages(session, state["choices"]))
                state["view"] = session.view.model_dump(mode="json")
                return state, replies
            session.edit(
                EditRequest(
                    kind="replace" if offer.replaces_line_id else "add",
                    offer_id=offer.id,
                    line_id=offer.replaces_line_id,
                    quantity=intent.quantity or offer.suggested_quantity,
                    expected_version=session.view.version,
                )
            )
            selected_queries = {
                result.query
                for result in session.view.results
                if any(item.id == offer.id for item in result.offers)
            }
            remaining_ids = {
                item.id
                for result in session.view.results
                if result.query not in selected_queries
                for item in result.offers
                if item.provider == offer.provider
            }
            state["choices"] = [id for id in state["choices"] if id in remaining_ids]
            replies.extend(self.basket_messages(session))
            if state["choices"]:
                replies.extend(self.choice_messages(session, state["choices"]))
        elif intent.action == "edit":
            if not 1 <= len(intent.edits) <= 10:
                raise DomainError("Please describe the basket change again.")
            for edit in intent.edits:
                session.edit(EditRequest(**edit.model_dump(), expected_version=session.view.version))
            state["choices"] = []
            replies.extend(self.basket_messages(session))
        elif intent.action == "review":
            session.view.review = None
            session.view.payment_preparation = None
            session.view.phase = "shopping"
            state["cart_reconciliation"] = None
            if not session.view.lines and session.view.synced_provider:
                await self.workers.clear(session.view)
                session.view.synced_provider = None
                state["cart_checkpoint"] = None
                session.changed()
                replies.append(text_message("Your store cart is empty."))
            else:
                if session.view.lines:
                    session.view.synced_provider = session.view.lines[0].offer.provider
                # Persist the intended provider even when a partial browser write fails.
                state["view"] = session.view.model_dump(mode="json")
                try:
                    review = await self.workers.review(session.view)
                except CartConflict as conflict:
                    pending = reconciliation.proposal(session.view, conflict)
                    if not pending:
                        raise
                    state["cart_reconciliation"] = pending if pending.get("checkpoint") else None
                    replies.extend(reconciliation.messages(session.view, pending))
                else:
                    session.accept_review(session.view.version, review)
                    session.view.payment_preparation = None
                    state["cart_checkpoint"] = session.view.model_dump(mode="json")
                    replies.extend(self.review_messages(session))
        elif intent.action == "checkout":
            # Persist revocation even if the live read fails and this turn is rejected.
            state["view"]["payment_preparation"] = None
            session.view.payment_preparation = None
            review = session.view.review
            if not review:
                raise DomainError("Please review your basket first.")
            if not breakdown(review)["reconciled"]:
                raise DomainError("The charges do not reconcile. Review again before approving this bill.")
            # Validate review identity/expiry before touching even the read-only store path.
            if now() - review.created_at > timedelta(minutes=5):
                raise DomainError("This bill expired. Review your basket again before approving.")
            fresh = await self.workers.revalidate(session.view)
            session.view.payment_preparation = None
            if fingerprint(session.view, fresh) != fingerprint(session.view, review):
                session.accept_review(session.view.version, fresh)
                replies.append(text_message("The total or fees changed. Please check the updated bill."))
                replies.extend(self.review_messages(session))
            else:
                session.confirm(session.view.version, review.id, True)
                session.view.payment_preparation = {
                    "status": "approved_for_preparation_only",
                    "review_id": review.id,
                    "fingerprint": fingerprint(session.view, fresh),
                    "approved_at": now().isoformat(),
                    "provider": fresh.provider.value,
                    "total_paise": fresh.total_paise,
                    "breakdown": breakdown(fresh),
                }
                methods = await self.workers.payment_options(session.view, fresh)
                menu = {
                    "id": uuid4().hex,
                    "review_id": review.id,
                    "created_at": now().isoformat(),
                    "methods": methods,
                }
                state["payment_menu"] = menu
                replies.append(self.payment_choices(session, menu))
        elif intent.action == "payment":
            if not selected_method or not session.view.review:
                raise DomainError("Please choose a payment method from the menu.")
            review = session.view.review
            fresh = await self.workers.revalidate(session.view)
            if fingerprint(session.view, fresh) != fingerprint(session.view, review):
                session.accept_review(session.view.version, fresh)
                state["payment_menu"] = None
                replies = [
                    text_message("The bill changed. Please check it again."),
                    *self.review_messages(session),
                ]
            elif selected_method["id"] == "upi":
                if not customer or not getattr(self, "payments", None):
                    raise DomainError("Payment-link setup is incomplete.")
                state["payment_menu"] = None
                prepared = await self.payments.create(customer, session.view, fresh, self.workers)
                session.view.payment_preparation = {"status": "request_created", **prepared}
                replies.append(
                    text_message(
                        f"{money(fresh.total_paise)} · {fresh.provider.value.title()}\n"
                        f"Open your UPI link: {prepared['url']}\nIt expires in a few minutes. Approve only in your UPI app."
                    )
                )
            elif selected_method["id"] == "cash":
                replies.append(
                    text_message(
                        selected_method.get("note")
                        or "Cash on delivery requires placing the order. EasyDial won’t place it yet."
                    )
                )
            else:
                from ..providers.payments import STORE_CHECKOUT

                replies.append(
                    text_message(
                        f"Use {selected_method['title'].lower()} on {fresh.provider.value.title()}: "
                        f"{STORE_CHECKOUT[fresh.provider]}\nSign in with the same store account if asked. Check the bill before paying."
                    )
                )
        elif intent.action == "basket":
            replies.extend(self.basket_messages(session))
        elif intent.action == "stop":
            state["paused"] = True
            state["choices"] = []
            session.changed()
            replies.append(
                text_message(
                    "Paused. No order was placed. Your existing store cart may remain. "
                    "Send START when you want to continue."
                )
            )
        else:
            replies.append(
                text_message(intent.clarification or "Which groceries and pack sizes would you like?")
            )
        for reply in replies:
            if reply["type"] == "text":
                session.say(reply["text"]["body"])
        state["view"] = session.view.model_dump(mode="json")
        # Keep only offers relevant to the current choices or draft, bounding database growth.
        keep = set(state["choices"]) | {line.offer.id for line in session.view.lines}
        state["offers"] = {id: o.model_dump(mode="json") for id, o in session.offers.items() if id in keep}
        return state, replies

    @staticmethod
    def payment_choices(session, menu):
        return {
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": f"{money(session.view.review.total_paise)} · Choose a payment method."},
                "action": {
                    "button": "Payment options",
                    "sections": [
                        {
                            "title": "From your store",
                            "rows": [
                                {
                                    "id": f"payment:{session.view.version}:{menu['id']}_{item['id']}",
                                    "title": item["title"][:24],
                                    "description": (
                                        item.get("note")
                                        or (
                                            "Generate a UPI link / QR"
                                            if item["id"] == "upi"
                                            else "Continue securely on the store"
                                        )
                                    )[:72],
                                }
                                for item in menu["methods"]
                            ],
                        }
                    ],
                },
            },
        }

    @staticmethod
    def review_messages(session):
        review = session.view.review
        items = "\n".join(
            f"• {line.quantity} × {line.offer.name} ({line.offer.pack_size})" for line in session.view.lines
        )
        replies = [
            text_message(
                f"{review.provider.value.title()} — live cart review\n{items}\n\n"
                f"{table(review)}\n\nDelivery: {review.address_label}\n"
                "Check the total, then choose how to pay."
            )
        ]
        if breakdown(review)["reconciled"]:
            replies.append(
                {
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "body": {"text": "How would you like to pay?"},
                        "action": {
                            "buttons": [
                                {
                                    "type": "reply",
                                    "reply": {
                                        "id": f"checkout:{session.view.version}:{review.id}",
                                        "title": "Choose payment",
                                    },
                                }
                            ]
                        },
                    },
                }
            )
        return replies

    @staticmethod
    def choice_messages(session, choices):
        rows, descriptions, photos = [], [], []
        for i, id in enumerate(choices, 1):
            offer = session.offers[id]
            title = f"{i}. {offer.provider.value.title()}"
            detail = f"{offer.name[:100]} · {offer.pack_size or 'unknown pack'} · {money(offer.price_paise)}"
            descriptions.append(f"{title}: {detail} · In stock · {offer.suggested_quantity} pack(s)")
            image_url = product_image_url(offer.image_url)
            if image_url and len(photos) < 2:
                photos.append(
                    {
                        "type": "image",
                        "image": {
                            "link": image_url,
                            "caption": f"{title} · {offer.name} · {offer.pack_size} · {money(offer.price_paise)} · In stock"[
                                :1024
                            ],
                        },
                    }
                )
            rows.append(
                {"id": f"pick:{session.view.version}:{id}", "title": title[:24], "description": detail[:72]}
            )
        return photos + [
            text_message("\n".join(descriptions) + "\nChoose a number. Delivery and fees come at review."),
            {
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": "Which product would you like?"},
                    "action": {
                        "button": "Choose a product",
                        "sections": [{"title": "Live results", "rows": rows}],
                    },
                },
            },
        ]

    @staticmethod
    def basket_messages(session):
        return [
            text_message(basket_text(session)),
            {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": "Change anything, or review your basket?"},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": f"review:{session.view.version}:current",
                                    "title": "Review basket",
                                },
                            }
                        ]
                    },
                },
            },
        ]
