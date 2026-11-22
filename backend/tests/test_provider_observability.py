"""Provider diagnostics expose stages/reasons without logging customer or proxy data."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.models import Provider
from easydial.observability import LOGGER_NAME, configure_logging
from easydial.providers import ProviderWorkers
from easydial.providers.browser import BrowserSession
from easydial.providers.proxy import configure_blinkit_proxy
from easydial.settings import Settings


@pytest.fixture
def telemetry(caplog):
    configure_logging()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


def records(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == LOGGER_NAME]


async def test_changed_proxy_reason_is_explicit_without_logging_ip_or_credentials(telemetry):
    caplog = telemetry
    browser = BrowserSession()
    configure_blinkit_proxy(browser, Settings(_env_file=None, blinkit_proxy_username='secretuser',
                                             blinkit_proxy_password='secretpassword'))
    browser.proxy.exit_ip = '203.0.113.7'
    original_session = browser.proxy.session_id
    page = SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(
        ok=True, text=AsyncMock(return_value='203.0.113.8'))), close=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    browser.context = context
    with caplog.at_level(logging.INFO), pytest.raises(DomainError, match='proxy IP changed'):
        await browser._verify_proxy()
    emitted = records(caplog)
    mismatches = [record for record in emitted if record.get('error_code') == 'proxy_ip_changed']
    assert len(mismatches) == 3 and all(record['pinned'] is True for record in mismatches)
    assert page.goto.await_count == 3 and page.close.await_count == 3
    assert browser.proxy.exit_ip == '203.0.113.7' and browser.proxy.session_id == original_session
    context.close.assert_awaited_once()
    logged = caplog.text + json.dumps(emitted)
    for secret in ('secretuser', 'secretpassword', '203.0.113.7', '203.0.113.8', original_session):
        assert secret not in logged


async def test_converted_search_error_is_logged_without_query_or_exception_message(telemetry):
    caplog = telemetry
    workers = ProviderWorkers(Settings(_env_file=None))
    failure = DomainError('private-address-and-secret-cookie')
    workers.workers[0] = SimpleNamespace(search=AsyncMock(side_effect=failure))
    with caplog.at_level(logging.INFO):
        results = await workers.search('private-product-query', 'private-delivery-address', Provider.BLINKIT)
    emitted = records(caplog)
    assert len(results) == 1 and results[0].status == 'error' and results[0].message == failure.message
    assert any(record['event'] == 'provider.search_result' and record['status'] == 'error'
               and record['offer_count'] == 0 and record['provider'] == 'blinkit' for record in emitted)
    logged = caplog.text + json.dumps(emitted)
    for secret in ('private-product-query', 'private-delivery-address', failure.message):
        assert secret not in logged


async def test_payment_options_string_provider_keeps_domain_error_conversion(telemetry, monkeypatch):
    from easydial.providers import payments

    caplog = telemetry
    workers = ProviderWorkers(Settings(_env_file=None))
    operation = AsyncMock(side_effect=TimeoutError('private payment-page diagnostic'))
    monkeypatch.setattr(payments, 'options', operation)
    # ProviderWorkers accepts a serialized provider string as well as its StrEnum.
    review = SimpleNamespace(provider='blinkit')
    with caplog.at_level(logging.INFO), pytest.raises(DomainError, match="Payment options didn't load"):
        await workers.payment_options(SimpleNamespace(), review)
    operation.assert_awaited_once()
    emitted = records(caplog)
    failure = next(record for record in emitted if record['event'] == 'provider.operation_failed')
    assert failure['stage'] == 'provider.payment_options'
    assert failure['provider'] == 'blinkit' and failure['error_type'] == 'TimeoutError'
    assert 'private payment-page diagnostic' not in caplog.text + json.dumps(emitted)
    assert not any(record.get('error_type') == 'AttributeError' for record in emitted)
