import asyncio
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..billing import breakdown, fingerprint, table
from ..controller import DomainError, Session, exact_comparison
from ..master import INSTRUCTIONS
from ..models import EditRequest, IntentEdit, Offer, RequestedProduct, SessionView, now


class ChatIntent(BaseModel):
    action: Literal["search", "edit", "select", "review", "checkout", "basket", "clarify", "stop"]
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
        return "Your draft basket is empty."
    return (
        "Your basket:\n"
        + "\n".join(
            f"• {line.quantity} × {line.offer.name} ({line.offer.pack_size or 'pack unspecified'}) — "
            f"{money(line.subtotal)}"
            for line in session.view.lines
        )
        + "\nYou can ask to change any item. Changes reach the store when you review."
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
Stop pauses this chat; do not interpret 'don't place an order yet, compare milk' as opt-out.
Return empty queries/edits unless needed, null offer_id/quantity unless selecting.
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

    async def handle(self, text, state, action_id=None):
        session = Session(
            view=SessionView.model_validate(state["view"]),
            offers={id: Offer.model_validate(value) for id, value in state["offers"].items()},
        )
        normalized = text.strip().casefold()
        if state["paused"]:
            if normalized not in {"start", "resume", "hi", "hello"}:
                return state, []
            state["paused"] = False
            return state, [
                text_message("Welcome back. Send a voice note or tell me what groceries you need.")
            ]
        kwargs = {"queries": [], "edits": [], "offer_id": None, "quantity": None, "clarification": ""}
        if action_id:
            parts = action_id.split(":", 2)
            if len(parts) != 3 or parts[1] != str(session.view.version):
                raise DomainError("That choice is old. Ask to see your basket or search again.")
            if parts[0] == "pick":
                intent = ChatIntent(action="select", **{**kwargs, "offer_id": parts[2]})
            elif parts[0] in {"review", "basket", "checkout"}:
                if parts[0] == "checkout" and (not session.view.review or parts[2] != session.view.review.id):
                    raise DomainError("Please review the current bill first.")
                intent = ChatIntent(action=parts[0], **kwargs)
            else:
                raise DomainError("Please use the latest choices.")
        elif normalized in {"hi", "hello", "start", "help"}:
            return state, [
                text_message(
                    "Hi, I'm EasyDial's automated grocery assistant. Send a voice note in your language, "
                    "or type what you need. I'll search Blinkit and Instamart, and help you change your basket. "
                    "This pilot stops before payment; no order will be placed. "
                    f"For help: {self.settings.whatsapp_support_contact}. Reply STOP to pause."
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
            for query, batch in zip(intent.queries, batches):
                for result in batch:
                    for offer in result.offers:
                        offer.suggested_quantity = query.quantity
                        offer.replaces_line_id = query.replaces_line_id
                        session.offers[offer.id] = offer
                    results.append(result)
                    # Give each provider/query space instead of allowing the first store to fill the list.
                    choices.extend(
                        o.id for o in result.offers[:2] if o.available and o.price_paise is not None
                    )
            state["choices"] = list(dict.fromkeys(choices))[:10]
            session.view.results = results
            session.view.comparison = exact_comparison([o for r in results for o in r.offers])
            session.changed()
            if state["choices"]:
                replies.extend(self.choice_messages(session, state["choices"]))
                if session.view.comparison:
                    replies.append(text_message(session.view.comparison))
            else:
                replies.append(
                    text_message("No selectable products were returned. Try a brand or pack size.")
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
            session.view.phase = "shopping"
            if not session.view.lines and session.view.synced_provider:
                await self.workers.clear(session.view)
                session.view.synced_provider = None
                state["cart_checkpoint"] = None
                session.changed()
                replies.append(text_message("Your store basket is now empty. No order was placed."))
            else:
                if session.view.lines:
                    session.view.synced_provider = session.view.lines[0].offer.provider
                # Persist the intended provider even when a partial browser write fails.
                state["view"] = session.view.model_dump(mode="json")
                review = await self.workers.review(session.view)
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
            from datetime import timedelta

            if now() - review.created_at > timedelta(minutes=5):
                raise DomainError("This bill expired. Review your basket again before approving.")
            fresh = await self.workers.revalidate(session.view)
            session.view.payment_preparation = None
            if fingerprint(session.view, fresh) != fingerprint(session.view, review):
                session.accept_review(session.view.version, fresh)
                replies.append(
                    text_message("The live bill changed. Please check this new table and approve again.")
                )
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
                replies.append(
                    text_message(
                        "Amount approved for payment preparation. Pending — no order was placed; no payment request was submitted. "
                        "The payment handoff is not enabled yet."
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
    def review_messages(session):
        review = session.view.review
        items = "\n".join(
            f"• {line.quantity} × {line.offer.name} ({line.offer.pack_size})" for line in session.view.lines
        )
        replies = [
            text_message(
                f"{review.provider.value.title()} — live cart review\n{items}\n\n"
                f"{table(review)}\n\nDelivery: {review.address_label}\n"
                "Payment preparation only: no payment or order will be submitted."
            )
        ]
        if breakdown(review)["reconciled"]:
            replies.append(
                {
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "body": {
                            "text": "Check every charge above, including any small-cart fee. "
                            "Approve this amount for preparation, or ask to change your basket."
                        },
                        "action": {
                            "buttons": [
                                {
                                    "type": "reply",
                                    "reply": {
                                        "id": f"checkout:{session.view.version}:{review.id}",
                                        "title": "Approve amount",
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
        rows, descriptions = [], []
        for i, id in enumerate(choices, 1):
            offer = session.offers[id]
            title = f"{i}. {offer.provider.value.title()}"
            detail = f"{offer.name[:100]} · {offer.pack_size or 'unknown pack'} · {money(offer.price_paise)}"
            descriptions.append(f"{title}: {detail}; {offer.suggested_quantity} pack(s)")
            rows.append(
                {"id": f"pick:{session.view.version}:{id}", "title": title[:24], "description": detail[:72]}
            )
        return [
            text_message(
                "\n".join(descriptions) + "\nItem prices only; fees are checked on review. "
                "Choose a product by number, voice or the menu."
            ),
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
                    "body": {"text": "Ready to check the live store bill?"},
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
