---
title: Auto-Tracing Integrations
---

# Auto-Tracing Integrations

[← Index](../index.html) · [← AI Platform](../ai-platform.html)

The fastest-moving area in the GenAI codebase. Every LLM SDK or orchestration framework gets a flavor module under `mlflow/<library>/`, and most of them follow the same auto-tracing pattern. This page documents that pattern and surveys the in-tree integrations.

## 1. The recipe

Each integration boils down to five steps inside a `autolog()` function:

```python
# mlflow/<library>/__init__.py
from mlflow.utils.autologging_utils import autologging_integration, safe_patch

FLAVOR_NAME = "<library>"

@autologging_integration(FLAVOR_NAME)
def autolog(disable=False, silent=False, log_traces=True, ...):
    # 1. Pick the SDK entry point(s) to patch
    # 2. safe_patch each one with a wrapper that:
    safe_patch(
        FLAVOR_NAME,
        TargetClass,
        "method_name",
        _patched_method,           # see below
    )
```

The `_patched_method` body:

```python
def _patched_method(original, self, *args, **kwargs):
    with mlflow.start_span(
        name=f"{TargetClass.__name__}.{method}",
        span_type=SpanType.LLM,        # or CHAT_MODEL / CHAIN / TOOL / ...
    ) as span:
        # 1. Convert provider inputs → standard ChatMessage / ChatTool
        messages = _to_mlflow_messages(args, kwargs)
        set_span_chat_messages(span, messages)
        if "tools" in kwargs:
            set_span_chat_tools(span, _to_mlflow_tools(kwargs["tools"]))
        span.set_inputs(_safe_inputs(args, kwargs))

        # 2. Run the original SDK call
        result = original(self, *args, **kwargs)

        # 3. Record outputs in the same standard schema
        span.set_outputs(_to_mlflow_outputs(result))
        span.set_attribute(SpanAttributeKey.MODEL_PROVIDER, "<library>")
        return result
```

The boilerplate is real, but the design is deliberate: each adapter is a small, focused conversion layer. New providers slot in without touching the core tracing code.

## 2. `safe_patch` — the patching primitive

[`mlflow/utils/autologging_utils/safety.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/utils/autologging_utils/safety.py) provides `safe_patch`. It is the same utility that drives classical autologging (sklearn `fit`, PyTorch lightning callbacks). Properties:

- **Safe by default** — exceptions inside the patch never break the underlying SDK call. If patching fails, the original method runs and a warning is logged.
- **Idempotent** — patching twice does not double-instrument.
- **Reversible** — `mlflow.autolog(disable=True)` restores the original method.
- **Reentrancy-aware** — recursion guards prevent infinite span trees when an SDK calls back into itself.
- **Disable hooks** — patches honour `disable=True`, `silent=True`, and per-flavor exclusion lists.

It is the single most important utility in both the autologging and the auto-tracing systems.

## 3. The chat-standard converters

Every auto-tracing adapter ultimately must answer: *“What does this provider’s message format look like in MLflow’s standard `ChatMessage` schema?”* Each flavor includes a small converter module:

| File | Direction |
|---|---|
| `mlflow/anthropic/chat.py` | Anthropic → MLflow (and back for tool results) |
| `mlflow/gemini/utils.py` | Gemini → MLflow |
| `mlflow/bedrock/chat.py` | Bedrock Converse + invoke_model → MLflow |
| `mlflow/groq/utils.py` | Groq → MLflow (Groq is OpenAI-compatible, so the converter is thin) |
| `mlflow/litellm/utils.py` | LiteLLM is already OpenAI-shaped |

The standard schema (`ChatMessage`, `ChatTool`, `ChatCompletionResponse`) is in [`mlflow/types/chat.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/chat.py) and [`mlflow/types/llm.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/llm.py). It is intentionally OpenAI-shaped because that is the closest thing to a de facto standard. Every other provider gets converted into it.

## 4. Survey of in-tree integrations

| Library | Patched surface | Span types | Notes |
|---|---|---|---|
| **OpenAI** | `chat.completions.create`, `completions.create`, `embeddings.create`, **Swarm**’s `agent_get_chat_completion` and `Swarm.run` | `LLM`, `EMBEDDING`, `AGENT` | Reference implementation. |
| **Anthropic** | `Messages.create` (sync + async + streaming) | `LLM` | Recently extended (#14164) to emit standard chat messages and tools attributes. |
| **LangChain** | Custom `LangchainTracer` (a LangChain `BaseCallbackHandler`) | `CHAIN`, `LLM`, `TOOL`, `RETRIEVER` | Tool span attribute support added in #14159. |
| **LlamaIndex** | Workflow / query-engine instrumentation via `llama_index.core.instrumentation` | `CHAIN`, `LLM`, `RETRIEVER` | Workflow steps each get their own span. |
| **DSPy** | `dspy.Module.__call__` and `Predict.forward` | `CHAIN`, `LLM` | DSPy compilation traces are also captured. |
| **Bedrock** | `boto3.client("bedrock-runtime").invoke_model`, `converse`, `converse_stream` | `LLM` | Handles Anthropic, Mistral, Cohere, Titan model families on Bedrock. |
| **Gemini** | `google.generativeai.GenerativeModel.generate_content` (sync + async + streaming) | `LLM` | |
| **Groq** | `groq.resources.chat.Completions.create` | `LLM` | Added in #14006. Groq’s API is OpenAI-shaped. |
| **Mistral** | `mistralai.client.MistralClient.chat` and async equivalents | `LLM` | Added in #14195. |
| **Ollama** | Local `ollama` SDK | `LLM` | |
| **LiteLLM** | `litellm.completion`, `litellm.acompletion`, `litellm.embedding` | `LLM`, `EMBEDDING` | Aggregator over many providers; gives unified tracing under one flavor. |
| **CrewAI** | Crew runs, agent step events | `AGENT`, `LLM`, `TOOL` | Multi-agent collaboration shows up as nested agent spans. |
| **Autogen** | Autogen agent message handling | `AGENT`, `LLM` | |
| **PromptFlow** | `promptflow.core` execution events | `CHAIN` | |
| **Semantic Kernel** | Kernel function invocation | `CHAIN`, `LLM` | |
| **transformers** | text-generation pipeline + chat templates | `LLM`, `CHAT_MODEL` | LLM portion of the broader transformers flavor. |
| **sentence_transformers** | encode | `EMBEDDING` | |

`mlflow.autolog()` (with no flavor) flips on every available integration whose SDK is installed. Per-flavor `mlflow.<flavor>.autolog()` opts in only that one.

## 5. Tracing inside packaged models

If you package an agent as a `ChatModel` and call OpenAI inside `predict`, the OpenAI flavor’s `safe_patch` is *not* automatically active in the serving process — autolog is opt-in. Two ways to enable it inside a deployed model:

1. Call `mlflow.openai.autolog()` (or `mlflow.autolog()`) inside `load_context`.
2. Configure the serving environment to set `MLFLOW_AUTOLOGGING_ENABLED=true` and rely on the broad autolog().

Either way, traces produced inside the served model land via the configured exporter (the tracking store, an OTel collector, or — on Databricks Model Serving — the inference table for that endpoint).

## 6. The conversion guarantees

Every adapter is expected to:

1. **Round-trip messages.** A user message goes in as a `user` message; a tool response comes back as `tool`; assistant tool calls become `tool_calls` on the `assistant` message.
2. **Preserve token usage.** When the provider returns it, the span attributes `mlflow.usage.input_tokens` / `output_tokens` / `total_tokens` are set.
3. **Capture errors.** If the SDK call raises, the span’s status is set to `ERROR` and the exception is recorded as a span event before re-raising.
4. **Avoid double-instrumentation.** If a higher-level span (e.g. LangChain CHAIN) already wraps the SDK call, the lower-level adapter is still active but produces a child span — no de-duplication is required at the adapter layer.

## 7. Where to look in the code

| Need | File |
|---|---|
| `safe_patch` and autologging utilities | [`mlflow/utils/autologging_utils/safety.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/utils/autologging_utils/safety.py) |
| Standard chat types | [`mlflow/types/chat.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/chat.py) |
| Span chat helpers | [`mlflow/tracing/utils/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/tracing/utils) |
| OpenAI adapter | [`mlflow/openai/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/openai/__init__.py) + [`autolog.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/openai/autolog.py) |
| Anthropic adapter | [`mlflow/anthropic/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/anthropic/__init__.py) + [`autolog.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/anthropic/autolog.py) + [`chat.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/anthropic/chat.py) |
| LangChain adapter | [`mlflow/langchain/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/langchain) |
| LlamaIndex adapter | [`mlflow/llama_index/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/llama_index) |

[← Back to AI Platform](../ai-platform.html)
