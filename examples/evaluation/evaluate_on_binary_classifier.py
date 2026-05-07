import shap
import xgboost
import numpy as np
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
import mlflow
from mlflow.models import infer_signature
import mlflow.xgboost
from hyperopt import fmin, tpe, hp, STATUS_OK, Trials
import logging
import pandas as pd
from urllib.parse import urlparse
import optuna
import time
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, BatchSpanProcessor
import matplotlib.pyplot as plt  # Add this if not already present

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set up OpenTelemetry tracing
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(ConsoleSpanExporter())
)

# Define the experiment name
EXPERIMENT_NAME = "adult_income_prediction"

def create_experiment():
    """Create or get existing MLflow experiment."""
    try:
        experiment_id = mlflow.create_experiment(EXPERIMENT_NAME)
    except Exception:
        experiment_id = mlflow.get_experiment_by_name(EXPERIMENT_NAME).experiment_id
    mlflow.set_experiment(EXPERIMENT_NAME)
    return experiment_id

def load_data():
    """Load and preprocess the data."""
    with tracer.start_as_current_span("load_data") as span:
        X, y = shap.datasets.adult()
        span.set_attribute("dataset_size", len(X))
        return train_test_split(X, y, test_size=0.33, random_state=42)

def objective(params):
    """Objective function for hyperparameter optimization."""
    with tracer.start_as_current_span("hyperopt_objective") as span:
        with mlflow.start_run(nested=True):
            mlflow.log_params(params)
            
            model = xgboost.XGBClassifier(**params)
            scores = cross_val_score(model, X_train, y_train, cv=3, scoring='f1')
            
            mean_f1 = scores.mean()
            mlflow.log_metric("cv_f1_score", mean_f1)
            
            span.set_attribute("f1_score", mean_f1)
            return {'loss': -mean_f1, 'status': STATUS_OK}
    
def train_model(best_params):
    """Train the model with the best parameters."""
    with tracer.start_as_current_span("train_model") as span:
        start_time = time.time()
        
        # Create model with best parameters
        model = xgboost.XGBClassifier(
            **best_params,
            use_label_encoder=False,  # Add this to avoid warnings
        )
        
        # Simple fit without callbacks
        model.fit(
            X_train, 
            y_train,
            eval_set=[(X_test, y_test)],
            verbose=True
        )
        
        training_time = time.time() - start_time
        span.set_attribute("training_time", training_time)
        
        # Add training metrics to span
        y_pred = model.predict(X_test)
        test_score = f1_score(y_test, y_pred)
        span.set_attribute("test_f1_score", test_score)
        
        return model

def evaluate_model(model, run_id):
    """Evaluate the model and log metrics."""
    with tracer.start_as_current_span("evaluate_model") as span:
        # Make predictions
        y_pred = model.predict(X_test)
        y_prob = model.predict_proba(X_test)

        # Calculate metrics
        metrics = {
            "accuracy": accuracy_score(y_test, y_pred),
            "precision": precision_score(y_test, y_pred),
            "recall": recall_score(y_test, y_pred),
            "f1": f1_score(y_test, y_pred)
        }
        
        # Log metrics
        for metric_name, metric_value in metrics.items():
            mlflow.log_metric(metric_name, metric_value)
            span.set_attribute(f"metric_{metric_name}", metric_value)

        # Generate and log SHAP values
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_test)
        
        # Create and save SHAP plots
        shap.summary_plot(shap_values, X_test, show=False)
        mlflow.log_figure(plt.gcf(), f"shap_summary_plot.png")
        plt.close()

        return metrics

def main():
    """Main execution function."""
    with tracer.start_as_current_span("main"):
        # Create or get experiment
        experiment_id = create_experiment()
        
        # Load data
        global X_train, X_test, y_train, y_test
        X_train, X_test, y_train, y_test = load_data()

        # Define hyperparameter search space
        space = {
            'max_depth': hp.choice('max_depth', range(3, 10)),
            'learning_rate': hp.loguniform('learning_rate', np.log(0.01), np.log(0.3)),
            'n_estimators': hp.choice('n_estimators', [100, 200, 300, 400, 500]),
            'min_child_weight': hp.choice('min_child_weight', range(1, 7)),
            'subsample': hp.uniform('subsample', 0.6, 1.0),
            'colsample_bytree': hp.uniform('colsample_bytree', 0.6, 1.0),
            'gamma': hp.uniform('gamma', 0, 0.5)
        }

        with mlflow.start_run(experiment_id=experiment_id) as run:
            # Hyperparameter optimization
            trials = Trials()
            best = fmin(fn=objective,
                       space=space,
                       algo=tpe.suggest,
                       max_evals=20,
                       trials=trials)

            # Convert hyperopt results to actual parameter values
            best_params = {
                'max_depth': best['max_depth'] + 3,
                'learning_rate': best['learning_rate'],
                'n_estimators': [100, 200, 300, 400, 500][best['n_estimators']],
                'min_child_weight': best['min_child_weight'] + 1,
                'subsample': best['subsample'],
                'colsample_bytree': best['colsample_bytree'],
                'gamma': best['gamma'],
                'objective': 'binary:logistic'  # Add this for binary classification
            }

            # Log best parameters
            mlflow.log_params(best_params)

            # Train model with best parameters
            model = train_model(best_params)

            # Evaluate model
            metrics = evaluate_model(model, run.info.run_id)

            # Log model
            signature = infer_signature(X_train, model.predict(X_train))
            mlflow.xgboost.log_model(model, 
                                   "model", 
                                   signature=signature,
                                   registered_model_name="adult_income_classifier")

            # Create evaluation dataset
            eval_data = X_test.copy()
            eval_data["label"] = y_test

            # Evaluate using MLflow's built-in evaluator
            model_uri = f"runs:/{run.info.run_id}/model"
            result = mlflow.evaluate(
                model_uri,
                eval_data,
                targets="label",
                model_type="classifier",
                evaluators=["default"],
            )

            logger.info(f"Best parameters: {best_params}")
            logger.info(f"Metrics: {metrics}")
            logger.info(f"MLflow Run ID: {run.info.run_id}")
            logger.info(f"MLflow tracking URI: {mlflow.get_tracking_uri()}")

if __name__ == "__main__":
    main()