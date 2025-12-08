"""
Spectral forecaster inspired by PSRNN (Periodic and Spike Response Neural Networks)
Uses frequency domain analysis and harmonic decomposition for forecasting
"""

import pickle
import numpy as np
from scipy import signal
from scipy.fft import fft, ifft, fftfreq
from scipy.optimize import minimize
from sklearn.preprocessing import MinMaxScaler
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timedelta
import logging
import warnings
warnings.filterwarnings('ignore')

from .base_forecaster import BaseForecaster, ForecasterError

logger = logging.getLogger(__name__)


class SpectralForecaster(BaseForecaster):
    """
    Spectral forecaster using frequency domain analysis
    Inspired by PSRNN approach for periodic pattern detection
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Spectral forecaster

        Args:
            config: Configuration parameters
                - n_harmonics: Number of harmonics to extract (default: 20)
                - min_frequency: Minimum frequency to consider (default: 0.01)
                - max_frequency: Maximum frequency to consider (default: 0.5)
                - frequency_threshold: Power threshold for frequency selection (default: 0.05)
                - trend_order: Order of polynomial trend (default: 2)
                - seasonal_periods: Expected seasonal periods (default: [24, 168]) # hourly, weekly
                - detrend_method: Method for detrending ('linear', 'polynomial', 'none')
                - smoothing_window: Window size for smoothing (default: 5)
        """
        super().__init__(config)

        self.n_harmonics = self.config.get('n_harmonics', 20)
        self.min_frequency = self.config.get('min_frequency', 0.01)
        self.max_frequency = self.config.get('max_frequency', 0.5)
        self.frequency_threshold = self.config.get('frequency_threshold', 0.05)
        self.trend_order = self.config.get('trend_order', 2)
        self.seasonal_periods = self.config.get('seasonal_periods', [24, 168])  # hourly, weekly
        self.detrend_method = self.config.get('detrend_method', 'linear')
        self.smoothing_window = self.config.get('smoothing_window', 5)

        self.frequencies = None
        self.amplitudes = None
        self.phases = None
        self.trend_coeffs = None
        self.selected_frequencies = None
        self.scaler = None
        self.training_data = None
        self.last_timestamp = None
        self.sampling_interval = None

    def get_min_required_samples(self) -> int:
        """Get minimum samples required for spectral analysis"""
        return max(100, max(self.seasonal_periods) * 2)

    def _detrend_series(self, values: np.ndarray, timestamps: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Remove trend from time series

        Args:
            values: Time series values
            timestamps: Timestamp array

        Returns:
            Tuple of (detrended_values, trend_coefficients)
        """
        if self.detrend_method == 'none':
            return values, np.zeros(self.trend_order + 1)

        # Convert timestamps to numeric (hours since start)
        time_numeric = np.array([(ts - timestamps[0]).total_seconds() / 3600.0 for ts in timestamps])

        if self.detrend_method == 'linear':
            # Linear detrending
            X = np.column_stack([np.ones(len(time_numeric)), time_numeric])
            trend_coeffs = np.linalg.lstsq(X, values, rcond=None)[0]
            trend = X @ trend_coeffs
        else:  # polynomial
            # Polynomial detrending
            X = np.column_stack([time_numeric**i for i in range(self.trend_order + 1)])
            trend_coeffs = np.linalg.lstsq(X, values, rcond=None)[0]
            trend = X @ trend_coeffs

        detrended = values - trend
        return detrended, trend_coeffs

    def _apply_seasonal_decomposition(self, values: np.ndarray, timestamps: np.ndarray) -> np.ndarray:
        """
        Apply seasonal decomposition to remove known seasonal patterns

        Args:
            values: Time series values
            timestamps: Timestamp array

        Returns:
            Seasonally adjusted values
        """
        try:
            import pandas as pd
            from statsmodels.tsa.seasonal import seasonal_decompose

            # Create pandas Series
            ts = pd.Series(values, index=pd.to_datetime(timestamps))

            # Apply seasonal decomposition for each expected period
            result_values = values.copy()

            for period in self.seasonal_periods:
                if len(values) >= 2 * period:
                    try:
                        decomposition = seasonal_decompose(ts, period=period, extrapolate_trend='freq')
                        result_values -= decomposition.seasonal.values
                    except Exception as e:
                        logger.debug(f"Seasonal decomposition failed for period {period}: {e}")
                        continue

            return result_values

        except ImportError:
            logger.warning("statsmodels not available for seasonal decomposition")
            return values

    def _extract_dominant_frequencies(self, values: np.ndarray, sampling_rate: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Extract dominant frequencies using FFT

        Args:
            values: Time series values
            sampling_rate: Sampling rate (samples per hour)

        Returns:
            Tuple of (frequencies, amplitudes, phases)
        """
        # Apply FFT
        n = len(values)
        fft_values = fft(values)

        # Get frequency bins
        freqs = fftfreq(n, d=1.0/sampling_rate)

        # Only consider positive frequencies within range
        positive_mask = (freqs > 0) & (freqs >= self.min_frequency) & (freqs <= self.max_frequency)
        freqs = freqs[positive_mask]
        fft_values = fft_values[positive_mask]

        # Calculate power spectrum
        power = np.abs(fft_values) ** 2

        # Normalize power
        power = power / np.sum(power)

        # Select top frequencies based on power threshold
        significant_mask = power >= self.frequency_threshold
        if not np.any(significant_mask):
            # If no frequencies meet threshold, take top n_harmonics
            top_indices = np.argsort(power)[-self.n_harmonics:]
            significant_mask = np.zeros_like(power, dtype=bool)
            significant_mask[top_indices] = True

        selected_freqs = freqs[significant_mask]
        selected_fft = fft_values[significant_mask]

        # Limit to n_harmonics
        if len(selected_freqs) > self.n_harmonics:
            top_indices = np.argsort(np.abs(selected_fft))[-self.n_harmonics:]
            selected_freqs = selected_freqs[top_indices]
            selected_fft = selected_fft[top_indices]

        # Extract amplitudes and phases
        amplitudes = 2 * np.abs(selected_fft) / n
        phases = np.angle(selected_fft)

        return selected_freqs, amplitudes, phases

    def _detect_spectral_patterns(self, values: np.ndarray, timestamps: np.ndarray) -> Dict[str, Any]:
        """
        Detect various spectral patterns in the time series

        Args:
            values: Time series values
            timestamps: Timestamp array

        Returns:
            Dictionary of detected patterns
        """
        patterns = {}

        # Calculate sampling rate (samples per hour)
        if len(timestamps) > 1:
            time_diff = (timestamps[1] - timestamps[0]).total_seconds() / 3600.0
            sampling_rate = 1.0 / time_diff if time_diff > 0 else 1.0
        else:
            sampling_rate = 1.0

        # Spectral entropy (measure of complexity)
        try:
            fft_values = fft(values)
            power = np.abs(fft_values) ** 2
            power = power / np.sum(power)
            entropy = -np.sum(power * np.log(power + 1e-10))
            patterns['spectral_entropy'] = entropy
        except:
            patterns['spectral_entropy'] = 0

        # Dominant frequency
        try:
            freqs, amplitudes, _ = self._extract_dominant_frequencies(values, sampling_rate)
            if len(amplitudes) > 0:
                dominant_freq_idx = np.argmax(amplitudes)
                patterns['dominant_frequency'] = float(freqs[dominant_freq_idx])
                patterns['dominant_period'] = float(1.0 / freqs[dominant_freq_idx]) if freqs[dominant_freq_idx] > 0 else np.inf
            else:
                patterns['dominant_frequency'] = 0
                patterns['dominant_period'] = np.inf
        except:
            patterns['dominant_frequency'] = 0
            patterns['dominant_period'] = np.inf

        # Harmonic content
        try:
            power_spectrum = np.abs(fft(values)) ** 2
            total_power = np.sum(power_spectrum)
            harmonic_power = np.sum(power_spectrum[1:int(len(power_spectrum)/2)])
            patterns['harmonic_content'] = float(harmonic_power / total_power) if total_power > 0 else 0
        except:
            patterns['harmonic_content'] = 0

        return patterns

    def fit(self, time_series: List[Tuple[datetime, float]]) -> None:
        """
        Fit spectral model to time series data

        Args:
            time_series: List of (timestamp, value) tuples
        """
        if not self.validate_time_series(time_series):
            raise ForecasterError("Invalid time series data")

        logger.info(f"Training Spectral forecaster on {len(time_series)} samples")

        # Preprocess data
        timestamps, values = self.preprocess_time_series(time_series)
        self.training_data = (timestamps, values)
        self.last_timestamp = timestamps[-1]

        # Calculate sampling rate
        if len(timestamps) > 1:
            time_diff = (timestamps[1] - timestamps[0]).total_seconds() / 3600.0  # Convert to hours
            self.sampling_interval = time_diff * 3600  # in seconds
            sampling_rate = 1.0 / time_diff
        else:
            self.sampling_interval = 3600  # 1 hour default
            sampling_rate = 1.0

        # Normalize data
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        normalized_values = self.scaler.fit_transform(values.reshape(-1, 1)).flatten()

        # Apply smoothing to reduce noise
        if self.smoothing_window > 1:
            kernel = np.ones(self.smoothing_window) / self.smoothing_window
            smoothed_values = np.convolve(normalized_values, kernel, mode='same')
        else:
            smoothed_values = normalized_values

        # Remove trend
        detrended_values, self.trend_coeffs = self._detrend_series(smoothed_values, timestamps)

        # Remove known seasonal patterns
        seasonal_adjusted = self._apply_seasonal_decomposition(detrended_values, timestamps)

        # Extract dominant frequencies
        self.frequencies, self.amplitudes, self.phases = self._extract_dominant_frequencies(
            seasonal_adjusted, sampling_rate
        )

        # Store selected frequencies
        self.selected_frequencies = self.frequencies.copy()

        # Detect spectral patterns
        patterns = self._detect_spectral_patterns(seasonal_adjusted, timestamps)

        # Store model information
        self.model_info = {
            'n_frequencies': len(self.frequencies),
            'trend_order': self.trend_order,
            'detrend_method': self.detrend_method,
            'sampling_rate': sampling_rate,
            'frequency_threshold': self.frequency_threshold,
            'spectral_patterns': patterns,
            'training_samples': len(values),
            'dominant_frequencies': self.frequencies.tolist() if len(self.frequencies) > 0 else [],
            'dominant_amplitudes': self.amplitudes.tolist() if len(self.amplitudes) > 0 else []
        }

        self.is_trained = True
        logger.info(f"Spectral model trained successfully: {self.model_info}")

    def predict(self, horizon_minutes: int) -> List[Tuple[datetime, float]]:
        """
        Generate predictions using spectral decomposition

        Args:
            horizon_minutes: How many minutes ahead to predict

        Returns:
            List of (timestamp, predicted_value) tuples
        """
        if not self.is_trained:
            raise ForecasterError("Model must be trained before prediction")

        logger.info(f"Generating Spectral predictions for {horizon_minutes} minutes")

        try:
            # Generate time points for prediction
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

            # Convert to numeric time
            time_numeric = np.array([
                (ts - self.training_data[0][0]).total_seconds() / 3600.0  # hours
                for ts in prediction_timestamps
            ])

            # Generate predictions
            predictions = np.zeros(len(time_numeric))

            # Add trend component
            if self.detrend_method != 'none':
                trend_values = np.polyval(self.trend_coeffs[::-1], time_numeric)
                predictions += trend_values

            # Add harmonic components
            if len(self.frequencies) > 0:
                for freq, amp, phase in zip(self.frequencies, self.amplitudes, self.phases):
                    harmonic_component = amp * np.cos(2 * np.pi * freq * time_numeric + phase)
                    predictions += harmonic_component

            # Apply inverse transformation
            predictions = predictions.reshape(-1, 1)
            predictions = self.scaler.inverse_transform(predictions).flatten()

            # Ensure non-negative predictions
            predictions = np.maximum(0, predictions)

            # Combine timestamps and predictions
            result = list(zip(prediction_timestamps, predictions))

            logger.info(f"Generated {len(result)} Spectral predictions")
            return result

        except Exception as e:
            raise ForecasterError(f"Failed to generate predictions: {e}")

    def get_frequency_spectrum(self) -> Dict[str, List[float]]:
        """
        Get the frequency spectrum used by the model

        Returns:
            Dictionary with frequencies, amplitudes, and phases
        """
        if not self.is_trained:
            raise ForecasterError("Model must be trained to get frequency spectrum")

        return {
            'frequencies': self.frequencies.tolist() if self.frequencies is not None else [],
            'amplitudes': self.amplitudes.tolist() if self.amplitudes is not None else [],
            'phases': self.phases.tolist() if self.phases is not None else []
        }

    def analyze_seasonal_patterns(self) -> Dict[str, Any]:
        """
        Analyze seasonal patterns in the training data

        Returns:
            Dictionary with seasonal analysis results
        """
        if not self.is_trained:
            raise ForecasterError("Model must be trained for seasonal analysis")

        analysis = {
            'dominant_frequencies': [],
            'dominant_periods': [],
            'seasonal_strength': {}
        }

        if self.frequencies is not None and len(self.frequencies) > 0:
            # Convert frequencies to periods
            periods = 1.0 / self.frequencies
            analysis['dominant_frequencies'] = self.frequencies.tolist()
            analysis['dominant_periods'] = periods.tolist()

            # Analyze strength of different seasonal patterns
            for expected_period in self.seasonal_periods:
                # Find frequencies close to expected periods
                tolerance = 0.1  # 10% tolerance
                mask = np.abs(periods - expected_period) / expected_period < tolerance
                if np.any(mask):
                    strength = np.sum(self.amplitudes[mask])
                    analysis['seasonal_strength'][f'period_{expected_period}'] = float(strength)
                else:
                    analysis['seasonal_strength'][f'period_{expected_period}'] = 0.0

        return analysis

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
                'frequencies': self.frequencies,
                'amplitudes': self.amplitudes,
                'phases': self.phases,
                'trend_coeffs': self.trend_coeffs,
                'selected_frequencies': self.selected_frequencies,
                'scaler': self.scaler,
                'config': self.config,
                'model_info': self.model_info,
                'training_data': self.training_data,
                'last_timestamp': self.last_timestamp,
                'sampling_interval': self.sampling_interval
            }

            with open(filepath, 'wb') as f:
                pickle.dump(model_data, f)

            logger.info(f"Spectral model saved to {filepath}")

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

            self.frequencies = model_data['frequencies']
            self.amplitudes = model_data['amplitudes']
            self.phases = model_data['phases']
            self.trend_coeffs = model_data['trend_coeffs']
            self.selected_frequencies = model_data.get('selected_frequencies')
            self.scaler = model_data['scaler']
            self.config = model_data.get('config', {})
            self.model_info = model_data.get('model_info', {})
            self.training_data = model_data.get('training_data')
            self.last_timestamp = model_data.get('last_timestamp')
            self.sampling_interval = model_data.get('sampling_interval')
            self.is_trained = True

            logger.info(f"Spectral model loaded from {filepath}")

        except Exception as e:
            raise ForecasterError(f"Failed to load model: {e}")