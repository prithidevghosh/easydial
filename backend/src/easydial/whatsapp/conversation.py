import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..billing import breakdown, can_choose_payment, fingerprint, table
from ..controller import DomainError, Session, exact_comparison
from ..master import INSTRUCTIONS
from ..models import BasketLine, EditRequest, IntentEdit, Offer, Provider, RequestedProduct, SessionView, now
from ..providers.cart_guard import CartConflict
from ..providers.images import product_image_url
from . import reconciliation


class ChatIntent(BaseModel):
    action: Literal[
        "search",
        "edit",
        "select",
        "review",
        "checkout",
        "basket",
        "clarify",
        "stop",
        "payment",
        "restore",
        "status",
        "logout",
        "account_status",
    ]
    queries: list[RequestedProduct]
    edits: list[IntentEdit]
    offer_id: str | None
    quantity: int | None = Field(ge=1, le=100)
    clarification: str
    replaces_line_id: str | None = None
    unclear_request: bool = False
    target_provider: Provider | None = None
    switch_store: bool = False


class SelectionDecision(BaseModel):
    operation: Literal["replace", "add", "clarify"]


class RemovalCheck(BaseModel):
    explicit_removal: bool


def text_message(text):
    return {"type": "text", "text": {"body": text}}


def money(paise):
    return "not verified" if paise is None else f"₹{paise / 100:.2f}"


def basket_text(session):
    if not session.view.lines:
        return "Your basket is empty."
    stores = ", ".join(sorted({line.offer.provider.value.title() for line in session.view.lines}))
    return f"Your {stores} basket:\n" + "\n".join(
        f"• {line.quantity} × {line.offer.name} ({line.offer.pack_size or 'pack unspecified'}) — "
        f"{money(line.subtotal)}"
        for line in session.view.lines
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

    async def interpret(self, text, session, state, *, repair=False):
        # A number after a completed selection does not distinguish add from replace.
        # Ask rather than let stochastic model output choose a destructive interpretation.
        if (
            text.strip().isascii()
            and text.strip().isdigit()
            and not state["choices"]
            and self.recent_choices(session, state)
        ):
            return ChatIntent(
                action="clarify",
                queries=[],
                edits=[],
                offer_id=None,
                quantity=None,
                clarification="Should that option replace your selected item, or be added as well?",
            )
        client = self.master.require_client()
        result = await client.responses.parse(
            model=self.settings.openai_text_model,
            instructions=INSTRUCTIONS.replace("in\nthe user's language", "in English").replace(
                "If the request needs both edits and discovery, clarify which to do first rather than silently dropping part.",
                "For explicit edits plus discovery, return action search with both edits and queries. Never drop either part.",
            )
            + """
This is the WhatsApp channel. Additional actions: select, basket, checkout, restore, logout, account_status.
Account management IS supported here. Use logout for a clear current request to sign out,
disconnect all saved store accounts or remove saved logins, including polite/informal multilingual
phrasing. Never use logout for negation, hypothetical questions or unclear speech; clarify instead.
Use account_status for questions about saved/connected logins. These actions have no queries,
edits or selection. Never claim logout is unavailable. They are handled by account management,
not by shopping tools, even when an order/payment attempt exists.
Use restore for "put the same order in the cart again", "undo removal", or "bring back my basket"
when CURRENT_BASKET is empty and PREVIOUS_BASKET is provided. Do not ask the user to list those items again.
PREVIOUS_BASKET is historical context only: never restore without the current user's explicit request.
NEXT_STEP is the actual conversation state. After a reviewed bill, "ha thik h, isko move kijiye"
means continue to payment-method choice (checkout), never remove. Move/proceed is not remove/delete.
If speech/text is garbled, incoherent, or its intended action is unclear, set unclear_request=true.
This takes priority over every action and all conversation context. Do not repair unclear speech
into a guessed request. Ask exactly: "I couldn't understand that clearly. Could you say it again?"
Return action clarify, empty queries/edits, and null selection fields. Never suggest a guessed action.
Clear multilingual requests and obvious brand spellings are not automatically unclear.
Broken speech such as "हरके के मूव कीजिए" is ambiguous: clarify briefly in English, never delete items.
Never guess a removal from an unclear transcript, and never infer a completed payment from agreement.
Use status for requests to check an order/payment or claims of payment completion in any language
(e.g. payment ho gaya, paise de diye). This only checks merchant history; it never proves payment.
Distinguish a request TO PROCEED with payment from a claim of completed payment using meaning
and NEXT_STEP, not the presence of payment/confirm words. With items but no reviewed bill,
use review so the customer sees complete charges and Choose payment. With an already reviewed
bill, use checkout to show methods. A request to retry payment is checkout, not status.
Never infer authorization to submit an order or pay.
RECENT_SELECTION preserves the last selected line and its numbered menu, even after active choices clear.
"first wla nhi, ap second wla choose kro" after selecting option 1 means select option 2 from
RECENT_CHOICES and set replaces_line_id to RECENT_SELECTION.line_id. Preserve its quantity unless changed.
Do not search again or add both products when the user explicitly corrects the selection.
Before any selection, "not first, second" simply selects option 2, with replaces_line_id null.
For an explicit additional item ("also", "bhi") use select without replacement.
If only a bare number follows a completed selection, clarify whether to replace or add.
Never invent a numbered option or a replacement line ID. A new search supersedes the old menu.
Select only a specific offer from DISPLAYED_CHOICES when the user unambiguously chooses it;
use offer_id exactly, and quantity null to preserve the suggested pack count. Understand spoken
option numbers in Indian languages. Never select an arbitrary cheapest/first match when ambiguous.
A generic product request ("ek dahi ka packet") is a fresh search even if a similar item is displayed;
select requires its option number or an explicit choice of that exact brand/variant. "haan" alone
is clarify, even if only one option is displayed. Never fill in an unstated choice.
Basket means show the draft. Questions asking what is in the cart AND which store it belongs to
are action basket, with no search, edits, target_provider or store switch. Mentioning store names
as alternatives in a question does not request switching stores.
Review means sync the store cart and read its bill (no purchase).
Checkout ONLY means an explicit request to continue AFTER a reviewed bill; it is local Pending,
never a purchase. A bare 'yes' is ambiguous: clarify, never infer a checkout or product selection.
A search alone never changes basket lines; explicit accompanying edits may change the draft.
Preserve brand, formulation and pack size. Normalize obvious brand misspellings: thumsp/thumsup -> Thums Up.
Handle the entire latest request, including negation, rejection and the positive request after it.
"yeh sab kuch nhi lena, thumsp kr do ek bass" means search for ONE Thums Up.
If CURRENT_BASKET is nonempty and the user rejects everything, include remove edits for its exact IDs
alongside that search. If the basket is empty, only search. Rejecting DISPLAYED_CHOICES alone never
removes basket items. "milk hatao aur bread dikhao" means search bread plus remove the milk line.
A specific replacement uses replaces_line_id instead of a remove edit for that same line.
Never return edit alone when a new product is also requested. Never replace the whole basket for "also"/"bhi".
"dahi rehne do, ek Thums Up bhi chahiye" preserves dahi: search the additional drink with NO edits.
Treat keep/rakhna/rehne do in an additive request as preservation, not removal. If genuinely uncertain
whether "rehne do" means keep or cancel, ask rather than removing. Explicit "hata do" means remove.
If a target is genuinely ambiguous, clarify; otherwise complete both parts without asking the user to repeat.
Adding a different product keeps every existing basket line. Do not infer replacement or store
switching from a request for another product. Only explicit replacement sets replaces_line_id.
Set target_provider to the explicitly requested search store, otherwise null. Never choose the
existing store instead of the requested store. Include provider in contextual reasoning.
For "remove this from Instamart and add the same/similar product on Blinkit", return search,
target_provider=blinkit, switch_store=true, and a query preserving the basket product name,
pack and quantity with replaces_line_id. NO remove edits: the harness stages this replacement
until a destination product is selected. Reverse directions work the same way.
Only set switch_store for an explicit request to move/replace the basket at another store;
"compare Blinkit prices", "don't switch", or adding a different product are NOT store switches.
For a requested store when the basket is empty, use search with target_provider and switch_store=false.
If multiple basket items make the move ambiguous, ask which entire basket should be used.
Without an explicit store switch, existing-basket choices remain in its store; other-store prices
are comparison only. Never claim a store was changed in clarification.
Stop pauses this chat; do not interpret 'don't place an order yet, compare milk' as opt-out.
Return empty queries/edits unless needed, null offer_id/quantity unless selecting.
Use short, everyday language: one short sentence where possible. Avoid technical words and repeated disclaimers.
All customer-facing replies and clarifications MUST be in English, even for Hindi/Hinglish or other input. Never claim any completed action or price
in clarification. Shopping only: redirect unrelated questions to groceries.
Interpret only the current request. CURRENT_BASKET is authoritative; an empty list means no items.
A request such as "मुझे दही चाहिए", "दही मंगवा दो", or "dahi order karo" means search
for curd/dahi, not set_quantity or remove. Translate generic Indian-language grocery names into
searchable catalogue terms while preserving brands, quantity and variants. Never edit a missing item.
Use edit only for an explicit quantity change/removal of an exact CURRENT_BASKET line ID.
""",
            input=json.dumps(
                {
                    "request": text,
                    "UNRESOLVED_REQUESTS": state.get("unresolved_requests", []),
                    "CURRENT_BASKET": [
                        {
                            "line_id": line.id,
                            "provider": line.offer.provider.value,
                            "name": line.offer.name,
                            "pack_size": line.offer.pack_size,
                            "quantity": line.quantity,
                        }
                        for line in session.view.lines
                    ],
                    "RECENT_SELECTION": state.get("recent_selection"),
                    "RECENT_CHOICES": [
                        {"number": i + 1, **session.offers[id].model_dump(mode="json")}
                        for i, id in enumerate(self.recent_choices(session, state))
                    ],
                    "PREVIOUS_BASKET": [
                        {
                            "name": line["offer"]["name"],
                            "pack": line["offer"].get("pack_size"),
                            "quantity": line["quantity"],
                        }
                        for line in state.get("previous_basket", [])
                    ],
                    "NEXT_STEP": "choose_payment_method"
                    if session.view.review
                    else "review_basket"
                    if session.view.lines
                    else "choose_products",
                    "has_reviewed_bill": session.view.review is not None,
                    "validation_feedback": (
                        "Your previous interpretation had invalid targets or inconsistent action fields. "
                        "Reinterpret the current request: new products require search, "
                        "and ambiguous changes require clarify. Do not invent line IDs."
                        if repair
                        else None
                    ),
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
        intent = result.output_parsed
        if intent.unclear_request:
            return ChatIntent(
                action="clarify",
                queries=[],
                edits=[],
                offer_id=None,
                quantity=None,
                clarification="I couldn't understand that clearly. Could you say it again?",
                unclear_request=True,
            )
        line_ids = {line.id for line in session.view.lines}
        invalid_target = (
            intent.action in {"edit", "search"}
            and (
                (intent.action == "edit" and not intent.edits)
                or any(edit.line_id not in line_ids for edit in intent.edits)
                or len({edit.line_id for edit in intent.edits}) != len(intent.edits)
                or any(
                    q.replaces_line_id in {e.line_id for e in intent.edits}
                    for q in intent.queries
                    if q.replaces_line_id
                )
            )
        ) or (
            intent.action == "search"
            and any(q.replaces_line_id and q.replaces_line_id not in line_ids for q in intent.queries)
        )
        invalid_shape = (
            (bool(intent.queries) and intent.action != "search")
            or (bool(intent.edits) and intent.action not in {"search", "edit"})
            or (intent.action == "search" and not 1 <= len(intent.queries) <= 3)
            or len(intent.edits) > 10
            or (
                intent.action == "select"
                and intent.offer_id not in state["choices"] + self.recent_choices(session, state)
            )
            or (
                intent.replaces_line_id is not None
                and (intent.action != "select" or intent.replaces_line_id not in line_ids)
            )
        )
        if invalid_target or invalid_shape:
            if not repair:
                return await self.interpret(text, session, state, repair=True)
            return ChatIntent(
                action="clarify",
                queries=[],
                edits=[],
                offer_id=None,
                quantity=None,
                clarification="Which product or option would you like?",
            )
        if intent.action == "select" and not intent.replaces_line_id and self.recent_choices(session, state):
            decision = await client.responses.parse(
                model=self.settings.openai_text_model,
                instructions=(
                    "The customer already selected an item from a numbered menu. Classify the CURRENT "
                    "request as replace (reject/correct the earlier selection), add (explicitly wants another item too), "
                    "or clarify (neither is explicit). 'First nahi, second wala' means replace; 'second bhi' means add. "
                    "Do not infer addition merely because the requested offer differs."
                ),
                input=json.dumps(
                    {"request": text, "recent_selection": state["recent_selection"]}, ensure_ascii=False
                ),
                text_format=SelectionDecision,
                store=False,
            )
            if decision.output_parsed and decision.output_parsed.operation == "replace":
                intent.replaces_line_id = state["recent_selection"]["line_id"]
            elif not decision.output_parsed or decision.output_parsed.operation == "clarify":
                return ChatIntent(
                    action="clarify",
                    queries=[],
                    edits=[],
                    offer_id=None,
                    quantity=None,
                    clarification="Should that option replace your selected item, or be added as well?",
                )
        if any(edit.kind == "remove" for edit in intent.edits):
            check = await client.responses.parse(
                model=self.settings.openai_text_model,
                instructions=(
                    "Verify whether the CURRENT utterance explicitly asks to remove the proposed items. "
                    "Return false for garbled or ambiguous speech, negated removal, keep/retain requests, "
                    "or move/proceed/continue toward checkout. Move is NOT remove. Do not repair broken "
                    "speech into a destructive command. True requires clear deletion intent in context. "
                    "Examples: 'har ke ke move kijiye' / 'हरके के मूव कीजिए' is false; "
                    "'ha thik h, isko move kijiye' is false; 'cart se orange hata do' is true."
                ),
                input=json.dumps(
                    {
                        "utterance": text,
                        "proposed_removals": [
                            line.offer.name
                            for line in session.view.lines
                            if line.id in {e.line_id for e in intent.edits if e.kind == "remove"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                text_format=RemovalCheck,
                store=False,
            )
            if check.output_parsed is None or not check.output_parsed.explicit_removal:
                return ChatIntent(
                    action="clarify",
                    queries=[],
                    edits=[],
                    offer_id=None,
                    quantity=None,
                    clarification="I couldn't understand that clearly. Could you say it again?",
                )
        # Never pass Indic-script model prose through to an English-only customer chat.
        if re.search(r"[\u0600-\u06ff\u0900-\u0dff]", intent.clarification):
            intent.clarification = (
                "Which product or option would you like?" if intent.action == "clarify" else ""
            )
        return intent

    @staticmethod
    def retry_payment_confirmation(state, view):
        pending = {"id": uuid4().hex, "order_id": view.id, "created_at": now().isoformat()}
        state["retry_confirmation"] = pending
        return [{
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Retry payment? No order was found, but a late confirmation could mean a duplicate. I’ll recheck your bill and show payment options."},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": f"confirm_retry:{view.version}:{pending['id']}", "title": "Retry payment"}},
                    {"type": "reply", "reply": {"id": f"status:{view.version}:{view.id}", "title": "Check status"}},
                ]},
            },
        }]

    @staticmethod
    def recent_choices(session, state):
        recent = state.get("recent_selection") or {}
        target = next((line for line in session.view.lines if line.id == recent.get("line_id")), None)
        if not target or target.offer.id != recent.get("offer_id"):
            return []
        return [id for id in recent.get("choices", []) if id in session.offers]

    @staticmethod
    def restore_legacy_choices(session, state):
        # Old versions pruned alternatives but retained the full search result. Recover only
        # a single-provider, single-query menu with exactly one matching selected basket line.
        if state["choices"] or state.get("recent_selection") or len(session.view.lines) != 1:
            return
        results = [r for r in session.view.results if r.offers]
        if len(results) != 1:
            return
        choices = [o for o in results[0].offers[:2] if o.available and o.price_paise is not None]
        line = session.view.lines[0]
        if line.offer.id not in {o.id for o in choices}:
            return
        for offer in choices:
            session.offers[offer.id] = offer
            state["offers"][offer.id] = offer.model_dump(mode="json")
        state["recent_selection"] = {
            "choices": [o.id for o in choices],
            "line_id": line.id,
            "offer_id": line.offer.id,
        }

    async def review_before_payment(self, state, progress, customer):
        if not state["view"]["lines"]:
            return state, [text_message("Your basket is empty. What would you like to order?")]
        if progress:
            progress.update("Checking your cart and fees…")
        return await self.handle("review", state, progress=progress, customer=customer)

    async def handle(self, text, state, action_id=None, progress=None, customer=None, _intent=None):
        session = Session(
            view=SessionView.model_validate(state["view"]),
            offers={id: Offer.model_validate(value) for id, value in state["offers"].items()},
        )
        original_lines = [line.model_dump(mode="json") for line in session.view.lines]
        self.restore_legacy_choices(session, state)
        selected_method = None
        normalized = text.strip().casefold()
        from .order_tracking import status_button, status_messages

        payments = getattr(self, "payments", None)
        checking = normalized in {
            "paid",
            "payment done",
            "check payment",
            "check status",
            "order status",
            "i paid",
            "payment completed",
        }
        if (action_id and action_id.startswith("status:")) or checking:
            if action_id and action_id.split(":", 2)[-1] != session.view.id:
                raise DomainError("That button belongs to another basket. Ask to check your current order.")
            if not customer or not payments or not payments.existing(customer, session.view.id):
                return await self.review_before_payment(state, progress, customer)
            if progress:
                progress.update("Checking your order with the store…")
            if normalized in {"paid", "payment done", "i paid", "payment completed"}:
                await asyncio.sleep(5)
            result = await payments.monitor.check(customer, session.view.id, self.workers)
            return state, status_messages(result, session.view)
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
        queued_search = _intent is not None
        if not action_id and normalized in {"retry item", "skip item"} and state.get("active_request"):
            action_id = f"{'retry_search' if normalized == 'retry item' else 'skip_search'}:{session.view.version}:current"
        if _intent is not None:
            intent = _intent
        elif action_id:
            parts = action_id.split(":", 2)
            if len(parts) != 3 or parts[1] != str(session.view.version):
                raise DomainError("That choice is old. Ask to see your basket or search again.")
            if parts[0] in {"retry_search", "skip_search"}:
                if not state.get("active_request"):
                    raise DomainError("That request is already handled. Tell me what you need next.")
                if parts[0] == "retry_search":
                    request = state["active_request"]
                else:
                    pending = state.get("request_queue", [])
                    request = pending.pop(0) if pending else None
                    state["active_request"] = None
                    state["unresolved_requests"] = []
                    state["choices"] = []
                    if request is None:
                        session.changed()
                        state["view"] = session.view.model_dump(mode="json")
                        return state, self.basket_messages(session)
                intent = ChatIntent(action="search", **{**kwargs,
                    "queries": [RequestedProduct.model_validate(request)]})
                queued_search = True
            elif parts[0] in {"change_basket", "save_basket"}:
                if not session.view.review or parts[2] != session.view.review.id:
                    raise DomainError("That choice is old. Ask to see your basket.")
                if parts[0] == "change_basket":
                    state.pop("cash_confirmation", None)
                    return state, [
                        text_message(
                            "What would you like to add, remove, or change? Send a message or voice note."
                        )
                    ]
                return state, [
                    text_message("Your basket is saved. Message me whenever you’re ready to continue.")
                ]
            elif parts[0] == "pick":
                intent = ChatIntent(action="select", **{**kwargs, "offer_id": parts[2]})
            elif parts[0] == "retry_payment":
                if parts[2] != session.view.id:
                    raise DomainError("That retry belongs to another basket.")
                intent = ChatIntent(action="checkout", **kwargs)
            elif parts[0] == "confirm_retry":
                pending = state.get("retry_confirmation")
                if (
                    not pending or parts[2] != pending["id"]
                    or pending["order_id"] != session.view.id
                    or now() - datetime.fromisoformat(pending["created_at"]) > timedelta(minutes=5)
                    or not customer or not payments
                ):
                    raise DomainError("That retry confirmation expired. Ask to retry payment again.")
                payments.release_for_confirmed_retry(customer, session.view.id)
                state.pop("retry_confirmation", None)
                state.pop("cash_confirmation", None)
                state.pop("payment_menu", None)
                session.view.payment_preparation = None
                state["view"] = session.view.model_dump(mode="json")
                return await self.handle("checkout", state, progress=progress, customer=customer)
            elif parts[0] == "place_cod":
                pending = state.get("cash_confirmation")
                if (
                    not pending
                    or parts[2] != pending["id"]
                    or not session.view.review
                    or pending["review_id"] != session.view.review.id
                    or now() - datetime.fromisoformat(pending["created_at"]) > timedelta(minutes=5)
                ):
                    raise DomainError("That order confirmation expired. Choose pay on delivery again.")
                selected_method = {"id": "cash_submit"}
                intent = ChatIntent(action="payment", **kwargs)
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
                allowed = {"cash", "upi"} if session.view.review.provider.value == "blinkit" else {"cash"}
                if selected_method is None or method_id not in allowed:
                    raise DomainError("That payment method is no longer offered. Choose payment again.")
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
            elif parts[0] == "restore":
                pending = state.get("pending_restore")
                if not pending or parts[2] != pending["id"]:
                    raise DomainError("That basket confirmation is old. Ask to restore your basket again.")
                intent = ChatIntent(action="restore", **kwargs)
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
        if intent.action in {"logout", "account_status"}:
            return state, [{"type": "account_action", "action": intent.action}]
        if intent.action == "status":
            if customer and payments and payments.existing(customer, session.view.id):
                if progress:
                    progress.update("Checking your order with the store…")
                result = await payments.monitor.check(customer, session.view.id, self.workers)
                return state, status_messages(result, session.view)
            return await self.review_before_payment(state, progress, customer)
        if customer and payments and intent.action in {"review", "checkout", "payment"} and payments.existing(customer, session.view.id):
            if progress:
                progress.update("Checking your previous order attempt…")
            previous = payments.monitor.get(customer, session.view.id)
            if previous and previous["status"] == "not_found":
                return state, self.retry_payment_confirmation(state, session.view)
            result = await payments.monitor.check(customer, session.view.id, self.workers)
            if result["status"] == "not_found":
                return state, self.retry_payment_confirmation(state, session.view)
            return state, status_messages(result, session.view)
        if (
            customer
            and getattr(self, "payments", None)
            and (
                intent.action in {"edit", "select", "review", "checkout", "payment", "restore"}
                or intent.edits
                or intent.switch_store
            )
            and self.payments.existing(customer, session.view.id)
        ):
            raise DomainError(
                "An order or payment request already exists. Check the store’s Orders page before starting another."
            )
        if state.get("store_switch") and intent.action in {"review", "checkout", "payment"}:
            target_store = state["store_switch"]["target"].title()
            replies = [
                text_message(
                    f"Choose your {target_store} replacement first. Your old basket has not been moved yet."
                )
            ]
            if state["choices"]:
                replies.extend(self.choice_messages(session, state["choices"]))
            else:
                replies.append(
                    text_message(
                        f"I couldn’t find an available {target_store} option. Tell me another brand or pack size to search."
                    )
                )
            return state, replies
        if progress:
            progress.update(
                {
                    "search": "Finding matching products…",
                    "review": "Checking your cart and fees…",
                    "checkout": "Checking your final total…",
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
            all_queries = list(intent.queries)
            if not queued_search:
                if len(intent.queries) > 1:
                    replies.append(text_message("We'll choose one item at a time: " + "; ".join(
                        f"{q.quantity} × {q.query}" for q in intent.queries
                    ) + "."))
            intent = intent.model_copy(update={"queries": intent.queries[:1]})
            # Validate the whole compound request before any discovery or draft mutation.
            line_ids = {line.id for line in session.view.lines}
            if len(intent.edits) > 10 or any(e.line_id not in line_ids for e in intent.edits):
                raise DomainError("Which basket item would you like to change?")
            source = session.view.synced_provider or (
                session.view.lines[0].offer.provider if session.view.lines else None
            )
            pending_target = (
                state.get("store_switch", {}).get("target") if state.get("store_switch") else None
            )
            target_store = intent.target_provider or (Provider(pending_target) if pending_target else None)
            switching = bool(
                (intent.switch_store or pending_target) and target_store and source and target_store != source
            )
            if intent.switch_store and not target_store:
                raise DomainError("Which store would you like to switch to?")
            if switching and (len(session.view.lines) != 1 or len(all_queries) != 1):
                raise DomainError(
                    "I can move one selected product between stores at a time. Which product should the new basket contain?"
                )
            if switching and intent.edits:
                raise DomainError(
                    "Please ask me to move the product without other basket changes, so I can keep it safe until you choose."
                )
            batches = await asyncio.gather(
                *(
                    self.workers.search(q.query, session.view.address_label, provider=target_store)
                    if target_store
                    else self.workers.search(q.query, session.view.address_label)
                    for q in intent.queries
                )
            )
            if not queued_search:
                state["request_queue"] = [q.model_dump(mode="json") for q in all_queries[1:]]
            state["active_request"] = intent.queries[0].model_dump(mode="json")
            if not switching:
                for edit in intent.edits:
                    session.edit(EditRequest(**edit.model_dump(), expected_version=session.view.version))
            results = []
            choices = []
            basket_store = (
                target_store
                or session.view.synced_provider
                or (session.view.lines[0].offer.provider if session.view.lines else None)
            )
            state["store_switch"] = (
                {
                    "source": source.value,
                    "target": target_store.value,
                    "lines": [x.model_dump(mode="json") for x in session.view.lines],
                }
                if switching
                else None
            )
            for query, batch in zip(intent.queries, batches):
                for result in batch:
                    if target_store and result.provider != target_store:
                        continue
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
            state["unresolved_requests"] = [
                {"query": q.query, "quantity": q.quantity}
                for q, batch in zip(intent.queries, batches)
                if not any(o.id in choices for r in batch for o in r.offers)
            ]
            if len(intent.queries) > 1:
                replies.append(text_message("Looking for: " + "; ".join(
                    f"{q.quantity} × {q.query}" for q in intent.queries
                ) + "."))
            if state["unresolved_requests"] and choices:
                missing = "; ".join(f"{q['quantity']} × {q['query']}" for q in state["unresolved_requests"])
                replies.append(text_message(
                    f"I couldn’t find verified options for {missing}. These are still pending. "
                    "You can ask me to retry them or try another brand."
                ))
            state["recent_selection"] = None
            state["choices"] = list(dict.fromkeys(choices))[:10]
            session.view.results = results
            session.view.comparison = exact_comparison([o for r in results for o in r.offers])
            session.changed()
            if state["choices"]:
                product_messages = self.choice_messages(
                    session, state["choices"], partial=any(r.status != "ok" for r in results)
                )
                replies.extend(product_messages[:-1])
                if session.view.comparison:
                    replies.append(text_message(session.view.comparison))
            else:
                replies.append(
                    text_message(
                        "I couldn’t check products right now. Please try again shortly."
                        if any(r.status != "ok" for r in results)
                        else "I couldn’t find a match. Try a brand or pack size."
                    )
                )
            if switching:
                replies.append(
                    text_message(
                        f"Choose a {target_store.value.title()} replacement below. Your {source.value.title()} basket stays unchanged until you choose."
                        if state["choices"]
                        else f"I couldn’t find an available {target_store.value.title()} replacement. Your {source.value.title()} basket is unchanged."
                    )
                )
            # Search replies describe actual results, never unexecuted model promises.
            if state["choices"]:
                replies.append(product_messages[-1])
            else:
                replies.append({"type": "interactive", "interactive": {
                    "type": "button", "body": {"text": "Try this item again, or skip it?"},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": f"{action}:{session.view.version}:current", "title": title}}
                        for action, title in [("retry_search", "Retry item"), ("skip_search", "Skip item")]
                    ]}}})
        elif intent.action == "select":
            recent_ids = self.recent_choices(session, state)
            if intent.offer_id not in state["choices"] + recent_ids:
                raise DomainError("Please select one of the current products, or search again.")
            offer = session.offers[intent.offer_id]
            basket_store = session.view.synced_provider or (
                session.view.lines[0].offer.provider if session.view.lines else None
            )
            # Recover old mixed-store menus without deleting items or silently substituting a product.
            pending_switch = state.get("store_switch")
            switching = bool(pending_switch and pending_switch["target"] == offer.provider.value)
            if switching and pending_switch["lines"] != [
                x.model_dump(mode="json") for x in session.view.lines
            ]:
                raise DomainError("Your basket changed. Please ask me to move the updated basket again.")
            if basket_store and offer.provider != basket_store and not switching:
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
            menu_ids = list(state["choices"] if intent.offer_id in state["choices"] else recent_ids)
            replacement = intent.replaces_line_id or offer.replaces_line_id
            target = next((line for line in session.view.lines if line.id == replacement), None)
            if replacement and target is None:
                raise DomainError("Your basket changed. Please choose the item to replace again.")
            quantity = intent.quantity or (target.quantity if target else offer.suggested_quantity)
            if now() - offer.retrieved_at > timedelta(minutes=10):
                if progress:
                    progress.update("Checking the latest price…")
                fresh = await self.workers.refresh_offer(offer, session.view.address_label)
                if not fresh.available or fresh.price_paise is None:
                    raise DomainError(
                        "That option is unavailable right now. Your basket is unchanged. Would you like another option?"
                    )
                if quantity > fresh.max_quantity:
                    raise DomainError(
                        f"Only {fresh.max_quantity} packs are available now. Your basket is unchanged. How many would you like?"
                    )
                fresh = fresh.model_copy(
                    update={
                        "id": offer.id,
                        "suggested_quantity": quantity,
                        "replaces_line_id": replacement,
                    }
                )
                session.offers[offer.id] = fresh
                if fresh.price_paise != offer.price_paise:
                    # Preserve the original basket until the changed price is explicitly accepted.
                    session.changed()
                    state["choices"] = [fresh.id]
                    state["recent_selection"] = None
                    state["view"] = session.view.model_dump(mode="json")
                    keep = {fresh.id} | {line.offer.id for line in session.view.lines}
                    state["offers"] = {
                        id: item.model_dump(mode="json") for id, item in session.offers.items() if id in keep
                    }
                    return state, [
                        text_message(
                            f"{fresh.name} ({fresh.pack_size}) is now {money(fresh.price_paise)} per pack "
                            f"(was {money(offer.price_paise)}). Your basket is unchanged."
                        ),
                        *self.choice_messages(session, [fresh.id]),
                    ]
                offer = fresh
            if switching:
                # Validate the destination draft before any source-cart mutation.
                candidate = Session(view=session.view.model_copy(deep=True), offers=session.offers)
                candidate.view.lines = []
                candidate.view.synced_provider = None
                candidate.edit(
                    EditRequest(
                        kind="add",
                        offer_id=offer.id,
                        quantity=quantity,
                        expected_version=candidate.view.version,
                    )
                )
                if session.view.synced_provider:
                    empty = session.view.model_copy(deep=True)
                    empty.lines = []
                    if progress:
                        progress.update(f"Removing the old item from {basket_store.value.title()}…")
                    await self.workers.clear(empty)
                session = candidate
                state["cart_checkpoint"] = None
                state["store_switch"] = None
                replacement = None
            if not switching:
                session.edit(
                    EditRequest(
                        kind="replace" if replacement else "add",
                        offer_id=offer.id,
                        line_id=replacement,
                        quantity=quantity,
                        expected_version=session.view.version,
                    )
                )
            selected_line = next(line for line in session.view.lines if line.offer.id == offer.id)
            state["recent_selection"] = {
                "choices": menu_ids,
                "line_id": selected_line.id,
                "offer_id": offer.id,
            }
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
            state["active_request"] = None
            state["unresolved_requests"] = []
            replies.extend(self.basket_messages(session))
            if state.get("unresolved_requests"):
                missing = "; ".join(f"{q['quantity']} × {q['query']}" for q in state["unresolved_requests"])
                replies.insert(len(replies) - 1, text_message(
                    f"Still not added: {missing}. Ask me to search for them again."
                ))
            if state["choices"]:
                replies.extend(self.choice_messages(session, state["choices"]))
        elif intent.action == "edit":
            if not 1 <= len(intent.edits) <= 10:
                raise DomainError("Please describe the basket change again.")
            for edit in intent.edits:
                session.edit(EditRequest(**edit.model_dump(), expected_version=session.view.version))
            state["choices"] = []
            replies.extend(self.basket_messages(session))
        elif intent.action == "restore":
            if session.view.lines:
                raise DomainError("Your basket already has items. Would you like to replace them?")
            source = state.get("previous_basket", [])
            if not source:
                raise DomainError("Which items would you like to bring back?")
            lines = [BasketLine.model_validate(line) for line in source]
            if len({line.offer.provider for line in lines}) != 1 or (
                session.view.synced_provider and lines[0].offer.provider != session.view.synced_provider
            ):
                raise DomainError("Please choose which store basket to restore.")
            if progress:
                progress.update("Checking your previous basket…")
            fresh_lines = []
            for line in lines:
                fresh = await self.workers.refresh_offer(line.offer, session.view.address_label)
                if not fresh.available or fresh.price_paise is None or line.quantity > fresh.max_quantity:
                    raise DomainError(
                        "Some previous items aren’t available in that quantity. Your basket is unchanged."
                    )
                fresh_lines.append(line.model_copy(update={"offer": fresh}))
            expected = state.get("pending_restore", {}).get("lines", []) if action_id else source
            expected_prices = {
                (line["offer"]["product_id"], line["offer"]["sku_id"]): line["offer"]["price_paise"]
                for line in expected
            }
            if any(
                expected_prices.get((line.offer.product_id, line.offer.sku_id)) != line.offer.price_paise
                for line in fresh_lines
            ):
                pending = {"id": uuid4().hex, "lines": [line.model_dump(mode="json") for line in fresh_lines]}
                state["pending_restore"] = pending
                description = "\n".join(
                    f"• {line.quantity} × {line.offer.name} ({line.offer.pack_size}) — {money(line.subtotal)}"
                    for line in fresh_lines
                )
                return state, [
                    text_message("Prices have changed. Restore these items?\n" + description),
                    {
                        "type": "interactive",
                        "interactive": {
                            "type": "button",
                            "body": {"text": "Restore your previous basket?"},
                            "action": {
                                "buttons": [
                                    {
                                        "type": "reply",
                                        "reply": {
                                            "id": f"restore:{session.view.version}:{pending['id']}",
                                            "title": "Restore basket",
                                        },
                                    }
                                ]
                            },
                        },
                    },
                ]
            session.view.lines = fresh_lines
            for line in fresh_lines:
                session.offers[line.offer.id] = line.offer
            session.changed()
            state["choices"] = []
            state["recent_selection"] = None
            state["pending_restore"] = None
            replies = [text_message("Restored your previous basket."), *self.basket_messages(session)]
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
                    state["cart_checkpoint"] = session.view.model_dump(mode="json")
                    review = await self.resolve_bill(session, review, progress)
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
                return await self.review_before_payment(state, progress, customer)
            expired = now() - review.created_at > timedelta(minutes=5)
            fresh = await self.workers.revalidate(session.view)
            fresh = await self.resolve_bill(session, fresh, progress)
            session.view.payment_preparation = None
            if (
                expired
                or not can_choose_payment(fresh)
                or fingerprint(session.view, fresh) != fingerprint(session.view, review)
            ):
                session.accept_review(session.view.version, fresh)
                state["payment_menu"] = None
                if can_choose_payment(fresh):
                    replies.append(text_message("Here’s the latest bill. Please check it before continuing."))
                replies.extend(self.review_messages(session))
            else:
                methods = await self.workers.payment_options(session.view, fresh)
                allowed = {"cash", "upi"} if fresh.provider.value == "blinkit" else {"cash"}
                methods = [item for item in methods if item["id"] in allowed]
                if not methods:
                    raise DomainError(
                        "Our supported payment options aren’t available for this order right now."
                    )
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
            fresh = await self.resolve_bill(session, fresh, progress)
            if not can_choose_payment(fresh) or fingerprint(session.view, fresh) != fingerprint(
                session.view, review
            ):
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
                replies.append({"type": "payment_qr", "request_id": prepared["id"]})
                replies.append(status_button(session.view))
            elif selected_method["id"] == "cash":
                await self.workers.prepare_cash(session.view, fresh)
                pending = {
                    "id": uuid4().hex,
                    "review_id": review.id,
                    "fingerprint": fingerprint(session.view, fresh),
                    "created_at": now().isoformat(),
                }
                state["cash_confirmation"] = pending
                state["payment_menu"] = None
                replies.append(
                    {
                        "type": "interactive",
                        "interactive": {
                            "type": "button",
                            "body": {
                                "text": f"Place this {money(fresh.total_paise)} order with {fresh.provider.value.title()}? Pay cash on delivery. Tapping Place order submits the order."
                            },
                            "action": {
                                "buttons": [
                                    {
                                        "type": "reply",
                                        "reply": {
                                            "id": f"place_cod:{session.view.version}:{pending['id']}",
                                            "title": "Place order",
                                        },
                                    },
                                    {
                                        "type": "reply",
                                        "reply": {
                                            "id": f"change_basket:{session.view.version}:{review.id}",
                                            "title": "Change basket",
                                        },
                                    },
                                ]
                            },
                        },
                    }
                )
            elif selected_method["id"] == "cash_submit":
                if not customer or not getattr(self, "payments", None):
                    raise DomainError("Order submission is not connected.")
                if state["cash_confirmation"]["fingerprint"] != fingerprint(session.view, fresh):
                    raise DomainError("Your bill changed. Check the updated total before placing the order.")
                result = await self.payments.place_cash(customer, session.view, fresh, self.workers)
                state["cash_confirmation"] = None
                state["payment_menu"] = None
                session.view.payment_preparation = {"status": "cash_" + result["status"], **result}
                replies.extend(status_messages(result, session.view))
            else:
                raise DomainError("Please choose pay on delivery or an available UPI QR option.")
        elif intent.action == "basket":
            replies.extend(self.basket_messages(session))
        elif intent.action == "stop":
            state["paused"] = True
            state["choices"] = []
            session.changed()
            replies.append(
                text_message(
                    "Paused. Existing orders or payment requests are not cancelled. "
                    "Send START when you want to continue."
                )
            )
        else:
            replies.append(
                text_message(intent.clarification or "Which groceries and pack sizes would you like?")
            )
        if original_lines and original_lines != [line.model_dump(mode="json") for line in session.view.lines]:
            state["previous_basket"] = original_lines
        for reply in replies:
            if reply["type"] == "text":
                session.say(reply["text"]["body"])
        state["view"] = session.view.model_dump(mode="json")
        # Keep only offers relevant to the current choices or draft, bounding database growth.
        keep = (
            set(state["choices"])
            | set(self.recent_choices(session, state))
            | {line.offer.id for line in session.view.lines}
        )
        state["offers"] = {id: o.model_dump(mode="json") for id, o in session.offers.items() if id in keep}
        if intent.action == "select" and not state["choices"] and state.get("request_queue"):
            next_request = RequestedProduct.model_validate(state["request_queue"].pop(0))
            next_intent = ChatIntent(action="search", **{**kwargs, "queries": [next_request],
                                                        "target_provider": offer.provider})
            state, next_replies = await self.handle(
                next_request.query, state, progress=progress, customer=customer, _intent=next_intent
            )
            replies = [r for r in replies if r.get("type") != "interactive"]
            replies.append(text_message(f"Next: {next_request.quantity} × {next_request.query}."))
            replies.extend(next_replies)
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
                                            "Generate a UPI QR"
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

    async def resolve_bill(self, session, review, progress=None):
        """Bounded read-only recovery; never repeat cart writes or infer missing charges."""
        if progress and not can_choose_payment(review):
            progress.update("Double-checking the store’s total…")
        for _ in range(2):
            if can_choose_payment(review):
                return review
            try:
                review = await self.workers.revalidate(session.view)
            except DomainError:
                raise DomainError(
                    "I couldn’t verify the store’s bill just now. Your basket is saved; no payment was started."
                ) from None
        return review

    @staticmethod
    def review_messages(session):
        review = session.view.review
        items = "\n".join(
            f"• {line.quantity} × {line.offer.name} ({line.offer.pack_size})" for line in session.view.lines
        )
        reconciled = can_choose_payment(review)
        next_step = (
            "Tap Choose payment to continue, or tell me what to change."
            if reconciled
            else "Your basket is saved. Choose what you’d like to do below."
        )
        replies = [
            text_message(
                f"{review.provider.value.title()} — live cart review\n{items}\n\n"
                f"{table(review)}\n\nDelivery: {review.address_label}\n"
                f"{next_step}"
            )
        ]
        if can_choose_payment(review):
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
        else:
            replies.append(
                {
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "body": {"text": "Would you like to change your basket or save it for later?"},
                        "action": {
                            "buttons": [
                                {
                                    "type": "reply",
                                    "reply": {
                                        "id": f"change_basket:{session.view.version}:{review.id}",
                                        "title": "Change basket",
                                    },
                                },
                                {
                                    "type": "reply",
                                    "reply": {
                                        "id": f"save_basket:{session.view.version}:{review.id}",
                                        "title": "Save for later",
                                    },
                                },
                            ]
                        },
                    },
                }
            )
        return replies

    @staticmethod
    def choice_messages(session, choices, partial=False):
        rows, descriptions, photos = [], [], []
        for i, id in enumerate(choices, 1):
            offer = session.offers[id]
            title = f"{i}. {offer.provider.value.title()}"
            detail = f"{offer.name[:100]} · {offer.pack_size or 'unknown pack'} · {money(offer.price_paise)}"
            image_url = product_image_url(offer.image_url)
            if image_url and len(photos) < 2:
                photos.append(
                    {
                        "type": "image",
                        "image": {
                            "link": image_url,
                            "caption": f"{title} · {offer.name} · {offer.pack_size} · {money(offer.price_paise)} · In stock · {offer.suggested_quantity} pack(s). Reply {i} to choose."[
                                :1024
                            ],
                        },
                    }
                )
            else:
                descriptions.append(
                    f"{title}: {detail} · In stock · {offer.suggested_quantity} pack(s). Reply {i} to choose."
                )
            rows.append(
                {"id": f"pick:{session.view.version}:{id}", "title": title[:24], "description": detail[:72]}
            )
        return (
            photos
            + ([text_message("\n".join(descriptions))] if descriptions else [])
            + [
                {
                    "type": "interactive",
                    "interactive": {
                        "type": "list",
                        "body": {
                            "text": "Which product would you like? Tap below or reply with its number. Delivery and fees come at review."
                            + (" I couldn’t compare all stores this time." if partial else "")
                        },
                        "action": {
                            "button": "Choose a product",
                            "sections": [{"title": "Live results", "rows": rows}],
                        },
                    },
                },
            ]
        )

    @staticmethod
    def basket_messages(session):
        if not session.view.lines:
            return [text_message("Your basket is empty. What would you like instead?")]
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
