import copy

import pytest

from easydial.billing import breakdown, table
from easydial.controller import DomainError
from easydial.models import EditRequest, Provider, Review, SessionView
from easydial.providers.blinkit import BlinkitProvider, normalized
from tests.test_whatsapp import make_runtime


def review(lines, subtotal=3000, total=10300):
    return Review(
        version=0,
        provider=Provider.BLINKIT,
        address_label="test address",
        item_subtotal_paise=subtotal,
        fees_paise=None,
        total_paise=total,
        payment_method="Pending",
        mode="live",
        bill_lines=[{"label": a, "value": b} for a, b in lines],
    )


def test_observed_blinkit_breakdown_and_small_cart_warning():
    r = review(
        [
            ("Items total", "₹30"),
            ("Delivery charge", "₹35"),
            ("Handling charge", "₹13"),
            ("Small cart charge", "₹25"),
            ("Grand total", "₹103"),
        ]
    )
    result = breakdown(r)
    assert result["reconciled"]
    assert "Small cart charge: ₹25" in result["warnings"][0]
    assert "| Amount" in table(r) and "₹103.00" in table(r)
    assert "threshold" not in table(r)


def test_free_delivery_and_discount_are_preserved():
    r = review(
        [
            ("Item Total", "₹332.00"),
            ("Handling Fee", "₹12.00"),
            ("Delivery Partner Fee", "FREE"),
            ("To Pay", "₹344"),
        ],
        33200,
        34400,
    )
    assert breakdown(r)["reconciled"]
    assert next(x for x in breakdown(r)["rows"] if x["label"] == "Delivery Partner Fee")["amount_paise"] == 0
    r = review([("Item total", "₹100"), ("Coupon discount", "₹10"), ("Delivery fee", "FREE")], 10000, 9000)
    assert breakdown(r)["reconciled"]


@pytest.mark.parametrize("label", ["Minimum order fee", "Small order charge", "Small-cart fee"])
def test_minimum_order_fee_is_explicit(label):
    r = review([("Items total", "₹30"), (label, "₹25")], 3000, 5500)
    assert label in table(r) and breakdown(r)["warnings"]


def test_unknown_or_missing_charge_blocks_approval():
    r = review([("Items total", "₹30"), ("Handling fee", "varies")])
    assert not breakdown(r)["reconciled"]
    assert "Unverified" in table(r)
    r = review([("Items total", "₹30")])
    assert not breakdown(r)["reconciled"]


async def test_changed_live_fee_requires_new_approval_and_edit_invalidates_record(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    state, _ = await runtime.chat.handle("review", state)
    old = copy.deepcopy(state)
    worker = runtime.chat.workers.workers[0]
    original = worker.revalidate

    async def changed(view):
        r = await original(view)
        r.total_paise += 2500
        r.fees_paise = 2500
        r.bill_lines = [
            {"label": "Items total", "value": "₹56"},
            {"label": "Small cart charge", "value": "₹25"},
        ]
        return r

    worker.revalidate = changed
    state, replies = await runtime.chat.handle("checkout", state)
    assert state["view"]["phase"] == "review" and state["view"]["payment_preparation"] is None
    assert "latest bill" in replies[0]["text"]["body"]
    assert "Small cart charge" in replies[1]["text"]["body"]
    stale = f"checkout:{old['view']['version']}:{old['view']['review']['id']}"
    with pytest.raises(DomainError):
        await runtime.chat.handle("approve", state, stale)
    state, replies = await runtime.chat.handle("checkout", state)
    assert state["view"]["payment_preparation"]["total_paise"] == 8100
    from easydial.controller import Session

    current = Session(SessionView.model_validate(state["view"]))
    current.edit(
        EditRequest(
            kind="set_quantity",
            expected_version=current.view.version,
            line_id=current.view.lines[0].id,
            quantity=1,
        )
    )
    assert current.view.payment_preparation is None
    runtime.inbox.close()


async def test_revalidate_does_not_call_cart_mutations():
    from contextlib import asynccontextmanager

    from easydial.models import BasketLine
    from tests.fakes import FixtureProvider

    offer = (await FixtureProvider(Provider.BLINKIT).search("milk", "home")).offers[0]
    view = SessionView(
        id="known", mode="live", address_label="home", lines=[BasketLine(offer=offer, quantity=1)]
    )
    worker = BlinkitProvider("", "home", "home")
    worker.guard.owner = "known"
    worker.guard.expected = ((normalized(offer.name), normalized(offer.pack_size), 1),)

    @asynccontextmanager
    async def page():
        yield None, None

    async def navigate(*args):
        pass

    async def cart(*args):
        return {
            "items": [{"name": offer.name, "pack": offer.pack_size, "quantity": 1}],
            "address": "home",
            "total": "₹28",
            "bill": [{"label": "Items total", "value": "₹28"}],
        }

    async def forbid(*args):
        raise AssertionError("mutation called")

    worker.browser_page = page
    worker.navigate = navigate
    worker.read_cart = cart
    worker.set_quantity = forbid
    assert (await worker.revalidate(view)).total_paise == 2800


async def test_checkpoint_restores_ownership_but_rejects_external_cart(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    state, _ = await runtime.chat.handle("review", state)
    worker = runtime.chat.workers.workers[0]
    worker.guard.owner = None
    runtime.chat.workers.restore_checkpoint(state["cart_checkpoint"], state["view"]["id"])
    worker.guard.check(state["view"]["id"], worker.guard.expected)
    with pytest.raises(DomainError):
        worker.guard.check(state["view"]["id"], (("other", "pack", 1),))
    with pytest.raises(DomainError):
        runtime.chat.workers.restore_checkpoint(state["cart_checkpoint"], "different")
    runtime.inbox.close()


async def test_failed_recheck_revokes_previous_preparation(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    state, _ = await runtime.chat.handle("review", state)
    state, _ = await runtime.chat.handle("checkout", state)
    assert state["view"]["payment_preparation"]

    async def fail(view):
        raise DomainError("Store unavailable")

    runtime.chat.workers.workers[0].revalidate = fail
    with pytest.raises(DomainError):
        await runtime.chat.handle("checkout", state)
    assert state["view"]["payment_preparation"] is None
    runtime.inbox.close()


def test_unreconciled_review_does_not_delegate_retry_or_offer_payment():
    from easydial.controller import Session
    from easydial.whatsapp.conversation import ShoppingChat

    bill = review([
        ("Item Total", "₹85"), ("Handling Fee", "₹12"),
        ("Small Cart Fee", "₹23.60"), ("Delivery Partner Fee", "₹35.70"),
    ], 8500, 15600)
    session = Session(SessionView(id="test", mode="live", address_label="test", review=bill))
    replies = ShoppingChat.review_messages(session)
    assert not breakdown(bill)["reconciled"]
    assert "choose how to pay" not in replies[0]["text"]["body"]
    assert len(replies) == 2
    assert "Recheck total" not in replies[0]["text"]["body"]
    assert "basket is saved" in replies[0]["text"]["body"]
    bill.total_paise = 15630
    replies = ShoppingChat.review_messages(session)
    assert replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Choose payment"


@pytest.mark.parametrize("recovers", [True, False])
async def test_checkout_rechecks_mismatched_bill_without_cart_writes(tmp_path, recovers):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    state, _ = await runtime.chat.handle("review", state)
    state["view"]["review"]["total_paise"] += 30
    calls = []
    worker = runtime.chat.workers.workers[0]
    original = worker.revalidate

    async def revalidate(view):
        calls.append(True)
        bill = await original(view)
        if not recovers or len(calls) == 1:
            bill.total_paise += 30
        return bill

    async def forbid(*args):
        raise AssertionError("cart writes must not be retried")

    worker.revalidate = revalidate
    runtime.chat.workers.review = forbid
    before = copy.deepcopy(state["view"]["lines"])
    state, replies = await runtime.chat.handle("checkout", state)
    assert len(calls) == (2 if recovers else 3)
    assert state["view"]["lines"] == before
    assert state["view"]["payment_preparation"] is None
    assert not any("Review again" in str(r) or "Recheck total" in str(r) for r in replies)
    if recovers:
        assert replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Choose payment"
    else:
        assert replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Change basket"
    runtime.inbox.close()

async def test_initial_review_recovers_bill_without_resyncing_cart(tmp_path):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    worker = runtime.chat.workers.workers[0]
    original_review = worker.review
    calls = []

    async def first(view):
        calls.append("sync")
        bill = await original_review(view)
        bill.total_paise += 30
        return bill

    async def refresh(view):
        calls.append("read")
        assert view.review is not None
        return await original_review(view)

    worker.review, worker.revalidate = first, refresh
    state, replies = await runtime.chat.handle("review", state)
    assert calls == ["sync", "read"]
    assert replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Choose payment"
    assert state["view"]["payment_preparation"] is None
    runtime.inbox.close()


@pytest.mark.parametrize("choice", ["change_basket", "save_basket"])
async def test_blocked_bill_buttons_preserve_basket_and_do_not_call_provider(tmp_path, choice):
    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle("milk", runtime.chat.initial())
    state, _ = await runtime.chat.handle("1", state)
    state, _ = await runtime.chat.handle("review", state)
    state["view"]["review"]["total_paise"] += 30
    before = copy.deepcopy(state)

    async def forbid(*args, **kwargs):
        raise AssertionError("No interpretation or provider operation expected")

    runtime.chat.interpret = forbid
    runtime.chat.workers.review = forbid
    runtime.chat.workers.revalidate = forbid
    action = f"{choice}:{state['view']['version']}:{state['view']['review']['id']}"
    state, replies = await runtime.chat.handle("button", state, action)
    assert state == before
    assert "voice note" in replies[0]["text"]["body"] if choice == "change_basket" else "whenever" in replies[0]["text"]["body"]
    with pytest.raises(DomainError):
        await runtime.chat.handle("button", state, action + "old")
    runtime.inbox.close()
