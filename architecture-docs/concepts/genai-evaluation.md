---
title: GenAI Evaluation
---

# GenAI Evaluation

[← Index](../index.html) · [← AI Platform](../ai-platform.html)

Classical metrics — accuracy, F1, RMSE — assume a single deterministic prediction with a known ground truth. LLM systems break both halves of that assumption: outputs are stochastic free-form text, and ground truth is often subjective. MLflow’s GenAI evaluation harness exists to score those outputs anyway, using a combination of heuristics, traditional metrics, and **LLM-as-judge** scoring.

The architectural insight: **every LLM call inside an evaluation is itself a trace.** A low-scoring row in the eval UI is one click from the trace that produced it. That closed loop — eval scores ↔ traces — is the whole point.

## 1. The data model

[`mlflow/evaluation/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/evaluation) defines:

| Entity | Purpose |
|---|---|
| `Evaluation` | A single example: `inputs`, `outputs`, optional `targets`, plus `assessments` and `metrics`. One row per case. |
| `Assessment` | One judgment on one example: `name`, `value`, optional `rationale`, `source`, `metadata`. |
| `AssessmentSource` | Who/what produced it: human label, LLM judge, heuristic, code-based check. |
| `Metric` | Aggregate over many assessments: `correctness/mean`, `toxicity/p95`, etc. |

An `Assessment.value` can be:

- **Numeric** — for graded scores (0.0–1.0 quality, latency in ms).
- **Categorical** — for labels (`safe` / `unsafe`, `correct` / `incorrect` / `partial`).
- **Boolean** — for pass/fail checks.

`Assessment.source` is what makes the eval auditable — every score has provenance, so a UI can filter to just human-labelled rows or just LLM-judge rows.

## 2. The judge concept

A *judge* is a callable that takes an evaluation row and returns one or more `Assessment`s. Three judge categories:

### 2.1 Heuristic / code judges

Pure Python: regex matches, JSON schema validation, semantic-similarity to a reference, BLEU/ROUGE for translation, exact-match accuracy. They are fast and deterministic and cost nothing per call.

### 2.2 LLM-as-judge

A judge that calls another LLM with a structured prompt (“rate the assistant’s answer for correctness on a 1–5 scale and explain why”) and parses the response. Built-in judges include:

- **correctness** — does the answer match the target?
- **groundedness** — is the answer supported by retrieved context?
- **relevance** — does the answer address the question?
- **safety** — is the answer free of disallowed content?
- **toxicity** — does the answer contain harmful content?

LLM judges are slow and stochastic, but cover dimensions heuristics can’t. The harness records the judge’s LLM call as a trace, so a confusing assessment is itself debuggable.

### 2.3 Custom judges

Arbitrary Python callables registered with `mlflow.evaluate(..., extra_metrics=[...])`. They follow the same `Assessment`-returning contract as the built-ins.

## 3. The orchestration entry point

`mlflow.evaluate(...)` and `mlflow.genai.evaluate(...)` are the user-facing APIs. They:

1. Take a `model_uri`, a Python callable, or pre-computed predictions.
2. Take an evaluation dataset (a pandas frame, an `EvaluationDataset`, a list of dicts).
3. Take a list of judges and metrics.
4. Run inference on each row (unless predictions are pre-computed).
5. Apply each judge; collect `Assessment`s.
6. Aggregate per-row assessments into per-run `Metric`s.
7. Persist everything to the active MLflow run.

The result is browseable in the UI: a per-row grid with input, output, target, every judge’s assessment with its rationale, and a chart of aggregate metrics. Clicking through to a row opens the trace.

## 4. EvaluationDataset

[`mlflow/data/evaluation_dataset.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/data/evaluation_dataset.py) extends the `Dataset` abstraction with eval-specific structure: an inputs column, an optional targets column, optional grouping/ID columns. It carries a digest like any dataset, so reusing the same eval set across runs is detectable.

## 5. The trace ↔ assessment link

Every row in an eval produces:

- A trace for the **agent under test** (the model whose output is being judged).
- One trace per **LLM-judge call** that scored the row.

`Assessment.metadata.trace_request_id` (where applicable) connects an assessment back to the originating trace. The UI renders the linkage as a per-row drilldown.

This is also why the gateway and tracing systems matter for evaluation — without traces, judges produce numbers without context. With traces, every score is one click from the call chain that produced it.

## 6. Where evaluation lives in code

[`mlflow/evaluation/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/evaluation) contains the entity definitions and shared infrastructure. [`mlflow/genai/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/genai) holds the GenAI-specific orchestration, including the LLM-judge prompts and the API entry points.

The classical `mlflow.evaluate` (in `mlflow/models/evaluation/`) is older and broader — it covers tabular regression/classification metrics, SHAP explanations, fairness, and similar. It now shares entities with the GenAI path so a single evaluation run can mix metrics across the spectrum.

## 7. Worked example — what happens during `mlflow.evaluate`

```python
import mlflow

with mlflow.start_run():
    result = mlflow.evaluate(
        model="runs:/abc/agent",
        data=eval_df,
        targets="ground_truth_answer",
        model_type="question-answering",
        extra_metrics=[mlflow.metrics.genai.answer_correctness()],
    )
```

1. The harness loads `runs:/abc/agent` as a `PyFuncModel` (likely a `ChatModel`).
2. For each row in `eval_df`:
   a. Calls `model.predict({"messages": [...]})` → produces a trace.
   b. Computes deterministic metrics (token counts, exact match).
   c. Calls each LLM judge → each call is itself a trace.
   d. Collects `Assessment`s.
3. Aggregates per-row assessments into per-run metrics (`answer_correctness/mean`, `latency/p95`).
4. Logs the `Evaluation` rows and the metrics to the active run.
5. Returns an `EvaluationResult` with a DataFrame of per-row assessments.

After `evaluate` returns:

- The run’s metrics page shows aggregate scores.
- The run’s traces page shows one trace per row plus all judge traces.
- The run’s evaluation page shows a per-row grid with assessments and rationales.

All three views read from the same tracking store rows.

## 8. Where to look in the code

| Need | File |
|---|---|
| `Evaluation` entity | [`mlflow/evaluation/evaluation.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/evaluation/evaluation.py) |
| `Assessment` entity | [`mlflow/evaluation/assessment.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/evaluation/assessment.py) |
| Eval dataset | [`mlflow/data/evaluation_dataset.py`](https://github.com/SrikanthVelpuri/mlflow/blob/master/mlflow/data/evaluation_dataset.py) |
| `mlflow.evaluate` (classical + GenAI) | [`mlflow/models/evaluation/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/models/evaluation) |
| GenAI judges & API | [`mlflow/genai/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/genai) |
| Built-in metric / judge prompts | [`mlflow/metrics/genai/`](https://github.com/SrikanthVelpuri/mlflow/tree/master/mlflow/metrics/genai) |

[← Back to AI Platform](../ai-platform.html)
