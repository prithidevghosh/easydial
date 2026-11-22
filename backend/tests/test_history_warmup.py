import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from tests.test_onboarding import connect, runtime, turn


async def test_incoming_turn_cancels_warmup_before_shopping_and_preserves_basket(tmp_path):
    r = runtime(tmp_path)
    sender = '919999999999'
    await connect(r, provider='blinkit')
    chat = await r.customer_chats.get(sender)
    worker = chat.workers.workers[0]
    path = Path(r.onboarding.accounts.session_path(sender, 'blinkit'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('test-session-never-opened')
    started = asyncio.Event()
    warm_lock = asyncio.Lock()
    async def warming(*args):
        async with warm_lock:
            started.set()
            await asyncio.Event().wait()
    chat.workers.warm_history = warming
    worker.browser.close = AsyncMock()
    r.customer_chats.start_history_warmup(sender)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert warm_lock.locked() and not worker.lock.locked()
    replies = await asyncio.wait_for(turn(r, 'basket'), timeout=1)
    assert not worker.lock.locked()
    assert r.customer_chats.history_warmup is None
    worker.browser.close.assert_not_awaited()
    assert r.inbox.load(sender)['view']['lines'] == []
    assert replies
    await r.customer_chats.close()
    r.inbox.close()


async def test_logout_and_reconnection_remove_cached_history(tmp_path):
    r = runtime(tmp_path)
    sender = '919999999999'
    await connect(r, provider='blinkit')
    accounts = r.onboarding.accounts
    path = Path(accounts.session_path(sender, 'blinkit') + '.history')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('old-account-cache')
    accounts.claim_account(sender, 'blinkit', '9999999998')
    assert not path.exists()
    path.write_text('old-account-cache')
    accounts.forget(sender, 'blinkit')
    assert not path.exists()
    r.inbox.close()


async def test_completed_warmup_does_not_hold_worker_lock(tmp_path):
    r = runtime(tmp_path)
    sender = '919999999999'
    await connect(r, provider='blinkit')
    chat = await r.customer_chats.get(sender)
    path = Path(r.onboarding.accounts.session_path(sender, 'blinkit'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('test-session-never-opened')
    chat.workers.warm_history = AsyncMock()
    r.customer_chats.start_history_warmup(sender)
    task = r.customer_chats.history_warmup
    await task
    chat.workers.warm_history.assert_awaited_once()
    assert r.customer_chats.history_warmup is None
    assert not chat.workers.workers[0].lock.locked()
    await r.customer_chats.close()
    r.inbox.close()


async def test_warmup_never_evicts_another_active_customer(tmp_path):
    r = runtime(tmp_path)
    sender = '919999999999'
    await connect(r, provider='blinkit')
    path = Path(r.onboarding.accounts.session_path(sender, 'blinkit'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('test-session-never-opened')
    r.customer_chats.active = 'another-customer'
    r.customer_chats.get = AsyncMock()
    r.customer_chats.start_history_warmup(sender)
    await r.customer_chats.history_warmup
    r.customer_chats.get.assert_not_awaited()
    assert r.customer_chats.active == 'another-customer'
    r.inbox.close()


async def test_prefetch_uses_separate_browser_even_when_shopping_lock_is_held(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from easydial.models import Provider
    from easydial.providers import ProviderWorkers
    from tests.test_whatsapp import settings

    workers = ProviderWorkers(settings(tmp_path))
    primary = workers._worker(Provider.BLINKIT)
    primary.connected = lambda: True
    clone = SimpleNamespace(browser=SimpleNamespace(), close=AsyncMock())
    def factory(*args):
        return clone
    monkeypatch.setattr('easydial.providers.BlinkitProvider', factory)
    monkeypatch.setattr('easydial.providers.configure_blinkit_proxy', lambda *args: None)
    read = AsyncMock()
    monkeypatch.setattr('easydial.providers.past_orders.read_past_orders', read)
    async with primary.lock:
        await asyncio.wait_for(workers.warm_history(), timeout=1)
    read.assert_awaited_once_with(clone)
    clone.close.assert_awaited_once()
    await workers.close()
