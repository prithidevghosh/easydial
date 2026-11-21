from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def now() -> datetime:
    return datetime.now(UTC)


class Provider(StrEnum):
    BLINKIT = "blinkit"
    INSTAMART = "instamart"


class Offer(BaseModel):
    id: str
    provider: Provider
    product_id: str
    sku_id: str
    name: str
    brand: str | None = None
    variant: str | None = None
    pack_size: str | None = None
    price_paise: int | None = Field(default=None, ge=0)
    available: bool | None = None
    max_quantity: int = Field(default=10, ge=1, le=100)
    mode: Literal["live"]
    address_label: str
    retrieved_at: datetime = Field(default_factory=now)
    suggested_quantity: int = Field(default=1, ge=1, le=100)
    replaces_line_id: str | None = None


class SearchResult(BaseModel):
    provider: Provider
    query: str
    offers: list[Offer] = Field(default_factory=list)
    status: Literal["ok", "unavailable", "not_connected", "error"] = "ok"
    message: str | None = None
    truncated: bool = False


class BasketLine(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    offer: Offer
    quantity: int = Field(ge=1, le=100)

    @property
    def subtotal(self) -> int:
        return (self.offer.price_paise or 0) * self.quantity


class Review(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    version: int
    provider: Provider
    address_label: str
    item_subtotal_paise: int | None
    fees_paise: int | None
    total_paise: int | None
    payment_method: str
    mode: Literal["live"]
    bill_lines: list[dict[str, str]] = Field(default_factory=list)
    cart_reference: str | None = None
    created_at: datetime = Field(default_factory=now)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class SessionView(BaseModel):
    id: str
    mode: Literal["live"]
    version: int = 0
    address_label: str
    lines: list[BasketLine] = Field(default_factory=list)
    results: list[SearchResult] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    review: Review | None = None
    phase: Literal["shopping", "review", "checkout_pending"] = "shopping"
    comparison: str | None = None
    synced_provider: Provider | None = None
    payment_preparation: dict | None = None


class SessionCreated(BaseModel):
    session: SessionView
    token: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=150)
    expected_version: int = Field(ge=0)


class EditRequest(BaseModel):
    kind: Literal["add", "set_quantity", "remove", "replace"]
    expected_version: int = Field(ge=0)
    offer_id: str | None = None
    line_id: str | None = None
    quantity: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def required_ids(self):
        if self.kind in {"add", "replace"} and not self.offer_id:
            raise ValueError("Select a product first.")
        if self.kind in {"remove", "set_quantity", "replace"} and not self.line_id:
            raise ValueError("Select a basket item first.")
        return self


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=0)


class ConfirmRequest(VersionRequest):
    review_id: str
    confirmed: bool


class TurnRequest(VersionRequest):
    text: str = Field(min_length=1, max_length=3000)


class IntentEdit(BaseModel):
    kind: Literal["set_quantity", "remove"]
    line_id: str
    quantity: int = Field(ge=1, le=100)


class RequestedProduct(BaseModel):
    query: str = Field(min_length=1, max_length=150)
    quantity: int = Field(ge=1, le=100)
    replaces_line_id: str | None


class Intent(BaseModel):
    action: Literal["search", "edit", "review", "clarify", "stop"]
    queries: list[RequestedProduct]
    edits: list[IntentEdit]
    clarification: str
