"""
Auto-Regressive (AR) forecaster using statsmodels
Implements ARIMA-based time series forecasting
"""

import pickle
import numpy as np
import pandas as pd
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timedelta
import logging
from scipy import stats
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.seasonal import seasonal_decompose
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf

from .base_forecaster import BaseForecaster, ForecasterError

logger = logging.getLogger(__name__)


class ARForecaster(BaseForecaster):
    """
    Auto-Regressive forecaster using ARIMA models
    Automatically determines optimal AR order and parameters
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize AR forecaster

        Args:
            config: Configuration parameters
                - max_order: Maximum AR order to consider (default: 10)
                - seasonal: Whether to consider seasonal patterns (default: True)
                - seasonal_periods: Number of seasonal periods (default: 24 for hourly)
                - auto_order: Whether to automatically determine AR order (default: True)
                - information_criterion: Information criterion for model selection (aic, bic, hqic)
                - alpha: Significance level for confidence intervals (default: 0.05)
        """
        super().__init__(config)

        self.max_order = self.config.get('max_order', 10)
        self.seasonal = self.config.get('seasonal', True)
        self.seasonal_periods = self.config.get('seasonal_periods', 24)
        self.auto_order = self.config.get('auto_order', True)
        self.information_criterion = self.config.get('information_criterion', 'aic')
        self.alpha = self.config.get('alpha', 0.05)

        self.model = None
        self.order = None
        self.seasonal_order = None
        self.training_data = None
        self.last_timestamp = None

    def get_min_required_samples(self) -> int:
        """Get minimum samples required for AR modeling"""
        return max(50, self.max_order * 5)

    def _check_stationarity(self, values: np.ndarray) -> Tuple[bool, float]:
        """
        Check if time series is stationary using Augmented Dickey-Fuller test

        Args:
            values: Time series values

        Returns:
            Tuple of (is_stationary, p_value)
        """
        try:
            result = adfuller(values)
            p_value = result[1]
            is_stationary = p_value < 0.05  # 5% significance level
            return is_stationary, p_value
        except Exception as e:
            logger.warning(f"Stationarity test failed: {e}")
            return True, 0.01  # Assume stationary if test fails

    def _difference_series(self, values: np.ndarray, d: int = 1) -> np.ndarray:
        """
        Apply differencing to make series stationary

        Args:
            values: Original series
            d: Order of differencing

        Returns:
            Differenced series
        """
        result = values.copy()
        for _ in range(d):
            result = np.diff(result)
        return result

    def _determine_optimal_order(self, values: np.ndarray) -> Tuple[int, int]:
        """
        Determine optimal AR order using information criteria

        Args:
            values: Time series values

        Returns:
            Tuple of (ar_order, diff_order)
        """
        best_ic = np.inf
        best_order = (1, 0)  # Default: AR(1) with no differencing

        # Check stationarity first
        is_stationary, p_value = self._check_stationarity(values)
        max_d = 2 if not is_stationary else 1  # Allow up to 2nd order differencing

        for d in range(max_d + 1):
            # Apply differencing
            if d > 0:
                diff_values = self._difference_series(values, d)
                if len(diff_values) < self.max_order * 2:
                    continue
            else:
                diff_values = values

            # Try different AR orders
            for p in range(1, min(self.max_order, len(diff_values) // 3) + 1):
                try:
                    # Fit AR model
                    model = ARIMA(diff_values, order=(p, 0, 0))
                    fitted = model.fit()

                    # Get information criterion
                    if self.information_criterion == 'aic':
                        ic = fitted.aic
                    elif self.information_criterion == 'bic':
                        ic = fitted.bic
                    else:  # hqic
                        ic = fitted.hqic

                    if ic < best_ic:
                        best_ic = ic
                        best_order = (p, d)

                except Exception as e:
                    logger.debug(f"AR({p}, {d}) fitting failed: {e}")
                    continue

        logger.info(f"Selected AR order: {best_order[0]}, differencing: {best_order[1]}, IC: {best_ic:.2f}")
        return best_order

    def _detect_seasonality(self, timestamps: np.ndarray, values: np.ndarray) -> bool:
        """
        Detect if time series has seasonal patterns

        Args:
            timestamps: Timestamp array
            values: Values array

        Returns:
            True if seasonal pattern detected
        """
        if len(values) < self.seasonal_periods * 2:
            return False

        try:
            # Create pandas Series for seasonal decomposition
            ts = pd.Series(values, index=pd.to_datetime(timestamps))

            # Try seasonal decomposition
            decomposition = seasonal_decompose(ts, period=self.seasonal_periods, extrapolate_trend='freq')

            # Check if seasonal component is significant
            seasonal_std = np.std(decomposition.seasonal)
            residual_std = np.std(decomposition.resid)

            # Seasonal pattern detected if seasonal variation is significant
            is_seasonal = seasonal_std > 0.5 * residual_std
            logger.info(f"Seasonality detected: {is_seasonal}, seasonal_std: {seasonal_std:.3f}, residual_std: {residual_std:.3f}")

            return is_seasonal

        except Exception as e:
            logger.warning(f"Seasonality detection failed: {e}")
            return False

    def fit(self, time_series: List[Tuple[datetime, float]]) -> None:
        """
        Train AR model on time series data

        Args:
            time_series: List of (timestamp, value) tuples
        """
        if not self.validate_time_series(time_series):
            raise ForecasterError("Invalid time series data")

        logger.info(f"Training AR forecaster on {len(time_series)} samples")

        # Preprocess data
        timestamps, values = self.preprocess_time_series(time_series)
        self.training_data = (timestamps, values)
        self.last_timestamp = timestamps[-1]

        # Determine model order
        if self.auto_order:
            self.order = self._determine_optimal_order(values)
        else:
            self.order = (self.config.get('ar_order', 5), 0)

        # Check for seasonality
        if self.seasonal:
            has_seasonality = self._detect_seasonality(timestamps, values)
            if has_seasonality:
                # Use seasonal ARIMA (SARIMA)
                self.seasonal_order = (1, 1, 1, self.seasonal_periods)
            else:
                self.seasonal_order = (0, 0, 0, 0)
        else:
            self.seasonal_order = (0, 0, 0, 0)

        try:
            # Fit ARIMA model
            if self.seasonal_order[0] > 0:
                # SARIMA model
                model = ARIMA(values, order=(self.order[0], self.order[1], 0),
                             seasonal_order=self.seasonal_order)
            else:
                # Simple AR model
                model = ARIMA(values, order=(self.order[0], self.order[1], 0))

            self.model = model.fit()

            # Store model information
            self.model_info = {
                'ar_order': self.order[0],
                'diff_order': self.order[1],
                'seasonal_order': self.seasonal_order,
                'aic': self.model.aic,
                'bic': self.model.bic,
                'log_likelihood': self.model.llf,
                'training_samples': len(values),
                'training_start': timestamps[0].isoformat(),
                'training_end': timestamps[-1].isoformat(),
                'fitted_params': self.model.params.tolist() if hasattr(self.model, 'params') else {}
            }

            self.is_trained = True
            logger.info(f"AR model trained successfully: {self.model_info}")

        except Exception as e:
            raise ForecasterError(f"Failed to train AR model: {e}")

    def predict(self, horizon_minutes: int) -> List[Tuple[datetime, float]]:
        """
        Generate predictions for future time points

        Args:
            horizon_minutes: How many minutes ahead to predict

        Returns:
            List of (timestamp, predicted_value) tuples
        """
        if not self.is_trained or self.model is None:
            raise ForecasterError("Model must be trained before prediction")

        logger.info(f"Generating AR predictions for {horizon_minutes} minutes")

        try:
            # Generate forecast
            forecast_result = self.model.forecast(steps=horizon_minutes)

            if isinstance(forecast_result, np.ndarray):
                predictions = forecast_result
            else:
                predictions = forecast_result.predicted_mean

            # Generate timestamps for predictions
            prediction_timestamps = []
            current_time = self.last_timestamp

            # Determine time interval from training data
            if len(self.training_data[0]) > 1:
                time_diff = self.training_data[0][-1] - self.training_data[0][-2]
                interval_minutes = int(time_diff.total_seconds() / 60)
            else:
                interval_minutes = 1  # Default to 1 minute

            for i in range(horizon_minutes):
                pred_time = current_time + timedelta(minutes=(i + 1) * interval_minutes)
                prediction_timestamps.append(pred_time)

            # Combine timestamps and predictions
            result = list(zip(prediction_timestamps, predictions))

            # Ensure non-negative predictions (query counts can't be negative)
            result = [(ts, max(0, val)) for ts, val in result]

            logger.info(f"Generated {len(result)} AR predictions")
            return result

        except Exception as e:
            raise ForecasterError(f"Failed to generate predictions: {e}")

    def predict_with_confidence(self, horizon_minutes: int) -> List[Tuple[datetime, float, float, float]]:
        """
        Generate predictions with confidence intervals

        Args:
            horizon_minutes: How many minutes ahead to predict

        Returns:
            List of (timestamp, prediction, lower_bound, upper_bound) tuples
        """
        if not self.is_trained or self.model is None:
            raise ForecasterError("Model must be trained before prediction")

        try:
            # Generate forecast with confidence intervals
            forecast_result = self.model.get_forecast(steps=horizon_minutes)
            predictions = forecast_result.predicted_mean
            conf_int = forecast_result.conf_int(alpha=self.alpha)

            # Generate timestamps
            prediction_timestamps = []
            current_time = self.last_timestamp

            if len(self.training_data[0]) > 1:
                time_diff = self.training_data[0][-1] - self.training_data[0][-2]
                interval_minutes = int(time_diff.total_seconds() / 60)
            else:
                interval_minutes = 1

            for i in range(horizon_minutes):
                pred_time = current_time + timedelta(minutes=(i + 1) * interval_minutes)
                prediction_timestamps.append(pred_time)

            # Combine results
            result = []
            for i, ts in enumerate(prediction_timestamps):
                pred_val = max(0, predictions.iloc[i] if hasattr(predictions, 'iloc') else predictions[i])
                lower_val = max(0, conf_int.iloc[i, 0] if hasattr(conf_int, 'iloc') else conf_int[i][0])
                upper_val = max(0, conf_int.iloc[i, 1] if hasattr(conf_int, 'iloc') else conf_int[i][1])
                result.append((ts, pred_val, lower_val, upper_val))

            return result

        except Exception as e:
            logger.warning(f"Failed to generate confidence intervals: {e}")
            # Fall back to regular predictions
            predictions = self.predict(horizon_minutes)
            return [(ts, val, val, val) for ts, val in predictions]

    def save_model(self, filepath: str) -> None:
        """
        Save trained model to file

        Args:
            filepath: Path to save the model
        """
        if not self.is_trained:
            raise ForecasterError("Cannot save untrained model")

        try:
            model_data = {
                'model': self.model,
                'order': self.order,
                'seasonal_order': self.seasonal_order,
                'config': self.config,
                'model_info': self.model_info,
                'training_data': self.training_data,
                'last_timestamp': self.last_timestamp
            }

            with open(filepath, 'wb') as f:
                pickle.dump(model_data, f)

            logger.info(f"AR model saved to {filepath}")

        except Exception as e:
            raise ForecasterError(f"Failed to save model: {e}")

    def load_model(self, filepath: str) -> None:
        """
        Load trained model from file

        Args:
            filepath: Path to load the model from
        """
        try:
            with open(filepath, 'rb') as f:
                model_data = pickle.load(f)

            self.model = model_data['model']
            self.order = model_data['order']
            self.seasonal_order = model_data.get('seasonal_order', (0, 0, 0, 0))
            self.config = model_data.get('config', {})
            self.model_info = model_data.get('model_info', {})
            self.training_data = model_data.get('training_data')
            self.last_timestamp = model_data.get('last_timestamp')
            self.is_trained = True

            logger.info(f"AR model loaded from {filepath}")

        except Exception as e:
            raise ForecasterError(f"Failed to load model: {e}")

    def analyze_residuals(self) -> Dict[str, Any]:
        """
        Analyze model residuals for diagnostic purposes

        Returns:
            Dictionary of residual analysis results
        """
        if not self.is_trained or self.model is None:
            raise ForecasterError("Model must be trained for residual analysis")

        try:
            residuals = self.model.resid

            # Residual statistics
            residual_stats = {
                'mean': float(np.mean(residuals)),
                'std': float(np.std(residuals)),
                'min': float(np.min(residuals)),
                'max': float(np.max(residuals)),
                'skewness': float(stats.skew(residuals)),
                'kurtosis': float(stats.kurtosis(residuals))
            }

            # Ljung-Box test for autocorrelation in residuals
            try:
                from statsmodels.stats.diagnostic import acorr_ljungbox
                lb_test = acorr_ljungbox(residuals, lags=[10], return_df=True)
                ljung_box_p = float(lb_test['lb_pvalue'].iloc[0])
            except:
                ljung_box_p = None

            # Shapiro-Wilk test for normality
            try:
                shapiro_test = stats.shapiro(residuals[:5000])  # Limit to 5000 samples
                shapiro_p = float(shapiro_test.pvalue)
            except:
                shapiro_p = None

            return {
                'residual_stats': residual_stats,
                'ljung_box_p_value': ljung_box_p,
                'shapiro_wilk_p_value': shapiro_p,
                'residuals_are_white_noise': ljung_box_p and ljung_box_p > 0.05 if ljung_box_p else None,
                'residuals_are_normal': shapiro_p and shapiro_p > 0.05 if shapiro_p else None
            }

        except Exception as e:
            logger.warning(f"Residual analysis failed: {e}")
            return {}