---
title: Tracking — Runs, Metrics, Params, Artifacts
---

# Tracking — Runs, Metrics, Params, Artifacts

[← Index](../index.html) · [← ML Platform](../ml-platform.html)

Tracking is the spine of MLflow. Every other subsystem — registry, projects, serving, even the new tracing surface — writes data into the tracking store or reads metadata from it. Understanding the tracking layer is the prerequisite for everything else.

## 1. The entities

Defined under [`mlflow/entities/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/entities):

| Entity | Key fields | Purpose |
|---|---|---|
| `Experiment` | `experiment_id`, `name`, `artifact_location`, `lifecycle_stage`, tags | A bucket for runs that share a goal. |
| `Run` | `run_id`, `experiment_id`, `status`, `start_time`, `end_time`, `artifact_uri`, `user_id`, `lifecycle_stage` | A single execution. |
| `RunData` | `metrics`, `params`, `tags` | The mutable, queryable payload of a run. |
| `Metric` | `key`, `value`, `timestamp`, `step` | A scalar observation. Time-series, not last-write-wins. |
| `Param` | `key`, `value` | An immutable string-typed input. |
| `RunTag` | `key`, `value` | Mutable string-typed metadata (run name, source git commit, etc.). |
| `LoggedModel` | `model_id`, `name`, `flavors`, `metrics`, `params`, `tags` | A model logged to a run, queryable independently. |
| `Dataset` / `DatasetInput` | name, digest, source, schema | What data fed the run. |
| `TraceInfo` | `request_id`, `experiment_id`, status, timestamps, tags | Header for a tracing trace (covered in the AI platform). |

The same `tracking_uri` configuration governs all of them. They land in the same store and the same artifact repository.

## 2. The three layers

```
fluent.py            ── start_run, log_metric, log_param, log_artifact, log_model, autolog
   │                    Holds the active run in a thread-local + RunContextProvider chain
   ▼
client.py            ── MlflowClient: every fluent call has a 1:1 method here
   │                    Adds explicit `run_id` parameters and methods that fluent doesn’t expose
   ▼
_tracking_service/   ── TrackingServiceClient: dispatch to a Store implementation
client.py            ── Selects FileStore / SqlAlchemyStore / RestStore based on tracking_uri
   │
   ▼
store/tracking/      ── AbstractStore subclasses
```

### 2.1 Fluent — `mlflow/tracking/fluent.py`

Everything you can do with `mlflow.<verb>(...)` lives here. Notable globals:

- `_active_run_stack` — a process-level stack so nested runs work.
- `_active_experiment_id` — the currently selected experiment.
- `RunContextProvider` chain — populates default tags (git commit, user, source name) by walking a list of providers (`mlflow/tracking/context/`).

Autologging entry points (`mlflow.autolog`, `mlflow.sklearn.autolog`, …) live alongside the fluent API and use `safe_patch` to wrap framework calls.

### 2.2 Client — `mlflow/tracking/client.py`

`MlflowClient` is what you reach for in scripts that manage other people’s runs (a CI job promoting a model, a UI backend reading metric history). Every method takes a run_id; it has no concept of an active run. It is also the only layer that exposes some advanced operations: `search_runs` filtering by metrics, `get_metric_history`, `update_model_version`, `set_registered_model_alias`.

### 2.3 Service / store — `mlflow/tracking/_tracking_service/`

`TrackingServiceClient` exists so the public `MlflowClient` doesn’t directly depend on a store implementation. The service client also contains the `AbstractStore` registry: schemes (`file://`, `sqlite:///`, `postgresql://`, `http://`) are mapped to store classes via `_tracking_store_registry`.

## 3. The store contract

`mlflow/store/tracking/abstract_store.py` is the contract. Every backend implements:

- **Experiments** — `create_experiment`, `get_experiment`, `get_experiment_by_name`, `search_experiments`, `delete_experiment`, `restore_experiment`, `set_experiment_tag`.
- **Runs** — `create_run`, `update_run_info`, `delete_run`, `restore_run`, `get_run`, `search_runs`, `set_tag`, `delete_tag`, `log_param`, `log_metric`, `log_batch`, `record_logged_model`.
- **Metrics** — `get_metric_history` (time series).
- **Datasets** — `log_inputs` (link a `DatasetInput` to a run).
- **Traces** — `start_trace`, `end_trace`, `delete_traces`, `search_traces`, `get_trace_info`. Yes — traces live in the same store.
- **Logged Models** — `create_logged_model`, `get_logged_model`, `search_logged_models`, `finalize_logged_model`.

### 3.1 FileStore — `mlflow/store/tracking/file_store.py`

Layout on disk:

```
mlruns/
  0/                                  # experiment_id (folder name)
    meta.yaml                         # Experiment metadata
    <run_id>/                         # one run
      meta.yaml                       # Run info
      params/<param_name>             # one file per param
      metrics/<metric_name>           # newline-delimited (timestamp, value, step)
      tags/<tag_name>                 # tag value
      artifacts/                      # artifact root for this run
```

It is human-readable and works on any filesystem — but concurrent writers can corrupt metric files, so it is laptop-only.

### 3.2 SqlAlchemyStore — `mlflow/store/tracking/sqlalchemy_store.py`

Tables (declared in `mlflow/store/tracking/dbmodels/models.py`):

- `experiments`, `experiment_tags`
- `runs`, `tags`, `params`, `metrics`, `latest_metrics`
- `datasets`, `inputs`, `input_tags`
- `traces`, `trace_tags`, `trace_request_metadata`
- `logged_models`, `logged_model_metrics`, `logged_model_params`, `logged_model_tags`

`latest_metrics` exists because `search_runs` needs O(1) access to the most recent value of every metric without scanning the time series. Triggers / writes keep it consistent.

Schema migrations are managed by Alembic under `mlflow/store/db_migrations/`. Every schema change ships a versioned migration script so existing tracking servers can upgrade in place.

### 3.3 RestStore — `mlflow/store/tracking/rest_store.py`

Same `AbstractStore` interface, but every method serialises to a protobuf request and HTTPs it to `<tracking_uri>/api/2.0/mlflow/...`. Used whenever `tracking_uri` is `http(s)://` or `databricks`.

## 4. The async logging queue

`AsyncLoggingQueue` (in `mlflow/tracking/_tracking_service/`) wraps `log_batch`. It is enabled by default for fluent calls and:

- Batches metric/param/tag operations from the active run.
- Flushes on a timer, on backpressure, on `end_run`, and at interpreter exit.
- Surfaces errors back to the caller asynchronously (to avoid silent data loss).

This is the difference between “tracking adds 0.5 ms to my training loop” and “tracking is the bottleneck.” Important to know if you ever need to disable it (e.g. for deterministic test ordering): `mlflow.config.enable_async_logging(False)`.

## 5. The REST API

The Flask app is wired in [`mlflow/server/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/server/__init__.py); endpoints in [`mlflow/server/handlers.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/server/handlers.py). The handler module:

1. Decodes a protobuf request from `mlflow.protos.service_pb2`.
2. Calls the configured store.
3. Encodes the protobuf response.

The same proto schemas back the JS UI, the R client, the Java client, and any third-party SDK. **If you need to add a tracking field, you add it to the proto file first, then to the store, then to the handler.**

## 6. Run context providers

When you `start_run`, MLflow auto-tags it with metadata that didn’t come from your code. That magic lives under [`mlflow/tracking/context/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/tracking/context):

- `git_context.py` — git commit, branch, dirty state.
- `default_context.py` — user, source name, source type.
- `databricks_*` — notebook id, cluster id, job id.
- `system_environment_context.py` — environment variables matched by an allow-list.

Each provider implements `RunContextProvider.in_context()` and `tags()`. They are discovered through entry points, so plugins can add their own.

## 7. Querying runs

`search_runs(experiment_ids, filter_string, run_view_type, max_results, order_by)` is the workhorse. The filter mini-language supports:

- `metrics."val_loss" < 0.1`
- `params.optimizer = "adam" AND params.lr > "0.001"`
- `tags.git_branch = "main"`
- `attributes.status = "FINISHED"`

Parsing is in `mlflow/utils/search_utils.py` — a small SQL-like grammar implemented with `sqlparse`. Both the file and SQL stores translate the parsed AST into their respective query languages.

## 8. Where to look in the code

| Need | File |
|---|---|
| Public fluent API | [`mlflow/tracking/fluent.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracking/fluent.py) |
| Run-id-based client | [`mlflow/tracking/client.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracking/client.py) |
| Store contract | [`mlflow/store/tracking/abstract_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/tracking/abstract_store.py) |
| File backend | [`mlflow/store/tracking/file_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/tracking/file_store.py) |
| SQL backend | [`mlflow/store/tracking/sqlalchemy_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/tracking/sqlalchemy_store.py) |
| REST handlers | [`mlflow/server/handlers.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/server/handlers.py) |
| Search grammar | [`mlflow/utils/search_utils.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/utils/search_utils.py) |
| Protobuf schemas | [`mlflow/protos/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/protos) |

[← Back to ML Platform](../ml-platform.html)
