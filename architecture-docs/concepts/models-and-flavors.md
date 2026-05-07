---
title: Models & Flavors — The Universal Packaging Format
---

# Models & Flavors — The Universal Packaging Format

[← Index](../index.html) · [← ML Platform](../ml-platform.html)

A *flavor* is MLflow’s answer to the question: how do you serve a sklearn model, a PyTorch model, a fine-tuned LLM, and a LangChain agent through the **same** HTTP endpoint? Each framework gets a small adapter module; every adapter contributes to a single `MLmodel` manifest; everything flows through one universal interface called **pyfunc**.

## 1. The MLmodel manifest

The on-disk shape of any saved model:

```
my_model/
├── MLmodel                   # YAML manifest (the only required file)
├── conda.yaml                # Conda env recreating training/inference deps
├── python_env.yaml           # pip-only fallback env
├── requirements.txt          # pinned pip requirements
├── input_example.json        # Optional sample input (drives signature inference)
├── model.pkl                 # Native serialized weights (per flavor)
└── ...                       # Anything else the flavor wants to ship
```

A real `MLmodel` (sklearn):

```yaml
artifact_path: model
flavors:
  python_function:
    loader_module: mlflow.sklearn
    model_path: model.pkl
    predict_fn: predict
    env: conda.yaml
  sklearn:
    pickled_model: model.pkl
    sklearn_version: 1.4.0
    serialization_format: cloudpickle
mlflow_version: 2.20.0
model_uuid: 6b...
run_id: 9f...
signature:
  inputs: '[{"name": "age", "type": "long"}, ...]'
  outputs: '[{"type": "double"}]'
  params: null
saved_input_example_info:
  artifact_path: input_example.json
  type: dataframe
```

`flavors` is a dict of overlapping representations of the same underlying model:

- **`python_function`** — the universal interface. `loader_module` is the Python module whose `_load_pyfunc(path)` function returns a `PyFuncModel`. **Almost every native flavor registers a `python_function` flavor too.** That is what makes the universal pipeline possible.
- **Native flavor** (`sklearn` here) — keeps native typing, lets `mlflow.sklearn.load_model` return the exact estimator instead of a wrapper.

## 2. The Model class

[`mlflow/models/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/model.py) defines:

- `Model` — the in-memory representation of `MLmodel`. Methods include `save`, `load`, `add_flavor`, `get_input_schema`, `get_output_schema`, `to_yaml`, `to_json`.
- `ModelInfo` — a read-only metadata wrapper returned by `log_model` (so callers don’t mutate the saved manifest).
- The constants `MLMODEL_FILE_NAME = "MLmodel"` and `_LOG_MODEL_METADATA_TAG`.

Flavors call `Model.add_flavor(name, **kwargs)` to add their slot, then `Model.save(path)` writes `MLmodel`.

## 3. Signatures and schemas

Defined in [`mlflow/models/signature.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/signature.py) and [`mlflow/types/schema.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/schema.py):

- `Schema` — a list of `ColSpec`s.
- `ColSpec(type, name=None, optional=False, required=True)` — a typed column.
- `TensorSpec(type, shape, name=None)` — for tensors.
- `ParamSchema` / `ParamSpec` — for runtime parameters (e.g. `temperature` for an LLM, threshold for a classifier).
- `ModelSignature(inputs, outputs, params)` — the full signed contract.

`infer_signature(model_input, model_output, params)` walks pandas / numpy / dict / typed Python and produces a signature. The serving layer enforces it on every request: an unexpected column is a 400 before the model ever sees the data.

For GenAI, the `ChatModel` runtime auto-derives a signature from `ChatMessage` + `ChatParams`, so users never write one by hand.

## 4. Pyfunc — the universal interface

[`mlflow/pyfunc/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/pyfunc) is the most important directory in the repo. It provides:

### 4.1 `PyFuncModel` — the runtime wrapper

```python
class PyFuncModel:
    def predict(self, model_input, params: dict | None = None): ...
    def predict_stream(self, model_input, params=None): ...     # for streaming flavors
    @property
    def metadata(self) -> Model: ...
    @property
    def model_config(self) -> dict | None: ...
```

`PyFuncModel.predict` accepts:
- `pandas.DataFrame`
- `numpy.ndarray`
- `pyspark.sql.DataFrame` (Spark UDF path)
- `dict` / `list` (for chat-style and JSON-shaped inputs)

It validates against the signature, calls the flavor’s underlying predict, and returns the result.

### 4.2 `PythonModel` — for custom logic

If you have logic that doesn’t fit any framework — preprocessing, multiple sub-models, business rules — you subclass `PythonModel` (defined in [`mlflow/pyfunc/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/model.py)):

```python
class PythonModel:
    def load_context(self, context): ...      # called once on load
    def predict(self, context, model_input, params=None): ...
    def predict_stream(self, context, model_input, params=None): ...
```

`context` is a `PythonModelContext` that carries `artifacts` (file paths the model needs at inference) and `model_config` (a dict of config knobs).

### 4.3 `ChatModel` — typed chat agents

The GenAI counterpart of `PythonModel` (covered fully in [ChatModel & Agents](chatmodel-agents.html)):

```python
class ChatModel(PythonModel):
    def predict(self, context, messages: list[ChatMessage], params: ChatParams)
        -> ChatCompletionResponse: ...
```

The pyfunc loader transparently coerces JSON to `ChatMessage` / `ChatParams` and back.

### 4.4 The dispatch table

When you `mlflow.pyfunc.load_model("models:/my-model@prod")`:

1. The artifact repo downloads the model dir.
2. `MLmodel` is read; `flavors.python_function.loader_module` tells pyfunc *which* module knows how to deserialize.
3. That module exposes `_load_pyfunc(path)` (a private convention). It returns a callable wrapped in `PyFuncModel`.
4. The wrapper applies signature validation and (for `ChatModel` / `PythonModel` subclasses) the typed adapter layer.

## 5. The 28+ flavors

Each lives in `mlflow/<framework>/`. They share the same skeleton:

```python
# mlflow/<framework>/__init__.py
FLAVOR_NAME = "<framework>"

def save_model(<framework>_model, path, signature=None, input_example=None, ...):
    # 1. Serialise model into `path`
    # 2. Build conda/pip env
    # 3. Build Model() and add native flavor + python_function flavor
    # 4. Write MLmodel

def log_model(<framework>_model, artifact_path, ...) -> ModelInfo:
    # save_model into a temp dir, then upload to artifact repo + register w/ run

def load_model(model_uri, dst_path=None):
    # Download artifact if needed, deserialize, return native object

def _load_pyfunc(path):
    # Internal hook used by mlflow.pyfunc.load_model.
    # Returns either the model itself (if it has .predict) or a wrapper.

@autologging_integration(FLAVOR_NAME)
def autolog(...):
    # safe_patch(framework.<entry_point>, _autolog_wrapper, ...)
```

The 28+ flavors split into rough categories:

- **Classical ML:** sklearn, xgboost, lightgbm, catboost, prophet, statsmodels, spacy, mleap, onnx, paddle, h2o, pmdarima, fastai, diviner, shap.
- **Deep learning:** pytorch, tensorflow, keras.
- **Spark:** spark, pyspark.ml.
- **GenAI / LLMs:** transformers, langchain, llama_index, dspy, openai, bedrock, anthropic, gemini, groq, crewai, autogen, sentence_transformers, litellm, promptflow.
- **Universal:** pyfunc.

The GenAI flavors are documented in detail under [Auto-Tracing Integrations](auto-tracing.html) and [ChatModel & Agents](chatmodel-agents.html).

## 6. Environments and reproducibility

Every saved model includes:

- **`conda.yaml`** — the “heavy” environment, with conda-forge channels.
- **`python_env.yaml`** + **`requirements.txt`** — pip-only path for non-conda servers.

The flavor’s `save_model` builds these from `pip_requirements` + `extra_pip_requirements` + the flavor’s own pinned dependencies. At load time, the serving layer can recreate the env (`mlflow models build-docker`, `mlflow models predict --env-manager conda`, …).

## 7. Recent: pyfunc data validation in `predict`

PR [#14130](https://github.com/SrikanthVelpuri/mlflow/pull/14130) added stricter data validation inside `PyFuncModel.predict`. Type-hinted custom `PythonModel.predict` signatures are now honored — when the developer annotates `def predict(self, context, model_input: list[dict], params=None)`, the wrapper coerces and validates the input automatically. This is part of the broader push to make agent code as ergonomic as a typed function call while still being safe to expose over HTTP.

## 8. Where to look in the code

| Need | File |
|---|---|
| `Model`, `ModelInfo`, MLmodel constants | [`mlflow/models/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/model.py) |
| `ModelSignature`, `infer_signature` | [`mlflow/models/signature.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/models/signature.py) |
| `Schema`, `ColSpec`, `ParamSchema` | [`mlflow/types/schema.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/types/schema.py) |
| Pyfunc runtime wrapper | [`mlflow/pyfunc/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/__init__.py) |
| `PythonModel`, `ChatModel`, `PythonModelContext` | [`mlflow/pyfunc/model.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/pyfunc/model.py) |
| Pyfunc loaders (chat, function) | [`mlflow/pyfunc/loaders/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/pyfunc/loaders) |
| Reference flavor (sklearn) | [`mlflow/sklearn/__init__.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/sklearn/__init__.py) |

[← Back to ML Platform](../ml-platform.html)
