---
title: The AI / GenAI Platform
---

# The AI / GenAI Platform

[← Back to index](index.html)

The GenAI platform is built **on top of** the traditional ML platform — not next to it. It reuses the tracking store, the model registry, the artifact repositories, the `MLmodel` format, and the serving stack. What it adds is:

1. **A trace data model** for fine-grained LLM call observability.
2. **Auto-tracing adapters** for the major LLM SDKs and orchestration frameworks.
3. **A `ChatModel` runtime** so agents are first-class pyfunc models with a typed chat interface.
4. **An AI Gateway** that turns provider APIs into uniform, governed endpoints.
5. **An evaluation harness** with judges that scores LLM outputs and links assessments back to traces.

> **Mental model.** A *Run* contains *Models, Metrics, and Artifacts*. A **Trace** is a new sibling of those: a tree of `Span`s recorded for one logical LLM/agent invocation, owned by an experiment, indexable by tag, and retrievable through the same UI and REST API.

## 1. Tracing — the new foundation

### 1.1 The data model

Defined in [`mlflow/entities/span.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/span.py) and [`mlflow/entities/trace*.py`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/entities):

| Type | Description |
|---|---|
| `SpanType` | Enum: `LLM`, `CHAT_MODEL`, `CHAIN`, `AGENT`, `TOOL`, `RETRIEVER`, `EMBEDDING`, `RERANKER`, `PARSER`, `UNKNOWN` |
| `LiveSpan` | A mutable span that is currently being recorded (start, set attribute, end, set status, set events). |
| `Span` | The immutable, persisted form of a `LiveSpan`. |
| `Trace` | `(TraceInfo, TraceData)` — header metadata plus the full span list. |
| `TraceInfo` | Identifies the trace: `request_id`, `experiment_id`, `timestamp_ms`, `execution_time_ms`, `status`, tags. |

A `Trace` is logically a single root span and the tree underneath it; serialised, it is a flat span list plus header.

### 1.2 The TraceManager and OTel bridge

[`mlflow/tracing/trace_manager.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/trace_manager.py) holds an in-memory buffer keyed by `request_id`. Spans are appended as they end; when the root span ends, the manager finalises the `Trace` and exports it.

MLflow does **not** use the global OpenTelemetry tracer provider. [`mlflow/tracing/provider.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracing/provider.py) creates and owns a private `TracerProvider` so MLflow’s instrumentation does not collide with another OTel-using library running in the same process (PromptFlow, Snowpark, the user’s own observability stack).

### 1.3 Exporters

`mlflow/tracing/export/` provides:

- **MLflow exporter** — writes traces to the configured tracking store, where they are queryable through the same REST API as runs.
- **Inference Table exporter** — used by Databricks Model Serving to land traces in a Delta table.
- **OpenTelemetry exporter** — forwards spans to a user-provided OTel collector for integration with external observability tooling.

### 1.4 The tracing public API

`mlflow.start_span(...)`, the `@mlflow.trace` decorator, and `mlflow.update_current_trace(...)` are the user-facing surface. Internally they bottom out in `TraceManager` plus the patched providers.

→ [Concept deep dive: Tracing](concepts/tracing.html)

## 2. Auto-tracing — how 14+ libraries get instrumented

The single most-changed area in the GenAI codebase. Every LLM SDK or orchestration framework gets a flavor module that follows the same pattern:

1. **Patch the SDK’s entry point.** Use `safe_patch` (from `mlflow/utils/autologging_utils/safety.py`) to wrap the relevant method — `OpenAI.chat.completions.create`, `Anthropic.messages.create`, `LangChain.invoke`, `Bedrock.invoke_model`, etc.
2. **Open a span before the call.** Span type is chosen by what the patched method does (`LLM` for raw model calls, `CHAIN` for orchestrators, `TOOL` for tool dispatch).
3. **Record inputs in the standard chat schema.** Provider-specific message formats are converted to MLflow’s `ChatMessage` / `ChatTool` schema (`mlflow/types/chat.py`, `mlflow/types/llm.py`). Set with `set_span_chat_messages` and `set_span_chat_tools` so the UI can render them uniformly.
4. **Let the call execute** and capture the result, token usage, and any errors.
5. **Close the span.** Status, attributes, output messages, finish reason — all written before the span ends.

Currently auto-instrumented (each lives under `mlflow/<name>/`):

| Library | Patched surface | Notes |
|---|---|---|
| **OpenAI** | `chat.completions`, `completions`, `embeddings`, **Swarm agents** | Reference implementation for the pattern. |
| **Anthropic** | `Messages.create` | Recently extended (#14164) to emit the chat *messages* and *tools* schema. |
| **LangChain** | runnables / chains / agents / tools | Custom callback handler `LangchainTracer`; tool spans added in #14159. |
| **LlamaIndex** | query engines & workflows | `tracer.py` records each step. |
| **DSPy** | predict / forward calls on modules | |
| **Bedrock** | `invoke_model`, `converse` | |
| **Gemini** | generation API | |
| **Groq** | chat completions | Added in #14006. |
| **Mistral** | chat completions | Added in #14195. |
| **Ollama** | local inference | |
| **LiteLLM** | unified completion proxy | |
| **CrewAI** | multi-agent runs | |
| **Autogen** | agent conversations | |
| **PromptFlow** | DAG nodes | |
| **Semantic Kernel** | kernel invocations | |

The `transformers` flavor also emits traces for LLM-style tasks (text-generation, chat).

→ [Concept deep dive: Auto-Tracing Integrations](concepts/auto-tracing.html)

## 3. Unified types — the chat standard

Every auto-tracing adapter converts to a single chat schema so that the UI, evaluation, and downstream consumers don’t have to know which provider produced a span. The schema lives in [`mlflow/types/chat.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/chat.py) and [`mlflow/types/llm.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/llm.py):

- **`ChatMessage`** — `role` (`system`/`user`/`assistant`/`tool`), `content` (text *or* multi-modal parts: text, image, audio), `tool_calls`, `tool_call_id`, `name`.
- **`ChatTool`** — function-calling schema (name, description, parameters JSONSchema).
- **`ChatParams`** — `temperature`, `max_tokens`, `top_p`, `top_k`, `frequency_penalty`, `presence_penalty`, `stop`, `tools`, `tool_choice`, `metadata`.
- **`ChatCompletionResponse` / `ChatCompletionChunk`** — list of `ChatChoice`s plus model and usage metadata; chunks are the streaming variant.

This is intentionally OpenAI-shaped because that is the closest thing to a de facto standard. Every other provider (Anthropic, Gemini, Bedrock, …) gets a *converter* in its flavor module (e.g. `mlflow/anthropic/chat.py`).

## 4. ChatModel — agents as pyfunc

If you write a custom agent — a chain of LLM calls, retrievers, tools — you package it as a `ChatModel`. This is the GenAI counterpart of `PythonModel`.

[`mlflow/pyfunc/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/model.py) defines:

```python
class ChatModel(PythonModel):
    def predict(
        self, context, messages: list[ChatMessage], params: ChatParams
    ) -> ChatCompletionResponse: ...

    def predict_stream(
        self, context, messages, params
    ) -> Generator[ChatCompletionChunk, None, None]: ...
```

The matching loader in [`mlflow/pyfunc/loaders/chat_model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/loaders/chat_model.py) (`_ChatModelPyfuncWrapper`):

- Receives a JSON dict from the scoring server (`{"messages": [...], "temperature": 0.7, ...}`).
- Coerces it into typed `ChatMessage` / `ChatParams` Pydantic objects.
- Calls `predict` (or `predict_stream`).
- Serialises the response back to a chat-completion JSON.

Because `ChatModel` *is* a `PythonModel`, the **whole tracking, registry, signature, scoring-server, and deployment chain works unchanged**. You can register an agent (`models:/my-agent@prod`), serve it (`mlflow models serve`), evaluate it, and trace it — exactly like a sklearn model — but with the chat contract instead of dataframes.

A new `ChatAgent` API extends this for multi-step / streaming agent runs.

→ [Concept deep dive: ChatModel & Agents](concepts/chatmodel-agents.html)

## 5. The AI Gateway

`mlflow/gateway/` (server) and `mlflow/deployments/mlflow/` (client) implement the AI Gateway: a unified endpoint layer in front of provider APIs.

The gateway addresses three problems:

1. **Provider lock-in.** Code calls `mlflow.deployments.get_deploy_client("databricks").predict(endpoint="my-chat", inputs=...)` and the gateway routes to OpenAI, Anthropic, Cohere, Bedrock, Azure, or whatever route is configured.
2. **Credentials.** Provider keys live in the gateway’s config (or Databricks secret scopes). Application code only holds a gateway URL.
3. **Governance.** Rate limits, request queuing, and audit are enforced centrally.

Gateway routes are typed (`mlflow/gateway/schemas/`): `chat`, `completions`, `embeddings`. Each provider has an adapter under `mlflow/gateway/providers/` that maps the route schema to provider-specific calls.

The deployment plugin protocol (`BaseDeploymentClient.predict`) is the same interface a Databricks Model Serving endpoint serving a `ChatModel` exposes. Same client, same payload — different backend.

→ [Concept deep dive: AI Gateway](concepts/gateway.html)

## 6. GenAI Evaluation

`mlflow/evaluation/` and `mlflow/genai/` define an evaluation harness designed for non-deterministic outputs.

Core entities:

- **`Evaluation`** — `inputs`, `outputs`, optional `targets`, plus `assessments` (per-row scores) and `metrics` (aggregates). One row per example.
- **`Assessment`** — a single judgment: `name`, `value` (categorical or numeric), `rationale`, `source` (human / LLM judge / heuristic), `error` (if a judge failed).
- **Judges** — callables that take a row and return an `Assessment`. Built-in judges call an LLM with a structured prompt; custom judges are arbitrary Python.

The `mlflow.evaluate` / `mlflow.genai.evaluate` entry points orchestrate:

1. Run inference on a dataset (or accept pre-computed predictions).
2. For each row, invoke each judge (heuristic + LLM-as-judge).
3. Aggregate assessments into metrics (e.g. `correctness/mean`, `toxicity/p95`).
4. Persist the whole evaluation to the run, where the UI shows per-row scores and rationales.

Critically, **every LLM call inside an eval is itself traced**. The judge’s LLM call leaves a trace; the evaluated agent’s calls leave traces; the evaluation links assessments to those traces by `request_id`. That is the closed loop: a low-scoring row in the eval UI is one click away from the trace that produced it.

→ [Concept deep dive: GenAI Evaluation](concepts/genai-evaluation.html)

## 7. Putting it all together — a single agent invocation

When a user calls a deployed `ChatModel` agent, here is what happens across the platform:

1. The gateway / serving endpoint receives `POST /invocations` with a chat payload.
2. The pyfunc scoring server validates the payload against the `ChatModel` signature.
3. `_ChatModelPyfuncWrapper` deserialises into typed `ChatMessage`s, opens a root **`AGENT` span** (because the model declared itself a chat agent), and calls `predict`.
4. Inside `predict`, every LLM call (OpenAI, Anthropic, …) made through a SDK is **auto-traced**: each one becomes an `LLM` span under the agent root span.
5. Tool calls are wrapped in `TOOL` spans; retrieval steps in `RETRIEVER` spans.
6. The agent returns a `ChatCompletionResponse`. The wrapper closes the root span, the `TraceManager` finalises the trace, and the configured exporter (MLflow store, OTel collector, or Databricks inference table) ships it.
7. The trace lands in the same experiment as the run that registered the agent. The UI surfaces it next to its metrics and the model card.
8. If the agent is being run inside `mlflow.evaluate`, judges are applied to each row and produce `Assessment`s linked back to that trace’s `request_id`.

Every step is a layer that already existed in the ML platform, plus a span. That is the architectural thesis of the GenAI surface: **don’t build a parallel system, extend the existing one.**

---

Return to the **[index](index.html)** or jump into a concept deep dive:
[Tracing](concepts/tracing.html) · [Auto-tracing](concepts/auto-tracing.html) · [ChatModel & Agents](concepts/chatmodel-agents.html) · [Gateway](concepts/gateway.html) · [GenAI Evaluation](concepts/genai-evaluation.html)
