"""Privacy-safe, best-effort structured telemetry; never log request data or exception text."""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import Enum
from functools import wraps
from pathlib import PurePath
from uuid import uuid4

LOGGER_NAME = 'easydial.telemetry'
_trace = ContextVar('easydial_trace_id', default=None)
_span = ContextVar('easydial_span_id', default=None)
_parent_span = ContextVar('easydial_parent_span_id', default=None)
_ref_key = os.urandom(32)
_lock = threading.RLock()
_configured = False

_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_.:-]{0,79}$')
_OPAQUE_REF = re.compile(r'^r_[0-9a-f]{24}$')
_TRACE_ID = re.compile(r'^[0-9a-f]{32}$')
_NUMBER_FIELDS = {
    'count', 'attempt', 'max_attempts', 'retry_count', 'offer_count', 'reply_count',
    'product_count', 'item_count', 'image_count', 'result_count', 'order_count',
    'tool_count', 'round_count', 'round', 'http_status', 'status_code', 'api_code',
    'api_subcode', 'query_count', 'edit_count',
}
_BOOL_FIELDS = {'pinned', 'success', 'truncated', 'cached', 'image_fallback'}
_REF_FIELDS = {'event_ref', 'customer_ref', 'session_ref', 'order_ref'}
_TOKEN_FIELDS = {'stage', 'tool', 'action', 'error_code', 'error_type', 'model'}
_ENUM_FIELDS = {
    'provider': {'blinkit', 'instamart', 'unknown'},
    'message_type': {'text', 'audio', 'image', 'interactive', 'button', 'document', 'video',
                     'sticker', 'location', 'unknown', 'unsupported', 'reaction', 'system', 'payment_qr'},
    'phase': {'shopping', 'review', 'checkout_pending', 'store', 'consent', 'phone', 'otp',
              'address', 'ready', 'payment', 'confirmed', 'unknown'},
    'status': {'started', 'ok', 'success', 'error', 'failed', 'cancelled', 'skipped', 'pending',
               'unavailable', 'not_connected', 'no_matches', 'unknown', 'confirmed', 'delivered',
               'read', 'sent', 'queued', 'retry', 'retrying', 'rejected', 'done', 'accepted',
               'not_found', 'processing', 'expired', 'unsupported', 'returned', 'uncertain', 'answer', 'clarification', 'stale', 'partial'},
    'result': {'ok', 'success', 'error', 'failed', 'cancelled', 'skipped', 'pending', 'unknown',
               'confirmed', 'delivered', 'sent', 'queued', 'retry', 'retrying', 'rejected', 'done',
               'accepted', 'not_found', 'processing', 'expired', 'unsupported', 'read', 'uncertain',
               'handled', 'completed', 'no_matches', 'unavailable', 'not_connected', 'onboarding', 'partial'},
}


def _token(value):
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        return None
    # Defensive rejection of URLs, phone-like identifiers and common credential formats.
    if re.search(r'\d{7,}', value) or value.lower().startswith(('sk-', 'sk_', 'bearer', 'eyj')):
        return None
    return value


class _SafeStreamHandler(logging.StreamHandler):
    def handleError(self, record):
        # Do not emit logging's secondary raw traceback/source dump if stderr is unavailable.
        pass


def configure_logging(key: str | bytes | None = None):
    """Install one JSON-only stderr handler. An optional secret gives stable opaque refs across restarts."""
    global _configured, _ref_key
    try:
        with _lock:
            if isinstance(key, str):
                key = key.encode()
            if isinstance(key, bytes) and key:
                _ref_key = hashlib.sha256(b'easydial-observability-v1\0' + key).digest()
            logger = logging.getLogger(LOGGER_NAME)
            if not any(getattr(handler, '_easydial_telemetry', False) for handler in logger.handlers):
                handler = _SafeStreamHandler(sys.stderr)
                handler._easydial_telemetry = True
                handler.setFormatter(logging.Formatter('%(message)s'))
                logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            _configured = True
    except BaseException:
        # Observability must never prevent startup, a customer turn, or an error response.
        pass


def stable_ref(value: str) -> str:
    """An opaque HMAC reference. Never use an unsalted customer/event hash in logs."""
    try:
        raw = value.encode() if isinstance(value, str) else value if isinstance(value, bytes) else b'invalid-reference'
        return 'r_' + hmac.new(_ref_key, raw, hashlib.sha256).hexdigest()[:24]
    except BaseException:
        return 'r_' + uuid4().hex[:24]


def _trace_id(value):
    if isinstance(value, str) and (_TRACE_ID.fullmatch(value) or _OPAQUE_REF.fullmatch(value)):
        return value
    return stable_ref(value) if value is not None else uuid4().hex


@contextmanager
def trace_scope(trace_id: str):
    """Bind one trace without emitting anything; restore all parent context on exit/cancellation."""
    trace_token = _trace.set(_trace_id(trace_id))
    span_token = _span.set(None)
    parent_token = _parent_span.set(None)
    try:
        yield _trace.get()
    finally:
        _parent_span.reset(parent_token)
        _span.reset(span_token)
        _trace.reset(trace_token)


def error_fields(error):
    """Exception class and code locations only: no str(error), source lines, arguments or locals."""
    try:
        frames = deque(maxlen=8)
        cursor = error.__traceback__
        while cursor is not None:
            code = cursor.tb_frame.f_code
            filename = PurePath(code.co_filename).name
            function = code.co_name
            if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', filename):
                frames.append({'file': filename, 'function': _token(function) or 'anonymous',
                               'line': cursor.tb_lineno})
            cursor = cursor.tb_next
        return {'error_type': _token(type(error).__name__) or 'Exception', 'frames': list(frames)}
    except BaseException:
        return {'error_type': 'Exception', 'frames': []}


def _safe_frames(value):
    if not isinstance(value, (list, tuple)):
        return None
    frames = []
    for frame in value[-8:]:
        if not isinstance(frame, dict):
            continue
        filename, function, line = frame.get('file'), frame.get('function'), frame.get('line')
        if (isinstance(filename, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', filename)
                and _token(function) and type(line) is int and 0 <= line <= 10_000_000):
            frames.append({'file': filename, 'function': function, 'line': line})
    return frames


def _safe_fields(fields):
    result = {}
    for name, value in fields.items():
        if name in _NUMBER_FIELDS and type(value) is int and 0 <= value <= 1_000_000_000:
            result[name] = value
        elif name == 'duration_ms' and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86_400_000:
            result[name] = round(value, 3)
        elif name in _BOOL_FIELDS and type(value) is bool:
            result[name] = value
        elif name in _REF_FIELDS and isinstance(value, str) and _OPAQUE_REF.fullmatch(value):
            result[name] = value
        elif name in _TOKEN_FIELDS:
            token = _token(value)
            if token:
                result[name] = token
        elif name in _ENUM_FIELDS:
            scalar = value.value if isinstance(value, Enum) else value
            if isinstance(scalar, str) and scalar in _ENUM_FIELDS[name]:
                result[name] = scalar
        elif name == 'frames':
            frames = _safe_frames(value)
            if frames is not None:
                result[name] = frames
    return result


def emit(event: str, **safe_fields):
    """Write one bounded JSON record, dropping unknown/unsafe fields. Never raises."""
    try:
        if not _configured:
            configure_logging()
        record = {
            'timestamp': datetime.now(UTC).isoformat(timespec='milliseconds'),
            'event': _token(event) or 'telemetry.invalid_event',
            'trace_id': _trace.get(), 'span_id': _span.get(), 'parent_span_id': _parent_span.get(),
            **_safe_fields(safe_fields),
        }
        logging.getLogger(LOGGER_NAME).info(json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    except BaseException:
        pass


def _safe_emit(event, **fields):
    # Keep decorated business code safe even if the emitter is replaced/misconfigured.
    try:
        emit(event, **fields)
    except BaseException:
        pass


def _provider(args, kwargs):
    try:
        value = kwargs.get('provider')
        if value is None and args:
            value = getattr(args[0], 'provider', None)
        return value.value if isinstance(value, Enum) else value
    except BaseException:
        return None


def traced(stage: str):
    """Trace an async coroutine, preserving returns, exceptions and cancellation exactly."""
    stage = _token(stage) or 'unknown_stage'

    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with trace_scope(uuid4().hex) if _trace.get() is None else nullcontext():
                parent = _span.get()
                span_token = _span.set(uuid4().hex[:16])
                parent_token = _parent_span.set(parent)
                started = time.perf_counter()
                provider = _provider(args, kwargs)
                _safe_emit('stage.start', stage=stage, provider=provider, status='started')
                try:
                    value = await function(*args, **kwargs)
                except BaseException as error:
                    _safe_emit('stage.fail', stage=stage, provider=provider,
                               status='cancelled' if isinstance(error, asyncio.CancelledError) else 'error',
                               duration_ms=(time.perf_counter() - started) * 1000, **error_fields(error))
                    raise
                else:
                    _safe_emit('stage.end', stage=stage, provider=provider, status='ok',
                               duration_ms=(time.perf_counter() - started) * 1000)
                    return value
                finally:
                    _parent_span.reset(parent_token)
                    _span.reset(span_token)
        return wrapped
    return decorate
