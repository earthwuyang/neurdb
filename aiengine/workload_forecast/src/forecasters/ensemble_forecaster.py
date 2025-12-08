"""
Ensemble forecaster that combines multiple models with dynamic weight adjustment
Implements weighted averaging and performance-based weight optimization
"""

import pickle
import numpy as np
from typing import List, Tuple, Dict, Any, Optional, Union
from datetime import datetime, timedelta
import logging
from scipy.optimize import minimize
from sklearn.metrics import mean_squared_error

from .base_forecaster import BaseForecaster, ForecasterError
from .ar_forecaster import ARForecaster
from .rnn_forecaster import RNNForecaster
from .spectral_forecaster import SpectralForecaster

logger = logging.getLogger(__name__)


class EnsembleForecaster(BaseForecaster):
    """
    Ensemble forecaster that combines multiple models
    Uses dynamic weight adjustment based on recent performance
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Ensemble forecaster

        Args:
            config: Configuration parameters
                - models: List of model types to include (default: ['ar', 'rnn', 'spectral'])
                - initial_weights: Initial weights for models (default: equal weights)
                - weight_optimization: Method for weight optimization ('performance', 'uniform', 'manual')
                - performance_window: Window size for performance evaluation (default: 50)
                - weight_smoothing: Smoothing factor for weight updates (default: 0.1)
                - min_weight: Minimum weight for any model (default: 0.05)
                - max_weight: Maximum weight for any model (default: 0.8)
                - retrain_interval: How often to retrain individual models (default: 100)
                - enable_diversity_bonus: Whether to encourage model diversity (default: True)
                - diversity_weight: Weight for diversity bonus (default: 0.1)
        """
        super().__init__(config)

        self.model_types = self.config.get('models', ['ar', 'rnn', 'spectral'])
        self.initial_weights = self.config.get('initial_weights', None)
        self.weight_optimization = self.config.get('weight_optimization', 'performance')
        self.performance_window = self.config.get('performance_window', 50)
        self.weight_smoothing = self.config.get('weight_smoothing', 0.1)
        self.min_weight = self.config.get('min_weight', 0.05)
        self.max_weight = self.config.get('max_weight', 0.8)
        self.retrain_interval = self.config.get('retrain_interval', 100)
        self.enable_diversity_bonus = self.config.get('enable_diversity_bonus', True)
        self.diversity_weight = self.config.get('diversity_weight', 0.1)

        self.forecasters = {}
        self.weights = {}
        self.performance_history = {}
        self.prediction_history = {}
        self.training_data = None
        self.last_timestamp = None
        self.prediction_count = 0

        # Initialize individual forecasters
        self._initialize_forecasters()

        # Initialize weights
        self._initialize_weights()

    def _initialize_forecasters(self):
        """Initialize individual forecaster models"""
        model_configs = {
            'ar': {'max_order': 10, 'auto_order': True, 'seasonal': True},
            'rnn': {'hidden_size': 64, 'num_layers': 2, 'sequence_length': 24, 'epochs': 50},
            'spectral': {'n_harmonics': 15, 'frequency_threshold': 0.05, 'detrend_method': 'linear'}
        }

        for model_type in self.model_types:
            if model_type == 'ar':
                self.forecasters[model_type] = ARForecaster(model_configs.get('ar', {}))
            elif model_type == 'rnn':
                self.forecasters[model_type] = RNNForecaster(model_configs.get('rnn', {}))
            elif model_type == 'spectral':
                self.forecasters[model_type] = SpectralForecaster(model_configs.get('spectral', {}))
            else:
                logger.warning(f"Unknown model type: {model_type}")

            # Initialize performance tracking
            self.performance_history[model_type] = []
            self.prediction_history[model_type] = []

        logger.info(f"Initialized forecasters: {list(self.forecasters.keys())}")

    def _initialize_weights(self):
        """Initialize model weights"""
        n_models = len(self.forecasters)

        if self.initial_weights is not None and len(self.initial_weights) == n_models:
            weights = np.array(self.initial_weights)
        else:
            # Equal weights initially
            weights = np.ones(n_models) / n_models

        # Normalize weights
        weights = weights / np.sum(weights)

        # Apply min/max constraints
        weights = np.clip(weights, self.min_weight, self.max_weight)
        weights = weights / np.sum(weights)

        for i, model_type in enumerate(self.forecasters.keys()):
            self.weights[model_type] = weights[i]

        logger.info(f"Initial weights: {self.weights}")

    def _calculate_diversity_bonus(self, predictions: Dict[str, np.ndarray]) -> Dict[str, float]:
        """
        Calculate diversity bonus for each model based on prediction differences

        Args:
            predictions: Dictionary of model predictions

        Returns:
            Dictionary of diversity bonuses
        """
        if len(predictions) < 2:
            return {model: 0.0 for model in predictions}

        diversity_scores = {}
        model_names = list(predictions.keys())
        n_models = len(model_names)

        for model in model_names:
            diversity_score = 0.0
            model_preds = predictions[model]

            for other_model in model_names:
                if other_model != model:
                    other_preds = predictions[other_model]
                    # Calculate negative correlation as diversity measure
                    if len(model_preds) == len(other_preds) and len(model_preds) > 1:
                        correlation = np.corrcoef(model_preds, other_preds)[0, 1]
                        if not np.isnan(correlation):
                            diversity_score += (1.0 - abs(correlation))

            diversity_scores[model] = diversity_score / (n_models - 1)

        return diversity_scores

    def _optimize_weights_performance(self):
        """Optimize weights based on recent performance"""
        if not all(len(history) >= 10 for history in self.performance_history.values()):
            return  # Not enough performance data

        # Calculate recent performance scores
        performance_scores = {}
        for model_type, history in self.performance_history.items():
            if len(history) > 0:
                # Use inverse of recent RMSE as performance score
                recent_errors = history[-self.performance_window:]
                avg_error = np.mean(recent_errors)
                performance_scores[model_type] = 1.0 / (avg_error + 1e-6)
            else:
                performance_scores[model_type] = 0.1

        # Normalize performance scores
        total_performance = sum(performance_scores.values())
        if total_performance > 0:
            for model_type in performance_scores:
                performance_scores[model_type] /= total_performance

        # Apply diversity bonus if enabled
        if self.enable_diversity_bonus and len(self.prediction_history) > 0:
            recent_predictions = {}
            for model_type in self.forecasters.keys():
                if len(self.prediction_history[model_type]) > 0:
                    recent_predictions[model_type] = np.array(self.prediction_history[model_type][-10:])

            if len(recent_predictions) == len(self.forecasters):
                diversity_scores = self._calculate_diversity_bonus(recent_predictions)
                for model_type in performance_scores:
                    diversity_bonus = diversity_scores.get(model_type, 0.0) * self.diversity_weight
                    performance_scores[model_type] += diversity_bonus

        # Normalize again
        total_performance = sum(performance_scores.values())
        if total_performance > 0:
            for model_type in performance_scores:
                performance_scores[model_type] /= total_performance

        # Smooth weight updates
        for model_type in self.forecasters.keys():
            new_weight = performance_scores.get(model_type, 0.0)
            old_weight = self.weights.get(model_type, 0.0)
            self.weights[model_type] = (1 - self.weight_smoothing) * old_weight + self.weight_smoothing * new_weight

        # Apply constraints
        self._apply_weight_constraints()

    def _apply_weight_constraints(self):
        """Apply min/max weight constraints and renormalize"""
        # Apply min/max constraints
        for model_type in self.weights:
            self.weights[model_type] = np.clip(self.weights[model_type], self.min_weight, self.max_weight)

        # Renormalize to sum to 1
        total_weight = sum(self.weights.values())
        if total_weight > 0:
            for model_type in self.weights:
                self.weights[model_type] /= total_weight

    def get_min_required_samples(self) -> int:
        """Get minimum samples required for ensemble training"""
        # Use the maximum requirement among individual models
        min_samples = 50
        for forecaster in self.forecasters.values():
            min_samples = max(min_samples, forecaster.get_min_required_samples())
        return min_samples

    def fit(self, time_series: List[Tuple[datetime, float]]) -> None:
        """
        Train all individual models

        Args:
            time_series: List of (timestamp, value) tuples
        """
        if not self.validate_time_series(time_series):
            raise ForecasterError("Invalid time series data")

        logger.info(f"Training Ensemble forecaster on {len(time_series)} samples")

        self.training_data = time_series
        self.last_timestamp = time_series[-1][0]

        # Train individual models
        successful_models = []
        for model_type, forecaster in self.forecasters.items():
            try:
                logger.info(f"Training {model_type} model...")
                forecaster.fit(time_series)
                successful_models.append(model_type)
                logger.info(f"{model_type} model trained successfully")
            except Exception as e:
                logger.error(f"Failed to train {model_type} model: {e}")
                # Remove failed model
                self.forecasters.pop(model_type, None)
                self.weights.pop(model_type, None)
                self.performance_history.pop(model_type, None)
                self.prediction_history.pop(model_type, None)

        if not successful_models:
            raise ForecasterError("All models failed to train")

        # Re-normalize weights for remaining models
        self._apply_weight_constraints()

        # Store model information
        self.model_info = {
            'n_models': len(self.forecasters),
            'model_types': list(self.forecasters.keys()),
            'weights': self.weights.copy(),
            'weight_optimization': self.weight_optimization,
            'training_samples': len(time_series),
            'successful_models': successful_models,
            'individual_model_info': {
                model_type: forecaster.get_model_info()
                for model_type, forecaster in self.forecasters.items()
            }
        }

        self.is_trained = True
        logger.info(f"Ensemble model trained successfully with {len(self.forecasters)} models")
        logger.info(f"Final weights: {self.weights}")

    def predict(self, horizon_minutes: int) -> List[Tuple[datetime, float]]:
        """
        Generate ensemble predictions

        Args:
            horizon_minutes: How many minutes ahead to predict

        Returns:
            List of (timestamp, predicted_value) tuples
        """
        if not self.is_trained:
            raise ForecasterError("Model must be trained before prediction")

        logger.info(f"Generating Ensemble predictions for {horizon_minutes} minutes")

        try:
            # Generate predictions from individual models
            individual_predictions = {}
            timestamps = None

            for model_type, forecaster in self.forecasters.items():
                try:
                    predictions = forecaster.predict(horizon_minutes)
                    individual_predictions[model_type] = [val for _, val in predictions]

                    if timestamps is None:
                        timestamps = [ts for ts, _ in predictions]

                except Exception as e:
                    logger.error(f"Failed to generate predictions with {model_type}: {e}")
                    continue

            if not individual_predictions:
                raise ForecasterError("No models generated predictions")

            # Weighted average of predictions
            ensemble_predictions = np.zeros(horizon_minutes)
            total_weight = 0.0

            for model_type, preds in individual_predictions.items():
                weight = self.weights.get(model_type, 0.0)
                ensemble_predictions += weight * np.array(preds)
                total_weight += weight

            # Normalize if total weight is not 1.0
            if total_weight > 0:
                ensemble_predictions /= total_weight

            # Ensure non-negative predictions
            ensemble_predictions = np.maximum(0, ensemble_predictions)

            # Store predictions for performance tracking
            self.prediction_count += 1
            for model_type, preds in individual_predictions.items():
                if len(self.prediction_history[model_type]) >= self.performance_window:
                    self.prediction_history[model_type].pop(0)
                self.prediction_history[model_type].extend(preds[-10:])  # Keep last 10 predictions

            # Optimize weights periodically
            if self.weight_optimization == 'performance' and self.prediction_count % 10 == 0:
                self._optimize_weights_performance()

            # Combine timestamps and predictions
            result = list(zip(timestamps[:horizon_minutes], ensemble_predictions))

            logger.info(f"Generated {len(result)} Ensemble predictions")
            return result

        except Exception as e:
            raise ForecasterError(f"Failed to generate ensemble predictions: {e}")

    def update_performance(self, actual_values: List[float], model_predictions: Dict[str, List[float]]):
        """
        Update performance history with actual values

        Args:
            actual_values: Actual observed values
            model_predictions: Dictionary of model predictions
        """
        if len(actual_values) == 0:
            return

        for model_type, predictions in model_predictions.items():
            if model_type in self.forecasters and len(predictions) > 0:
                # Calculate RMSE for overlapping period
                min_length = min(len(actual_values), len(predictions))
                if min_length > 0:
                    rmse = np.sqrt(mean_squared_error(actual_values[:min_length], predictions[:min_length]))

                    # Update performance history
                    history = self.performance_history[model_type]
                    history.append(rmse)

                    # Keep only recent performance
                    if len(history) > self.performance_window:
                        history.pop(0)

    def get_model_weights(self) -> Dict[str, float]:
        """
        Get current model weights

        Returns:
            Dictionary of model weights
        """
        return self.weights.copy()

    def set_model_weights(self, weights: Dict[str, float]):
        """
        Set model weights manually

        Args:
            weights: Dictionary of model weights
        """
        for model_type, weight in weights.items():
            if model_type in self.forecasters:
                self.weights[model_type] = weight

        self._apply_weight_constraints()
        logger.info(f"Manually set weights: {self.weights}")

    def get_model_contributions(self) -> Dict[str, Dict[str, float]]:
        """
        Get individual model contributions to the ensemble

        Returns:
            Dictionary with model statistics
        """
        contributions = {}

        for model_type in self.forecasters.keys():
            performance = self.performance_history.get(model_type, [])
            weight = self.weights.get(model_type, 0.0)

            contributions[model_type] = {
                'weight': weight,
                'avg_rmse': np.mean(performance) if performance else 0.0,
                'latest_rmse': performance[-1] if performance else 0.0,
                'prediction_count': len(self.prediction_history.get(model_type, [])),
                'model_info': self.forecasters[model_type].get_model_info()
            }

        return contributions

    def save_model(self, filepath: str) -> None:
        """
        Save ensemble model to file

        Args:
            filepath: Path to save the model
        """
        if not self.is_trained:
            raise ForecasterError("Cannot save untrained model")

        try:
            # Save individual models first
            base_path = filepath.rsplit('.', 1)[0]
            model_paths = {}

            for model_type, forecaster in self.forecasters.items():
                model_path = f"{base_path}_{model_type}.pkl"
                forecaster.save_model(model_path)
                model_paths[model_type] = model_path

            # Save ensemble metadata
            ensemble_data = {
                'model_paths': model_paths,
                'weights': self.weights,
                'config': self.config,
                'model_info': self.model_info,
                'performance_history': self.performance_history,
                'training_data': self.training_data,
                'last_timestamp': self.last_timestamp,
                'prediction_count': self.prediction_count
            }

            with open(filepath, 'wb') as f:
                pickle.dump(ensemble_data, f)

            logger.info(f"Ensemble model saved to {filepath}")

        except Exception as e:
            raise ForecasterError(f"Failed to save ensemble model: {e}")

    def load_model(self, filepath: str) -> None:
        """
        Load ensemble model from file

        Args:
            filepath: Path to load the model from
        """
        try:
            # Load ensemble metadata
            with open(filepath, 'rb') as f:
                ensemble_data = pickle.load(f)

            # Reinitialize forecasters
            self._initialize_forecasters()

            # Load individual models
            model_paths = ensemble_data['model_paths']
            for model_type, model_path in model_paths.items():
                if model_type in self.forecasters:
                    self.forecasters[model_type].load_model(model_path)

            # Restore ensemble state
            self.weights = ensemble_data['weights']
            self.config = ensemble_data.get('config', {})
            self.model_info = ensemble_data.get('model_info', {})
            self.performance_history = ensemble_data.get('performance_history', {})
            self.training_data = ensemble_data.get('training_data')
            self.last_timestamp = ensemble_data.get('last_timestamp')
            self.prediction_count = ensemble_data.get('prediction_count', 0)

            self.is_trained = True
            logger.info(f"Ensemble model loaded from {filepath}")

        except Exception as e:
            raise ForecasterError(f"Failed to load ensemble model: {e}")

    def evaluate_individual_models(self, test_data: List[Tuple[datetime, float]]) -> Dict[str, Dict[str, float]]:
        """
        Evaluate individual models on test data

        Args:
            test_data: Test time series data

        Returns:
            Dictionary of evaluation metrics for each model
        """
        if not self.is_trained:
            raise ForecasterError("Model must be trained before evaluation")

        results = {}
        horizon = min(50, len(test_data) // 4)  # Predict up to 50 points or 1/4 of test data

        for model_type, forecaster in self.forecasters.items():
            try:
                # Generate predictions
                predictions = forecaster.predict(horizon)
                predicted_values = [val for _, val in predictions]
                actual_values = [val for _, val in test_data[:horizon]]

                # Calculate metrics
                metrics = forecaster.evaluate_predictions(
                    test_data[:horizon],
                    predictions
                )
                results[model_type] = metrics

            except Exception as e:
                logger.error(f"Failed to evaluate {model_type}: {e}")
                results[model_type] = {'error': str(e)}

        return results