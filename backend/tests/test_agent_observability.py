"""Real tool/transport/login spans expose timing and categories, never customer data."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from easydial.controller import DomainError
from easydial.observability import LOGGER_NAME, configure_logging, trace_scope
from easydial.whatsapp.store_login import StoreLogin
from easydial.whatsapp.transport import MetaTransport
from tests.test_exploration import call, response, run
from tests.test_exploration import research as research
from tests.test_whatsapp import settings


@pytest.fixture
async def telemetry():
    configure_logging()
    records = []
    class Capture(logging.Handler):
        def emit(self, record):
            records.append(json.loads(record.getMessage()))
    handler = Capture()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


async def test_harness_trace_links_model_tools_and_safe_failure_without_private_content(research, telemetry):
    runtime, _, _, _, create = research
    private = 'secret-query-address-cookie-do-not-log'
    runtime.chat.workers.explore_search.side_effect = RuntimeError(private)
    create.side_effect = [response(call('search_products', query=private, provider='blinkit')),
                          response(text='I could not verify those products.')]
    trace = 'a' * 32
    with trace_scope(trace):
        result = await run(research, private)
    assert result['action'] is None
    records = [record for record in telemetry if record['trace_id'] == trace]
    stages = {record.get('stage') for record in records}
    assert {'agent.run', 'agent.model', 'agent.tool'} <= stages
    model_results = [record for record in records if record['event'] == 'agent.model_result']
    assert [record['round_count'] for record in model_results] == [1, 2]
    selected = next(record for record in records if record['event'] == 'agent.tool_selected')
    assert selected['tool'] == 'search_products' and selected['tool_count'] == 1
    failed = next(record for record in records if record['event'] == 'stage.fail' and record['stage'] == 'agent.tool')
    assert failed['error_type'] == 'RuntimeError' and failed['duration_ms'] >= 0 and failed['frames']
    assert all('/' not in frame['file'] for frame in failed['frames'])
    tool_result = next(record for record in records if record['event'] == 'agent.tool_result')
    assert tool_result['status'] == 'error' and tool_result['error_code'] == 'store_read_failed'
    root = next(record for record in records if record['event'] == 'stage.start' and record['stage'] == 'agent.run')
    assert selected['span_id'] == root['span_id']
    assert failed['parent_span_id'] == root['span_id']
    assert private not in json.dumps(records)
    assert not any(key in record for record in records for key in ('arguments', 'query', 'output', 'text', 'address'))


async def test_unknown_tool_name_cannot_write_attacker_text_into_logs(research, telemetry):
    _, _, _, _, create = research
    private = 'secret-cookie-999999999999'
    create.side_effect = [response(call(private)), response(text='That action is unavailable.')]
    with trace_scope('b' * 32):
        await run(research)
    records = [record for record in telemetry if record['trace_id'] == 'b' * 32]
    assert any(record.get('tool') == 'unknown' for record in records)
    assert private not in json.dumps(records)


async def test_meta_send_trace_exposes_http_code_but_not_recipient_token_or_body(tmp_path, telemetry):
    config = settings(tmp_path, whatsapp_access_token='secret-access-token-never-log')
    transport = MetaTransport(config)
    await transport.http.aclose()
    transport.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        400, json={'error': {'code': 131000, 'error_subcode': 12, 'message': 'secret-response-content'}})))
    private = 'secret-message-content'
    try:
        with trace_scope('c' * 32):
            with pytest.raises(httpx.HTTPStatusError):
                await transport.send('919876543210', {'type': 'text', 'text': {'body': private}})
        records = [record for record in telemetry if record['trace_id'] == 'c' * 32]
        rejected = next(record for record in records if record['event'] == 'meta.send_rejected')
        assert rejected['http_status'] == 400 and rejected['api_code'] == 131000 and rejected['api_subcode'] == 12
        failed = next(record for record in records if record['event'] == 'stage.fail')
        assert failed['stage'] == 'meta.send' and failed['error_type'] == 'HTTPStatusError'
        serialized = json.dumps(records)
        assert not any(value in serialized for value in (private, '919876543210', 'secret-access-token-never-log',
                                                         'secret-response-content', 'graph.facebook.com'))
    finally:
        await transport.close()


async def test_login_otp_argument_is_never_logged_on_consent_failure(tmp_path, telemetry):
    login = StoreLogin(settings(tmp_path), SimpleNamespace(consented=lambda *args: False))
    login.addresses = AsyncMock()
    with trace_scope('d' * 32):
        with pytest.raises(DomainError):
            await login.request_otp('private-sender', 'blinkit', '919876543210')
        with pytest.raises(DomainError):
            await login.verify_otp('private-sender', 'blinkit', '654321')
    records = [record for record in telemetry if record['trace_id'] == 'd' * 32]
    assert {record['stage'] for record in records if record['event'] == 'stage.fail'} == {
        'login.request_otp', 'login.verify_otp'}
    serialized = json.dumps(records)
    assert not any(value in serialized for value in ('private-sender', '919876543210', '654321', 'Permission is required'))
    login.addresses.assert_not_awaited()


async def test_caught_order_history_failure_logs_type_not_merchant_payload(research, telemetry):
    runtime, _, session, _, _ = research
    workers = SimpleNamespace(order_history=AsyncMock(return_value={'ids': [], 'orders': []}))
    await runtime.payments.monitor.capture('secret-customer', session.view, 'upi', workers)
    runtime.payments.monitor.activate('secret-customer', session.view.id)
    workers.order_history.side_effect = RuntimeError('secret-cookie-and-address')
    with trace_scope('e' * 32):
        result = await runtime.payments.monitor.check('secret-customer', session.view.id, workers, max_checks=1)
    assert result['status'] == 'pending'
    records = [record for record in telemetry if record['trace_id'] == 'e' * 32]
    failure = next(record for record in records if record['event'] == 'order.history_read_failed')
    assert failure['error_type'] == 'RuntimeError' and failure['frames']
    observed = next(record for record in records if record['event'] == 'order.status_observed')
    assert observed['error_code'] == 'history_read_error' and observed['status'] == 'pending'
    assert observed['attempt'] == observed['max_attempts'] == 1
    serialized = json.dumps(records)
    assert not any(value in serialized for value in ('secret-customer', 'secret-cookie-and-address', session.view.address_label))
