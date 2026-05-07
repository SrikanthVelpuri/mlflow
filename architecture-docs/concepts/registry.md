---
title: Model Registry — Versioning, Stages, Aliases
---

# Model Registry — Versioning, Stages, Aliases

[← Index](../index.html) · [← ML Platform](../ml-platform.html)

The registry sits between *training* (which produces a model in a run) and *serving* (which needs a stable, named, versioned reference). Architecturally it is a near-mirror of the tracking store — same `AbstractStore` pattern, same FileStore / SqlAlchemyStore split, same REST handlers, same Python client structure.

## 1. The registry primitives

| Entity | Purpose | Source |
|---|---|---|
| `RegisteredModel` | A named container (e.g. `"churn-classifier"`). Has tags and a description. | `mlflow/entities/model_registry/registered_model.py` |
| `ModelVersion` | An immutable snapshot of a model under a registered model. Identified by an auto-incrementing integer. Points at a `source` URI (the run’s artifact). | `mlflow/entities/model_registry/model_version.py` |
| `ModelVersionStage` | Legacy: `None`, `Staging`, `Production`, `Archived`. | `mlflow/entities/model_registry/model_version_stages.py` |
| `RegisteredModelAlias` | Mutable named pointer to a version (`champion`, `challenger`). | `mlflow/entities/model_registry/registered_model_alias.py` |
| `RegisteredModelTag`, `ModelVersionTag` | Free-form metadata. | same dir |

## 2. Stages vs aliases — why both exist

The original lifecycle model was four fixed stages: `None → Staging → Production → Archived`. That worked when one model meant one production deployment, but it falls apart when:

- You want shadow/champion-challenger setups (need *two* “production” versions).
- You want environment-specific labels (`dev`, `qa`, `prod-eu`, `prod-us`).
- A model is promoted by tag, not by stage.

**Aliases** replace stages: an alias is just a mutable string pointing to a version. You can point `champion` at v17 and `challenger` at v18; you can have any number of aliases. New work should use aliases; stages remain for back-compat.

URIs reflect both:

- `models:/churn-classifier/3` — version 3 (immutable).
- `models:/churn-classifier/Staging` — latest in stage (legacy, fragile).
- `models:/churn-classifier@champion` — current alias target (preferred).

## 3. The store layer

`mlflow/store/model_registry/abstract_store.py` is the contract. Operations:

- **Registered models** — `create_registered_model`, `update_registered_model`, `delete_registered_model`, `get_registered_model`, `search_registered_models`, `set_registered_model_tag`, `delete_registered_model_tag`, `set_registered_model_alias`, `delete_registered_model_alias`, `get_model_version_by_alias`.
- **Model versions** — `create_model_version`, `update_model_version`, `transition_model_version_stage`, `delete_model_version`, `get_model_version`, `search_model_versions`, `get_model_version_download_uri`, `set_model_version_tag`, `delete_model_version_tag`.

Concrete stores follow the tracking pattern:

| Backend | File |
|---|---|
| File | [`mlflow/store/model_registry/file_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/model_registry/file_store.py) |
| SQL | [`mlflow/store/model_registry/sqlalchemy_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/model_registry/sqlalchemy_store.py) |
| REST | [`mlflow/store/model_registry/rest_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/model_registry/rest_store.py) |
| Databricks SDK store | [`mlflow/store/model_registry/databricks_workspace_model_registry_rest_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/model_registry/databricks_workspace_model_registry_rest_store.py) |
| Unity Catalog | [`mlflow/store/_unity_catalog/registry/rest_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/_unity_catalog/registry/rest_store.py) |

Unity Catalog is interesting because it changes more than the store — three-level naming (`catalog.schema.model`) shows up in URIs and validation, governed by a different permission model.

## 4. The client layer

[`mlflow/tracking/_model_registry/client.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracking/_model_registry/client.py) defines `ModelRegistryClient` (analogous to `TrackingServiceClient`). Every public registry method on `MlflowClient` ultimately calls one of its methods. The fluent shortcut `mlflow.register_model(model_uri, name)` wraps `MlflowClient.create_model_version` and waits for the version to leave `PENDING_REGISTRATION`.

## 5. The “model copy” on registration

When you register a model, the registry **does not** alias the run’s artifact path — it copies the artifact dir into the registry’s configured artifact location. This is so the registered version remains valid if the originating run is deleted or its artifacts are GC’d. The copy is done by the registry store using `mlflow.utils.model_utils.MODEL_FILE_LIST` to walk the model directory.

## 6. Resolving `models:/...` URIs

[`mlflow/store/artifact/models_artifact_repo.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/models_artifact_repo.py) is what turns a `models:/...` URI into a concrete artifact location:

1. Parse name + (version | stage | alias).
2. Hit the registry store to resolve to a `ModelVersion`.
3. The version’s `source` field gives the underlying artifact URI (s3://, dbfs:/, file://, …).
4. Delegate to the appropriate artifact repository to download.

This is how `mlflow.pyfunc.load_model("models:/churn@champion")` works without the user ever knowing where the bytes actually live.

## 7. REST surface

Same Flask handler module as tracking. Notable endpoints (paths under `/api/2.0/mlflow/registered-models/...` and `.../model-versions/...`):

- `CreateRegisteredModel`, `UpdateRegisteredModel`, `DeleteRegisteredModel`, `SearchRegisteredModels`
- `CreateModelVersion`, `UpdateModelVersion`, `DeleteModelVersion`, `SearchModelVersions`
- `TransitionModelVersionStage`, `GetLatestVersions`
- `SetRegisteredModelAlias`, `DeleteRegisteredModelAlias`, `GetModelVersionByAlias`
- `SetRegisteredModelTag`, `SetModelVersionTag`, `DeleteRegisteredModelTag`, `DeleteModelVersionTag`

The protobufs are in `mlflow/protos/model_registry.proto`.

## 8. Webhooks (and the lack thereof)

Open-source MLflow does not ship a webhook system for stage/alias transitions. Databricks-hosted MLflow does. If you need event-driven CI on transitions, you either poll `search_model_versions` or rely on the platform’s native registry events.

## 9. Where to look in the code

| Need | File |
|---|---|
| Registered model entity | [`mlflow/entities/model_registry/registered_model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/model_registry/registered_model.py) |
| Model version entity | [`mlflow/entities/model_registry/model_version.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/entities/model_registry/model_version.py) |
| Store contract | [`mlflow/store/model_registry/abstract_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/model_registry/abstract_store.py) |
| SQL backend | [`mlflow/store/model_registry/sqlalchemy_store.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/model_registry/sqlalchemy_store.py) |
| `ModelRegistryClient` | [`mlflow/tracking/_model_registry/client.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/tracking/_model_registry/client.py) |
| `models:/...` resolver | [`mlflow/store/artifact/models_artifact_repo.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/models_artifact_repo.py) |
| REST handlers | [`mlflow/server/handlers.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/server/handlers.py) |
| Proto schema | [`mlflow/protos/model_registry.proto`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/protos/model_registry.proto) |

[← Back to ML Platform](../ml-platform.html)
