---
title: Serving & Deployments
---

# Serving & Deployments

[← Index](../index.html) · [← ML Platform](../ml-platform.html)

Two distinct things live under the “serving” umbrella:

1. **The pyfunc scoring server** — the Flask app that turns *any* logged model into an HTTP endpoint, locally or in a container.
2. **The deployments plugin interface** — an abstraction over external serving platforms (Databricks Model Serving, SageMaker, the AI Gateway).

Both surface the model through the same `predict` semantics, but the operational story differs.

## 1. The pyfunc scoring server

`mlflow/pyfunc/scoring_server/` builds a Flask WSGI app at request time. The CLI hooks are:

- `mlflow models serve -m <model_uri> [-p 5000]` — start the server.
- `mlflow models predict -m <model_uri> -i <input_path>` — invoke once, no HTTP.
- `mlflow models build-docker -m <model_uri> -n my-image` — produce a Docker image that runs the same server.
- `mlflow models generate-dockerfile -m <model_uri>` — emit the Dockerfile for inspection.

### 1.1 Endpoints

| Method | Path | Behaviour |
|---|---|---|
| GET | `/ping`, `/health` | Loads the model lazily on first request; returns 200 only after a successful warm load. |
| GET | `/version` | Server + MLflow versions. |
| POST | `/invocations` | The actual prediction endpoint. |

Headers control behaviour: `Content-Type` selects parser (`application/json`, `text/csv`, `application/json; format=pandas-split`, `application/json; format=pandas-records`).

### 1.2 Payload formats

`/invocations` accepts a deliberate set of shapes (parsed in `scoring_server/__init__.py`):

| Shape | Example |
|---|---|
| `dataframe_split` | `{"dataframe_split": {"columns": ["a","b"], "data": [[1,2],[3,4]]}}` |
| `dataframe_records` | `{"dataframe_records": [{"a":1,"b":2}, {"a":3,"b":4}]}` |
| `instances` | `{"instances": [[1,2],[3,4]]}` *(TF-Serving compatible)* |
| `inputs` | `{"inputs": {"a": [1,3], "b": [2,4]}}` *(SageMaker-compatible columnar)* |
| `messages`/`params` | `{"messages": [...], "temperature": 0.7}` *(ChatModel)* |

The parser converts to a pandas DataFrame (or routes to ChatModel’s typed coercion), validates against the model signature, calls `predict`, and JSON-serialises the response.

### 1.3 Streaming

Pyfunc supports streaming via `predict_stream`. The serving layer detects a streaming flavor (transformers text-generation, ChatModel) and exposes `POST /invocations` as a chunked response. ChatModel emits `ChatCompletionChunk` JSON lines compatible with the OpenAI streaming format.

### 1.4 Environment management

`--env-manager <local|virtualenv|conda>` chooses how dependencies are materialised before the server starts. `local` is fastest (assumes the host already has compatible deps); `virtualenv` and `conda` recreate exactly what was logged at training time.

## 2. The deployments interface

`mlflow/deployments/` defines the abstraction *over* concrete serving backends. The contract is [`mlflow/deployments/base.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/deployments/base.py):

```python
class BaseDeploymentClient:
    def create_deployment(self, name, model_uri, flavor=None, config=None, endpoint=None): ...
    def update_deployment(self, name, ...): ...
    def delete_deployment(self, name, ...): ...
    def list_deployments(self, ...): ...
    def get_deployment(self, name, ...): ...
    def predict(self, deployment_name=None, inputs=None, endpoint=None): ...
    def explain(self, ...): ...

    # Endpoint primitives (newer, used by the AI Gateway)
    def create_endpoint(self, name, config=None): ...
    def update_endpoint(self, endpoint, config): ...
    def delete_endpoint(self, endpoint): ...
    def list_endpoints(self): ...
    def get_endpoint(self, endpoint): ...
```

**Plugin discovery** is in [`mlflow/deployments/plugin_manager.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/deployments/plugin_manager.py): targets are registered as Python entry points (`mlflow.deployments` group). `get_deploy_client(target_uri)` parses the scheme and returns the right plugin.

In-tree targets:

| Target URI | What it talks to | Module |
|---|---|---|
| `local`, `mlflow://...` | The pyfunc scoring server (via Docker or a local process) | `mlflow/deployments/mlflow/` |
| `databricks` | Databricks Model Serving REST API | `mlflow/deployments/databricks/` |
| `sagemaker` | AWS SageMaker | `mlflow/sagemaker/` |
| `openai` | OpenAI-compatible endpoint (used by the AI Gateway client) | `mlflow/deployments/openai/` |

Out-of-tree plugins follow the same pattern.

## 3. The relationship between the scoring server and the gateway

A `ChatModel` agent registered with MLflow can be deployed:

- **Self-hosted** — `mlflow models serve` starts the pyfunc scoring server. The endpoint accepts `{"messages": [...], "temperature": 0.7}` and returns a `ChatCompletionResponse`.
- **Behind the AI Gateway** — the gateway adds a route configured to call the local scoring server (or a Databricks Model Serving endpoint). Clients then talk to the gateway, not directly to the model.
- **On Databricks** — Databricks Model Serving recognises `ChatModel` as a chat-style model and exposes it on the serving endpoint with the standard chat schema; the same `ChatCompletionResponse` is what comes back.

In all three cases, the *contract* is identical because the wrapper layer is identical (`_ChatModelPyfuncWrapper`). The deployment plugin only changes where the bytes run.

## 4. Building Docker images

`mlflow models build-docker` ([implementation](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/docker_utils.py)) generates a Dockerfile that:

1. Bases on a thin Python image.
2. Copies the model dir.
3. Recreates the env (`pip install -r requirements.txt` or conda).
4. `CMD ["mlflow", "models", "serve", "-m", "/opt/ml/model", "-h", "0.0.0.0", "-p", "5000"]`.

This is the same path SageMaker uses to deploy models — the SageMaker plugin builds an image with `build-docker`, pushes to ECR, and registers it with the SageMaker endpoint.

## 5. Where to look in the code

| Need | File |
|---|---|
| Scoring server (Flask app) | [`mlflow/pyfunc/scoring_server/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/scoring_server/__init__.py) |
| Streaming server | [`mlflow/pyfunc/scoring_server/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/pyfunc/scoring_server) |
| Docker build helpers | [`mlflow/models/docker_utils.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/docker_utils.py) |
| Deployment client interface | [`mlflow/deployments/base.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/deployments/base.py) |
| Plugin manager | [`mlflow/deployments/plugin_manager.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/deployments/plugin_manager.py) |
| Databricks deploy client | [`mlflow/deployments/databricks/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/deployments/databricks/__init__.py) |
| SageMaker | [`mlflow/sagemaker/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/sagemaker) |

[← Back to ML Platform](../ml-platform.html)
