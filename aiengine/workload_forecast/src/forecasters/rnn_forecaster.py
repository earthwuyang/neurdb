"""
RNN/LSTM forecaster using PyTorch
Implements deep learning models for time series forecasting
"""

import os
import pickle
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import MinMaxScaler
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from .base_forecaster import BaseForecaster, ForecasterError

logger = logging.getLogger(__name__)


class LSTMModel(nn.Module):
    """LSTM model for time series forecasting"""

    def __init__(self, input_size: int = 1, hidden_size: int = 128, num_layers: int = 2,
                 output_size: int = 1, dropout: float = 0.2):
        super(LSTMModel, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        # LSTM layers
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                           batch_first=True, dropout=dropout if num_layers > 1 else 0)

        # Dropout layer
        self.dropout = nn.Dropout(dropout)

        # Fully connected layers
        self.fc1 = nn.Linear(hidden_size, hidden_size // 2)
        self.fc2 = nn.Linear(hidden_size // 2, output_size)
        self.relu = nn.ReLU()

    def forward(self, x):
        # Initialize hidden state
        batch_size = x.size(0)
        h0 = torch.zeros(self.num_layers, batch_size, self.hidden_size).to(x.device)
        c0 = torch.zeros(self.num_layers, batch_size, self.hidden_size).to(x.device)

        # LSTM forward pass
        out, _ = self.lstm(x, (h0, c0))

        # Take the last output
        out = out[:, -1, :]

        # Apply dropout
        out = self.dropout(out)

        # Fully connected layers
        out = self.relu(self.fc1(out))
        out = self.fc2(out)

        return out


class RNNForecaster(BaseForecaster):
    """
    RNN/LSTM forecaster for time series forecasting
    Supports both LSTM and GRU architectures
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize RNN forecaster

        Args:
            config: Configuration parameters
                - model_type: 'lstm' or 'gru' (default: 'lstm')
                - hidden_size: Hidden layer size (default: 128)
                - num_layers: Number of RNN layers (default: 2)
                - sequence_length: Input sequence length (default: 24)
                - batch_size: Training batch size (default: 32)
                - learning_rate: Learning rate (default: 0.001)
                - epochs: Number of training epochs (default: 100)
                - dropout: Dropout rate (default: 0.2)
                - early_stopping_patience: Patience for early stopping (default: 10)
                - validation_split: Fraction of data for validation (default: 0.2)
        """
        super().__init__(config)

        self.model_type = self.config.get('model_type', 'lstm')
        self.hidden_size = self.config.get('hidden_size', 128)
        self.num_layers = self.config.get('num_layers', 2)
        self.sequence_length = self.config.get('sequence_length', 24)
        self.batch_size = self.config.get('batch_size', 32)
        self.learning_rate = self.config.get('learning_rate', 0.001)
        self.epochs = self.config.get('epochs', 100)
        self.dropout = self.config.get('dropout', 0.2)
        self.early_stopping_patience = self.config.get('early_stopping_patience', 10)
        self.validation_split = self.config.get('validation_split', 0.2)

        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        logger.info(f"Using device: {self.device}")

        self.model = None
        self.scaler = None
        self.training_data = None
        self.last_timestamp = None
        self.training_losses = []

    def get_min_required_samples(self) -> int:
        """Get minimum samples required for RNN training"""
        return max(100, self.sequence_length * 4)

    def _create_sequences(self, data: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Create input sequences and targets for RNN training

        Args:
            data: Time series data

        Returns:
            Tuple of (sequences, targets)
        """
        sequences = []
        targets = []

        for i in range(len(data) - self.sequence_length):
            sequences.append(data[i:i + self.sequence_length])
            targets.append(data[i + self.sequence_length])

        return np.array(sequences), np.array(targets)

    def _build_model(self) -> nn.Module:
        """Build RNN model"""
        if self.model_type.lower() == 'lstm':
            model = LSTMModel(
                input_size=1,
                hidden_size=self.hidden_size,
                num_layers=self.num_layers,
                output_size=1,
                dropout=self.dropout
            )
        else:  # GRU
            model = nn.Sequential(
                nn.GRU(input_size=1, hidden_size=self.hidden_size,
                      num_layers=self.num_layers, batch_first=True,
                      dropout=self.dropout if self.num_layers > 1 else 0),
                nn.Dropout(self.dropout),
                nn.Linear(self.hidden_size, self.hidden_size // 2),
                nn.ReLU(),
                nn.Linear(self.hidden_size // 2, 1)
            )

        return model.to(self.device)

    def fit(self, time_series: List[Tuple[datetime, float]]) -> None:
        """
        Train RNN model on time series data

        Args:
            time_series: List of (timestamp, value) tuples
        """
        if not self.validate_time_series(time_series):
            raise ForecasterError("Invalid time series data")

        logger.info(f"Training RNN forecaster on {len(time_series)} samples")

        # Preprocess data
        timestamps, values = self.preprocess_time_series(time_series)
        self.training_data = (timestamps, values)
        self.last_timestamp = timestamps[-1]

        # Normalize data
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        scaled_values = self.scaler.fit_transform(values.reshape(-1, 1)).flatten()

        # Create sequences
        X, y = self._create_sequences(scaled_values)

        # Split data
        split_idx = int(len(X) * (1 - self.validation_split))
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        # Convert to PyTorch tensors
        X_train = torch.FloatTensor(X_train).unsqueeze(-1).to(self.device)
        y_train = torch.FloatTensor(y_train).unsqueeze(-1).to(self.device)
        X_val = torch.FloatTensor(X_val).unsqueeze(-1).to(self.device)
        y_val = torch.FloatTensor(y_val).unsqueeze(-1).to(self.device)

        # Create data loaders
        train_dataset = TensorDataset(X_train, y_train)
        train_loader = DataLoader(train_dataset, batch_size=self.batch_size, shuffle=True)
        val_dataset = TensorDataset(X_val, y_val)
        val_loader = DataLoader(val_dataset, batch_size=self.batch_size, shuffle=False)

        # Build and train model
        self.model = self._build_model()
        criterion = nn.MSELoss()
        optimizer = optim.Adam(self.model.parameters(), lr=self.learning_rate)
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

        # Training loop
        best_val_loss = float('inf')
        patience_counter = 0
        self.training_losses = []

        for epoch in range(self.epochs):
            # Training phase
            self.model.train()
            train_loss = 0.0
            for batch_X, batch_y in train_loader:
                optimizer.zero_grad()
                outputs = self.model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()
                train_loss += loss.item()

            train_loss /= len(train_loader)

            # Validation phase
            self.model.eval()
            val_loss = 0.0
            with torch.no_grad():
                for batch_X, batch_y in val_loader:
                    outputs = self.model(batch_X)
                    loss = criterion(outputs, batch_y)
                    val_loss += loss.item()

            val_loss /= len(val_loader)
            self.training_losses.append(train_loss)

            # Learning rate scheduling
            scheduler.step(val_loss)

            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                # Save best model
                best_model_state = self.model.state_dict().copy()
            else:
                patience_counter += 1

            if (epoch + 1) % 10 == 0:
                logger.info(f"Epoch {epoch + 1}/{self.epochs}, "
                          f"Train Loss: {train_loss:.6f}, Val Loss: {val_loss:.6f}")

            if patience_counter >= self.early_stopping_patience:
                logger.info(f"Early stopping at epoch {epoch + 1}")
                break

        # Load best model
        if 'best_model_state' in locals():
            self.model.load_state_dict(best_model_state)

        # Store model information
        self.model_info = {
            'model_type': self.model_type,
            'hidden_size': self.hidden_size,
            'num_layers': self.num_layers,
            'sequence_length': self.sequence_length,
            'training_epochs': epoch + 1,
            'final_train_loss': train_loss,
            'final_val_loss': val_loss,
            'best_val_loss': best_val_loss,
            'training_samples': len(values),
            'device': str(self.device)
        }

        self.is_trained = True
        logger.info(f"RNN model trained successfully: {self.model_info}")

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

        logger.info(f"Generating RNN predictions for {horizon_minutes} minutes")

        try:
            self.model.eval()
            predictions = []

            # Start with the last sequence from training data
            _, values = self.training_data
            scaled_values = self.scaler.transform(values.reshape(-1, 1)).flatten()

            # Get last sequence
            if len(scaled_values) >= self.sequence_length:
                current_sequence = scaled_values[-self.sequence_length:].copy()
            else:
                # Pad with zeros if not enough data
                padding = np.zeros(self.sequence_length - len(scaled_values))
                current_sequence = np.concatenate([padding, scaled_values])

            with torch.no_grad():
                for i in range(horizon_minutes):
                    # Prepare input
                    input_seq = torch.FloatTensor(current_sequence).unsqueeze(0).unsqueeze(-1).to(self.device)

                    # Make prediction
                    pred = self.model(input_seq)
                    pred_value = pred.cpu().numpy()[0, 0]

                    # Inverse transform to original scale
                    pred_value = self.scaler.inverse_transform([[pred_value]])[0, 0]
                    predictions.append(pred_value)

                    # Update sequence for next prediction
                    current_sequence = np.append(current_sequence[1:],
                                                self.scaler.transform([[pred_value]])[0, 0])

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

            # Combine timestamps and predictions
            result = list(zip(prediction_timestamps, np.maximum(0, predictions)))

            logger.info(f"Generated {len(result)} RNN predictions")
            return result

        except Exception as e:
            raise ForecasterError(f"Failed to generate predictions: {e}")

    def save_model(self, filepath: str) -> None:
        """
        Save trained model to file

        Args:
            filepath: Path to save the model
        """
        if not self.is_trained:
            raise ForecasterError("Cannot save untrained model")

        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            # Save model state
            model_state = {
                'model_state_dict': self.model.state_dict(),
                'model_config': {
                    'model_type': self.model_type,
                    'hidden_size': self.hidden_size,
                    'num_layers': self.num_layers,
                    'sequence_length': self.sequence_length,
                    'dropout': self.dropout
                },
                'scaler': self.scaler,
                'config': self.config,
                'model_info': self.model_info,
                'training_data': self.training_data,
                'last_timestamp': self.last_timestamp,
                'training_losses': self.training_losses
            }

            torch.save(model_state, filepath)
            logger.info(f"RNN model saved to {filepath}")

        except Exception as e:
            raise ForecasterError(f"Failed to save model: {e}")

    def load_model(self, filepath: str) -> None:
        """
        Load trained model from file

        Args:
            filepath: Path to load the model from
        """
        try:
            # Load model state
            model_state = torch.load(filepath, map_location=self.device)

            # Restore configuration
            self.model_type = model_state['model_config']['model_type']
            self.hidden_size = model_state['model_config']['hidden_size']
            self.num_layers = model_state['model_config']['num_layers']
            self.sequence_length = model_state['model_config']['sequence_length']
            self.dropout = model_state['model_config']['dropout']

            # Restore scaler and data
            self.scaler = model_state['scaler']
            self.config = model_state.get('config', {})
            self.model_info = model_state.get('model_info', {})
            self.training_data = model_state.get('training_data')
            self.last_timestamp = model_state.get('last_timestamp')
            self.training_losses = model_state.get('training_losses', [])

            # Rebuild and load model
            self.model = self._build_model()
            self.model.load_state_dict(model_state['model_state_dict'])
            self.is_trained = True

            logger.info(f"RNN model loaded from {filepath}")

        except Exception as e:
            raise ForecasterError(f"Failed to load model: {e}")

    def get_training_curve(self) -> List[float]:
        """
        Get training loss curve

        Returns:
            List of training losses per epoch
        """
        return self.training_losses.copy() if self.training_losses else []

    def evaluate_on_test_data(self, test_data: List[Tuple[datetime, float]]) -> Dict[str, float]:
        """
        Evaluate model on test data

        Args:
            test_data: Test time series data

        Returns:
            Dictionary of evaluation metrics
        """
        if not self.is_trained:
            raise ForecasterError("Model must be trained before evaluation")

        if len(test_data) < self.sequence_length + 1:
            raise ForecasterError("Test data too short for evaluation")

        logger.info(f"Evaluating RNN model on {len(test_data)} test samples")

        try:
            # Prepare test data
            test_timestamps, test_values = self.preprocess_time_series(test_data)
            scaled_test_values = self.scaler.transform(test_values.reshape(-1, 1)).flatten()

            # Create test sequences
            X_test, y_test = self._create_sequences(scaled_test_values)

            # Convert to tensors
            X_test = torch.FloatTensor(X_test).unsqueeze(-1).to(self.device)
            y_test = torch.FloatTensor(y_test).unsqueeze(-1).to(self.device)

            # Make predictions
            self.model.eval()
            predictions = []
            with torch.no_grad():
                for i in range(0, len(X_test), self.batch_size):
                    batch_X = X_test[i:i + self.batch_size]
                    batch_pred = self.model(batch_X)
                    predictions.extend(batch_pred.cpu().numpy().flatten())

            # Inverse transform predictions
            predictions = np.array(predictions)
            predictions = self.scaler.inverse_transform(predictions.reshape(-1, 1)).flatten()

            # Inverse transform actual values
            y_test_actual = self.scaler.inverse_transform(y_test.cpu().numpy().reshape(-1, 1)).flatten()

            # Calculate metrics
            return self.evaluate_predictions(
                list(zip(test_timestamps[self.sequence_length:], y_test_actual)),
                list(zip(test_timestamps[self.sequence_length:], predictions))
            )

        except Exception as e:
            raise ForecasterError(f"Failed to evaluate model: {e}")