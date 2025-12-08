"""
Model training pipeline for workload forecasting
Handles model training, evaluation, and persistence
"""

import os
import json
import pickle
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
from pathlib import Path

from forecasters import BaseForecaster, ARForecaster, RNNForecaster, SpectralForecaster, EnsembleForecaster
from workload_store import WorkloadStore
from templatizer import AdvancedTemplatizer

logger = logging.getLogger(__name__)


class ForecastingPipeline:
    """
    Pipeline for training and managing forecasting models
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize forecasting pipeline

        Args:
            config: Configuration dictionary
                - models_dir: Directory to save trained models
                - retrain_interval_hours: How often to retrain models
                - min_training_samples: Minimum samples required for training
                - validation_split: Fraction of data for validation
                - enable_auto_retraining: Whether to automatically retrain models
                - model_configs: Configuration for individual models
        """
        self.config = config
        self.models_dir = Path(config.get('models_dir', 'models/forecasting'))
        self.retrain_interval_hours = config.get('retrain_interval_hours', 24)
        self.min_training_samples = config.get('min_training_samples', 100)
        self.validation_split = config.get('validation_split', 0.2)
        self.enable_auto_retraining = config.get('enable_auto_retraining', True)
        self.model_configs = config.get('model_configs', {})

        # Ensure models directory exists
        self.models_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database connection
        db_config = config.get('database')
        if db_config is not None:
            self.workload_store = WorkloadStore(db_config)
        else:
            self.workload_store = None
            logger.info("Database connection disabled")

        # Initialize templatizer
        self.templatizer = AdvancedTemplatizer()

        # Model registry
        self.models = {}
        self.model_metadata = {}
        self.training_history = {}

        logger.info("Forecasting pipeline initialized")

    def prepare_training_data(self,
                            template_hash: Optional[str] = None,
                            cluster_id: Optional[int] = None,
                            days_back: int = 30) -> Dict[str, List[Tuple[datetime, float]]]:
        """
        Prepare training data from database

        Args:
            template_hash: Specific template to train for
            cluster_id: Specific cluster to train for
            days_back: How many days of historical data to use

        Returns:
            Dictionary mapping identifiers to time series data
        """
        logger.info(f"Preparing training data for the last {days_back} days")

        # If no database connection, generate sample data for testing
        if self.workload_store is None:
            logger.info("No database connection - generating sample training data")
            return self._generate_sample_training_data()

        try:
            # Get training data
            if template_hash:
                # Single template data
                data = self.workload_store.get_template_time_series(
                    template_hash, days_back=days_back
                )
                if data:
                    return {template_hash: data}
                else:
                    logger.warning(f"No data found for template {template_hash}")
                    return {}

            elif cluster_id:
                # Aggregate data for cluster
                cluster_data = self.workload_store.get_cluster_time_series(
                    cluster_id, days_back=days_back
                )
                if cluster_data:
                    return {f"cluster_{cluster_id}": cluster_data}
                else:
                    logger.warning(f"No data found for cluster {cluster_id}")
                    return {}

            else:
                # Get all active templates with sufficient data
                all_data = {}

                # Get template time series
                template_series = self.workload_store.get_all_template_time_series(
                    days_back=days_back, min_samples=self.min_training_samples
                )

                for template_hash, time_series in template_series.items():
                    if len(time_series) >= self.min_training_samples:
                        all_data[template_hash] = time_series

                # Also get cluster time series
                cluster_series = self.workload_store.get_all_cluster_time_series(
                    days_back=days_back, min_samples=self.min_training_samples
                )

                for cluster_id, time_series in cluster_series.items():
                    cluster_key = f"cluster_{cluster_id}"
                    if len(time_series) >= self.min_training_samples:
                        all_data[cluster_key] = time_series

                logger.info(f"Prepared training data for {len(all_data)} time series")
                return all_data

        except Exception as e:
            logger.error(f"Failed to prepare training data: {e}")
            return {}

    def create_forecaster(self, model_type: str, config: Optional[Dict] = None) -> BaseForecaster:
        """
        Create a forecaster instance

        Args:
            model_type: Type of forecaster to create
            config: Configuration for the forecaster

        Returns:
            Forecaster instance
        """
        forecaster_config = config or self.model_configs.get(model_type, {})

        if model_type == 'ar':
            return ARForecaster(forecaster_config)
        elif model_type == 'rnn':
            return RNNForecaster(forecaster_config)
        elif model_type == 'spectral':
            return SpectralForecaster(forecaster_config)
        elif model_type == 'ensemble':
            return EnsembleForecaster(forecaster_config)
        else:
            raise ValueError(f"Unknown model type: {model_type}")

    def train_model(self,
                   model_type: str,
                   data: List[Tuple[datetime, float]],
                   model_key: str,
                   save_model: bool = True) -> Optional[BaseForecaster]:
        """
        Train a single forecasting model

        Args:
            model_type: Type of model to train
            data: Training time series data
            model_key: Unique key for the model
            save_model: Whether to save the trained model

        Returns:
            Trained forecaster or None if training failed
        """
        logger.info(f"Training {model_type} model for {model_key}")

        if len(data) < self.min_training_samples:
            logger.warning(f"Insufficient data for {model_key}: {len(data)} < {self.min_training_samples}")
            return None

        try:
            # Create forecaster
            forecaster = self.create_forecaster(model_type)

            # Train model
            forecaster.fit(data)

            # Save model if requested
            if save_model:
                model_path = self.models_dir / f"{model_key}_{model_type}.pkl"
                forecaster.save_model(str(model_path))

            # Store model in registry
            self.models[f"{model_key}_{model_type}"] = forecaster

            # Record training metadata
            self.model_metadata[f"{model_key}_{model_type}"] = {
                'model_type': model_type,
                'model_key': model_key,
                'trained_at': datetime.now().isoformat(),
                'training_samples': len(data),
                'model_info': forecaster.get_model_info(),
                'model_path': str(model_path) if save_model else None
            }

            logger.info(f"Successfully trained {model_type} model for {model_key}")
            return forecaster

        except Exception as e:
            logger.error(f"Failed to train {model_type} model for {model_key}: {e}")
            return None

    def train_ensemble(self,
                      data: List[Tuple[datetime, float]],
                      model_key: str,
                      model_types: Optional[List[str]] = None) -> Optional[EnsembleForecaster]:
        """
        Train an ensemble model

        Args:
            data: Training time series data
            model_key: Unique key for the model
            model_types: List of model types to include in ensemble

        Returns:
            Trained ensemble forecaster or None if training failed
        """
        logger.info(f"Training ensemble model for {model_key}")

        if model_types is None:
            model_types = ['ar', 'rnn', 'spectral']

        try:
            # Create ensemble configuration
            ensemble_config = {
                'models': model_types,
                **self.model_configs.get('ensemble', {})
            }

            # Create and train ensemble
            ensemble = EnsembleForecaster(ensemble_config)
            ensemble.fit(data)

            # Save ensemble
            model_path = self.models_dir / f"{model_key}_ensemble.pkl"
            ensemble.save_model(str(model_path))

            # Store model in registry
            self.models[f"{model_key}_ensemble"] = ensemble

            # Record training metadata
            self.model_metadata[f"{model_key}_ensemble"] = {
                'model_type': 'ensemble',
                'model_key': model_key,
                'trained_at': datetime.now().isoformat(),
                'training_samples': len(data),
                'model_info': ensemble.get_model_info(),
                'model_path': str(model_path),
                'ensemble_models': model_types,
                'model_weights': ensemble.get_model_weights()
            }

            logger.info(f"Successfully trained ensemble model for {model_key}")
            return ensemble

        except Exception as e:
            logger.error(f"Failed to train ensemble model for {model_key}: {e}")
            return None

    def train_all_models(self,
                        model_types: List[str] = None,
                        train_ensemble: bool = True) -> Dict[str, int]:
        """
        Train models for all available time series

        Args:
            model_types: List of model types to train
            train_ensemble: Whether to also train ensembles

        Returns:
            Dictionary with training statistics
        """
        if model_types is None:
            model_types = ['ar', 'rnn', 'spectral']

        logger.info("Starting training for all models")

        # Prepare training data
        training_data = self.prepare_training_data()

        stats = {
            'total_series': len(training_data),
            'successful_trainings': {model_type: 0 for model_type in model_types},
            'failed_trainings': {model_type: 0 for model_type in model_types},
            'ensemble_trainings': 0,
            'failed_ensemble_trainings': 0
        }

        # Train individual models
        for series_key, time_series in training_data.items():
            logger.info(f"Training models for {series_key} ({len(time_series)} samples)")

            for model_type in model_types:
                forecaster = self.train_model(model_type, time_series, series_key)
                if forecaster:
                    stats['successful_trainings'][model_type] += 1
                else:
                    stats['failed_trainings'][model_type] += 1

            # Train ensemble
            if train_ensemble:
                ensemble = self.train_ensemble(time_series, series_key, model_types)
                if ensemble:
                    stats['ensemble_trainings'] += 1
                else:
                    stats['failed_ensemble_trainings'] += 1

        # Save training statistics
        self._save_training_history(stats)

        logger.info(f"Training completed: {stats}")
        return stats

    def load_model(self, model_key: str, model_type: str) -> Optional[BaseForecaster]:
        """
        Load a trained model from disk

        Args:
            model_key: Key for the model
            model_type: Type of model

        Returns:
            Loaded forecaster or None if not found
        """
        model_path = self.models_dir / f"{model_key}_{model_type}.pkl"

        if not model_path.exists():
            logger.warning(f"Model file not found: {model_path}")
            return None

        try:
            forecaster = self.create_forecaster(model_type)
            forecaster.load_model(str(model_path))

            # Store in registry
            self.models[f"{model_key}_{model_type}"] = forecaster

            logger.info(f"Loaded {model_type} model for {model_key}")
            return forecaster

        except Exception as e:
            logger.error(f"Failed to load model {model_key}_{model_type}: {e}")
            return None

    def get_model(self, model_key: str, model_type: str) -> Optional[BaseForecaster]:
        """
        Get a model from registry, loading if necessary

        Args:
            model_key: Key for the model
            model_type: Type of model

        Returns:
            Forecaster instance or None if not available
        """
        model_registry_key = f"{model_key}_{model_type}"

        # Return from registry if already loaded
        if model_registry_key in self.models:
            return self.models[model_registry_key]

        # Try to load from disk
        return self.load_model(model_key, model_type)

    def generate_forecasts(self,
                          model_key: str,
                          horizon_minutes: int = 60,
                          model_types: List[str] = None) -> Dict[str, List[Tuple[datetime, float]]]:
        """
        Generate forecasts using available models

        Args:
            model_key: Key for the time series to forecast
            horizon_minutes: Forecast horizon
            model_types: Model types to use (None for all available)

        Returns:
            Dictionary mapping model types to forecasts
        """
        if model_types is None:
            model_types = ['ar', 'rnn', 'spectral', 'ensemble']

        forecasts = {}

        for model_type in model_types:
            forecaster = self.get_model(model_key, model_type)
            if forecaster and forecaster.is_trained:
                try:
                    predictions = forecaster.predict(horizon_minutes)
                    forecasts[model_type] = predictions
                    logger.info(f"Generated {model_type} forecast for {model_key}")
                except Exception as e:
                    logger.error(f"Failed to generate {model_type} forecast for {model_key}: {e}")
            else:
                logger.warning(f"No trained {model_type} model available for {model_key}")

        return forecasts

    def evaluate_models(self,
                       model_key: str,
                       test_data: List[Tuple[datetime, float]],
                       model_types: List[str] = None) -> Dict[str, Dict[str, float]]:
        """
        Evaluate models on test data

        Args:
            model_key: Key for the time series
            test_data: Test time series data
            model_types: Model types to evaluate

        Returns:
            Dictionary of evaluation metrics
        """
        if model_types is None:
            model_types = ['ar', 'rnn', 'spectral']

        results = {}

        for model_type in model_types:
            forecaster = self.get_model(model_key, model_type)
            if forecaster and forecaster.is_trained:
                try:
                    # Generate predictions
                    horizon = min(50, len(test_data) // 2)
                    predictions = forecaster.predict(horizon)

                    # Calculate metrics
                    metrics = forecaster.evaluate_predictions(test_data[:horizon], predictions)
                    results[model_type] = metrics

                except Exception as e:
                    logger.error(f"Failed to evaluate {model_type} model for {model_key}: {e}")
                    results[model_type] = {'error': str(e)}

        # Evaluate ensemble if available
        ensemble_forecaster = self.get_model(model_key, 'ensemble')
        if ensemble_forecaster and ensemble_forecaster.is_trained:
            try:
                results['ensemble'] = ensemble_forecaster.evaluate_individual_models(test_data)
            except Exception as e:
                logger.error(f"Failed to evaluate ensemble model for {model_key}: {e}")
                results['ensemble'] = {'error': str(e)}

        return results

    def _save_training_history(self, stats: Dict[str, Any]):
        """Save training statistics to file"""
        try:
            history_file = self.models_dir / "training_history.json"

            # Load existing history
            if history_file.exists():
                with open(history_file, 'r') as f:
                    history = json.load(f)
            else:
                history = []

            # Add new entry
            history.append({
                'timestamp': datetime.now().isoformat(),
                'stats': stats
            })

            # Keep only last 100 entries
            history = history[-100:]

            # Save history
            with open(history_file, 'w') as f:
                json.dump(history, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to save training history: {e}")

    def get_model_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Get metadata for all trained models

        Returns:
            Dictionary of model metadata
        """
        return self.model_metadata.copy()

    def cleanup_old_models(self, days_old: int = 30):
        """
        Remove old model files

        Args:
            days_old: How old models should be before removal
        """
        cutoff_time = datetime.now() - timedelta(days=days_old)
        removed_count = 0

        try:
            for model_file in self.models_dir.glob("*.pkl"):
                if model_file.stat().st_mtime < cutoff_time.timestamp():
                    model_file.unlink()
                    removed_count += 1

            logger.info(f"Removed {removed_count} old model files")

        except Exception as e:
            logger.error(f"Failed to cleanup old models: {e}")

    def get_training_summary(self) -> Dict[str, Any]:
        """
        Get summary of training status

        Returns:
            Dictionary with training summary
        """
        total_models = len(self.model_metadata)
        model_counts = {}
        last_training = None

        for metadata in self.model_metadata.values():
            model_type = metadata['model_type']
            model_counts[model_type] = model_counts.get(model_type, 0) + 1

            if last_training is None or metadata['trained_at'] > last_training:
                last_training = metadata['trained_at']

        return {
            'total_models': total_models,
            'model_counts': model_counts,
            'last_training': last_training,
            'models_dir': str(self.models_dir),
            'auto_retraining_enabled': self.enable_auto_retraining
        }

    def _generate_sample_training_data(self) -> Dict[str, List[Tuple[datetime, float]]]:
        """
        Generate sample training data for testing when database is not available

        Returns:
            Dictionary mapping identifiers to time series data
        """
        import random

        datasets = {}
        base_time = datetime.now() - timedelta(days=30)

        # Generate 3 different time series
        for series_id in range(3):
            time_series = []
            current_time = base_time

            for i in range(500):  # 500 hourly data points
                # Generate synthetic data with different patterns
                if series_id == 0:
                    # Linear trend + noise
                    value = 50 + 0.1 * i + random.gauss(0, 5)
                elif series_id == 1:
                    # Seasonal pattern
                    value = 100 + 30 * np.sin(2 * np.pi * i / 24) + random.gauss(0, 10)
                else:
                    # Mix of patterns
                    value = 75 + 0.05 * i + 20 * np.sin(2 * np.pi * i / 12) + random.gauss(0, 8)

                value = max(0, value)  # Ensure non-negative
                time_series.append((current_time, value))
                current_time += timedelta(hours=1)

            datasets[f"test_template_{series_id}"] = time_series

        logger.info(f"Generated {len(datasets)} sample time series with {len(datasets['test_template_0'])} points each")
        return datasets