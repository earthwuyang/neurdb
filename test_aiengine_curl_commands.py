#!/usr/bin/env python3
"""
Show curl commands to test AI Engine automatic index creation
"""

def show_curl_commands():
    """Display curl commands for testing AI Engine index creation"""

    print("🔧" + "="*80)
    print("🌡️ TESTING AI ENGINE WITH CURL COMMANDS")
    print("🔧" + "="*80)

    print(f"\n📋 Basic curl command structure:")
    print(f"curl -X POST http://0.0.0.0:8777/index/auto_create \\")
    print(f"  -H \"Content-Type: application/json\" \\")
    print(f"  -d '{{\"workload_queries\": [...], \"database_host\": ...}}'")

    print(f"\n🎯 Test 1: Simple query that should benefit from indexing")

    # Simple test with clear indexing opportunity
    simple_query = {
        "workload_queries": [
            {
                "query": "SELECT * FROM cast_info WHERE cast_info.person_id = 12345",
                "execution_time": 150.0
            }
        ],
        "database_host": "localhost",
        "database_port": 15432,
        "database_name": "imdb_test",
        "database_user": "neurdb",
        "time_limit_seconds": 60
    }

    print(f"\ncurl -X POST http://0.0.0.0:8777/index/auto_create \\")
    print(f"  -H \"Content-Type: application/json\" \\")
    print(f"  -d '{json.dumps(simple_query)}'")

    print(f"\n🎯 Test 2: Multiple queries that should benefit from indexes")

    multi_query = {
        "workload_queries": [
            {
                "query": "SELECT * FROM title WHERE title.production_year > 2000 ORDER BY title.production_year DESC",
                "execution_time": 200.0
            },
            {
                "query": "SELECT * FROM movie_info WHERE movie_info.info_type_id = 3",
                "execution_time": 180.0
            },
            {
                "query": "SELECT * FROM cast_info WHERE cast_info.person_id = 12345",
                "execution_time": 150.0
            }
        ],
        "database_host": "localhost",
        "database_port": 15432,
        "database_name": "imdb_test",
        "database_user": "neurdb",
        "time_limit_seconds": 90
    }

    print(f"\ncurl -X POST http://0.0.0.0:8777/index/auto_create \\")
    print(f"  -H \"Content-Type: application/json\" \\")
    print(f"  -d '{json.dumps(multi_query)}'")

    print(f"\n🎯 Test 3: Complex multi-join query")

    complex_query = {
        "workload_queries": [
            {
                "query": "SELECT \"info_type\".\"info\", MAX(\"movie_companies\".\"company_type_id\") as agg_0 FROM \"movie_link\" JOIN \"title\" ON \"movie_link\".\"movie_id\" = \"title\".\"id\" JOIN \"complete_cast\" ON \"title\".\"id\" = \"complete_cast\".\"movie_id\" JOIN \"movie_companies\" ON \"title\".\"id\" = \"movie_companies\".\"movie_id\" WHERE \"movie_companies\".\"id\" >= 2461456.062838372 AND \"complete_cast\".\"id\" <= 27781.270662020706 GROUP BY \"info_type\".\"info\" ORDER BY \"info_type\".\"info\" LIMIT 10",
                "execution_time": 500.0
            }
        ],
        "database_host": "localhost",
        "database_port": 15432,
        "database_name": "imdb_test",
        "database_user": "neurdb",
        "time_limit_seconds": 120
    }

    print(f"\ncurl -X POST http://0.0.0.0:8777/index/auto_create \\")
    print(f"  -H \"Content-Type: application/json\" \\")
    print(f"  -d '{json.dumps(complex_query)}'")

    print(f"\n💰 Test 4: With increased budget simulation")

    # Test with direct index recommendations
    direct_rec = {
        "recommended_indexes": [
            {
                "table_name": "cast_info",
                "columns": ["person_id"],
                "index_type": "btree",
                "estimated_size_mb": 75,
                "benefit_score": 85
            },
            {
                "table_name": "title",
                "columns": ["production_year"],
                "index_type": "btree",
                "estimated_size_mb": 50,
                "benefit_score": 120
            },
            {
                "table_name": "movie_info",
                "columns": ["info_type_id"],
                "index_type": "btree",
                "estimated_size_mb": 45,
                "benefit_score": 100
            }
        ],
        "database_host": "localhost",
        "database_port": 15432,
        "database_name": "imdb_test",
        "database_user": "neurdb",
        "custom_storage_budget_mb": 10000.0
    }

    print(f"\ncurl -X POST http://0.0.0.0:8777/index/auto_create \\")
    print(f"  -H \"Content-Type: application/json\" \\")
    print(f"  -d '{json.dumps(direct_rec)}'")

    print(f"\n📊 What to look for in the response:")
    print(f"   • \"status\": \"success\" - Request processed")
    print(f"   • \"total_recommendations\": X - How many indexes were analyzed")
    print(f"   • \"created_indexes\": Y - How many were actually created")
    print(f"   • \"storage_budget\" - Budget usage details")
    print(f"   • \"created_indexes\" array - List of created indexes with sizes")

    print(f"\n🔍 Expected response format:")
    print(f"""{{
  "status": "success",
  "message": "Successfully created X out of Y recommended indexes",
  "storage_budget": {{
    "budget_mb": 1057.10,
    "usage_before_mb": 1783.17,
    "usage_after_mb": 1828.67,
    "size_added_mb": 45.50,
    "remaining_budget_mb": 628.43
  }},
  "index_creation_results": {{
    "total_recommendations": 3,
    "created_indexes": 1,
    "failed_indexes": 2,
    "success_rate": 0.33
  }},
  "created_indexes": [
    {{
      "index_name": "idx_auto_cast_info_person_id_0",
      "table_name": "cast_info",
      "columns": ["person_id"],
      "actual_size_mb": 75.20,
      "status": "created"
    }}
  ],
  "failed_indexes": [
    {{
      "table_name": "title",
      "reason": "Insufficient budget after eviction"
    }}
  ]
}}""")

    print(f"\n🏆 Step-by-step testing instructions:")
    print(f"   1. First, test with simple query to verify AI Engine is working")
    print(f"   2. Check if any indexes are recommended (look at total_recommendations)")
    print(f"   3. If recommendations > 0 but created_indexes = 0, budget is the issue")
    print(f"   4. Use Test 4 to bypass budget constraints and see direct creation")
    print(f"   5. Monitor the response for actual index creation")

    print(f"\n⚡ Quick test command (copy & paste):")
    print(f"curl -X POST http://0.0.0.0:8777/index/auto_create \\")
    print(f"  -H \"Content-Type: application/json\" \\")
    print(f"  -d '{{\"workload_queries\": [{{\"query\": \"SELECT * FROM cast_info WHERE person_id = 12345\", \"execution_time\": 150.0}}], \"database_host\": \"localhost\", \"database_port\": 15432, \"database_name\": \"imdb_test\", \"database_user\": \"neurdb\"}}'")

if __name__ == "__main__":
    import json
    show_curl_commands()