---
title: Tracing — Spans, the TraceManager, and OpenTelemetry
---

# Tracing — Spans, the TraceManager, and OpenTelemetry

[← Index](../index.html) · [← AI Platform](../ai-platform.html)

Tracing is the newest pillar in MLflow and the foundation of every other GenAI feature. A `Trace` is to a GenAI run what a `Run` is to a training job: the unit of observation that everything else attaches to.

## 1. Why tracing exists

Classical metrics (accuracy, F1, RMSE) are inadequate for LLM systems because:

- A single user query produces many model calls (LLM → tools → LLM → retriever → LLM).
- Behavior depends on intermediate text, not just final scores.
- Failures are stochastic and only diagnosable by inspecting the call chain.

Tracing records the full call chain. Every span carries its inputs, outputs, model, parameters, token usage, and timing. The eval harness, the UI, and any external observability tool consume the same trace.

## 2. The data model

[`mlflow/entities/span.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/span.py):

```python
class SpanType(str, Enum):
    LLM        = "LLM"          # raw model call
    CHAT_MODEL = "CHAT_MODEL"   # chat-style model call
    CHAIN      = "CHAIN"        # orchestrator
    AGENT      = "AGENT"        # top-level agent invocation
    TOOL       = "TOOL"         # tool / function call
    RETRIEVER  = "RETRIEVER"    # vector / search retrieval
    EMBEDDING  = "EMBEDDING"    # embedding generation
    RERANKER   = "RERANKER"
    PARSER     = "PARSER"
    UNKNOWN    = "UNKNOWN"
```

| Type | Description |
|---|---|
| `LiveSpan` | A mutable span being recorded. Methods: `set_inputs`, `set_outputs`, `set_attributes`, `set_status`, `add_event`, `end`. |
| `Span` | The immutable, frozen form persisted in a `TraceData`. Same field set, but read-only. |
| `TraceInfo` | Trace header. Fields: `request_id`, `experiment_id`, `timestamp_ms`, `execution_time_ms`, `status`, `request_metadata`, `tags`. |
| `TraceData` | Span list (the tree of nested calls). |
| `Trace` | `(TraceInfo, TraceData)` together — what the API returns. |

`request_id` is the trace’s primary key. Spans within a trace use OTel `span_id` / `parent_span_id` to form the tree.

### 2.1 Span attributes

[`mlflow/tracing/constant.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/constant.py) defines the attribute names. The most important:

| Constant | Stored name | Purpose |
|---|---|---|
| `SpanAttributeKey.INPUTS` | `mlflow.spanInputs` | JSON-encoded structured inputs |
| `SpanAttributeKey.OUTPUTS` | `mlflow.spanOutputs` | JSON-encoded structured outputs |
| `SpanAttributeKey.SPAN_TYPE` | `mlflow.spanType` | The `SpanType` value |
| `SpanAttributeKey.CHAT_MESSAGES` | `mlflow.chat.messages` | Messages as `ChatMessage` JSON |
| `SpanAttributeKey.CHAT_TOOLS` | `mlflow.chat.tools` | Tool definitions for function calling |
| `SpanAttributeKey.MESSAGE_FORMAT` | `mlflow.chat.messageFormat` | Provider hint |
| `SpanAttributeKey.MODEL_PROVIDER` | `mlflow.modelProvider` | `openai`, `anthropic`, … |

**Standardising chat attributes is what makes the UI and eval harness provider-agnostic.** Every auto-tracing adapter writes the same `mlflow.chat.messages` shape regardless of which SDK ran.

## 3. The TraceManager

[`mlflow/tracing/trace_manager.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/trace_manager.py) is the in-process buffer. Responsibilities:

- Hold a TTL cache of in-flight traces keyed by `request_id`.
- Aggregate spans as they end into the appropriate trace.
- When the root span ends (or the trace is explicitly finalised), hand the completed `Trace` to the configured exporters.
- Drop or evict traces past TTL — no leaked memory if a root span never closes.

The cache is intentionally process-local. Distributed tracing across processes is the OTel exporter’s job, not the manager’s.

## 4. The OpenTelemetry bridge

MLflow uses OpenTelemetry’s span model and APIs but **does not** use the global `TracerProvider`. Why:

- Many libraries (PromptFlow, Snowpark, customer apps) install their own global provider.
- A single global provider means MLflow’s spans go to *their* exporter, or theirs go to ours.
- Both modes are wrong.

[`mlflow/tracing/provider.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/provider.py) constructs an isolated `TracerProvider` MLflow owns. The `mlflow.start_span` API uses *that* provider. If a user has their own OTel pipeline, MLflow does not interfere with it.

The trade-off: SDKs that emit OTel spans natively (e.g. some auto-instrumentations) need a small bridge to copy their spans into MLflow’s provider. That bridge lives next to each affected adapter.

## 5. Exporters

Under [`mlflow/tracing/export/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/tracing/export):

| Exporter | Target |
|---|---|
| `mlflow.py` | Tracking store — stores `TraceInfo` rows, `TraceData` blobs, indexes for `search_traces`. |
| `inference_table.py` | Databricks Inference Tables — schema-controlled Delta table optimised for high-volume serving traces. |
| `otel.py` | Generic OTLP collector for downstream tools (Datadog, Honeycomb, Tempo, Jaeger). |

Multiple exporters can run side by side. A common production setup is `mlflow + otel`: traces both queryable inside MLflow and forwarded to the existing observability stack.

## 6. The public tracing API

`mlflow/tracing/fluent.py` and the top-level `mlflow.*` namespace expose:

- **`mlflow.start_span(name, span_type=SpanType.UNKNOWN, attributes=None)`** — context manager, opens a child of the current active span (or a new root if none).
- **`@mlflow.trace(span_type=..., name=...)`** — decorator that wraps a function call in a span; inputs/outputs auto-recorded.
- **`mlflow.update_current_trace(tags=None, metadata=None)`** — annotate the active trace.
- **`mlflow.get_trace(request_id)` / `mlflow.search_traces(...)`** — query the tracking store.
- **`mlflow.<flavor>.autolog()`** — turn on auto-tracing for a specific SDK.

Inside a span you can:

```python
with mlflow.start_span("retrieval", span_type=SpanType.RETRIEVER) as span:
    span.set_inputs({"query": q})
    docs = retrieve(q)
    span.set_outputs({"docs": docs})
    span.set_attribute("vector_store", "pgvector")
```

The decorator equivalent:

```python
@mlflow.trace(span_type=SpanType.TOOL)
def search_orders(customer_id: str) -> list[dict]: ...
```

## 7. How a trace is constructed during an LLM call

A typical OpenAI chat call inside an `@mlflow.trace`-decorated agent function:

1. The decorator opens a root **`AGENT`** span.
2. `openai.chat.completions.create(...)` is called — the OpenAI flavor’s `safe_patch` wrapper opens a child **`LLM`** span:
   - Records inputs (`messages`, `tools`, `temperature`) into `mlflow.chat.messages` and `mlflow.chat.tools`.
   - Calls the underlying SDK method.
   - Records outputs (`choices[0].message`, finish reason, token usage).
   - Closes the span.
3. If the agent dispatches a tool, `@mlflow.trace(span_type=TOOL)` on the tool function opens a **`TOOL`** span, captures the call, returns.
4. The decorator closes the root span.
5. The `TraceManager` finalises the trace and hands it to all configured exporters.

The result: a tree the UI renders as a flame chart, with each node showing its messages, model, latency, and tokens.

## 8. Storing traces in the tracking store

Trace persistence reuses the `AbstractStore` from tracking:

- `start_trace(experiment_id, ..., tags) -> TraceInfo`
- `end_trace(request_id, status, ...)`
- `get_trace_info(request_id)`
- `search_traces(experiment_ids, filter_string, max_results, ...)`
- `delete_traces(experiment_id, max_timestamp_millis | request_ids)`

Search supports a filter mini-language (parsed by the same `search_utils` module as runs):

```
attributes.status = 'OK' AND tags.user = 'alice' AND attributes.timestamp_ms > 1700000000000
```

`TraceData` (the span list) is stored as a single blob — it is not row-shredded. That keeps trace-level reads fast and avoids the storage explosion of one row per span attribute.

## 9. Where to look in the code

| Need | File |
|---|---|
| Span / SpanType / LiveSpan | [`mlflow/entities/span.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/span.py) |
| Trace / TraceInfo / TraceData | [`mlflow/entities/trace.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/trace.py), [`trace_info.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/trace_info.py), [`trace_data.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/trace_data.py) |
| TraceManager | [`mlflow/tracing/trace_manager.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/trace_manager.py) |
| OTel provider | [`mlflow/tracing/provider.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/provider.py) |
| Exporters | [`mlflow/tracing/export/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/tracing/export) |
| Public fluent API | [`mlflow/tracing/fluent.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/fluent.py) |
| Standardised attribute keys | [`mlflow/tracing/constant.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/constant.py) |
| Span chat helpers | [`mlflow/tracing/utils/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/tracing/utils) |

[← Back to AI Platform](../ai-platform.html)
