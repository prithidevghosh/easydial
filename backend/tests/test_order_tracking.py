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


async def test_pending_after_ten_checks_then_late_confirmation_survives_restart(tmp_path):
    runtime = make_runtime(tmp_path)
    view = SessionView.model_validate((await reviewed(runtime))["view"])
    monitor = runtime.payments.monitor
    worker = SimpleNamespace(order_history=empty_history)
    await monitor.capture("alice", view, "cash", worker)
    monitor.activate("alice", view.id)
    result = await monitor.check("alice", view.id, worker, sleep=no_sleep)
    assert result["status"] == "pending"
    assert monitor.get("alice", view.id)["checks"] == 10
    assert (await monitor.check("bob", view.id, worker))["status"] == "pending"

    async def history(*args):
        return {"ids": ["new"], "orders": [merchant_order(view)]}

    worker.order_history = history
    monitor = OrderMonitor(runtime.inbox)
    assert (await monitor.check("alice", view.id, worker))["status"] == "confirmed"
    assert monitor.get("alice", view.id)["checks"] == 11
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
    assert "hasn’t confirmed" in replies[0]["text"]["body"]
    assert replies[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Check status"
    await runtime.chat.handle("Check status", state, f"status:0:{view.id}", customer="alice")
    await runtime.chat.handle("paid", state, customer="bob")
    assert calls == [("alice", view.id)] * 2
    runtime.inbox.close()
