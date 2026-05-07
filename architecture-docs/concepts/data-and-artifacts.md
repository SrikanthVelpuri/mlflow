---
title: Datasets & Artifact Repositories
---

# Datasets & Artifact Repositories

[← Index](../index.html) · [← ML Platform](../ml-platform.html)

Two related but distinct subsystems share this page:

- **Datasets** — first-class metadata about the *data* a run consumed (or evaluated against). They are stored in the tracking store as inputs to a run.
- **Artifact repositories** — the byte-storage layer underneath models, datasets-as-files, plots, and anything else logged with `log_artifact`.

## 1. Datasets

`mlflow/data/` introduces a `Dataset` abstraction so a run can record *what data trained or evaluated it*. The motivation: metrics without a known data lineage are unreproducible.

### 1.1 The `Dataset` base class

[`mlflow/data/dataset.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/data/dataset.py):

```python
class Dataset:
    @property
    def name(self) -> str: ...
    @property
    def digest(self) -> str: ...        # content hash — identical data → identical digest
    @property
    def source(self) -> DatasetSource: ...
    @property
    def schema(self) -> Schema | None: ...
    @property
    def profile(self) -> dict | None: ...   # row count, null counts, etc.
    def to_dict(self) -> dict: ...
    def to_json(self) -> str: ...
```

The `digest` is what makes datasets useful across runs — two runs that consumed the exact same Parquet file will both reference the same digest, so the UI can group runs by dataset.

### 1.2 `DatasetSource` — where the data came from

[`mlflow/data/dataset_source.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/data/dataset_source.py) is the base; concrete sources include:

- `FilesystemDatasetSource` — local path or `file://`.
- `HttpDatasetSource` — URL.
- `HuggingFaceDatasetSource` — HF Hub dataset identifier.
- `DeltaDatasetSource` — Delta table reference.
- `SparkDatasetSource` — registered Spark table.
- `CodeDatasetSource` — “the data is constructed in code; here is the source”.
- `UCVolumeDatasetSource` — Unity Catalog Volume path.

Sources serialise to JSON for the tracking store and round-trip back via a registry keyed by source type.

### 1.3 Concrete dataset implementations

One per common in-memory representation:

| File | Wraps |
|---|---|
| `mlflow/data/pandas_dataset.py` | `pandas.DataFrame` |
| `mlflow/data/numpy_dataset.py` | `numpy.ndarray` |
| `mlflow/data/spark_dataset.py` | `pyspark.sql.DataFrame` |
| `mlflow/data/huggingface_dataset.py` | `datasets.Dataset` |
| `mlflow/data/tensorflow_dataset.py` | `tf.data.Dataset` |
| `mlflow/data/polars_dataset.py` | `polars.DataFrame` |
| `mlflow/data/evaluation_dataset.py` | A labelled dataset for `mlflow.evaluate` |

Each implementation provides constructors like `from_pandas(df, source, name=None, targets="label")`.

### 1.4 Linking a dataset to a run

`mlflow.log_input(dataset, context="train")` stores a `DatasetInput` row associating the run with the dataset and a free-form context label (`train`, `eval`, `validation`, …). The store call is `log_inputs` on `AbstractStore`.

## 2. Artifact repositories

Models and any other logged files live in artifact repositories. These are entirely independent of the tracking store — you can run MLflow with a SQL tracking store and an S3 artifact repo, or a file tracking store and DBFS artifacts. The two layers communicate only through URIs.

### 2.1 The contract

[`mlflow/store/artifact/artifact_repo.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/artifact_repo.py):

```python
class ArtifactRepository:
    def log_artifact(self, local_file, artifact_path=None): ...
    def log_artifacts(self, local_dir, artifact_path=None): ...
    def list_artifacts(self, path=None) -> list[FileInfo]: ...
    def download_artifacts(self, artifact_path, dst_path=None) -> str: ...
    def delete_artifacts(self, artifact_path=None): ...
```

The contract is short on purpose — anything more elaborate (resumable uploads, presigned URLs, multi-part) is an implementation detail of the concrete repo.

### 2.2 The URI-routed registry

[`mlflow/store/artifact/artifact_repository_registry.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/artifact_repository_registry.py) keeps a mapping from URI scheme to factory. `get_artifact_repository(uri)` parses the scheme and instantiates the right repo.

| Scheme | Implementation |
|---|---|
| `file://` (and bare paths) | `LocalArtifactRepository` |
| `s3://` | `S3ArtifactRepository` |
| `gs://` | `GCSArtifactRepository` |
| `wasbs://`, `abfss://` | `AzureBlobArtifactRepository`, `AzureDataLakeArtifactRepository` |
| `dbfs:/` | `DBFSArtifactRepository` |
| `databricks://...` | `DatabricksArtifactRepository`, `UnityCatalogModelsArtifactRepository` |
| `hdfs://` | `HdfsArtifactRepository` |
| `sftp://`, `ftp://` | `SftpArtifactRepository`, `FtpArtifactRepository` |
| `http://`, `https://` | `HttpArtifactRepository` |
| `r2://` | `R2ArtifactRepository` |
| `runs:/<run_id>/<path>` | `RunsArtifactRepository` (resolves to the run’s `artifact_uri`) |
| `models:/<name>/<version|alias|stage>` | `ModelsArtifactRepository` (resolves through the registry) |
| `mlflow-artifacts://` | `MlflowArtifactsRepository` (proxies through the tracking server) |

### 2.3 The mlflow-artifacts proxy

`mlflow-artifacts://` is interesting: it routes artifact reads/writes through the **tracking server**, which then forwards to whatever real artifact backend is configured. This lets you have a tracking server with private S3 credentials while clients only need network access to the tracking server. The handler lives in `mlflow/server/handlers.py` (look for `_get_artifact_repo_mlflow_artifacts`).

### 2.4 Cloud auth

Each cloud repo uses the cloud SDK’s default credential chain (`boto3` for S3, `google-cloud-storage` for GCS, `azure-identity` for Azure). MLflow does not invent its own credential mechanism. Notable exceptions:

- **Databricks artifacts** use the Databricks SDK’s credential resolution (PAT / OAuth / Azure AD).
- **Unity Catalog** artifacts use UC-issued temporary credentials with limited scope.
- **Presigned URL** repository (`presigned_url_artifact_repo.py`) lets the tracking server hand out short-lived signed URLs so clients can upload directly to cloud storage without ever holding cloud credentials.

### 2.5 The performance subtext

`log_artifacts(local_dir)` is what flavor `save_model` calls. For a 7B-parameter LLM saved to `transformers` flavor, that is ~14 GB of bytes. Each cloud repo implements multipart / parallel upload; the local repo just `shutil.copy`s. **Cross-region or cross-cloud uploads can dominate run time**, which is why `register_model` from a remote artifact location uses bulk copy semantics that the cloud SDK natively supports (e.g. `s3:CopyObject`) when the source and destination are in the same provider.

## 3. Where to look in the code

| Need | File |
|---|---|
| Dataset base class | [`mlflow/data/dataset.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/data/dataset.py) |
| Dataset source registry | [`mlflow/data/dataset_source_registry.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/data/dataset_source_registry.py) |
| Pandas dataset | [`mlflow/data/pandas_dataset.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/data/pandas_dataset.py) |
| Artifact repo contract | [`mlflow/store/artifact/artifact_repo.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/artifact_repo.py) |
| URI registry | [`mlflow/store/artifact/artifact_repository_registry.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/artifact_repository_registry.py) |
| S3 repo | [`mlflow/store/artifact/s3_artifact_repo.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/s3_artifact_repo.py) |
| Models URI repo | [`mlflow/store/artifact/models_artifact_repo.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/store/artifact/models_artifact_repo.py) |

[← Back to ML Platform](../ml-platform.html)
