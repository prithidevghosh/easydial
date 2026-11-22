import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easydial.models import Provider, Review, SessionView
from easydial.settings import Settings
from easydial.whatsapp.accounts import CustomerChats
from easydial.whatsapp.payments import PaymentRequests
from easydial.whatsapp.storage import Inbox


def view(provider='blinkit', order_id='old'):
    return SessionView(id=order_id, mode='live', address_label='Home', review=Review(
        version=0, provider=provider, address_label='Home', item_subtotal_paise=100,
        fees_paise=0, total_paise=100, payment_method='upi', mode='live'))


@pytest.fixture
def policy(tmp_path):
    inbox = Inbox(str(tmp_path / 'inbox.db'))
    payments = PaymentRequests(inbox, 'https://example.test')
    for sender in ('alice', 'bob'):
        inbox.finish({'sender': sender, 'id': sender}, {'view': view(order_id='current').model_dump(mode='json')}, [])
    return payments


def attempt(policy, *, sender='alice', method='upi', status='pending', provider='blinkit', result=None):
    policy.inbox.db.execute('INSERT INTO order_checks VALUES (?,?,?,?,?,?,?,?,?)',
        (sender, 'old', method, view(provider).model_dump_json(), '[]', 0, status,
         json.dumps(result) if result is not None else None, 0))
    policy.inbox.db.commit()


def payment(policy, *, table='payment_requests', status='unknown', sender='alice'):
    if table == 'payment_requests':
        policy.inbox.db.execute('INSERT INTO payment_requests VALUES (?,?,?,?,?,?,?,?,?)',
            ('request', sender, 'old', 'bill', status, 'hash', None, 0, 1))
    else:
        policy.inbox.db.execute('INSERT INTO cash_orders VALUES (?,?,?,?,?,?)',
            (sender, 'old', status, 'bill', None, 0))
    policy.inbox.db.commit()


@pytest.mark.parametrize('status', ['creating', 'ready', 'unknown', 'revoked'])
def test_old_expired_or_revoked_payment_still_blocks(policy, status):
    attempt(policy)
    payment(policy, status=status)
    assert not policy.proxy_recovery_allowed('alice')
    assert policy.proxy_recovery_allowed('bob')


@pytest.mark.parametrize('table,status', [('payment_requests', 'creating'), ('cash_orders', 'submitting')])
def test_restart_unknown_attempt_remains_blocked(policy, table, status):
    attempt(policy, method='cash' if table == 'cash_orders' else 'upi')
    payment(policy, table=table, status=status)
    restarted = PaymentRequests(policy.inbox, 'https://example.test')
    assert restarted.inbox.db.execute(f'SELECT status FROM {table}').fetchone()[0] == 'unknown'
    assert not restarted.proxy_recovery_allowed('alice')


@pytest.mark.parametrize('status', ['prepared', 'pending', 'not_found', 'unexpected'])
def test_unresolved_monitor_alone_blocks(policy, status):
    attempt(policy, status=status)
    assert not policy.proxy_recovery_allowed('alice')


@pytest.mark.parametrize('status,merchant', [('confirmed', 'delivered'), ('failed', 'cancelled')])
def test_verified_terminal_attempt_allows_new_journey(policy, status, merchant):
    attempt(policy, status=status, result={'status': status, 'order': {'order_id': 'verified', 'status': merchant}})
    payment(policy)
    assert policy.proxy_recovery_allowed('alice')


def test_terminal_label_without_evidence_does_not_allow(policy):
    attempt(policy, status='confirmed')
    assert not policy.proxy_recovery_allowed('alice')


def test_abandoned_observation_without_payment_allows(policy):
    attempt(policy, method='observe')
    assert policy.proxy_recovery_allowed('alice')
    payment(policy, status='revoked')
    assert not policy.proxy_recovery_allowed('alice')


@pytest.mark.parametrize('key', ['payment_menu', 'cash_confirmation', 'retry_confirmation'])
def test_active_menu_blocks_without_payment(policy, key):
    state = policy.inbox.load('alice')
    state[key] = {'active': True}
    policy.inbox.db.execute('UPDATE customers SET state=? WHERE sender=?', (json.dumps(state), 'alice'))
    assert not policy.proxy_recovery_allowed('alice')


def test_durable_checkout_phase_blocks(policy):
    state = policy.inbox.load('alice')
    state['view']['phase'] = 'checkout_pending'
    policy.inbox.finish({'sender': 'alice', 'id': 'pending'}, state, [])
    assert not policy.proxy_recovery_allowed('alice')


def test_other_provider_attempt_does_not_block_blinkit(policy):
    attempt(policy, provider='instamart')
    payment(policy)
    assert policy.proxy_recovery_allowed('alice')


def test_missing_or_corrupt_evidence_fails_closed(policy):
    assert not policy.proxy_recovery_allowed('missing')
    payment(policy)
    assert not policy.proxy_recovery_allowed('alice')
    policy.inbox.db.execute("UPDATE customers SET state='invalid' WHERE sender='bob'")
    assert not policy.proxy_recovery_allowed('bob')
    policy.inbox.db.close()
    assert not policy.proxy_recovery_allowed('alice')


async def test_bound_callback_reads_live_checkpoint_and_current_policy(monkeypatch, policy):
    worker = SimpleNamespace(provider=Provider.BLINKIT, browser=SimpleNamespace())
    monkeypatch.setattr('easydial.whatsapp.accounts.ProviderWorkers', lambda config: SimpleNamespace(workers=[worker]))
    accounts = SimpleNamespace(
        connections=lambda sender: {'blinkit': {'location': 'location', 'address': 'Home'}},
        consented=lambda *args: True, session_path=lambda *args: 'saved-session',
        session_box=lambda *args: object(), save_connection=Mock())
    chats = CustomerChats(Settings(_env_file=None), accounts, None, policy)
    await chats.get('alice')
    callback = worker.browser.proxy_recovery_allowed
    assert callback()
    worker._payment_checkpoint = {'page': object()}
    assert not callback()
    worker._payment_checkpoint = None
    attempt(policy)
    assert not callback()


def ended_journey(policy):
    attempt(policy, status='not_found')
    payment(policy, status='revoked')
    policy.inbox.db.execute('INSERT INTO automatic_order_checks VALUES (?,?,?,?,?)',
                           ('alice', 'old', 0, 3, 1))
    policy.inbox.db.commit()


def test_explicitly_ended_old_journey_can_recover_without_changing_payment_outcome(policy):
    ended_journey(policy)
    before = [tuple(row) for row in policy.inbox.db.execute('SELECT * FROM order_checks')]
    assert policy.proxy_recovery_allowed('alice')
    restarted = PaymentRequests(policy.inbox, 'https://example.test')
    assert restarted.proxy_recovery_allowed('alice')
    assert before == [tuple(row) for row in policy.inbox.db.execute('SELECT * FROM order_checks')]
    assert restarted.existing('alice', 'old')['status'] == 'revoked'


@pytest.mark.parametrize('change', ['current_draft', 'running_check', 'missing_check', 'pending',
                                  'unknown', 'ready', 'cash', 'active_menu'])
def test_ended_journey_exception_requires_every_gate(policy, change):
    ended_journey(policy)
    db = policy.inbox.db
    if change == 'current_draft':
        state = policy.inbox.load('alice')
        state['view']['id'] = 'old'
        policy.inbox.finish({'sender': 'alice', 'id': 'new-current'}, state, [])
    elif change == 'running_check':
        db.execute('UPDATE automatic_order_checks SET done=0')
    elif change == 'missing_check':
        db.execute('DELETE FROM automatic_order_checks')
    elif change == 'pending':
        db.execute("UPDATE order_checks SET status='pending'")
    elif change in {'unknown', 'ready'}:
        db.execute('UPDATE payment_requests SET status=?', (change,))
    elif change == 'cash':
        payment(policy, table='cash_orders')
    else:
        state = policy.inbox.load('alice')
        state['payment_menu'] = {'active': True}
        db.execute('UPDATE customers SET state=? WHERE sender=?', (json.dumps(state), 'alice'))
    assert not policy.proxy_recovery_allowed('alice')
