#!/usr/bin/env python3
"""
Test script for the new cost-based index advisor
Demonstrates hypopg-based cost estimation and progressive optimization
"""

import sys
import os
import json
import logging
from datetime import datetime
import time

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from cost_based_index_advisor import CostBasedIndexAdvisor, IndexType

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/test_cost_based_advisor.log')
    ]
)
logger = logging.getLogger(__name__)


def test_cost_based_advisor():
    """Test the cost-based index advisor with sample workload"""

    # Database connection parameters for NeurDB
    db_params = {
        'host': 'localhost',
        'port': 15432,
        'database': 'neurdb',
        'user': 'postgres',
        'password': 'postgres'
    }

    # Sample workload queries
    sample_queries = [
        "SELECT * FROM movie_info WHERE movie_id = 12345",
        "SELECT * FROM cast_info WHERE person_id = 67890 AND role_id = 1",
        "SELECT * FROM title WHERE production_year > 2000 ORDER BY title",
        "SELECT COUNT(*) FROM movie_keyword WHERE keyword_id = 11111",
        "SELECT name FROM person_name WHERE name LIKE 'John%' AND gender = 'm'",
        "SELECT * FROM movie_info WHERE info_type_id = 100 AND movie_id IN (1,2,3,4,5)",
        "SELECT DISTINCT movie_id FROM cast_info WHERE person_id = 12345 ORDER BY movie_id",
        "SELECT * FROM title WHERE kind_id = 1 AND production_year BETWEEN 1990 AND 2020",
        "SELECT COUNT(*) FROM cast_info GROUP BY role_id",
        "SELECT * FROM movie_info WHERE movie_id = 12345 AND info_type_id = 3"
    ]

    # Create some variation for workload testing
    workload_queries = []
    for query in sample_queries:
        # Add the query multiple times with slight variations
        for i in range(3):  # 3 variations of each query
            workload_queries.append(query)
            if 'movie_id = ' in query:
                # Change the ID
                modified_query = query.replace('movie_id = 12345', f'movie_id = {12345 + i * 1000}')
                workload_queries.append(modified_query)

    try:
        # Initialize the cost-based advisor
        advisor = CostBasedIndexAdvisor(db_params)
        logger.info("Cost-based index advisor initialized")

        # Create sample schema information
        schema_info = {
            'movie_info': {'columns': ['movie_id', 'info_type_id', 'info'], 'size_mb': 500},
            'cast_info': {'columns': ['person_id', 'movie_id', 'role_id', 'note'], 'size_mb': 800},
            'title': {'columns': ['title_id', 'title', 'kind_id', 'production_year'], 'size_mb': 600},
            'person_name': {'columns': ['person_id', 'name', 'gender'], 'size_mb': 100},
            'movie_keyword': {'columns': ['movie_id', 'keyword_id'], 'size_mb': 300}
        }

        logger.info(f"Starting optimization with {len(workload_queries)} queries")
        logger.info(f"Sample queries: {workload_queries[:3]}...")

        # Run progressive optimization
        result = advisor.recommend_indexes(
            workload_queries=workload_queries,
            schema_info=schema_info,
            time_limit_seconds=30  # Short time limit for testing
        )

        # Display results
        print("\n" + "="*60)
        print("COST-BASED INDEX OPTIMIZATION RESULTS")
        print("="*60)

        print(f"\nOptimization completed in {result.optimization_time:.2f} seconds")
        print(f"Final benefit score: {result.best_solution.benefit_score:.2f}")
        print(f"Recommended indexes: {len(result.best_solution.indexes)}")

        print(f"\nQuality improvements timeline:")
        for i, (time_improvement, benefit_score) in enumerate(result.quality_improvements):
            print(f"  {i+1}. Time: {time_improvement:.1f}s, Benefit Score: {benefit_score:.2f}")

        print(f"\nRecommended Indexes:")
        for i, index in enumerate(result.best_solution.indexes, 1):
            print(f"  {i}. {index.table_name}({', '.join(index.columns)})")
            print(f"     Type: {index.index_type.value}")
            print(f"     Estimated Size: {index.estimated_size_mb:.1f} MB")
            print(f"     Creation Cost: {index.creation_cost:.1f}")

        print(f"\nConfiguration Summary:")
        print(f"  Total Indexes: {len(result.best_solution.indexes)}")
        print(f"  Storage Cost: {result.best_solution.storage_cost:.1f} MB")
        print(f"  Maintenance Cost: {result.best_solution.maintenance_cost:.1f}")
        print(f"  Net Benefit Score: {result.best_solution.benefit_score:.2f}")

        # Test saving recommendations
        recommendations = {
            'optimization_time': result.optimization_time,
            'benefit_score': result.best_solution.benefit_score,
            'indexes': [
                {
                    'table': index.table_name,
                    'columns': index.columns,
                    'index_type': index.index_type.value,
                    'estimated_size_mb': index.estimated_size_mb,
                    'sql': f"CREATE INDEX idx_{index.table_name}_{'_'.join(index.columns)} ON {index.table_name} ({', '.join(index.columns)}) USING {index.index_type.value}"
                }
                for index in result.best_solution.indexes
            ],
            'quality_timeline': [
                {'time': time_improvement, 'benefit_score': benefit_score}
                for time_improvement, benefit_score in result.quality_improvements
            ]
        }

        # Save to JSON file
        with open('/tmp/cost_based_recommendations.json', 'w') as f:
            json.dump(recommendations, f, indent=2)

        print(f"\nRecommendations saved to: /tmp/cost_based_recommendations.json")

        return True

    except Exception as e:
        logger.error(f"Error during cost-based optimization: {e}")
        return False


def compare_with_pattern_based():
    """Compare cost-based results with pattern-based approach"""
    print("\n" + "="*60)
    print("COMPARISON WITH PATTERN-BASED APPROACH")
    print("="*60)

    print("\nPattern-Based Approach (Current Phase 4):")
    print("- Uses regex patterns to extract SQL constructs")
    print("- Heuristic scoring based on frequency")
    print("- No real PostgreSQL cost model integration")
    print("- Limited to obvious patterns")
    print("- Quick but potentially suboptimal")

    print("\nCost-Based Approach (New Implementation):")
    print("- Uses hypopg for real PostgreSQL cost estimation")
    print("- EXPLAIN (ANALYZE, BUFFERS) for actual execution costs")
    print("- DTA-inspired progressive optimization")
    print("- Comprehensive combinatorial search")
    print("- Optimal but computationally intensive")

    print("\nKey Differences:")
    print("1. Cost Model: Heuristic vs Real PostgreSQL Costs")
    print("2. Search: Pattern Recognition vs Combinatorial Optimization")
    print("3. Validation: None vs Real Query Planner")
    print("4. Quality: Fast Approximation vs Optimal Solution")
    print("5. Progressivity: Single Pass vs Anytime Algorithm")


if __name__ == "__main__":
    print("Testing Cost-Based Index Advisor with HypoPG Integration")
    print("="*60)

    success = test_cost_based_advisor()

    if success:
        print("\n✅ Cost-based index advisor test completed successfully!")
        compare_with_pattern_based()
    else:
        print("\n❌ Cost-based index advisor test failed!")
        sys.exit(1)