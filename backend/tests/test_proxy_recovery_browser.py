import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from easydial.controller import DomainError
from easydial.providers.browser import BrowserSession
from easydial.providers.proxy import BlinkitProxy, ProxyIdentityChanged


def original_browser():
    browser = BrowserSession()
    browser.proxy = BlinkitProxy('private-user', 'private-password')
    browser.proxy.exit_ip = '203.0.113.1'
    browser.proxy.loaded = True
    browser.context = SimpleNamespace(storage_state=AsyncMock(return_value={'cookies': [], 'origins': []}), close=AsyncMock())
    browser.page = Mock()
    browser.proxy_recovery_allowed = lambda: True
    return browser


@pytest.mark.asyncio
async def test_recovery_reuses_cookies_and_preserves_original_until_verified(monkeypatch):
    browser = original_browser()
    original_context, original_proxy = browser.context, browser.proxy
    candidates = []

    async def open_candidate(candidate):
        assert original_context.close.await_count == 0
        assert candidate._initial_state == {'cookies': [], 'origins': []}
        candidate.context = SimpleNamespace(close=AsyncMock())
        candidate.page = Mock()
        candidates.append(candidate)

    async def verify(proxy, current):
        if proxy is original_proxy:
            raise ProxyIdentityChanged('changed', 503)
        assert current.storage_state == ''
        proxy.exit_ip = '203.0.113.2'

    monkeypatch.setattr(BrowserSession, '_open_context', open_candidate)
    monkeypatch.setattr(BlinkitProxy, 'verify', verify)
    await browser._verify_proxy()
    assert browser.proxy is candidates[0].proxy
    assert browser.proxy.exit_ip == '203.0.113.2'
    assert browser.context is not original_context
    original_context.close.assert_awaited_once()
    # Later checks keep the successful session and context.
    successful_proxy = browser.proxy
    await browser._verify_proxy()
    assert browser.proxy is successful_proxy
    assert len(candidates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_failed_or_cancelled_candidate_keeps_original(monkeypatch, cancel):
    browser = original_browser()
    original_context, original_proxy = browser.context, browser.proxy
    candidates, attempts = [], []

    async def open_candidate(candidate):
        candidate.context = SimpleNamespace(close=AsyncMock())
        candidates.append(candidate.context)

    async def verify(proxy, current):
        attempts.append(proxy)
        if proxy is original_proxy:
            raise ProxyIdentityChanged('changed', 503)
        if cancel:
            raise asyncio.CancelledError()
        raise ValueError('transport unavailable')

    monkeypatch.setattr(BrowserSession, '_open_context', open_candidate)
    monkeypatch.setattr(BlinkitProxy, 'verify', verify)
    with pytest.raises(asyncio.CancelledError if cancel else DomainError):
        await browser._verify_proxy()
    assert len(attempts) == (2 if cancel else 3)
    assert browser.proxy is original_proxy
    assert browser.context is original_context
    original_context.close.assert_not_awaited()
    for context in candidates:
        context.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('gate', [None, lambda: False, lambda: 1])
async def test_protected_context_never_rotates_or_closes(gate):
    browser = original_browser()
    browser.proxy_recovery_allowed = gate
    browser.proxy.verify = AsyncMock(side_effect=ProxyIdentityChanged('changed', 503))
    context, proxy = browser.context, browser.proxy
    with pytest.raises(DomainError):
        await browser._verify_proxy()
    assert browser.context is context and browser.proxy is proxy
    context.close.assert_not_awaited()
    assert proxy.verify.await_count == 3


def test_publishing_failure_restores_both_files(tmp_path):
    browser = original_browser()
    browser.storage_state = str(tmp_path / 'state')
    state_path = tmp_path / 'state'
    proxy_path = tmp_path / 'state.proxy'
    state_path.write_bytes(b'old-encrypted-state')
    proxy_path.write_bytes(b'old-encrypted-proxy')
    candidate = BlinkitProxy('private-user', 'private-password')
    candidate.exit_ip = '203.0.113.2'
    browser.secret_box = SimpleNamespace(write=Mock(side_effect=[None, ValueError('write failed')]))
    with pytest.raises(ValueError):
        browser._publish_proxy_recovery(candidate, {'cookies': []})
    assert state_path.read_bytes() == b'old-encrypted-state'
    assert proxy_path.read_bytes() == b'old-encrypted-proxy'


def test_recovery_persists_encrypted_cookies_and_pin_for_restart(tmp_path):
    from cryptography.fernet import Fernet

    from easydial.whatsapp.security import SecretBox

    browser = original_browser()
    browser.storage_state = str(tmp_path / 'session')
    browser.secret_box = SecretBox(Fernet.generate_key().decode())
    candidate = BlinkitProxy('private-user', 'private-password')
    candidate.exit_ip = '203.0.113.2'
    state = {'cookies': [{'name': 'auth', 'value': 'private-cookie'}], 'origins': []}
    browser._publish_proxy_recovery(candidate, state)
    restored = BrowserSession(storage_state=browser.storage_state)
    restored.secret_box = browser.secret_box
    restored.proxy = BlinkitProxy('private-user', 'private-password')
    restored.proxy.load(restored)
    assert restored.proxy.options == candidate.options
    assert restored.proxy.exit_ip == candidate.exit_ip
    assert browser.secret_box.open((tmp_path / 'session').read_text()) == state
    assert 'private-cookie' not in (tmp_path / 'session').read_text()
    assert candidate.exit_ip not in (tmp_path / 'session.proxy').read_text()


@pytest.mark.asyncio
async def test_gate_revoked_while_probe_pending_prevents_adoption(monkeypatch):
    browser = original_browser()
    original_context, original_proxy = browser.context, browser.proxy
    candidate_context = SimpleNamespace(close=AsyncMock())

    async def open_candidate(candidate):
        candidate.context = candidate_context

    async def verify(proxy, current):
        if proxy is original_proxy:
            raise ProxyIdentityChanged('changed', 503)
        proxy.exit_ip = '203.0.113.2'
        browser.proxy_recovery_allowed = lambda: False

    monkeypatch.setattr(BrowserSession, '_open_context', open_candidate)
    monkeypatch.setattr(BlinkitProxy, 'verify', verify)
    with pytest.raises(DomainError):
        await browser._verify_proxy()
    assert browser.proxy is original_proxy and browser.context is original_context
    candidate_context.close.assert_awaited_once()
    original_context.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_error_fails_closed():
    browser = original_browser()
    browser.proxy_recovery_allowed = Mock(side_effect=RuntimeError('policy unavailable'))
    browser.proxy.verify = AsyncMock(side_effect=ProxyIdentityChanged('changed', 503))
    context = browser.context
    with pytest.raises(DomainError):
        await browser._verify_proxy()
    assert browser.proxy.verify.await_count == 3
    assert browser.context is context
    context.close.assert_not_awaited()
