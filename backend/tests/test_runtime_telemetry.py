"""Trace actual persisted turns through delivery without logging customer content."""
import json
import logging
from unittest.mock import AsyncMock

from easydial.observability import stable_ref
from tests.test_whatsapp import make_runtime, message


def records(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == 'easydial.telemetry']


async def test_turn_and_outbound_delivery_share_trace_without_private_payload(tmp_path, caplog):
    runtime = make_runtime(tmp_path)
    logger = logging.getLogger('easydial.telemetry')
    logger.addHandler(caplog.handler)
    try:
        runtime.inbox.enqueue(message('private-event-id', 'my-secret-transcript 123456 private-address'))
        assert await runtime.process()
        while await runtime.send_one():
            pass
        events = records(caplog)
        received = next(event for event in events if event['event'] == 'turn.received')
        delivered = [event for event in events if event['event'] == 'delivery.result']
        assert delivered and all(event['result'] == 'sent' for event in delivered)
        assert all(event['trace_id'] == received['trace_id'] == stable_ref('private-event-id') for event in delivered)
        assert any(event['event'] == 'conversation.action_selected' for event in events)
        logged = json.dumps(events)
        for secret in ('my-secret-transcript', '123456', 'private-address', '919999999999', 'test-token', 'private-event-id'):
            assert secret not in logged
        caplog.clear()
        assert not await runtime.process() and not await runtime.send_one() and not await runtime.check_due_order()
        assert records(caplog) == []  # Polling idle workers must not flood CloudWatch.
    finally:
        logger.removeHandler(caplog.handler)
        runtime.inbox.close()


async def test_failed_turn_records_safe_origin_and_preserves_failure_behavior(tmp_path, caplog):
    runtime = make_runtime(tmp_path)
    logger = logging.getLogger('easydial.telemetry')
    logger.addHandler(caplog.handler)
    try:
        runtime.chat.handle = AsyncMock(side_effect=ValueError('secret-cookie=private-secret; OTP 123456'))
        runtime.inbox.enqueue(message('failure-event', 'private-input'))
        assert await runtime.process()
        events = records(caplog)
        failure = next(event for event in events if event['event'] == 'turn.failed')
        assert failure['error_type'] == 'ValueError' and failure['frames']
        assert any(event['event'] == 'turn.result' and event['result'] == 'failed' for event in events)
        logged = json.dumps(events)
        assert 'private-secret' not in logged and 'private-input' not in logged and '123456' not in logged
        assert runtime.inbox.db.execute("SELECT status FROM events WHERE id='failure-event'").fetchone()[0] == 'failed'
    finally:
        logger.removeHandler(caplog.handler)
        runtime.inbox.close()
