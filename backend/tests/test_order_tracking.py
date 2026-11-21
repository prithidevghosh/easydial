import time
from types import SimpleNamespace

import pytest

from easydial.models import SessionView
from easydial.whatsapp.order_tracking import OrderMonitor, matches
from tests.test_payment_handoff import reviewed
from tests.test_whatsapp import make_runtime


async def empty_history(*args):
    return {"ids": [], "orders": []}


def merchant_order(view, **changes):
    return (
        dict(
            order_id="merchant12345",
            history_id="new",
            provider=view.review.provider.value,
            placed_at=time.time(),
            total_paise=view.review.total_paise,
            address=view.review.address_label,
            items=[
                dict(
                    name=x.offer.name,
                    pack=x.offer.pack_size,
                    quantity=x.quantity,
                    catalog_id=x.offer.sku_id.split(":")[0],
                )
                for x in view.lines
            ],
            method="cash",
            payment_status="pending",
            status="confirmed",
            eta=None,
        )
        | changes
    )


async def no_sleep(seconds):
    assert seconds == 3


@pytest.mark.parametrize(
    "change",
    [
        {"history_id": "old"},
        {"total_paise": 1},
        {"address": "wrong address"},
        {"items": []},
        {"placed_at": 0},
        {"placed_at": time.time() + 3600},
        {"provider": "unrelated"},
        {"method": "upi"},
    ],
)
async def test_unrelated_or_old_orders_cannot_confirm(tmp_path, change):
    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    assert not matches(merchant_order(view, **change), view, "cash", ["old"], time.time())
    runtime.inbox.close()


async def test_pending_after_three_checks_then_late_confirmation_survives_restart(tmp_path):
    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "cash", worker)
    monitor.activate("alice", view.id)
    result = await monitor.check("alice", view.id, worker, sleep=no_sleep)
    assert result["status"] == "not_found"
    assert monitor.get("alice", view.id)["checks"] == 3
    assert (await monitor.check("bob", view.id, worker))["status"] == "pending"

    async def history(*args):
        return {"ids": ["new"], "orders": [merchant_order(view)]}

    worker.order_history = history
    monitor = OrderMonitor(runtime.inbox)
    assert (await monitor.check("alice", view.id, worker))["status"] == "confirmed"
    assert monitor.get("alice", view.id)["checks"] == 4
    runtime.inbox.close()


@pytest.mark.parametrize(
    "paid,status,expected",
    [(False, "confirmed", "pending"), (True, "confirmed", "confirmed"), (True, "cancelled", "failed")],
)
async def test_qr_requires_payment_and_order_confirmation(tmp_path, paid, status, expected):
    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "upi", worker)
    monitor.activate("alice", view.id)

    async def history(*args):
        return {
            "orders": [
                merchant_order(
                    view, method="online", payment_status="paid" if paid else "pending", status=status
                )
            ]
        }

    worker.order_history = history
    assert (await monitor.check("alice", view.id, worker, sleep=no_sleep))["status"] == expected
    runtime.inbox.close()


async def test_ambiguous_matches_and_browser_errors_stay_pending(tmp_path):
    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "cash", worker)
    monitor.activate("alice", view.id)

    async def history(*args):
        return {"orders": [merchant_order(view), merchant_order(view, order_id="another")]}

    worker.order_history = history
    assert (await monitor.check("alice", view.id, worker, sleep=no_sleep))["status"] == "pending"

    async def blocked(*args):
        raise TimeoutError()

    worker.order_history = blocked
    assert (await monitor.check("alice", view.id, worker, sleep=no_sleep))["status"] == "pending"
    runtime.inbox.close()


def test_instamart_parser_uses_milliseconds_catalog_identity_and_cash_not_paid():
    import json

    from easydial.providers.order_history import parse_instamart

    row = {
        "order_id": "test-order",
        "created_at": 1_790_000_000_000,
        "history_status": "DELIVERED",
        "order_data": {
            "orders": [
                {
                    "order_jobs": [
                        {
                            "metadata": json.dumps(
                                {
                                    "address": {"flatNo": "1", "address": "Test home"},
                                    "items": [{"name": "Milk", "quantity": 1, "itemId": "SKU1"}],
                                }
                            )
                        }
                    ]
                }
            ],
            "payment_info_v2": {
                "payment_transaction_reference": [{"primary_payment_method": "Cash", "state": "SUCCESS"}]
            },
        },
        "order_data_v2": {"total": {"currency_code": "INR", "units": 28, "nanos": 0}},
    }
    result = parse_instamart(row)
    assert result["placed_at"] == 1_790_000_000
    assert result["items"][0]["catalog_id"] == "SKU1"
    assert result["total_paise"] == 2800
    assert result["status"] == "delivered"
    assert result["payment_status"] == "pending"


def test_blinkit_cancelled_paid_order_is_not_a_success():
    from easydial.providers.order_history import parse_blinkit

    card = {
        "identity": {"id": "history-test"},
        "items": [{"data": {"title": {"text": "Order cancelled"}, "subtitle": {"text": "21 Nov, 2:43 am"}}}],
    }
    result = parse_blinkit(
        card,
        "1 item in this order\nTest Milk\n500 ml x 2\nBill total\n₹56\nOrder id\nORDTEST\nPayment\nPaid Online\nDeliver to\nTest home",
    )
    assert result["status"] == "cancelled"
    assert result["payment_status"] == "paid"
    assert result["items"] == [{"name": "Test Milk", "pack": "500 ml", "quantity": 2}]
    assert result["history_id"] == "history-test"


async def test_paid_claim_and_old_status_button_only_read_history(tmp_path):
    runtime = make_runtime(tmp_path)
    state = await reviewed(runtime)
    view = SessionView.model_validate(state["view"])
    with runtime.inbox.db:
        runtime.inbox.db.execute(
            "INSERT INTO cash_orders VALUES (?,?,?,?,?,?)", ("alice", view.id, "pending", "test", None, 0)
        )
    calls = []

    async def check(sender, order_id, workers):
        calls.append((sender, order_id))
        return {"status": "pending"}

    runtime.payments.monitor.check = check
    _, replies = await runtime.chat.handle("paid", state, customer="alice")
    assert "couldn’t verify" in replies[0]["text"]["body"]
    assert replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Check status"
    await runtime.chat.handle("Check status", state, f"status:0:{view.id}", customer="alice")
    await runtime.chat.handle("paid", state, customer="bob")
    assert calls == [("alice", view.id)] * 2
    runtime.inbox.close()


@pytest.mark.parametrize(
    "card_title,detail_title,expected",
    [
        ("Order update", "Arriving in 9 minutes", "confirmed"),
        ("Order update", "Delivered", "delivered"),
        ("Arriving in 9 minutes", "Order cancelled", "cancelled"),
        ("Order cancelled", "Arriving in 9 minutes", "cancelled"),
    ],
)
def test_blinkit_detail_status_and_terminal_cancellation(card_title, detail_title, expected):
    from easydial.providers.order_history import parse_blinkit

    card = {
        "identity": {"id": "new-order"},
        "items": [{"data": {"title": {"text": card_title}, "subtitle": {"text": "21 Nov, 7:40 pm"}}}],
    }
    result = parse_blinkit(
        card,
        f"""Order summary
{detail_title}
1 item in this order
Britannia Milk Bikis Milky Sandwich Creme Biscuits
188 g x 1
₹50
Bill details
MRP
₹55
Product discount
-₹5
Item total
₹50
Handling charge
+₹13
Small cart fee
+₹25
Delivery charges
+₹35
Bill total
₹123
Order details
Order id
TEST-ORDER
Payment
Paid Online
Deliver to
Test home""",
    )
    assert result["status"] == expected
    assert result["total_paise"] == 12300
    assert result["items"][0]["pack"] == "188 g"
    assert result["payment_status"] == "paid"


async def test_matching_paid_order_stops_on_first_check(tmp_path):
    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "upi", worker)
    monitor.activate("alice", view.id)
    calls = 0

    async def history(*args):
        nonlocal calls
        calls += 1
        return {"orders": [merchant_order(view, method="online", payment_status="paid")]}

    async def unexpected_sleep(seconds):
        pytest.fail("Confirmed orders must not be polled again")

    worker.order_history = history
    assert (await monitor.check("alice", view.id, worker, sleep=unexpected_sleep))["status"] == "confirmed"
    assert calls == 1
    assert monitor.get("alice", view.id)["checks"] == 1
    runtime.inbox.close()


async def test_pending_retains_safe_rejection_diagnostics(tmp_path):
    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "upi", worker)
    monitor.activate("alice", view.id)

    async def history(*args):
        return {"orders": [merchant_order(view, placed_at=None)], "issues": ["unreadable_history_link"]}

    worker.order_history = history
    result = await monitor.check("alice", view.id, worker, sleep=no_sleep)
    assert result["reason"] == "no_matching_order"
    assert result["rejected_fields"] == ["placed_at"]
    assert result["history_issues"] == ["unreadable_history_link"]
    assert "order" not in result
    runtime.inbox.close()


@pytest.mark.parametrize("stamp,day,hour", [
    ("Today, 9:22 pm", 21, 21),
    ("placed today, 9:22 PM", 21, 21),
    ("Yesterday, 9:22 pm", 20, 21),
    ("Today, 12:22 am", 21, 0),
    ("21 Nov, 9:22 pm", 21, 21),
])
def test_blinkit_relative_history_dates(stamp, day, hour):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from easydial.providers.order_history import blinkit_placed_at

    current = datetime(2026, 11, 21, 21, 23, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert blinkit_placed_at(stamp, current=current) == current.replace(day=day, hour=hour, minute=22).timestamp()


@pytest.mark.parametrize("stamp", ["Today", "Today, 99:22 pm", "unknown", ""])
def test_blinkit_invalid_dates_are_not_assumed_current(stamp):
    from easydial.providers.order_history import blinkit_placed_at
    assert blinkit_placed_at(stamp) is None


async def test_blinkit_today_paid_multiline_order_matches_and_confirms(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from easydial.providers.order_history import parse_blinkit

    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "upi", worker)
    monitor.activate("alice", view.id)
    stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("Today, %I:%M %p")
    card = {"identity": {"id": "new"}, "items": [{"data": {"title": {"text": "Arriving in 8 minutes"}, "subtitle": {"text": stamp}}}]}
    lines = '\n'.join(f'{x.offer.name}\n{x.offer.pack_size} x {x.quantity}' for x in view.lines)
    text = f'Order summary\nArriving in 8 minutes\n{len(view.lines)} items in this order\n{lines}\nBill total\n₹{view.review.total_paise/100:.2f}\nOrder id\nNEW123\nPayment\nPaid Online\nDeliver to\n{view.review.address_label}'
    order = parse_blinkit(card, text)
    # Fixture is Blinkit; exercise the actual parser through the matching monitor.
    async def history(*args):
        return {"orders": [order]}
    worker.order_history = history
    assert (await monitor.check("alice", view.id, worker, sleep=no_sleep))["status"] == "confirmed"
    assert monitor.get("alice", view.id)["checks"] == 1


@pytest.mark.parametrize("refresh", ["delivered", "cancelled", "missing", "error", "unrelated"])
async def test_confirmed_order_refreshes_delivery_without_reusing_eta(tmp_path, refresh):
    from easydial.whatsapp.order_tracking import status_messages

    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "cash", worker)
    monitor.activate("alice", view.id)
    original = merchant_order(view, eta="Arriving in 7 minutes")

    async def confirmed(*args):
        return {"orders": [original]}

    worker.order_history = confirmed
    assert (await monitor.check("alice", view.id, worker))["status"] == "confirmed"
    calls = 0

    async def updated(*args):
        nonlocal calls
        calls += 1
        if refresh == "error":
            raise TimeoutError()
        if refresh == "missing":
            return {"orders": []}
        return {"orders": [{**original, "status": refresh if refresh != "unrelated" else "delivered",
                            "order_id": "different" if refresh == "unrelated" else original["order_id"]}]}

    worker.order_history = updated
    result = await OrderMonitor(runtime.inbox).check("alice", view.id, worker)
    message = status_messages(result, view)[0]["text"]["body"]
    assert calls == 1
    assert "Arriving in 7 minutes" not in message
    if refresh == "delivered":
        assert "Order delivered" in message
        assert "Pay cash on delivery" not in message
    elif refresh == "cancelled":
        assert result["status"] == "failed"
    else:
        assert result["status"] == "confirmed"
        assert result["stale"]
        assert "couldn’t refresh delivery status" in message
        assert monitor.get("alice", view.id)["status"] == "confirmed"
    runtime.inbox.close()


@pytest.mark.parametrize("outcome", ["confirmed", "pending", "logout"])
async def test_automatic_check_is_delayed_durable_and_notifies_once(tmp_path, outcome):
    import json

    from easydial.whatsapp.payments import PaymentRequests

    runtime = make_runtime(tmp_path)
    state = await reviewed(runtime)
    view = SessionView.model_validate(state["view"])
    runtime.inbox.enqueue({"id": "setup", "from": "alice", "timestamp": int(time.time()), "type": "text", "text": {"body": "hi"}})
    row = runtime.inbox.claim()
    runtime.inbox.finish(row, state, [])
    worker = runtime.chat.workers
    worker.order_history = empty_history
    await runtime.payments.monitor.capture("alice", view, "cash", worker)
    runtime.payments.monitor.activate("alice", view.id)
    runtime.payments.schedule_check("alice", view.id)
    job = runtime.inbox.db.execute("SELECT * FROM automatic_order_checks").fetchone()
    assert job["due_at"] >= time.time() + 4
    assert not await runtime.check_due_order()
    runtime.payments = PaymentRequests(runtime.inbox, "https://example.test")
    calls = 0

    async def history(*args):
        nonlocal calls
        calls += 1
        return {"orders": [merchant_order(view)] if outcome == "confirmed" else []}

    worker.order_history = history
    if outcome == "logout":
        runtime.payments.revoke_customer("alice")
    with runtime.inbox.db:
        runtime.inbox.db.execute("UPDATE automatic_order_checks SET due_at=0")
    await runtime.check_due_order()
    messages = [json.loads(r[0]) for r in runtime.inbox.db.execute("SELECT payload FROM outbox WHERE event_id LIKE 'auto-status:%'")]
    assert calls == (0 if outcome == "logout" else 1)
    assert len(messages) == (1 if outcome == "confirmed" else 0)
    assert not await runtime.check_due_order()
    if outcome == "pending":
        with runtime.inbox.db:
            runtime.inbox.db.execute("UPDATE automatic_order_checks SET attempts=2,due_at=0")
        await runtime.check_due_order()
        assert runtime.inbox.db.execute("SELECT done FROM automatic_order_checks").fetchone()[0] == 1
    runtime.inbox.close()


@pytest.mark.parametrize('utterance', ['check status', 'payment confirm kijiye please', 'checkout'])
async def test_no_payment_attempt_advances_basket_to_bill_and_button(tmp_path, utterance):
    from unittest.mock import AsyncMock

    from tests.test_whatsapp import intent

    runtime = make_runtime(tmp_path)
    state, _ = await runtime.chat.handle('milk', runtime.chat.initial())
    state, _ = await runtime.chat.handle('1', state)
    runtime.chat.interpret = AsyncMock(return_value=intent('status'))
    runtime.payments.monitor.check = AsyncMock(side_effect=AssertionError('No attempt to check'))
    runtime.payments.create = AsyncMock(side_effect=AssertionError('Must not create payment'))
    runtime.payments.place_cash = AsyncMock(side_effect=AssertionError('Must not order'))
    state, replies = await runtime.chat.handle(utterance, state, customer='alice')
    assert state['view']['review']
    assert any('TOTAL PAYABLE' in x.get('text', {}).get('body', '') for x in replies)
    titles = [b['reply']['title'] for x in replies for b in x.get('interactive', {}).get('action', {}).get('buttons', [])]
    assert 'Choose payment' in titles
    assert not runtime.payments.existing('alice', state['view']['id'])
    runtime.inbox.close()


async def test_no_attempt_and_empty_basket_asks_for_products(tmp_path):
    runtime = make_runtime(tmp_path)
    state, replies = await runtime.chat.handle('check status', runtime.chat.initial(), customer='alice')
    assert replies[0]['text']['body'] == 'Your basket is empty. What would you like to order?'
    assert not state['view']['review']
    runtime.inbox.close()


async def test_repeat_payment_request_reconciles_existing_attempt_without_resubmitting(tmp_path):
    from unittest.mock import AsyncMock, Mock

    runtime = make_runtime(tmp_path)
    state = await reviewed(runtime)
    runtime.payments.existing = Mock(return_value={'status': 'pending'})
    runtime.payments.monitor.check = AsyncMock(return_value={'status': 'pending'})
    runtime.payments.place_cash = AsyncMock(side_effect=AssertionError('Duplicate submission'))
    runtime.payments.create = AsyncMock(side_effect=AssertionError('Duplicate payment'))
    _, replies = await runtime.chat.handle('checkout', state, customer='alice')
    runtime.payments.monitor.check.assert_awaited_once()
    assert not any('already exists' in x.get('text', {}).get('body', '') for x in replies)
    runtime.payments.place_cash.assert_not_awaited()
    runtime.payments.create.assert_not_awaited()
    runtime.inbox.close()


async def test_three_clean_empty_history_reads_stop_without_claiming_failure(tmp_path):
    from easydial.whatsapp.order_tracking import status_messages

    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))['view'])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture('alice', view, 'cash', worker)
    monitor.activate('alice', view.id)
    for index in range(3):
        result = await monitor.check('alice', view.id, worker, max_checks=1)
        assert result['status'] == ('not_found' if index == 2 else 'pending')
    replies = status_messages(result, view)
    assert 'Automatic checking has stopped' in replies[0]['text']['body']
    assert 'https://' in replies[0]['text']['body']
    assert not any(x.get('type') == 'interactive' for x in replies)
    runtime.inbox.close()
