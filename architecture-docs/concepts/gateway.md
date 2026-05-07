---
title: AI Gateway — Unified LLM Endpoints
---

# AI Gateway — Unified LLM Endpoints

[← Index](../index.html) · [← AI Platform](../ai-platform.html)

The MLflow AI Gateway turns provider APIs (OpenAI, Anthropic, Cohere, Bedrock, Azure, Mosaic, and more) into uniform, governed, application-facing endpoints. It is the LLM equivalent of a reverse proxy with built-in authn, schema enforcement, rate limiting, and pluggable provider backends.

## 1. Why a gateway

Three problems push every team that ships LLM features:

1. **Provider lock-in.** Application code embeds OpenAI types and SDKs. Switching to Anthropic or adding Bedrock as a fallback becomes a refactor.
2. **Credential sprawl.** Each developer machine, each CI runner, each notebook needs API keys. Rotating a compromised key means tracking down everywhere it landed.
3. **Governance.** Rate limits, audit logs, request inspection, prompt-injection filtering — these belong upstream of every consumer, not inline in each app.

The gateway absorbs all three.

## 2. Architecture

Two halves:

- **Server** (`mlflow/gateway/`) — a FastAPI app that ingests a YAML config of *routes*, each route declaring a logical endpoint name + a backend provider with its credentials.
- **Client** (`mlflow/deployments/mlflow/`) — a deployment plugin (`get_deploy_client("mlflow:<gateway_url>")`) that talks to the gateway using the same `BaseDeploymentClient` interface used everywhere else.

```
┌─────────────────┐  POST /endpoints/my-chat/invocations  ┌────────────────────┐
│   Application   │  ─────────────────────────────────▶  │  MLflow Gateway    │
│  (mlflow.deploy │                                       │   (FastAPI app)    │
│   .get_deploy_  │  {"messages":[...]}                   │                    │
│   client(...))  │  ◀─── ChatCompletionResponse ────     │  ┌──────────────┐  │
└─────────────────┘                                       │  │  Route:      │  │
                                                          │  │  my-chat     │  │
                                                          │  │  → openai    │  │
                                                          │  └──────────────┘  │
                                                          │  ┌──────────────┐  │
                                                          │  │  Route:      │  │
                                                          │  │  my-embed    │  │
                                                          │  │  → cohere    │  │
                                                          │  └──────────────┘  │
                                                          └─────────┬──────────┘
                                                                    │
                                  ┌────────────┬────────────┬───────┴───────┬────────┐
                                  │            │            │               │        │
                                  ▼            ▼            ▼               ▼        ▼
                                OpenAI    Anthropic     Cohere          Bedrock    Azure
```

## 3. Routes — the configuration unit

A route declares a logical endpoint and binds it to a typed schema and a provider:

```yaml
# gateway-config.yaml
endpoints:
  - name: my-chat
    endpoint_type: llm/v1/chat
    model:
      provider: openai
      name: gpt-4o-mini
      config:
        openai_api_key: $OPENAI_API_KEY

  - name: my-embed
    endpoint_type: llm/v1/embeddings
    model:
      provider: cohere
      name: embed-multilingual-v3.0
      config:
        cohere_api_key: $COHERE_API_KEY

  - name: my-completion
    endpoint_type: llm/v1/completions
    model:
      provider: anthropic
      name: claude-haiku-4.5
      config:
        anthropic_api_key: $ANTHROPIC_API_KEY
```

Three endpoint types — `llm/v1/chat`, `llm/v1/completions`, `llm/v1/embeddings` — define typed request/response schemas in [`mlflow/gateway/schemas/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/gateway/schemas). Provider adapters live in [`mlflow/gateway/providers/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/gateway/providers); each adapter knows how to translate the typed schema to and from one specific provider’s API.

## 4. Provider adapters

Each provider is a small subclass of `BaseProvider`. Methods to implement:

- `async def chat(self, payload: ChatPayload) -> ChatResponse:`
- `async def completions(self, payload: CompletionsPayload) -> CompletionsResponse:`
- `async def embeddings(self, payload: EmbeddingsPayload) -> EmbeddingsResponse:`

In-tree provider adapters (sample, not exhaustive):

| Provider | Module |
|---|---|
| OpenAI | `mlflow/gateway/providers/openai.py` |
| Anthropic | `mlflow/gateway/providers/anthropic.py` |
| Cohere | `mlflow/gateway/providers/cohere.py` |
| Bedrock / AWS | `mlflow/gateway/providers/bedrock.py` |
| Azure OpenAI | `mlflow/gateway/providers/azure_openai.py` |
| Hugging Face TGI | `mlflow/gateway/providers/huggingface_text_generation_inference.py` |
| MLflow Model Serving | `mlflow/gateway/providers/mlflow.py` |
| Mosaic ML | `mlflow/gateway/providers/mosaicml.py` |
| AI21 Labs | `mlflow/gateway/providers/ai21labs.py` |
| PaLM | `mlflow/gateway/providers/palm.py` |
| Together AI | `mlflow/gateway/providers/togetherai.py` |
| MistralAI | `mlflow/gateway/providers/mistral.py` |

The MLflow provider is recursive — it lets a route forward to *another* deployed MLflow model (a `ChatModel` running behind a scoring server, or a Databricks Model Serving endpoint). That is how you build a hybrid setup where the gateway routes some requests to OpenAI and others to your own fine-tuned model.

## 5. The client

The client side is just another deployment plugin. The same code that talks to Databricks Model Serving talks to the gateway:

```python
import mlflow.deployments

client = mlflow.deployments.get_deploy_client("mlflow:http://gateway:5000")

response = client.predict(
    endpoint="my-chat",
    inputs={"messages": [{"role": "user", "content": "Hello"}]},
)

print(response["choices"][0]["message"]["content"])
```

`predict` here is the same method `BaseDeploymentClient` exposes for every backend (Databricks, SageMaker, OpenAI). The application code does not know — and should not care — whether the backend is the gateway, a self-hosted scoring server, or a managed service.

## 6. Authentication and secrets

Provider credentials in `gateway-config.yaml` use environment variable references (`$OPENAI_API_KEY`). The gateway resolves them at startup. There are three deployment styles:

- **Self-hosted** — credentials in environment variables on the gateway host.
- **Databricks-hosted** — credentials in Databricks secret scopes; the gateway reads them via the Databricks SDK.
- **Container-orchestrated** — secrets injected by Kubernetes / ECS / Cloud Run; the gateway is provider-agnostic about how they got there.

What matters architecturally: **application code never sees provider keys.** It holds only a gateway URL and (optionally) an authn token for the gateway itself.

## 7. Governance features

The gateway is the natural place to add cross-cutting policies. Available or designed-for:

- **Rate limiting** — per route, per consumer.
- **Request inspection / logging** — every call lands in MLflow’s tracing system if configured.
- **Failover** — a route can declare a fallback chain (try OpenAI; on timeout, fall back to Anthropic).
- **Caching** — identical payloads can be served from cache (where the typed schema makes equality cheap to check).
- **PII filtering / DLP** — middleware on the FastAPI app inspects payloads.

These features ride on the gateway’s position as the single chokepoint for LLM traffic.

## 8. Gateway and tracing

Routes can be configured to emit a span per call into MLflow’s tracing system, exporting to the same store, OTel collector, or Databricks inference table that the rest of the platform uses. End-to-end visibility — application → gateway → provider — falls out of the same tracing stack documented in [Tracing](tracing.html).

## 9. Where to look in the code

| Need | File |
|---|---|
| Server entry point | [`mlflow/gateway/app.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/gateway/app.py) |
| Config / routes parsing | [`mlflow/gateway/config.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/gateway/config.py) |
| Typed route schemas | [`mlflow/gateway/schemas/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/gateway/schemas) |
| Provider base | [`mlflow/gateway/providers/base.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/gateway/providers/base.py) |
| Provider adapters | [`mlflow/gateway/providers/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/gateway/providers) |
| Client (deployments plugin) | [`mlflow/deployments/mlflow/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/deployments/mlflow/__init__.py) |

[← Back to AI Platform](../ai-platform.html)
