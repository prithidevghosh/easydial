# Structured AWS diagnostics

The application emits privacy-safe JSON events to stderr through the dedicated `easydial.telemetry` logger. The AWS container log configuration collects stderr into CloudWatch alongside the existing service logs. This module does not increase OpenAI, HTTP-client or browser SDK logging verbosity.

## Correlation and timing

`configure_logging(key=...)` installs one handler and derives an HMAC key from the runtime secret. Neither that secret nor the derived key is logged. `stable_ref(event_id)` produces an opaque reference that remains stable across restarts using the same configured key. With no key configured, references are stable only within the process. Key rotation intentionally changes reference values.

`trace_scope(stable_ref(event_id))` binds a trace to the actual unit of work. It emits nothing by itself, so idle polling does not create trace noise. Incoming message processing and its later outbox delivery can use the same event reference. Automatic order checks use their own stable event identity. Raw IDs must not be supplied as log fields. A supplied trace ID that is not already an opaque reference or UUID is normalized to an HMAC reference.

`@traced('provider.search')` wraps an async coroutine and emits:

- `stage.start`: stage, trace, span, parent span and provider when available.
- `stage.end`: the same span with status and elapsed milliseconds.
- `stage.fail`: the same span with elapsed milliseconds, exception class, and sanitized code locations. Cancellation is explicitly labelled `cancelled` and propagated.

Nested calls retain the trace and create child spans. Concurrent tasks have independent context variables. A standalone decorated operation creates a fresh trace when no surrounding scope exists. The decorator preserves the function's return value and original exception. It does not inspect function arguments or return payloads. It is intended for coroutine functions, not async generator/context-manager factories.

Provider labels are inferred from a worker's `provider` attribute or a named `provider` argument. An absent provider label is preferable to guessing from request content. Instrumented inner provider stages retain their own provider label.

## What is recorded

The field allowlist permits technical stage/tool/action/model/error-code identifiers, known provider/status/message/phase enums, opaque references, booleans and bounded numeric counters/timings/HTTP or Meta error codes. Unknown fields, invalid types, non-finite numbers, oversized values and unsafe identifier strings are dropped. Reserved trace/span values come from the context, not caller-supplied fields.

`error_fields(error)` returns only the exception class and up to eight code locations, each containing a file basename, function name and line number. It never calls `str(error)` or records source-code lines, locals, arguments or chained exception messages. An explicit reason such as `proxy_ip_changed` can be supplied as an `error_code`; the actual IP is not recorded.

**Never pass customer inputs as diagnostic enum fields.** An allowlisted field name is not permission to log user data. Technical identifiers must come from application-controlled constants/validated enums, not raw model responses or merchant content. Numeric counters must represent counts/status codes, not phone numbers, OTPs, prices or IDs.

Excluded data includes phone numbers, OTPs, transcripts, search queries, product names, customer addresses, message bodies, screenshots, URLs, cookies, session state, credentials, payment links and raw exception messages. The helper is deliberately not a full payload dumper. Existing non-telemetry logs should still be reviewed separately when expanding instrumentation.

## Investigating a failure

Start with the time window, then locate `turn.received` and its `trace_id`. Filter that trace to see model/tool selection, provider/browser stages, retries and outbound delivery. Compare each `stage.start` with its matching `span_id` completion to identify the slow or failing operation.

Useful CloudWatch Logs Insights queries for the structured events:

```text
fields @timestamp, event, trace_id, span_id, parent_span_id, stage, status, duration_ms, error_type, error_code
| filter trace_id = "r_REPLACE_WITH_OBSERVED_REFERENCE"
| sort @timestamp asc
```

```text
fields @timestamp, event, stage, provider, duration_ms, error_type, error_code, trace_id
| filter event = "stage.fail" or event = "proxy.verification_failed" or event = "turn.rejected"
| sort @timestamp desc
| limit 100
```

```text
filter event = "stage.end"
| stats count(*) as samples, avg(duration_ms) as mean_ms, pct(duration_ms, 95) as p95_ms by stage
| sort p95_ms desc
```

A successful outer stage means its coroutine returned normally; domain statuses such as unavailable or partial may be recorded separately by `*.result`/`*.status_observed` events. Do not interpret application health or normal coroutine completion as proof that a merchant order succeeded.

## Failure isolation and verification

Logging is best effort. Handler failures cannot change a customer operation's result or replace its original exception. The dedicated handler suppresses secondary logging tracebacks when stderr is unavailable. A telemetry outage may lose diagnostics; it must not trigger retries of shopping/payment operations.

Tests cover concurrent trace isolation, nested spans, stable keyed references, exception identity, cancellation cleanup, unsafe fields, hidden exception content, handler failures and emitter failures. These unit tests validate logging behavior; successful CloudWatch collection must also be checked on the deployed AWS service.
