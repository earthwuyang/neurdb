#!/usr/bin/env python3
"""
Test script for cost-based optimization endpoints
"""

import requests
import json
import time
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Server configuration
SERVER_URL = "http://localhost:8777"

def test_cost_based_comparison():
    """Test the comparison endpoint"""
    print("Testing cost-based vs pattern-based comparison...")

    try:
        response = requests.get(f"{SERVER_URL}/index/recommendations/cost_based/compare")

        if response.status_code == 200:
            data = response.json()
            print("✅ Comparison endpoint working")
            print(f"Pattern-based approach: {data['pattern_based']['methodology']}")
            print(f"Cost-based approach: {data['cost_based']['methodology']}")
            print(f"Comparison aspects: {len(data['comparison']['aspect'])}")
            return True
        else:
            print(f"❌ Comparison endpoint failed: {response.status_code}")
            print(f"Error: {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error testing comparison: {e}")
        return False

def test_cost_based_optimization():
    """Test the cost-based optimization endpoint"""
    print("\nTesting cost-based optimization...")

    # Sample workload for testing
    test_workload = [
        "SELECT * FROM movie_info WHERE movie_id = 12345",
        "SELECT * FROM cast_info WHERE person_id = 67890 AND role_id = 1",
        "SELECT * FROM title WHERE production_year > 2000 ORDER BY title",
        "SELECT COUNT(*) FROM movie_keyword WHERE keyword_id = 11111",
        "SELECT name FROM person_name WHERE name LIKE 'John%' AND gender = 'm'",
        "SELECT * FROM movie_info WHERE info_type_id = 100 AND movie_id IN (1,2,3,4,5)",
        "SELECT DISTINCT movie_id FROM cast_info WHERE person_id = 12345 ORDER BY movie_id",
        "SELECT * FROM title WHERE kind_id = 1 AND production_year BETWEEN 1990 AND 2020"
    ]

    # Test schema information
    test_schema = {
        'movie_info': {'columns': ['movie_id', 'info_type_id', 'info'], 'size_mb': 500},
        'cast_info': {'columns': ['person_id', 'movie_id', 'role_id', 'note'], 'size_mb': 800},
        'title': {'columns': ['title_id', 'title', 'kind_id', 'production_year'], 'size_mb': 600},
        'person_name': {'columns': ['person_id', 'name', 'gender'], 'size_mb': 100},
        'movie_keyword': {'columns': ['movie_id', 'keyword_id'], 'size_mb': 300}
    }

    request_data = {
        'database_host': 'localhost',
        'database_port': 15432,
        'database_name': 'neurdb',
        'database_user': 'postgres',
        'database_password': 'postgres',
        'workload_queries': test_workload,
        'schema_info': test_schema,
        'time_limit_seconds': 30,  # Short time limit for testing
        'max_indexes_per_table': 2
    }

    try:
        print(f"Sending cost-based optimization request with {len(test_workload)} queries...")
        start_time = time.time()

        response = requests.post(
            f"{SERVER_URL}/index/recommendations/cost_based",
            json=request_data,
            timeout=60  # Add timeout
        )

        end_time = time.time()
        request_time = end_time - start_time

        if response.status_code == 200:
            data = response.json()
            print("✅ Cost-based optimization successful")
            print(f"Optimization time: {request_time:.2f} seconds")
            print(f"Recommended indexes: {len(data['recommended_indexes'])}")
            print(f"Final benefit score: {data['final_benefit_score']:.2f}")
            print(f"Quality improvements: {len(data['quality_improvements'])}")

            # Display top recommendations
            if data['recommended_indexes']:
                print("\nTop recommendations:")
                for i, index in enumerate(data['recommended_indexes'][:3], 1):
                    print(f"  {i}. {index['table_name']}({', '.join(index['columns'])}) - Benefit: {index['benefit_score']:.2f}")

            return True
        else:
            print(f"❌ Cost-based optimization failed: {response.status_code}")
            print(f"Error: {response.text}")
            return False

    except requests.exceptions.Timeout:
        print("⚠️ Request timed out - cost-based optimization can take time")
        return False
    except Exception as e:
        print(f"❌ Error testing cost-based optimization: {e}")
        return False

def test_integrated_cost_based_optimization():
    """Test cost-based optimization through the main service"""
    print("\nTesting integrated cost-based optimization...")

    request_data = {
        'database_host': 'localhost',
        'database_port': 15432,
        'database_name': 'neurdb',
        'database_user': 'postgres',
        'database_password': 'postgres',
        'optimization_approach': 'cost_based',  # Use cost-based approach
        'cost_based_time_limit_seconds': 30,
        'cost_based_max_indexes_per_table': 2,
        'forecast_horizon_hours': 1,
        'max_recommendations': 10,
        'enable_hypothetical_analysis': True
    }

    try:
        print("Sending integrated cost-based optimization request...")
        start_time = time.time()

        response = requests.post(
            f"{SERVER_URL}/index/recommendations/generate",
            json=request_data,
            timeout=60
        )

        end_time = time.time()
        request_time = end_time - start_time

        if response.status_code == 200:
            data = response.json()
            print("✅ Integrated cost-based optimization successful")
            print(f"Total time: {request_time:.2f} seconds")
            print(f"Templates analyzed: {data['total_templates_analyzed']}")
            print(f"Recommendations generated: {data['total_recommendations_generated']}")
            print(f"High confidence recommendations: {data['high_confidence_recommendations']}")

            return True
        else:
            print(f"❌ Integrated optimization failed: {response.status_code}")
            print(f"Error: {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error testing integrated optimization: {e}")
        return False

def test_server_health():
    """Test if server is running"""
    print("Testing server health...")

    try:
        response = requests.get(f"{SERVER_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ Server is healthy")
            return True
        else:
            print(f"❌ Server health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Cannot connect to server: {e}")
        print(f"Make sure server is running on {SERVER_URL}")
        return False

def main():
    """Run all tests"""
    print("=" * 60)
    print("Testing Cost-Based Index Optimization")
    print("=" * 60)

    # Test server health first
    if not test_server_health():
        return

    # Run tests
    tests = [
        ("Comparison Endpoint", test_cost_based_comparison),
        ("Cost-Based Optimization", test_cost_based_optimization),
        ("Integrated Cost-Based Optimization", test_integrated_cost_based_optimization)
    ]

    results = []
    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        result = test_func()
        results.append((test_name, result))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = 0
    total = len(results)

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{test_name}: {status}")
        if result:
            passed += 1

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed!")
    else:
        print("⚠️ Some tests failed - check logs for details")

if __name__ == "__main__":
    main()