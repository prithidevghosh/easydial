"""Recovery eligibility follows durable customer transactions, not transient chat flags."""

import copy
import json
from types import SimpleNamespace

import pytest

from easydial.models import SessionView
from easydial.whatsapp.payments import PaymentRequests
from tests.test_onboarding import turn
from tests.test_order_tracking import empty_history, merchant_order, no_sleep
from tests.test_whatsapp import make_runtime

SENDER = '919999999999'
OTHER = '918888888888'


@pytest.fixture
async def recovery(tmp_path):
    runtime = make_runtime(tmp_path)
    await turn(runtime, 'milk')
    await turn(runtime, '1')
    await turn(runtime, 'review')
    try:
        yield runtime, SessionView.model_validate(runtime.inbox.load(SENDER)['view'])
    finally:
        runtime.inbox.close()


async def capture(runtime, view, method='upi', sender=SENDER):
    await runtime.payments.monitor.capture(sender, view, method, SimpleNamespace(order_history=empty_history))


def request(runtime, view, status='ready', sender=SENDER):
    with runtime.inbox.db:
        runtime.inbox.db.execute('INSERT INTO payment_requests VALUES (?,?,?,?,?,?,?,?,?)',
            ('request-' + sender, sender, view.id, 'bill', status, 'token-' + sender, None, 0, 0))


async def test_safe_reviewed_basket_allows_recovery_without_changing_any_durable_state(recovery):
    runtime, _ = recovery
    before = copy.deepcopy(runtime.inbox.load(SENDER))
    rows = list(runtime.inbox.db.iterdump())
    assert runtime.payments.proxy_recovery_allowed(SENDER)
    assert runtime.inbox.load(SENDER) == before
    assert list(runtime.inbox.db.iterdump()) == rows
    assert not runtime.payments.proxy_recovery_allowed('unknown-customer')


@pytest.mark.parametrize('method', ['upi', 'cash'])
async def test_capture_before_submission_already_blocks_rotation(recovery, method):
    runtime, view = recovery
    await capture(runtime, view, method)
    assert not runtime.payments.proxy_recovery_allowed(SENDER)
    # Restart must not erase an in-flight attempt even before a payment row exists.
    assert not PaymentRequests(runtime.inbox, '').proxy_recovery_allowed(SENDER)


@pytest.mark.parametrize('status', ['creating', 'ready', 'unknown', 'revoked', 'expired'])
async def test_expired_or_revoked_qr_never_proves_merchant_payment_cannot_settle(recovery, status):
    runtime, view = recovery
    await capture(runtime, view)
    request(runtime, view, status)
    runtime.payments.schedule_check(SENDER, view.id)
    runtime.payments.revoke_customer(SENDER)
    assert runtime.inbox.db.execute('SELECT done FROM automatic_order_checks').fetchone()[0] == 1
    assert not PaymentRequests(runtime.inbox, '').proxy_recovery_allowed(SENDER)


async def test_exhausted_automatic_checks_and_new_draft_do_not_unlock_old_payment(recovery):
    runtime, view = recovery
    await capture(runtime, view)
    request(runtime, view)
    runtime.payments.monitor.activate(SENDER, view.id)
    result = await runtime.payments.monitor.check(SENDER, view.id, SimpleNamespace(order_history=empty_history), sleep=no_sleep)
    assert result['status'] == 'not_found'
    runtime.payments.schedule_check(SENDER, view.id)
    with runtime.inbox.db:
        runtime.inbox.db.execute('UPDATE automatic_order_checks SET attempts=3,done=1')
    # Simulate the customer moving to a fresh local draft while old merchant outcome is unresolved.
    fresh = runtime.chat.initial()
    with runtime.inbox.db:
        runtime.inbox.db.execute('UPDATE customers SET state=? WHERE sender=?', (json.dumps(fresh), SENDER))
        runtime.inbox.db.execute('UPDATE order_drafts SET order_id=?,snapshot=? WHERE sender=?',
                                (fresh['view']['id'], json.dumps(fresh['view']), SENDER))
    assert not PaymentRequests(runtime.inbox, '').proxy_recovery_allowed(SENDER)


async def test_pending_payment_is_customer_scoped(recovery):
    runtime, view = recovery
    await turn(runtime, 'milk', sender=OTHER)
    await capture(runtime, view)
    request(runtime, view)
    assert not runtime.payments.proxy_recovery_allowed(SENDER)
    assert runtime.payments.proxy_recovery_allowed(OTHER)


@pytest.mark.parametrize('method', ['upi', 'cash'])
async def test_verified_terminal_order_unlocks_after_restart_but_not_status_label_alone(recovery, method):
    runtime, view = recovery
    await capture(runtime, view, method)
    request(runtime, view)
    with runtime.inbox.db:
        runtime.inbox.db.execute("UPDATE order_checks SET status='confirmed',result=?", (json.dumps({'status': 'confirmed'}),))
    assert not runtime.payments.proxy_recovery_allowed(SENDER)
    with runtime.inbox.db:
        runtime.inbox.db.execute('UPDATE order_checks SET result=?', (json.dumps({
            'status': 'confirmed', 'order': merchant_order(view, method=method)}),))
    assert PaymentRequests(runtime.inbox, '').proxy_recovery_allowed(SENDER)


async def test_observation_without_payment_is_recoverable_but_corrupt_evidence_fails_closed(recovery):
    runtime, view = recovery
    await capture(runtime, view, 'observe')
    assert runtime.payments.proxy_recovery_allowed(SENDER)
    with runtime.inbox.db:
        runtime.inbox.db.execute("UPDATE order_checks SET snapshot='not-json'")
    assert not runtime.payments.proxy_recovery_allowed(SENDER)


async def test_customer_browser_callback_rechecks_durable_policy_and_in_memory_payment_checkpoint(recovery, tmp_path):
    from easydial.models import Provider
    from easydial.whatsapp.accounts import CustomerChats

    runtime, view = recovery
    accounts = SimpleNamespace(
        connections=lambda sender: {'blinkit': {'address': 'Home', 'location': 'Locality'}},
        consented=lambda sender, provider: True,
        session_path=lambda sender, provider: str(tmp_path / (sender + '.json')),
        session_box=lambda sender, provider: None,
    )
    chats = CustomerChats(runtime.settings, accounts, runtime.chat.master, runtime.payments)
    chat = await chats.get(SENDER)
    worker = next(w for w in chat.workers.workers if w.provider == Provider.BLINKIT)
    callback = worker.browser.proxy_recovery_allowed
    before = copy.deepcopy(runtime.inbox.load(SENDER))
    assert callback()
    worker._payment_checkpoint = {'pending': True}
    assert not callback()
    worker._payment_checkpoint = None
    assert callback()
    await capture(runtime, view)
    assert not callback()  # Same cached chat observes a newly persisted payment attempt.
    assert runtime.inbox.load(SENDER) == before
    await chats.close_customer(SENDER, save=False)
    reopened = await chats.get(SENDER)
    new_worker = next(w for w in reopened.workers.workers if w.provider == Provider.BLINKIT)
    assert new_worker is not worker and not new_worker.browser.proxy_recovery_allowed()
    await chats.close_customer(SENDER, save=False)


@pytest.mark.parametrize('payment_appears_during_verification', [False, True])
async def test_browser_recovery_rechecks_persisted_payment_before_adopting_new_pin(
        recovery, monkeypatch, payment_appears_during_verification):
    from unittest.mock import AsyncMock

    from easydial.controller import DomainError
    from easydial.providers.browser import BrowserSession
    from easydial.providers.proxy import BlinkitProxy, ProxyIdentityChanged

    runtime, view = recovery
    saved = copy.deepcopy(runtime.inbox.load(SENDER))
    cookies = {'cookies': [{'name': 'session', 'value': 'secret'}], 'origins': []}
    browser = BrowserSession()
    browser.context = SimpleNamespace(storage_state=AsyncMock(return_value=cookies))
    original_context = browser.context
    browser.proxy = BlinkitProxy('test', 'test')
    browser.proxy.exit_ip = '1.1.1.1'
    original_proxy = browser.proxy
    browser.proxy_recovery_allowed = lambda: runtime.payments.proxy_recovery_allowed(SENDER)
    candidates = []

    async def open_candidate(candidate):
        assert candidate._initial_state == cookies
        candidate.context = SimpleNamespace()
        candidate.page = SimpleNamespace()
        candidates.append(candidate)

    async def verify(proxy, owner):
        if proxy is original_proxy:
            raise ProxyIdentityChanged('Changed exit', 503)
        proxy.exit_ip = '2.2.2.2'
        if payment_appears_during_verification:
            await capture(runtime, view)

    close = AsyncMock()
    publish = []
    monkeypatch.setattr(BrowserSession, '_open_context', open_candidate)
    monkeypatch.setattr(BrowserSession, 'close', close)
    monkeypatch.setattr(BrowserSession, '_publish_proxy_recovery', lambda owner, proxy, state: publish.append(proxy))
    monkeypatch.setattr(BlinkitProxy, 'verify', verify)
    if payment_appears_during_verification:
        with pytest.raises(DomainError):
            await browser._verify_proxy()
        assert browser.proxy is original_proxy and browser.context is original_context
        assert publish == []
    else:
        await browser._verify_proxy()
        assert browser.proxy is not original_proxy and browser.proxy.exit_ip == '2.2.2.2'
        assert publish == [browser.proxy]
        # Every subsequent verification uses the adopted pin, with no candidate allocation.
        await browser._verify_proxy()
    assert len(candidates) == 1 and runtime.inbox.load(SENDER) == saved
