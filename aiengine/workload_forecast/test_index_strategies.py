#!/usr/bin/env python3
"""
Test script for the new index management strategy endpoints

Note: This test should be run inside the neurdb_dev Docker container.
To access the container: docker exec -it neurdb_dev bash
Then navigate to /code/neurdb-dev/aiengine/workload_forecast and run this script.
"""

import json
import requests
import time
import sys

# Server configuration - running inside Docker container
BASE_URL = "http://localhost:8777"
TIMEOUT = 30

def test_endpoint(method, endpoint, data=None, params=None):
    """Test a specific endpoint"""
    url = f"{BASE_URL}{endpoint}"

    try:
        if method == "GET":
            response = requests.get(url, params=params, timeout=TIMEOUT)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=TIMEOUT)
        else:
            print(f"Unsupported method: {method}")
            return False

        print(f"\n{'='*60}")
        print(f"TEST: {method} {endpoint}")
        if data:
            print(f"DATA: {json.dumps(data, indent=2)}")
        print(f"STATUS: {response.status_code}")

        try:
            result = response.json()
            print(f"RESPONSE: {json.dumps(result, indent=2)}")
        except:
            print(f"RESPONSE: {response.text}")

        return response.status_code < 400

    except requests.exceptions.ConnectionError:
        print(f"\nERROR: Could not connect to server at {BASE_URL}")
        print("Please start the server first: python run_server.py")
        return False
    except Exception as e:
        print(f"\nERROR: {e}")
        return False

def main():
    """Run all tests"""
    print("Testing Index Management Strategy Endpoints")
    print(f"Server URL: {BASE_URL}")

    # Test server health first
    if not test_endpoint("GET", "/health"):
        print("Server health check failed. Please start the server.")
        sys.exit(1)

    print("✓ Server is healthy")

    # Test predictive strategy endpoints

    # 1. Test predictive status (should return not_initialized)
    print("\n\n" + "="*80)
    print("TESTING PREDICTIVE STRATEGY")
    print("="*80)
    test_endpoint("GET", "/index/predictive/status")

    # 2. Test predictive optimization
    predictive_config = {
        "forecast_horizon_hours": 24,
        "storage_budget_mb": 100.0,
        "max_indexes_to_create": 5,
        "min_index_benefit_threshold": 0.1
    }
    test_endpoint("POST", "/index/predictive/optimize", predictive_config)

    # Test reactive strategy endpoints

    # 3. Test reactive status (should return not_initialized)
    print("\n\n" + "="*80)
    print("TESTING REACTIVE STRATEGY")
    print("="*80)
    test_endpoint("GET", "/index/reactive/status")

    # 4. Test reactive recommendation
    reactive_recommend_data = {
        "query_text": "SELECT * FROM users WHERE email = 'test@example.com'",
        "storage_budget_mb": 50.0,
        "min_benefit_threshold": 0.1
    }
    test_endpoint("POST", "/index/reactive/recommend", reactive_recommend_data)

    # 5. Test reactive management (with auto_create=False to be safe)
    reactive_manage_data = {
        "query_text": "SELECT * FROM orders WHERE user_id = 123 AND status = 'pending'",
        "auto_create": False,
        "storage_budget_mb": 50.0
    }
    test_endpoint("POST", "/index/reactive/manage", reactive_manage_data)

    # Test strategy switching
    print("\n\n" + "="*80)
    print("TESTING STRATEGY SWITCHING")
    print("="*80)

    # 6. Test switching to predictive
    test_endpoint("POST", "/index/switch_strategy", {"strategy": "predictive"})

    # 7. Test switching to reactive
    test_endpoint("POST", "/index/switch_strategy", {"strategy": "reactive"})

    # 8. Test invalid strategy
    test_endpoint("POST", "/index/switch_strategy", {"strategy": "invalid"})

    print("\n\n" + "="*80)
    print("ALL TESTS COMPLETED")
    print("="*80)
    print("\nNote: Some tests may show errors due to missing database connection or")
    print("hypothetical analyzer dependencies. This is expected in the current setup.")

if __name__ == "__main__":
    main()