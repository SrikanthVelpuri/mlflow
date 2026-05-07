import subprocess
import mlflow
import numpy as np
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.metrics import explained_variance_score, max_error, mean_absolute_percentage_error
import pandas as pd
from itertools import product

# Set tracking URI to local directory
mlflow.set_tracking_uri("./mlruns")

# Define experiment name
EXPERIMENT_NAME = "MultipleRandomSearchWithMetrics"

def calculate_metrics(y_true, y_pred):
    """Calculate multiple regression metrics"""
    metrics = {
        'rmse': np.sqrt(mean_squared_error(y_true, y_pred)),
        'mae': mean_absolute_error(y_true, y_pred),
        'r2': r2_score(y_true, y_pred),
        'explained_variance': explained_variance_score(y_true, y_pred),
        'max_error': max_error(y_true, y_pred),
        'mape': mean_absolute_percentage_error(y_true, y_pred),
        'median_absolute_error': np.median(np.abs(y_true - y_pred)),
        'mean_absolute_deviation': np.mean(np.abs(y_true - np.mean(y_true))),
        'standard_error': np.std(y_true - y_pred) / np.sqrt(len(y_true))
    }
    return metrics

def run_random_search(max_runs, max_p, epochs, metric, seed):
    """Run a single random search with given parameters"""
    cmd = [
        "python", "search_random.py",
        "winequality-white.csv",
        "--max-runs", str(max_runs),
        "--max-p", str(max_p),
        "--epochs", str(epochs),
        "--metric", str(metric),
        "--seed", str(seed)
    ]
    print(f"\nRunning Random Search with parameters:")
    print(f"max_runs: {max_runs}, max_p: {max_p}, epochs: {epochs}, metric: {metric}, seed: {seed}")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    
    if process.returncode != 0:
        print("Error in random search:")
        print(stderr.decode())
    else:
        print("Random search completed successfully")

def evaluate_model(model_path):
    """Load test data and evaluate model with all metrics"""
    # Load test data
    data = pd.read_csv("winequality-white.csv", sep=";")
    X_test = data.drop("quality", axis=1).values
    y_test = data["quality"].values
    
    # Load model and make predictions
    loaded_model = mlflow.keras.load_model(model_path)
    y_pred = loaded_model.predict(X_test)
    
    # Calculate and return all metrics
    return calculate_metrics(y_test, y_pred.flatten())

def main():
    # Create or get experiment
    experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
    if experiment is None:
        experiment_id = mlflow.create_experiment(EXPERIMENT_NAME)
    else:
        experiment_id = experiment.experiment_id
    mlflow.set_experiment(EXPERIMENT_NAME)
    
    # Define different configurations to try
    configs = [
        # Format: (max_runs, max_p, epochs, metric, seed)
        (4, 2, 10, "rmse", 42),      # Quick test with RMSE
        (8, 2, 15, "mae", 123),      # Small search with MAE
        (12, 3, 20, "mape", 456),    # Medium search with MAPE
        (16, 4, 25, "rmse", 789),    # Larger search with RMSE
        (20, 4, 30, "mae", 101)      # Extended search with MAE
    ]
    
    # Run random search with each configuration
    for i, (max_runs, max_p, epochs, metric, seed) in enumerate(configs, 1):
        print(f"\nStarting Random Search Configuration {i}/{len(configs)}")
        with mlflow.start_run(run_name=f"random_search_config_{i}") as run:
            # Log configuration parameters
            mlflow.log_params({
                "config_number": i,
                "max_runs": max_runs,
                "max_p": max_p,
                "epochs": epochs,
                "optimization_metric": metric,
                "seed": seed
            })
            
            # Run the random search
            run_random_search(max_runs, max_p, epochs, metric, seed)
            
            # Log additional metrics after the run
            try:
                # Get the latest model path from the run
                model_path = f"runs:/{run.info.run_id}/model"
                all_metrics = evaluate_model(model_path)
                
                # Log all calculated metrics
                mlflow.log_metrics(all_metrics)
                
                # Print metrics for monitoring
                print("\nFinal Metrics:")
                for metric_name, value in all_metrics.items():
                    print(f"{metric_name}: {value:.4f}")
                    
            except Exception as e:
                print(f"Error calculating additional metrics: {str(e)}")

if __name__ == "__main__":
    main()