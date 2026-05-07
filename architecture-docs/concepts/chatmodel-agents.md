---
title: ChatModel & Agents
---

# ChatModel & Agents

[← Index](../index.html) · [← AI Platform](../ai-platform.html)

`ChatModel` is the GenAI counterpart of `PythonModel`. It is the contract you implement when the thing you want to ship is a chat-style agent — a function that takes a list of messages and returns a chat completion (optionally streamed) — and you want the rest of the MLflow stack to recognise it as such.

The architectural payoff: **the entire ML platform (tracking, registry, signature, scoring server, deployment plugins) keeps working unchanged** because `ChatModel` *is* a `PythonModel`. The chat contract is a layer on top, not a parallel system.

## 1. The base class

[`mlflow/pyfunc/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/model.py):

```python
class ChatModel(PythonModel):
    """A PythonModel subclass with a typed chat interface."""

    def predict(
        self,
        context: PythonModelContext,
        messages: list[ChatMessage],
        params: ChatParams,
    ) -> ChatCompletionResponse: ...

    def predict_stream(
        self,
        context: PythonModelContext,
        messages: list[ChatMessage],
        params: ChatParams,
    ) -> Generator[ChatCompletionChunk, None, None]: ...
```

Things to notice:

- The signature is **fixed**. You don’t pick the parameter names; the wrapper relies on them.
- Inputs are typed Pydantic objects, not raw dicts. Inside `predict`, `messages[0].content` and `params.temperature` are real attributes with real types.
- `predict_stream` is optional. When defined, the scoring server exposes streaming.
- Anything you need at inference (vector indexes, prompt templates, model artifacts) is handed in via `context.artifacts`.

## 2. The wrapper that makes it work

[`mlflow/pyfunc/loaders/chat_model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/loaders/chat_model.py):

```python
class _ChatModelPyfuncWrapper:
    def __init__(self, chat_model: ChatModel, context: PythonModelContext): ...

    def predict(self, model_input, params=None):
        # 1. Coerce dict / dataframe / list[dict] → list[ChatMessage] + ChatParams
        messages, chat_params = self._parse_input(model_input, params)
        # 2. Open root span (auto-derived span_type = CHAT_MODEL)
        # 3. Call self._chat_model.predict(self._context, messages, chat_params)
        # 4. Serialise ChatCompletionResponse → JSON-friendly dict
```

The wrapper is the bridge between the *typed Python world* (where `ChatMessage` is a Pydantic class) and the *untyped HTTP world* (where the same data is JSON). It also auto-derives the model signature from the typed parameters, so users never write a `ModelSignature` for a `ChatModel`.

## 3. Saving and loading

`mlflow.pyfunc.log_model` knows how to handle `ChatModel`:

```python
class MyAgent(mlflow.pyfunc.ChatModel):
    def predict(self, context, messages, params):
        ...

mlflow.pyfunc.log_model(
    artifact_path="agent",
    python_model=MyAgent(),
    artifacts={"vector_index": "/local/path/to/faiss"},
    pip_requirements=["langchain", "openai"],
    input_example={"messages": [{"role": "user", "content": "hi"}]},
)
```

What this does:

1. Pickles `MyAgent` with cloudpickle.
2. Stages the `artifacts` into the model directory.
3. Auto-infers the `ModelSignature` from `ChatModel.predict`’s typed parameters (input is `messages` + `params`; output is `ChatCompletionResponse`).
4. Writes `MLmodel` declaring `loader_module: mlflow.pyfunc.loaders.chat_model` and `task: agent/v1/chat`.
5. Resolves and pins the env requirements.

`mlflow.pyfunc.load_model("models:/my-agent@prod")` returns a `PyFuncModel` whose underlying loader is `_ChatModelPyfuncWrapper`. So `loaded.predict({"messages": [...]})` Just Works.

## 4. The `task` field — why it matters

The `MLmodel` for a chat agent has `metadata.task = "agent/v1/chat"` (the constant `_DEFAULT_CHAT_MODEL_METADATA_TASK` in `mlflow/pyfunc/model.py`). This is what lets:

- The serving layer expose the chat-style endpoint with the standard payload format.
- Databricks Model Serving recognise the model as a chat agent and bind it to the chat schema.
- The UI render a chat playground for the model card.

A `PythonModel` without that task is served as a generic pyfunc; a `ChatModel` is served as a chat agent — same code path, different metadata.

## 5. Tools, tool calls, and streaming

When the agent invokes a tool that the LLM requested:

- Tool calls in `messages[i].tool_calls` follow the OpenAI shape: `{id, type: "function", function: {name, arguments}}`.
- Tool results come back as `messages[j].role = "tool"` with `tool_call_id` linking back to the call.
- The agent decides how to dispatch them — typically by name lookup into a registry of `@mlflow.trace(span_type=TOOL)`-decorated Python functions, so each tool execution is a child span on the trace.

For streaming, the wrapper iterates `predict_stream` and yields `ChatCompletionChunk` JSON lines compatible with OpenAI’s SSE format. The HTTP server emits `Transfer-Encoding: chunked` and clients can stream tokens to UIs without buffering.

## 6. ChatAgent (next-gen)

A newer evolution layered on top of `ChatModel`: `ChatAgent` adds:

- **Multi-step intermediate outputs** — the agent emits `ChatAgentMessage` events for each LLM call, tool dispatch, and reasoning step, not just a final response.
- **Custom outputs** — structured “return alongside the answer” fields (citations, tool traces, debug info).
- **Streaming for the entire run**, not just the final reply.

Architecturally `ChatAgent` is still a `PythonModel` subclass with a wrapper of its own. The same load/serve/register pipeline applies.

## 7. Why this is the right design

The alternative would have been a parallel “GenAI runtime” next to pyfunc — a separate registry, a separate scoring server, a separate deployment plugin protocol. MLflow chose to keep one runtime and add a typed contract on top. Consequences:

- A `ChatModel` and a sklearn classifier are both `models:/...@alias` URIs. Promotion semantics, audit history, ACLs are unified.
- A team can A/B-test a sklearn baseline and an LLM-powered alternative behind the same gateway endpoint with no special-case code.
- The deployment plugin authors only had to learn one interface; chat support fell out of metadata and signature, not new APIs.

## 8. Where to look in the code

| Need | File |
|---|---|
| `ChatModel` base class | [`mlflow/pyfunc/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/model.py) |
| Wrapper / loader | [`mlflow/pyfunc/loaders/chat_model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/loaders/chat_model.py) |
| Standard chat types | [`mlflow/types/chat.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/chat.py), [`mlflow/types/llm.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/llm.py) |
| Saving / `log_model` for pyfunc | [`mlflow/pyfunc/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/__init__.py) |
| Scoring server (chat path) | [`mlflow/pyfunc/scoring_server/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/scoring_server/__init__.py) |

[← Back to AI Platform](../ai-platform.html)
