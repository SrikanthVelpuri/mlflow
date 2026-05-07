---
title: The Traditional ML Platform
---

# The Traditional ML Platform

[← Back to index](index.html)

This is the “classic” MLflow surface: tracking experiments, packaging models from arbitrary frameworks, versioning them in a registry, and serving them. Every concept here predates the GenAI work, but the GenAI platform reuses **all** of this infrastructure — so understanding it pays double.

> **Mental model.** MLflow is a thin Python client and Flask server in front of a pluggable backend. The same five primitives — *Experiment, Run, Model, Registered Model, Artifact* — flow through every layer.

## 1. The five primitives

| Primitive | What it represents | Where it lives | Backed by |
|---|---|---|---|
| **Experiment** | A named bucket of runs (a project, a hyperparameter sweep) | `mlflow/entities/experiment.py` | tracking store |
| **Run** | A single execution: params in, metrics + artifacts out | `mlflow/entities/run.py` | tracking store |
| **Model** | A framework-agnostic package (the `MLmodel` file + weights) | `mlflow/models/model.py` | artifact repository |
| **Registered Model + Version** | A named, versioned reference to a Model | `mlflow/entities/model_registry/` | model-registry store |
| **Artifact** | Any file logged against a run (plots, data, model dirs) | — | artifact repository |

These five are the only nouns you need to read the codebase.

## 2. Tracking — runs, metrics, params, artifacts

Tracking is what you touch when you call `mlflow.log_metric(...)` or `mlflow.start_run()`. The path from your call to durable storage is the spine of the platform.

### 2.1 Three layers — fluent, client, store

```
your code
   │   mlflow.log_metric("loss", 0.1)
   ▼
mlflow/tracking/fluent.py        ← thread-local active run, `start_run`, `log_*`
   │
   ▼
mlflow/tracking/client.py        ← MlflowClient: explicit run_id-driven CRUD
   │
   ▼
mlflow/tracking/_tracking_service/client.py   ← TrackingServiceClient: RPC translation
   │
   ▼
mlflow/store/tracking/abstract_store.py       ← AbstractStore: the contract
   │
   ├── FileStore           (mlflow/store/tracking/file_store.py)         ─ YAML/JSON on disk
   ├── SqlAlchemyStore     (mlflow/store/tracking/sqlalchemy_store.py)   ─ Postgres / MySQL / SQLite / MSSQL
   └── RestStore           (mlflow/store/tracking/rest_store.py)         ─ talks to a remote MLflow server
```

The fluent API is convenient but optional. Every fluent call has a direct equivalent on `MlflowClient`. Internally the fluent module just looks up the active run id from a thread-local and calls the client.

### 2.2 The store contract

`AbstractStore` defines the operations every backend must implement: `create_experiment`, `search_experiments`, `create_run`, `update_run_info`, `log_batch`, `get_metric_history`, `record_logged_model`, etc. New backends only need to subclass this — the REST API and the Python client do not change.

`FileStore` writes one YAML/JSON file per entity. Good for laptops and CI; awful for concurrent writes. `SqlAlchemyStore` is the production backend — it owns the schema (`SqlExperiment`, `SqlRun`, `SqlMetric`, `SqlParam`, `SqlTag`, etc.) and uses Alembic migrations under `mlflow/store/db_migrations/`.

### 2.3 The async logging queue

High-throughput training loops call `log_metric` thousands of times per second. Synchronous round-trips to the store would dominate training time, so MLflow batches them through `AsyncLoggingQueue`: log calls land in an in-process queue, a worker thread flushes them with `log_batch`. The queue is flushed on `end_run` and at interpreter exit, so failures during training still preserve completed batches.

### 2.4 The REST surface

The Flask app lives in [`mlflow/server/handlers.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/server/handlers.py). Every endpoint:

1. Unmarshals a protobuf request from `mlflow/protos/`.
2. Delegates to a `Store` instance (chosen by config).
3. Marshals the proto response.

Because the *contract* is defined in `.proto` files, the JS UI, the R client, and the Java client all speak the same wire format as the Python `RestStore`.

→ [Concept deep dive: Tracking](concepts/tracking.html)

## 3. Models — the universal packaging format

A trained sklearn estimator and a fine-tuned transformer should both be loadable with the same `mlflow.pyfunc.load_model(uri).predict(x)`. That promise is delivered by the `MLmodel` file format.

### 3.1 The `MLmodel` file

Every saved model directory contains a YAML manifest:

```yaml
artifact_path: model
flavors:
  python_function:
    loader_module: mlflow.sklearn
    model_path: model.pkl
    env: conda.yaml
  sklearn:
    sklearn_version: 1.4.0
    serialization_format: cloudpickle
signature:
  inputs: '[{"name": "age", "type": "long"}, ...]'
  outputs: '[{"type": "double"}]'
run_id: 9f...
```

`flavors` is a dict of overlapping representations of the same model. Almost every native flavor *also* registers a `python_function` flavor — that is the universal entry point for serving and tools.

### 3.2 The `Model` Python class

[`mlflow/models/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/model.py) defines `Model` (the in-memory representation of `MLmodel`) and `ModelInfo` (the metadata returned by `log_model`). The class is what flavors read and write — they never touch YAML directly.

### 3.3 Signatures and schemas

[`mlflow/models/signature.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/signature.py) defines `ModelSignature` (input + output + params schemas) using `Schema`, `ColSpec`, and `ParamSchema` from `mlflow/types/schema.py`. Signatures are inferred from `input_example` whenever possible. They are enforced at serving time by the scoring server, which is what makes pyfunc safe to expose over HTTP.

### 3.4 Flavors

A *flavor* is a Python module that knows how to serialize and deserialize one framework. Every flavor exports the same trio:

```python
def save_model(model, path, signature=None, input_example=None, ...): ...
def log_model(model, artifact_path, ...) -> ModelInfo: ...
def load_model(model_uri, ...): ...
```

Native flavors in this repo (28+, see top-level `mlflow/*/`):

- **Classical ML:** `sklearn`, `xgboost`, `lightgbm`, `catboost`, `prophet`, `statsmodels`, `spacy`, `mleap`, `onnx`, `paddle`, `h2o`, `pmdarima`, `fastai`, `diviner`, `shap`
- **Deep learning:** `pytorch`, `tensorflow`, `keras`
- **Spark:** `spark`, `pyspark.ml`
- **GenAI** (covered on the AI page): `transformers`, `langchain`, `llama_index`, `dspy`, `openai`, `bedrock`, `anthropic`, `gemini`, `groq`, `crewai`, `autogen`, `sentence_transformers`, `litellm`, `promptflow`
- **Universal:** `pyfunc`

→ [Concept deep dive: Models & Flavors](concepts/models-and-flavors.html)

## 4. Model Registry — versioning and lifecycle

`mlflow/store/model_registry/` mirrors the tracking store layout: one `AbstractStore` with `FileStore` and `SqlAlchemyStore` implementations, fronted by `ModelRegistryClient`.

The registry’s primitives:

- **RegisteredModel** — a name (e.g. `"churn-classifier"`) and tags.
- **ModelVersion** — an immutable snapshot pointing at a tracked model artifact, identified by an auto-incrementing integer.
- **Stage** — `None`, `Staging`, `Production`, `Archived`. Legacy but still supported.
- **Alias** — a human-readable, mutable pointer (e.g. `champion`, `challenger`). Replaces stages for new workflows because aliases are decoupled from a fixed lifecycle.

Transitions and CRUD go through REST endpoints in `server/handlers.py` (`CreateRegisteredModel`, `TransitionModelVersionStage`, `SetRegisteredModelAlias`, …).

→ [Concept deep dive: Model Registry](concepts/registry.html)

## 5. Projects — reproducible runs

[`mlflow/projects/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/projects) lets you turn a directory containing an `MLproject` YAML into something that can be `mlflow run`-ed locally, in Docker, on Kubernetes, or on Databricks.

`MLproject` declares:

- A name.
- An environment: `conda.yaml`, a Docker image, a `python_env.yaml`, or a Databricks job spec.
- Entry points — named commands with typed parameters (`{model_name}`, `{epochs: int = 5}`).

Backends in `mlflow/projects/backend/` implement the same interface (`run_project`) for different execution targets. A `SubmittedRun` object polls status so the client can wait or cancel.

→ [Concept deep dive: Projects](concepts/projects.html)

## 6. Serving — the scoring server and the deployment interface

### 6.1 The pyfunc scoring server

`mlflow/pyfunc/scoring_server/` is the Flask WSGI app that turns any logged pyfunc model into an HTTP endpoint. It exposes:

- `GET /ping`, `GET /health`, `GET /version`
- `POST /invocations`

`/invocations` accepts a handful of payload shapes (`dataframe_split`, `dataframe_records`, `instances`, `inputs`) so it is compatible with both MLflow’s native format and TensorFlow Serving / SageMaker conventions. Inputs are validated against the `ModelSignature` before reaching the model.

### 6.2 The deployment plugin interface

[`mlflow/deployments/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/deployments) defines `BaseDeploymentClient` with one method per lifecycle action: `create_deployment`, `update_deployment`, `predict`, `list_endpoints`, `delete_deployment`. The `plugin_manager.py` discovers third-party deployment targets via Python entry points.

In-tree targets:

- `mlflow` — local MLflow gateway / scoring server
- `databricks` — Databricks Model Serving
- `sagemaker` — AWS SageMaker
- `openai` — OpenAI-compatible endpoint (used by the AI Gateway)

→ [Concept deep dive: Serving & Deployments](concepts/serving.html)

## 7. Datasets and artifact repositories

### 7.1 Datasets

`mlflow/data/` introduces a `Dataset` abstraction so a run can record *what data it was trained or evaluated on*. Each dataset has a name, a digest (a content hash, so identical datasets across runs match), a `DatasetSource`, and an optional schema/profile.

Concrete dataset classes wrap pandas, numpy, Spark, HuggingFace, Delta, and HTTP/UC-Volume sources.

### 7.2 Artifact repositories

Models and any other logged files land in an artifact repository. `mlflow/store/artifact/artifact_repository_registry.py` routes the URI scheme to a concrete implementation:

| Scheme | Backend |
|---|---|
| `file://`, local path | `LocalArtifactRepository` |
| `s3://` | `S3ArtifactRepository` |
| `gs://` | `GCSArtifactRepository` |
| `wasbs://`, `abfss://` | `AzureBlobArtifactRepository`, `AzureDataLakeArtifactRepository` |
| `dbfs:/`, `databricks://...` | `DBFSArtifactRepository`, `UnityCatalogModelsArtifactRepository` |
| `hdfs://`, `ftp://`, `sftp://`, `r2://`, `http(s)://` | dedicated repos |

Each implementation only needs `log_artifact`, `log_artifacts`, `download_artifacts`, `list_artifacts`, `delete_artifacts`. This is the same store-pattern dance, applied to bytes instead of rows.

→ [Concept deep dive: Datasets & Artifact Repositories](concepts/data-and-artifacts.html)

## 8. How a single training run flows through the system

Putting it all together — a typical `with mlflow.start_run(): model.fit(...)` block:

1. **`start_run`** (fluent.py) — resolves or creates an experiment, asks the configured tracking store for a `Run`, sets it as the thread-local active run.
2. **autologging** — if enabled, framework-specific autologgers (`mlflow.sklearn.autolog()`, etc.) `safe_patch` `fit` to record params, metrics, the trained model, and a signature — without you writing a single `log_*` call.
3. **`log_metric` / `log_param`** — buffered through `AsyncLoggingQueue`, flushed via `log_batch` to the store.
4. **`log_model`** — flavor’s `save_model` writes weights + `MLmodel` to a temp dir, the artifact repository uploads the dir, the run is annotated with the model URI, and a `LoggedModel` entity is recorded.
5. **`end_run`** — flushes the async queue, marks the run `FINISHED`, persists final tags.
6. **`register_model`** *(optional)* — copies the artifact dir into the registry’s configured artifact location and creates a new `ModelVersion` row.
7. **serving** — `mlflow models serve -m models:/churn@champion` resolves the alias to a version, downloads the artifact dir, loads the pyfunc flavor, and starts the scoring server.

Every arrow in that chain is a thin layer over the next one. That is the point.

---

Continue with the **[AI / GenAI Platform deep dive →](ai-platform.html)** to see how the same primitives extend to LLMs, agents, and traces.
