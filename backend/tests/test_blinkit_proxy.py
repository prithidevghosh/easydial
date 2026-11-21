from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from easydial.controller import DomainError
from easydial.providers import ProviderWorkers
from easydial.providers.browser import BrowserSession
from easydial.providers.proxy import configure_blinkit_proxy
from easydial.settings import Settings
from easydial.whatsapp.security import SecretBox


def config():
    return Settings(_env_file=None, blinkit_proxy_username="test-in-rotate",
                    blinkit_proxy_password="private")


def browser_response(browser, ip="203.0.113.7"):
    page = SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(ok=True, text=AsyncMock(return_value=ip))),
                           close=AsyncMock())
    browser.context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    return page


async def test_login_session_survives_encrypted_save_and_worker_recreation(tmp_path):
    login = BrowserSession()
    configure_blinkit_proxy(login, config())
    login.proxy.load(login)
    browser_response(login)
    await login.proxy.verify(login)
    login.storage_state = str(tmp_path / "customer.json")
    login.secret_box = SecretBox(Fernet.generate_key().decode())
    login.proxy.save(login)
    restored = BrowserSession(storage_state=login.storage_state)
    restored.secret_box = login.secret_box
    configure_blinkit_proxy(restored, config())
    restored.proxy.load(restored)
    assert restored.proxy.options == login.proxy.options
    assert restored.proxy.exit_ip == "203.0.113.7"
    assert "203.0.113.7" not in (tmp_path / "customer.json.proxy").read_text()


async def test_ip_change_closes_browser_without_adopting_new_ip():
    browser = BrowserSession()
    configure_blinkit_proxy(browser, config())
    browser.proxy.exit_ip = "203.0.113.7"
    page = browser_response(browser, "203.0.113.8")
    context = browser.context
    with pytest.raises(DomainError, match="proxy IP changed"):
        await browser._verify_proxy()
    assert browser.context is None
    assert browser.proxy.exit_ip == "203.0.113.7"
    context.close.assert_awaited_once()
    page.close.assert_awaited_once()


async def test_proxy_failure_does_not_fall_back_to_direct():
    browser = BrowserSession()
    configure_blinkit_proxy(browser, config())
    page = browser_response(browser)
    page.goto.side_effect = RuntimeError("sensitive credentials")
    with pytest.raises(DomainError, match="could not be verified") as error:
        await browser._verify_proxy()
    assert "sensitive" not in str(error.value)
    assert browser.context is None


def test_only_blinkit_uses_proxy_and_sessions_are_independent():
    workers = ProviderWorkers(config())
    assert workers.workers[0].browser.proxy.options["username"].startswith("test-in-")
    assert workers.workers[1].browser.proxy is None
    other = BrowserSession()
    configure_blinkit_proxy(other, config())
    assert other.proxy.session_id != workers.workers[0].browser.proxy.session_id
    assert "rotate" not in other.proxy.options["username"]


@pytest.mark.parametrize("profile", [False, True])
async def test_proxy_passed_to_both_launch_paths(monkeypatch, tmp_path, profile):
    browser = BrowserSession(profile=str(tmp_path) if profile else "")
    configure_blinkit_proxy(browser, config())
    page = SimpleNamespace(set_default_timeout=lambda _: None)
    context = SimpleNamespace(pages=[page])
    process = SimpleNamespace(new_context=AsyncMock(return_value=context))
    chromium = SimpleNamespace(launch=AsyncMock(return_value=process),
                                launch_persistent_context=AsyncMock(return_value=context))
    runtime = SimpleNamespace(chromium=chromium)
    monkeypatch.setattr("easydial.providers.browser.async_playwright",
                        lambda: SimpleNamespace(start=AsyncMock(return_value=runtime)))
    browser._verify_proxy = AsyncMock()
    await browser.open()
    launch = chromium.launch_persistent_context if profile else chromium.launch
    assert launch.call_args.kwargs["proxy"] == browser.proxy.options
    browser._verify_proxy.assert_awaited_once()
