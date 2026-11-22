import json
import time

from easydial.models import SessionView
from easydial.whatsapp.order_completion import completed_state
from easydial.whatsapp.payments import PaymentRequests
from tests.test_order_tracking import merchant_order
from tests.test_payment_handoff import reviewed
from tests.test_whatsapp import make_runtime


async def persisted(runtime):
    state = await reviewed(runtime)
    runtime.inbox.enqueue({'id': 'setup', 'from': 'alice', 'timestamp': int(time.time()),
                           'type': 'text', 'text': {'body': 'hi'}})
    runtime.inbox.finish(runtime.inbox.claim(), state, [])
    return state, SessionView.model_validate(state['view'])


async def test_completion_subtracts_only_purchased_quantities_and_preserves_new_draft(tmp_path):
    runtime = make_runtime(tmp_path)
    state, purchased = await persisted(runtime)
    state['view']['lines'][0]['quantity'] += 2
    state['cart_checkpoint'] = state['view'].copy()
    result = completed_state(state, purchased)
    assert result['view']['id'] != purchased.id
    assert result['view']['lines'][0]['quantity'] == 2
    assert result['view']['review'] is None
    assert 'cart_checkpoint' not in result
    assert result['last_order_id'] == purchased.id
    assert completed_state(result, purchased) == result
    runtime.inbox.close()


async def test_confirmed_purchase_cleans_draft_notifies_once_and_old_status_still_works(tmp_path):
    runtime = make_runtime(tmp_path)
    state, view = await persisted(runtime)
    worker = runtime.chat.workers
    await runtime.payments.watch_menu('alice', view, worker)
    runtime.payments.schedule_check('alice', view.id)
    order = merchant_order(view, method='online', payment_status='paid')

    async def history(*args):
        return {'ids': ['new'], 'orders': [order]}

    worker.order_history = history
    with runtime.inbox.db:
        runtime.inbox.db.execute('UPDATE automatic_order_checks SET due_at=0')
    await runtime.check_due_order()
    result = runtime.inbox.load('alice')
    assert result['view']['lines'] == []
    assert result['view']['id'] != view.id
    assert result['last_order_id'] == view.id
    assert runtime.inbox.db.execute('SELECT status FROM order_drafts WHERE order_id=?', (view.id,)).fetchone()[0] == 'completed'
    assert runtime.chat.status_view(result, 'alice').id == view.id
    assert runtime.chat.status_view(result, 'someone-else', view.id) is None
    count = runtime.inbox.db.execute('SELECT count(*) FROM outbox').fetchone()[0]
    runtime.payments = PaymentRequests(runtime.inbox, 'https://example.test')
    assert not await runtime.check_due_order()
    assert runtime.inbox.db.execute('SELECT count(*) FROM outbox').fetchone()[0] == count == 1
    runtime.inbox.close()


async def test_observation_stops_after_three_checks_without_claiming_payment_failure(tmp_path):
    runtime = make_runtime(tmp_path)
    _, view = await persisted(runtime)
    await runtime.payments.watch_menu('alice', view, runtime.chat.workers)
    runtime.payments.schedule_check('alice', view.id)
    for attempt in range(3):
        with runtime.inbox.db:
            runtime.inbox.db.execute('UPDATE automatic_order_checks SET due_at=0')
        await runtime.check_due_order()
        row = runtime.inbox.db.execute('SELECT * FROM automatic_order_checks').fetchone()
        assert row['attempts'] == attempt + 1
        assert row['due_at'] >= time.time() + 2
        assert bool(row['done']) == (attempt == 2)
    assert runtime.payments.monitor.get('alice', view.id)['status'] == 'pending'
    assert not await runtime.check_due_order()
    assert runtime.inbox.load('alice')['view']['lines']
    assert runtime.inbox.db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
    # Choosing a real payment after the watcher finishes creates a fresh monitored attempt.
    await runtime.payments.monitor.capture('alice', view, 'upi', runtime.chat.workers)
    assert runtime.payments.monitor.get('alice', view.id)['method'] == 'upi'
    runtime.inbox.close()


async def test_restart_recovers_confirmation_before_cleanup(tmp_path):
    runtime = make_runtime(tmp_path)
    _, view = await persisted(runtime)
    await runtime.payments.watch_menu('alice', view, runtime.chat.workers)
    result = {'status': 'confirmed', 'order': merchant_order(view)}
    with runtime.inbox.db:
        runtime.inbox.db.execute("UPDATE order_checks SET status='confirmed',result=?", (json.dumps(result),))
    runtime.payments = PaymentRequests(runtime.inbox, 'https://example.test')
    runtime.chat.payments = runtime.payments
    await runtime.check_due_order()
    assert runtime.inbox.load('alice')['view']['lines'] == []
    assert runtime.inbox.db.execute('SELECT count(*) FROM outbox').fetchone()[0] > 0
    runtime.inbox.close()


async def test_watch_starts_only_after_payment_menu_sent_and_internal_marker_is_not_sent(tmp_path):
    runtime = make_runtime(tmp_path)
    state, view = await persisted(runtime)
    state, replies = await runtime.chat.handle('checkout', state, customer='alice')
    assert replies[-1]['_watch_order_id'] == view.id
    assert runtime.inbox.db.execute('SELECT count(*) FROM automatic_order_checks').fetchone()[0] == 0
    runtime.inbox.finish({'id': 'menu', 'sender': 'alice'}, state, [replies[-1]])
    assert await runtime.send_one()
    assert '_watch_order_id' not in runtime.transport.sent[-1][1]
    job = runtime.inbox.db.execute('SELECT * FROM automatic_order_checks').fetchone()
    assert time.time() + 4 <= job['due_at'] <= time.time() + 5
    assert job['attempts'] == 0
    runtime.inbox.close()
