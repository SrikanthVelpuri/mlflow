import subprocess
import numpy as np
import mlflow
import pandas as pd
from itertools import product
from sklearn.metrics import (
    mean_squared_error,
    mean_absolute_error,
    r2_score,
    explained_variance_score,
    mean_absolute_percentage_error
)

# Set tracking URI to local directory
mlflow.set_tracking_uri("./mlruns")

# Define experiment name
EXPERIMENT_NAME = "WineQualityMultipleRuns"

def calculate_metrics(y_true, y_pred):
    """Calculate standard regression metrics"""
    metrics = {
        'rmse': np.sqrt(mean_squared_error(y_true, y_pred)),
        'mae': mean_absolute_error(y_true, y_pred),
        'r2': r2_score(y_true, y_pred),
        'explained_variance': explained_variance_score(y_true, y_pred),
        'mape': mean_absolute_percentage_error(y_true, y_pred),
        'median_ae': np.median(np.abs(y_true - y_pred)),
        'std_error': np.std(y_true - y_pred) / np.sqrt(len(y_true)),
        'mean_pred': np.mean(y_pred),
        'std_pred': np.std(y_pred)
    }
    return metrics

def run_experiment(learning_rate, momentum, batch_size, epochs):
    """Run a single experiment with given parameters"""
    cmd = [
        "python", "train.py",
        "winequality-white.csv",
        "--learning-rate", str(learning_rate),
        "--momentum", str(momentum),
        "--batch-size", str(batch_size),
        "--epochs", str(epochs)
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    
    if process.returncode != 0:
        print("Error in experiment:")
        print(stderr.decode())
    else:
        print("Experiment completed successfully")

def main():
    # Create or get experiment
    experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
    if experiment is None:
        mlflow.create_experiment(EXPERIMENT_NAME)
    mlflow.set_experiment(EXPERIMENT_NAME)
    
    # Define parameter grid
    param_grid = {
        'learning_rate': [0.001, 0.01, 0.1],
        'momentum': [0.0, 0.5, 0.9],
        'batch_size': [16, 32, 64],
        'epochs': [20]  # Fixed epochs for all runs
    }
    
    # Generate all combinations
    keys = param_grid.keys()
    values = param_grid.values()
    combinations = list(product(*values))
    
    # Load data for metric calculation
    data = pd.read_csv("winequality-white.csv", sep=";")
    X = data.drop("quality", axis=1).values
    y = data["quality"].values
    
    # Run experiments
    for i, combo in enumerate(combinations, 1):
        params = dict(zip(keys, combo))
        print(f"\nRunning experiment {i}/{len(combinations)}")
        print(f"Parameters: {params}")
        
        with mlflow.start_run(run_name=f"experiment_{i}") as run:
            # Log parameters
            mlflow.log_params(params)
            
            # Run the experiment
            run_experiment(
                learning_rate=params['learning_rate'],
                momentum=params['momentum'],
                batch_size=params['batch_size'],
                epochs=params['epochs']
            )
            
            try:
                # Load the trained model
                model_path = f"runs:/{run.info.run_id}/model"
                model = mlflow.keras.load_model(model_path)
                
                # Make predictions
                y_pred = model.predict(X).flatten()
                
                # Calculate and log all metrics
                metrics = calculate_metrics(y, y_pred)
                mlflow.log_metrics(metrics)
                
                # Print metrics for monitoring
                print("\nMetrics for this run:")
                for metric_name, value in metrics.items():
                    print(f"{metric_name}: {value:.4f}")
                
            except Exception as e:
                print(f"Error calculating metrics: {str(e)}")

if __name__ == "__main__":
    main()