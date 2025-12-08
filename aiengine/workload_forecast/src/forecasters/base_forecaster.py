"""
Base interface for all forecasting models
"""

from abc import ABC, abstractmethod
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timedelta
import numpy as np
import logging

logger = logging.getLogger(__name__)


class BaseForecaster(ABC):
    """
    Abstract base class for all forecasting models
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize forecaster with configuration

        Args:
            config: Model-specific configuration parameters
        """
        self.config = config or {}
        self.is_trained = False
        self.model_info = {}
        self.training_history = []

    @abstractmethod
    def fit(self, time_series: List[Tuple[datetime, float]]) -> None:
        """
        Train the forecasting model on time series data

        Args:
            time_series: List of (timestamp, value) tuples
        """
        pass

    @abstractmethod
    def predict(self, horizon_minutes: int) -> List[Tuple[datetime, float]]:
        """
        Generate predictions for future time points

        Args:
            horizon_minutes: How many minutes ahead to predict

        Returns:
            List of (timestamp, predicted_value) tuples
        """
        pass

    @abstractmethod
    def save_model(self, filepath: str) -> None:
        """
        Save trained model to file

        Args:
            filepath: Path to save the model
        """
        pass

    @abstractmethod
    def load_model(self, filepath: str) -> None:
        """
        Load trained model from file

        Args:
            filepath: Path to load the model from
        """
        pass

    def validate_time_series(self, time_series: List[Tuple[datetime, float]]) -> bool:
        """
        Validate input time series

        Args:
            time_series: Time series to validate

        Returns:
            True if valid, False otherwise
        """
        if not time_series:
            logger.error("Empty time series")
            return False

        if len(time_series) < self.get_min_required_samples():
            logger.error(f"Time series too short: {len(time_series)} < {self.get_min_required_samples()}")
            return False

        # Check for valid timestamps and values
        for ts, value in time_series:
            if not isinstance(ts, datetime):
                logger.error(f"Invalid timestamp: {ts}")
                return False
            if not isinstance(value, (int, float)) or np.isnan(value) or np.isinf(value):
                logger.error(f"Invalid value: {value} at {ts}")
                return False

        return True

    def get_min_required_samples(self) -> int:
        """
        Get minimum number of samples required for training

        Returns:
            Minimum number of samples
        """
        return 10  # Default minimum

    def preprocess_time_series(self, time_series: List[Tuple[datetime, float]]) -> Tuple[np.ndarray, np.ndarray]:
        """
        Preprocess time series for model consumption

        Args:
            time_series: List of (timestamp, value) tuples

        Returns:
            Tuple of (timestamps_array, values_array)
        """
        # Sort by timestamp
        time_series = sorted(time_series, key=lambda x: x[0])

        timestamps = np.array([ts for ts, _ in time_series])
        values = np.array([val for _, val in time_series])

        # Handle missing values by interpolation
        if np.any(np.isnan(values)):
            logger.warning("Time series contains NaN values, applying interpolation")
            valid_mask = ~np.isnan(values)
            timestamps_valid = timestamps[valid_mask]
            values_valid = values[valid_mask]

            # Linear interpolation
            values = np.interp(timestamps, timestamps_valid, values_valid)

        return timestamps, values

    def evaluate_predictions(self,
                           actual: List[Tuple[datetime, float]],
                           predicted: List[Tuple[datetime, float]]) -> Dict[str, float]:
        """
        Evaluate prediction quality

        Args:
            actual: Actual values
            predicted: Predicted values

        Returns:
            Dictionary of evaluation metrics
        """
        if len(actual) != len(predicted):
            raise ValueError("Actual and predicted series must have same length")

        actual_values = np.array([val for _, val in actual])
        predicted_values = np.array([val for _, val in predicted])

        # Calculate metrics
        mse = np.mean((actual_values - predicted_values) ** 2)
        rmse = np.sqrt(mse)
        mae = np.mean(np.abs(actual_values - predicted_values))

        # Mean Absolute Percentage Error (MAPE)
        mask = actual_values != 0
        mape = np.mean(np.abs((actual_values[mask] - predicted_values[mask]) / actual_values[mask])) * 100 if mask.any() else np.inf

        # R-squared
        ss_res = np.sum((actual_values - predicted_values) ** 2)
        ss_tot = np.sum((actual_values - np.mean(actual_values)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

        return {
            'mse': float(mse),
            'rmse': float(rmse),
            'mae': float(mae),
            'mape': float(mape),
            'r2': float(r2)
        }

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the trained model

        Returns:
            Dictionary with model information
        """
        info = {
            'model_type': self.__class__.__name__,
            'is_trained': self.is_trained,
            'config': self.config,
            'model_info': self.model_info
        }
        return info


class ForecasterError(Exception):
    """Custom exception for forecaster errors"""
    pass