---
title: Projects — Reproducible Runs
---

# Projects — Reproducible Runs

[← Index](../index.html) · [← ML Platform](../ml-platform.html)

A **Project** is a directory plus a `MLproject` YAML file that turns ad-hoc training scripts into something you can `mlflow run`-launch identically on a laptop, in Docker, on Kubernetes, or on Databricks. It is the older sibling of `mlflow models build-docker` — the pre-trained equivalent for the *training* side of the lifecycle.

## 1. The MLproject file

```yaml
name: my-training-project

# Pick exactly one environment style:
conda_env: conda.yaml             # conda
# docker_env: { image: my-org/trainer:1.0 }   # docker
# python_env: python_env.yaml     # pip-only
# databricks_spark_job: ...       # databricks job

entry_points:
  main:
    parameters:
      data_path: { type: string, default: "data/train.csv" }
      epochs:    { type: int,    default: 5 }
      lr:        { type: float,  default: 0.001 }
    command: "python train.py --data {data_path} --epochs {epochs} --lr {lr}"

  evaluate:
    parameters:
      model_uri: string
    command: "python evaluate.py --model {model_uri}"
```

`MLproject` files are case-insensitively located: `MLproject`, `MLProject`, `mlproject` are all accepted (`mlflow/projects/_project_spec.py` handles this). Entry points are typed; types currently include `string`, `int`, `float`, `path`, `uri`.

## 2. The project model

[`mlflow/projects/_project_spec.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/projects/_project_spec.py) parses YAML into a typed object graph:

- `Project` — name, env spec, dict of entry points.
- `EntryPoint(name, parameters, command)` — knows how to validate and substitute parameters into its command template (`compute_parameters`, `compute_command`).

Validation is strict: a parameter without a default that you don’t pass is an error before the run starts.

## 3. Backends

[`mlflow/projects/backend/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/projects/backend) implements one class per execution target. The shared interface is `AbstractBackend.run(project_uri, entry_point, parameters, ...) -> SubmittedRun`.

| Backend | When | File |
|---|---|---|
| Local | Default — same machine, conda env or python_env or docker | `mlflow/projects/backend/local.py` |
| Docker | When the project declares `docker_env` | `mlflow/projects/backend/docker.py` |
| Databricks | `--backend databricks`, runs as a Databricks job | `mlflow/projects/backend/databricks.py` |
| Kubernetes | `--backend kubernetes`, submits a Kubernetes Job | `mlflow/projects/backend/kubernetes.py` |

Plugin backends are supported via Python entry points (`mlflow.project_backend`). That is how third parties (e.g. AWS SageMaker) register custom execution targets.

## 4. SubmittedRun — the polling abstraction

`mlflow/projects/submitted_run.py` defines `SubmittedRun`, a thin handle returned from `backend.run`. It exposes:

- `run_id` — the MLflow run created for this project execution.
- `wait()` — block until the underlying job finishes; returns success.
- `cancel()` — best-effort kill.
- `get_status()` — `RUNNING` / `FINISHED` / `FAILED` / `KILLED`.

Local runs use `subprocess.Popen`; Databricks runs poll the Jobs API; Kubernetes runs poll job status via `kubectl`. The MLflow run row is updated in tandem so the UI reflects backend state.

## 5. Projects from URIs

Projects can be referenced by URI: a local path, a Git URL (with optional ref), or a `mlflow-artifacts://` URI. Resolution lives in [`mlflow/projects/utils.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/projects/utils.py) — for Git, it shells out to clone the repo into a working dir before parsing the `MLproject` file. That is what makes `mlflow run https://github.com/foo/bar.git -P epochs=10` work.

## 6. Environments

Three environment styles, three resolution paths:

- **Conda** — read `conda.yaml`, hash for cache key, materialize via `conda env create -f` into a per-project env directory under `~/.mlflow/envs`. Subsequent runs reuse the cached env.
- **Python (pip)** — read `python_env.yaml` (with optional `requirements.txt`), build a venv via `virtualenv`, `pip install -r requirements.txt`. Same caching scheme.
- **Docker** — build the declared image (or pull it if remote), then `docker run` with the entry-point command. The local working directory is bind-mounted unless the project is fetched from Git, in which case the cloned dir is mounted.
- **Databricks Spark Job** — translate the entry point into a Databricks Jobs API request; the job runs on the configured cluster.

The cache invalidation key is a hash of the env spec content, not the file mtime — so identical envs across projects share materialised environments.

## 7. Where Projects sit in the bigger picture

Projects predate the `ChatModel` / pyfunc deployment workflow. Most modern usage centres on **logging models** and **deploying them**, not on running `mlflow run`. But Projects remain useful for:

- Reproducing a published research artifact (`mlflow run https://github.com/...`).
- Standardising team training entry points (CI launches `mlflow run . -e nightly_train`).
- Multi-step pipelines (one entry point launches another via `mlflow.projects.run`).

The new GenAI surface does not introduce a parallel projects concept — agents are packaged via `ChatModel` and deployed directly.

## 8. Where to look in the code

| Need | File |
|---|---|
| Top-level run entry | [`mlflow/projects/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/projects/__init__.py) |
| MLproject parser | [`mlflow/projects/_project_spec.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/projects/_project_spec.py) |
| URI resolution | [`mlflow/projects/utils.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/projects/utils.py) |
| Backends | [`mlflow/projects/backend/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/projects/backend) |
| Submitted run abstraction | [`mlflow/projects/submitted_run.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/projects/submitted_run.py) |

[← Back to ML Platform](../ml-platform.html)
