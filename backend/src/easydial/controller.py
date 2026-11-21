import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from .models import BasketLine, EditRequest, Message, Offer, Review, SessionView, now


class DomainError(Exception):
    def __init__(self, message: str, status: int = 409):
        self.message = message
        self.status = status
        super().__init__(message)


@dataclass
class Session:
    view: SessionView
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    expires_at: object = field(default_factory=lambda: now() + timedelta(hours=2))
    offers: dict[str, Offer] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def check(self, version: int):
        if version != self.view.version:
            raise DomainError("Your basket changed. Refresh it and try again.")

    def changed(self):
        self.view.version += 1
        self.view.review = None
        self.view.phase = "shopping"

    def say(self, text: str, role="assistant"):
        self.view.messages.append(Message(role=role, text=text))
        self.view.messages = self.view.messages[-20:]

    def edit(self, request: EditRequest):
        self.check(request.expected_version)
        # Work on a copy: failed replacements/validation never partially mutate state.
        lines = [line.model_copy(deep=True) for line in self.view.lines]
        target = next((x for x in lines if x.id == request.line_id), None)
        if request.kind != "add" and target is None:
            raise DomainError("That item is no longer in your basket.", 404)
        if request.kind in {"add", "replace"}:
            offer = self.offers.get(request.offer_id or "")
            if not offer or offer.available is not True or offer.price_paise is None:
                raise DomainError("Choose an available product with a verified price.", 422)
            if self.view.synced_provider and self.view.synced_provider != offer.provider:
                raise DomainError("Sync an empty basket with the previous store before switching stores.")
            if offer.mode != self.view.mode or offer.address_label != self.view.address_label:
                raise DomainError("This product belongs to another delivery context.")
            if now() - offer.retrieved_at > timedelta(minutes=10):
                raise DomainError("This price is old. Search again before adding it.")
            if request.kind == "replace":
                lines.remove(target)
            if any(x.offer.provider != offer.provider for x in lines):
                raise DomainError("Use one store per basket for now. Remove the other store's items first.")
            existing = next((x for x in lines if x.offer.id == offer.id), None)
            quantity = request.quantity + (existing.quantity if existing else 0)
            if quantity > offer.max_quantity:
                raise DomainError(f"Only {offer.max_quantity} packs can be selected.", 422)
            if existing:
                existing.quantity = quantity
            else:
                lines.append(BasketLine(offer=offer, quantity=quantity))
        elif request.kind == "remove":
            lines.remove(target)
        else:
            if request.quantity > target.offer.max_quantity:
                raise DomainError(f"Only {target.offer.max_quantity} packs can be selected.", 422)
            target.quantity = request.quantity
        self.view.lines = lines
        self.changed()

    def accept_review(self, version: int, review: Review):
        self.check(version)
        if not self.view.lines:
            raise DomainError("Add something to your basket first.", 422)
        self.view.review = review
        self.view.phase = "review"
        self.say("Your store basket is synced. Check the current bill before checkout.")

    def confirm(self, version: int, review_id: str, confirmed: bool):
        self.check(version)
        review = self.view.review
        if not confirmed or not review or review.id != review_id or review.version != version:
            raise DomainError("Review the current basket before checkout.")
        if now() - review.created_at > timedelta(minutes=5):
            raise DomainError("This review expired. Review your basket again.")
        # A local checkpoint only: never invokes provider checkout or payment.
        if self.view.phase != "checkout_pending":
            self.view.phase = "checkout_pending"
            self.say(
                "Pending — payment isn't enabled. Your groceries are in the store basket; no order was placed."
            )


class SessionStore:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def create(self, mode: str, address: str) -> Session:
        self.sessions = {k: v for k, v in self.sessions.items() if v.expires_at > now()}
        if len(self.sessions) >= 100:
            raise DomainError("Too many local sessions. Try again later.", 429)
        session = Session(SessionView(id=uuid4().hex, mode=mode, address_label=address))
        session.say("Tell me what you need. We'll check Blinkit and Swiggy Instamart for your address.")
        self.sessions[session.view.id] = session
        return session

    def get(self, session_id: str, token: str) -> Session:
        session = self.sessions.get(session_id)
        if not session or session.expires_at <= now() or not secrets.compare_digest(session.token, token):
            raise DomainError("Your session expired. Start again to continue.", 401)
        return session


def exact_comparison(offers: list[Offer]) -> str | None:
    groups: dict[tuple, list[Offer]] = {}
    for offer in offers:
        if (
            not all([offer.brand, offer.variant, offer.pack_size])
            or offer.price_paise is None
            or offer.available is not True
        ):
            continue
        key = tuple(
            str(x).strip().casefold()
            for x in (offer.brand, offer.variant, offer.pack_size, offer.address_label, offer.mode)
        )
        groups.setdefault(key, []).append(offer)
    for options in groups.values():
        if len({x.provider for x in options}) < 2:
            continue
        cheap, expensive = (
            min(options, key=lambda x: x.price_paise),
            max(options, key=lambda x: x.price_paise),
        )
        delta = (expensive.price_paise - cheap.price_paise) / 100
        prefix = ""
        if delta == 0:
            return f"{prefix}{cheap.name}, {cheap.pack_size}, has the same item price at both stores. Fees may differ."
        return f"{prefix}{cheap.name}, {cheap.pack_size}, is ₹{delta:.2f} less at {cheap.provider.value.title()}. This compares item prices only; delivery and other fees may differ."
    return None
