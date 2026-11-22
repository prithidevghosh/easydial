"""Telemetry must correlate work without exposing inputs or changing business behavior."""

import asyncio
import json
import logging

import pytest

from easydial import observability as obs


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        self.events.append(json.loads(record.getMessage()))


@pytest.fixture
def capture():
    logger = logging.getLogger(obs.LOGGER_NAME)
    saved = logger.handlers[:], logger.level, logger.propagate, obs._configured, obs._ref_key
    handler = Capture()
    logger.handlers = [handler]
    obs.configure_logging(key='test-only-observability-key')
    try:
        yield handler.events
    finally:
        logger.handlers, logger.level, logger.propagate, obs._configured, obs._ref_key = saved


def test_private_inputs_and_exception_contents_never_enter_json(capture):
    secret = 'sk-secret-proxy-password-9352238571'
    try:
        raise ValueError(secret, {'otp': '123456', 'url': 'https://user:password@example.test'})
    except ValueError as error:
        with obs.trace_scope('9352238571'):
            obs.emit('turn.rejected', query='ingredients of secret product', transcript='voice transcript',
                     phone='9352238571', otp='123456', password=secret, url='https://private.example/test',
                     args={'secret': secret}, exception=error, message=str(error), error=str(error),
                     token=secret, stage='safe.stage', model=secret, status=secret,
                     http_status=503, **obs.error_fields(error))
    encoded = json.dumps(capture)
    for value in (secret, '9352238571', '123456', 'voice transcript', 'private.example', 'ingredients of secret product'):
        assert value not in encoded
    event = capture[0]
    assert event['error_type'] == 'ValueError' and event['http_status'] == 503
    assert event['stage'] == 'safe.stage' and event['trace_id'].startswith('r_')
    assert event['frames'] and all(set(frame) == {'file', 'function', 'line'} for frame in event['frames'])
    assert all('/' not in frame['file'] for frame in event['frames'])


def test_configuration_is_idempotent_and_refs_survive_same_key_reconfiguration(capture):
    key = 'private-runtime-key-never-log'
    obs.configure_logging(key=key)
    first = obs.stable_ref('same-event')
    obs.configure_logging(key='different-key')
    assert obs.stable_ref('same-event') != first
    obs.configure_logging(key=key.encode())
    assert obs.stable_ref('same-event') == first
    logger = logging.getLogger(obs.LOGGER_NAME)
    assert sum(bool(getattr(handler, '_easydial_telemetry', False)) for handler in logger.handlers) == 1
    assert logger.propagate is False and logger.level == logging.INFO
    assert not capture


def test_emit_rejects_unknown_fields_bad_types_and_unbounded_values(capture):
    obs.emit('tool.result', duration_ms=float('nan'), count=True, attempt=-1, tool_count=10**50,
             image_count=2, pinned=True, provider='blinkit', action='user@example.test',
             stage='https://private.example', error_code='x'*200, event_ref='raw-customer',
             arbitrary={'input': 'do not log'}, frames=[{'file': '/private/secret.py', 'function': 'f', 'line': 2}])
    event = capture[0]
    assert event['image_count'] == 2 and event['pinned'] is True and event['provider'] == 'blinkit'
    assert event['frames'] == []
    for field in ('duration_ms', 'count', 'attempt', 'tool_count', 'action', 'stage', 'error_code', 'event_ref', 'arbitrary'):
        assert field not in event


async def test_nested_stages_keep_trace_and_parent_span_without_arguments(capture):
    @obs.traced('provider.child')
    async def child(secret):
        return secret

    @obs.traced('runtime.parent')
    async def parent(secret):
        return await child(secret)

    trace = obs.stable_ref('event-1')
    with obs.trace_scope(trace):
        assert not capture
        assert await parent('hidden-argument') == 'hidden-argument'
    start_parent, start_child, end_child, end_parent = capture
    assert {event['trace_id'] for event in capture} == {trace}
    assert start_child['parent_span_id'] == start_parent['span_id']
    assert start_parent['parent_span_id'] is None
    assert start_child['span_id'] == end_child['span_id']
    assert start_parent['span_id'] == end_parent['span_id']
    assert end_child['duration_ms'] >= 0
    assert 'hidden-argument' not in json.dumps(capture)
    assert obs._trace.get() is None and obs._span.get() is None


async def test_concurrent_tasks_have_isolated_trace_and_span_context(capture):
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    @obs.traced('worker.operation')
    async def work(own, other):
        own.set()
        await other.wait()
        await asyncio.sleep(0)

    async def run(label, own, other):
        with obs.trace_scope(obs.stable_ref(label)):
            await work(own, other)

    await asyncio.gather(run('one', first_started, second_started), run('two', second_started, first_started))
    for label in ('one', 'two'):
        events = [event for event in capture if event['trace_id'] == obs.stable_ref(label)]
        assert [event['event'] for event in events] == ['stage.start', 'stage.end']
        assert len({event['span_id'] for event in events}) == 1
        assert all(event['parent_span_id'] is None for event in events)
    assert len({event['span_id'] for event in capture}) == 2


async def test_failure_rethrows_identical_exception_without_stringifying(capture):
    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError('Exception text must not be inspected')

    failure = PrivateError('secret-arguments')

    @obs.traced('store.failure')
    async def operation():
        raise failure

    with pytest.raises(PrivateError) as caught:
        await operation()
    assert caught.value is failure
    assert capture[-1]['event'] == 'stage.fail' and capture[-1]['error_type'] == 'PrivateError'
    assert capture[-1]['status'] == 'error' and 'secret-arguments' not in json.dumps(capture)
    assert obs._trace.get() is None and obs._span.get() is None


async def test_cancellation_logs_category_and_restores_parent_context(capture):
    @obs.traced('store.cancelled')
    async def operation():
        raise asyncio.CancelledError('private cancellation details')

    with obs.trace_scope(obs.stable_ref('parent')) as parent:
        with pytest.raises(asyncio.CancelledError):
            await operation()
        assert obs._trace.get() == parent and obs._span.get() is None
    assert capture[-1]['status'] == 'cancelled'
    assert capture[-1]['error_type'] == 'CancelledError'
    assert 'private cancellation details' not in json.dumps(capture)
    assert obs._trace.get() is None


async def test_broken_logging_handler_does_not_change_success_or_original_failure(capture):
    class Broken(logging.Handler):
        def emit(self, record):
            raise RuntimeError('logging backend unavailable')

    logger = logging.getLogger(obs.LOGGER_NAME)
    logger.handlers.insert(0, Broken())

    @obs.traced('store.read')
    async def operation(failure=None):
        if failure:
            raise failure
        return 42

    assert await operation() == 42
    failure = ValueError('actual business failure')
    with pytest.raises(ValueError) as caught:
        await operation(failure)
    assert caught.value is failure
    assert obs._trace.get() is None and obs._span.get() is None


async def test_replaced_emitter_cannot_break_decorated_business_code(capture, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('telemetry failure')

    monkeypatch.setattr(obs, 'emit', fail)

    @obs.traced('worker.safe')
    async def operation():
        return 'success'

    assert await operation() == 'success'
    assert obs._trace.get() is None and obs._span.get() is None
