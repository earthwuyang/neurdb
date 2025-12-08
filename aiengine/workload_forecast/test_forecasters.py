#!/usr/bin/env python3
"""
Test script for Phase 3 forecasting components
"""

import sys
import numpy as np
from datetime import datetime, timedelta

# Add src directory to Python path
sys.path.insert(0, 'src')

def generate_sample_time_series(n_samples=500):
    """Generate sample time series for testing"""
    timestamps = []
    values = []

    base_time = datetime.now() - timedelta(hours=n_samples)

    for i in range(n_samples):
        timestamp = base_time + timedelta(hours=i)
        # Generate synthetic data with trend, seasonality, and noise
        trend = 10 + 0.1 * i  # Linear trend
        seasonal = 5 * np.sin(2 * np.pi * i / 24)  # Daily seasonality
        noise = np.random.normal(0, 2)  # Random noise
        value = max(0, trend + seasonal + noise)  # Ensure non-negative

        timestamps.append(timestamp)
        values.append(value)

    return list(zip(timestamps, values))

def test_forecaster(forecaster_class, name, data):
    """Test individual forecaster"""
    print(f"\n--- Testing {name} Forecaster ---")

    try:
        # Create and configure forecaster
        if name == 'AR':
            config = {'max_order': 10, 'auto_order': True}
        elif name == 'RNN':
            config = {'hidden_size': 32, 'num_layers': 1, 'epochs': 10, 'batch_size': 16}
        elif name == 'Spectral':
            config = {'n_harmonics': 10}
        elif name == 'Ensemble':
            config = {'models': ['ar', 'rnn']}  # Only use models that work in test
        else:
            config = {}

        forecaster = forecaster_class(config)
        print(f"✓ {name} forecaster created")

        # Test training
        forecaster.fit(data)
        print(f"✓ {name} forecaster trained on {len(data)} samples")

        # Test prediction
        predictions = forecaster.predict(24)  # 24 hour predictions
        print(f"✓ {name} forecaster generated {len(predictions)} predictions")

        # Show sample predictions
        print(f"  Sample predictions:")
        for i, (ts, val) in enumerate(predictions[:5]):
            print(f"    {ts.isoformat()}: {val:.2f}")

        # Test model info
        model_info = forecaster.get_model_info()
        print(f"✓ Model info: {model_info.get('model_type', 'Unknown')}")

        return True

    except Exception as e:
        print(f"✗ {name} forecaster failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function"""
    print("=" * 60)
    print("Phase 3 Forecaster Testing")
    print("=" * 60)

    # Generate test data
    print("Generating sample time series data...")
    test_data = generate_sample_time_series(500)  # Use default 500 samples
    print(f"Generated {len(test_data)} data points")

    # Test individual forecasters
    try:
        from forecasters import ARForecaster, RNNForecaster, SpectralForecaster, EnsembleForecaster
        print("✓ All forecaster modules imported successfully")
    except Exception as e:
        print(f"✗ Failed to import forecasters: {e}")
        return

    results = {
        'AR': test_forecaster(ARForecaster, 'AR', test_data),
        'RNN': test_forecaster(RNNForecaster, 'RNN', test_data),
        'Spectral': test_forecaster(SpectralForecaster, 'Spectral', test_data),
        'Ensemble': test_forecaster(EnsembleForecaster, 'Ensemble', test_data)
    }

    # Test forecasting pipeline
    print(f"\n--- Testing Forecasting Pipeline ---")
    try:
        from forecasting_pipeline import ForecastingPipeline

        config = {
            'models_dir': 'test_models',
            'min_training_samples': 50,
            'model_configs': {
                'ar': {'max_order': 5},
                'rnn': {'hidden_size': 16, 'epochs': 5},
                'spectral': {'n_harmonics': 8}
            },
            'database': None  # Disable database for testing
        }

        pipeline = ForecastingPipeline(config)
        print("✓ ForecastingPipeline created")

        # Test training individual model
        forecaster = pipeline.train_model('ar', test_data, 'test_template')
        if forecaster:
            print("✓ AR model trained via pipeline")
        else:
            print("✗ AR model training failed via pipeline")

        # Test ensemble training
        ensemble = pipeline.train_ensemble(test_data, 'test_template', ['ar', 'spectral'])
        if ensemble:
            print("✓ Ensemble model trained via pipeline")
        else:
            print("✗ Ensemble model training failed via pipeline")

        # Test pipeline forecasts
        forecasts = pipeline.generate_forecasts('test_template', 12, ['ar', 'spectral'])
        if forecasts:
            print(f"✓ Pipeline generated forecasts: {list(forecasts.keys())}")
        else:
            print("✗ Pipeline forecast generation failed")

        print("✓ ForecastingPipeline tests completed")

    except Exception as e:
        print(f"✗ ForecastingPipeline test failed: {e}")
        import traceback
        traceback.print_exc()

    # Summary
    print(f"\n{'=' * 60}")
    print("Test Summary:")
    print(f"{'=' * 60}")
    for name, success in results.items():
        status = "✓ PASSED" if success else "✗ FAILED"
        print(f"{name:15}: {status}")

    total_passed = sum(results.values())
    print(f"\nTotal: {total_passed}/{len(results)} forecasters working")

    if total_passed == len(results):
        print("🎉 All tests passed! Phase 3 implementation is working.")
    else:
        print("⚠ Some tests failed. Check the errors above.")

if __name__ == "__main__":
    main()