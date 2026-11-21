"""Submission must settle before history can inspect a separate customer tab."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from easydial.models import Provider
from easydial.providers import payments
from easydial.providers.order_history import read_history


async def test_cash_click_retains_lock_until_store_response(monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    button = SimpleNamespace(click=AsyncMock())
    page = SimpleNamespace()

    async def wait(_page):
        entered.set()
        await finish.wait()

    @asynccontextmanager
    async def browser_page():
        yield page, None

    worker = SimpleNamespace(lock=asyncio.Lock(), browser_page=browser_page)
    monkeypatch.setattr(payments, "cash_control", AsyncMock(return_value=button))
    monkeypatch.setattr(payments, "wait_for_submission_response", wait)
    reservations = []
    task = asyncio.create_task(payments.submit_cash(worker, None, None, lambda: reservations.append(1)))
    await entered.wait()
    assert worker.lock.locked()
    assert not task.done()
    assert reservations == [1]
    button.click.assert_awaited_once()
    finish.set()
    assert await task == {"status": "pending"}
    assert not worker.lock.locked()


async def test_submission_timeout_does_not_navigate_close_or_retry():
    page = SimpleNamespace(
        wait_for_function=AsyncMock(side_effect=PlaywrightTimeoutError("timeout")),
        goto=AsyncMock(), close=AsyncMock(),
    )
    await payments.wait_for_submission_response(page)
    assert page.wait_for_function.call_args.kwargs["timeout"] == 30000
    page.goto.assert_not_called()
    page.close.assert_not_called()


async def test_history_uses_new_tab_and_closes_it_on_error():
    checkout = SimpleNamespace(goto=AsyncMock(), close=AsyncMock())
    history = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError("read failed")), close=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=history))

    @asynccontextmanager
    async def browser_page():
        yield checkout, context

    worker = SimpleNamespace(
        lock=asyncio.Lock(), browser_page=browser_page, provider=Provider.INSTAMART,
    )
    with pytest.raises(RuntimeError, match="read failed"):
        await read_history(worker, None)
    context.new_page.assert_awaited_once()
    history.close.assert_awaited_once()
    checkout.goto.assert_not_called()
    checkout.close.assert_not_called()
