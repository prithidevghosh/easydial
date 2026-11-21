import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.models import Provider
from easydial.providers import payments
from easydial.providers.browser import goto_with_retry


@pytest.mark.parametrize("invalid", [None, "bill", "address", "expired", "page", "screen"])
async def test_resume_requires_same_bill_address_page_and_live_screen(monkeypatch, invalid):
    page = object()
    checkpoint = {
        "page": page, "fingerprint": "bill", "address": "Home", "at": time.monotonic(),
    }
    if invalid == "bill":
        checkpoint["fingerprint"] = "old"
    elif invalid == "address":
        checkpoint["address"] = "Elsewhere"
    elif invalid == "expired":
        checkpoint["at"] -= 301
    elif invalid == "page":
        checkpoint["page"] = object()
    worker = SimpleNamespace(
        _payment_checkpoint=checkpoint, verified_address="Home", provider=Provider.BLINKIT,
    )
    monkeypatch.setattr(payments, "fingerprint", lambda *_: "bill")
    monkeypatch.setattr(payments, "current_payment_scope", AsyncMock(
        return_value=None if invalid == "screen" else "current screen",
    ))
    entry = AsyncMock(return_value="fresh screen")
    monkeypatch.setattr(payments, "open_options_once", entry)
    result = await payments.open_options(worker, None, None, page)
    assert result == ("current screen" if invalid is None else "fresh screen")
    assert entry.await_count == (0 if invalid is None else 1)


@pytest.mark.parametrize("persistent", [False, True])
async def test_upstream_error_retries_read_only_navigation(monkeypatch, persistent):
    marker = "upstream connect error or disconnect/reset before headers. reset reason: overflow"
    body = SimpleNamespace(inner_text=AsyncMock(
        side_effect=[marker, marker, marker] if persistent else [marker, "healthy store"],
    ))
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        locator=lambda _: body,
    )
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    if persistent:
        with pytest.raises(DomainError, match="temporarily busy"):
            await goto_with_retry(page, "https://blinkit.com/")
    else:
        await goto_with_retry(page, "https://blinkit.com/")
    assert page.goto.await_count == (3 if persistent else 2)


async def test_widget_overflow_retries_before_submission(monkeypatch):
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    frame = SimpleNamespace(locator=lambda _: SimpleNamespace(
        inner_text=AsyncMock(return_value="upstream connect error reset reason: overflow"),
    ))
    page = SimpleNamespace(frames=[frame])
    worker = SimpleNamespace(verified_address="Home")
    entry = AsyncMock(side_effect=[PlaywrightTimeoutError("waiting for UPI"), "loaded"])
    monkeypatch.setattr(payments, "open_options_once", entry)
    monkeypatch.setattr(payments, "fingerprint", lambda *_: "bill")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    assert await payments.open_options(worker, None, None, page) == "loaded"
    assert entry.await_count == 2


async def test_unloaded_blinkit_menu_uses_bounded_pre_submission_retry(monkeypatch):
    missing = SimpleNamespace(is_visible=AsyncMock(return_value=False), count=AsyncMock(return_value=0))
    missing.first = missing
    body = SimpleNamespace(inner_text=AsyncMock(return_value='Loading checkout'))
    page = SimpleNamespace(url='https://blinkit.com/checkout', get_by_text=lambda *_args, **_kwargs: missing,
                           locator=lambda selector: body if selector == 'body' else missing)
    monkeypatch.setattr(payments.asyncio, 'sleep', AsyncMock())
    with pytest.raises(payments.CheckoutTemporarilyUnavailable):
        await payments.continue_blinkit_address(None, None, None, page)


@pytest.mark.parametrize('provider,deadline', [(Provider.BLINKIT, 120), (Provider.INSTAMART, 75)])
async def test_payment_menu_budget_allows_bounded_recovery(monkeypatch, provider, deadline):
    from easydial.providers import ProviderWorkers
    from easydial.settings import Settings

    workers = ProviderWorkers(Settings(_env_file=None, openai_api_key=''))
    seen = []
    async def bounded(awaitable, *, timeout):
        seen.append(timeout)
        return await awaitable
    monkeypatch.setattr('easydial.providers.asyncio.wait_for', bounded)
    monkeypatch.setattr(payments, 'options', AsyncMock(return_value=['upi']))
    assert await workers.payment_options(None, SimpleNamespace(provider=provider)) == ['upi']
    assert seen == [deadline]
    await workers.close()
