import sys
import time
import numpy as np
import openai
from mlflow.metrics.genai import answer_correctness
from mlflow.metrics import latency
import pandas as pd
import mlflow
import os
import textstat
import evaluate

def get_standard_metrics():
    """Define standard metrics for QA evaluation"""
    metrics = [
        mlflow.metrics.make_metric(
            eval_fn=lambda _, predicted: len(str(predicted).split()),
            name="response_length",
            greater_is_better=None
        ),
        mlflow.metrics.make_metric(
            eval_fn=lambda _, predicted: textstat.flesch_kincaid_grade(str(predicted)),
            name="flesch_kincaid_grade",
            greater_is_better=False
        ),
        mlflow.metrics.make_metric(
            eval_fn=lambda _, predicted: evaluate.load("toxicity").compute(predictions=[str(predicted)])["toxicity"],
            name="toxicity",
            greater_is_better=False
        ),
    ]
    return metrics

def build_and_evaluate_model_with_prompt(system_prompt):
    """Build and evaluate a QA model with the given system prompt"""

    openai.api_key = os.getenv("OPENAI_API_KEY")
    if not openai.api_key:
        raise ValueError("OpenAI API key not found. Please set the OPENAI_API_KEY environment variable.")

    with mlflow.start_run(run_name="qa_model") as run:
        print(f"Using system prompt: {system_prompt}")
        
        # Remove the explicit parameter logging since autolog will handle it
        # mlflow.log_param("system_prompt", system_prompt)  # Removed this line
        
        mlflow.openai.autolog()

        # Create model
        logged_model = mlflow.openai.log_model(
            model="gpt-4",
            task=openai.chat.completions,
            artifact_path="model",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "{question}"},
            ],
        )

        # Sample questions for evaluation
        questions = pd.DataFrame({
            "question": [
                "How do you create a run with MLflow?",
                "How do you log a model with MLflow?",
                "What are the main components of MLflow?",
                "How do you track experiments in MLflow?",
                "How do you deploy models with MLflow?"
            ]
        })
        
        # Evaluate model with standard metrics
        evaluation_results = mlflow.evaluate(
            model=logged_model.model_uri,
            model_type="question-answering",
            data=questions,
            extra_metrics=get_standard_metrics(),answer_correctness(),latency(),
            evaluators="default"
        )

        # Load model for additional evaluation
        model = mlflow.pyfunc.load_model(f"runs:/{run.info.run_id}/model")
        
        # Track and display responses
        response_times = []
        print("\nModel Responses:")
        for question in questions["question"]:
            start_time = time.time()
            response = model.predict(question)
            response_time = time.time() - start_time
            response_times.append(response_time)
            
            print(f"\nQ: {question}")
            print(f"A: {response}")

        # Log performance metrics
        mlflow.log_metrics({
            "avg_response_time": np.mean(response_times),
            "max_response_time": np.max(response_times),
            "min_response_time": np.min(response_times),
            "num_questions": len(questions),
        })

        return run.info.run_id

def main():
    """Main function to run the QA model evaluation"""
    system_prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Your job is to answer questions about MLflow."
    
    try:
        run_id = build_and_evaluate_model_with_prompt(system_prompt)
        print(f"\nRun completed successfully with ID: {run_id}")
    except Exception as e:
        print(f"Error during execution: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()