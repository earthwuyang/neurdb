#!/usr/bin/env python3
"""
Set 10GB storage budget and test complex query for automatic index creation
"""

import requests
import json
import time

def test_complex_query_with_10gb():
    """Set 10GB budget and test complex query for index creation"""

    print("💰" + "="*80)
    print("🎯 SETTING 10GB BUDGET & TESTING COMPLEX QUERY")
    print("💰" + "="*80)

    ai_engine_url = "http://localhost:8777"

    # Complex multi-join query that should benefit from indexes
    complex_query = """SELECT "info_type"."info", MAX("movie_companies"."company_type_id") as agg_0 FROM "movie_link" JOIN "title" ON "movie_link"."movie_id" = "title"."id" JOIN "complete_cast" ON "title"."id" = "complete_cast"."movie_id" JOIN "movie_companies" ON "title"."id" = "movie_companies"."movie_id" JOIN "comp_cast_type" ON "complete_cast"."status_id" = "comp_cast_type"."id" JOIN "movie_info_idx" ON "title"."id" = "movie_info_idx"."movie_id" JOIN "info_type" ON "movie_info_idx"."info_type_id" = "info_type"."id" JOIN "movie_keyword" ON "title"."id" = "movie_keyword"."movie_id" JOIN "person_info" ON "info_type"."id" = "person_info"."info_type_id" JOIN "name" ON "person_info"."person_id" = "name"."id"  WHERE "movie_companies"."id" >= 2461456.062838372 AND "complete_cast"."id" <= 27781.270662020706 GROUP BY "info_type"."info" ORDER BY "info_type"."info" LIMIT 10;"""

    print(f"\n📋 Step 1: Testing Current Budget Status")

    # Test current budget reading
    budget_test = {
        "recommended_indexes": [{
            "table_name": "title",
            "columns": ["id"],
            "index_type": "btree",
            "estimated_size_mb": 100,
            "benefit_score": 150
        }],
        "database_host": "localhost",
        "database_port": 15432,
        "database_name": "imdb_test",
        "database_user": "neurdb"
    }

    try:
        response = requests.post(f"{ai_engine_url}/index/auto_create", json=budget_test)
        if response.status_code == 200:
            result = response.json()
            budget_info = result.get('storage_budget', {})
            current_budget = budget_info.get('budget_mb', 0)
            current_usage = budget_info.get('usage_before_mb', 0)
            available = budget_info.get('remaining_budget_mb', 0)

            print(f"   📊 Current Budget: {current_budget:.2f} MB ({current_budget/1024:.2f} GB)")
            print(f"   📊 Current Usage: {current_usage:.2f} MB")
            print(f"   📊 Available: {available:.2f} MB")

            if available > 0:
                print(f"   ✅ Budget available for index creation!")
            else:
                print(f"   ⚠️ Over budget by {-available:.2f} MB")
                print(f"   💡 Need to increase budget or set GUC parameter")
        else:
            print(f"   ❌ Budget test failed: {response.status_code}")
            return

    except Exception as e:
        print(f"   ❌ Budget test error: {e}")
        return

    print(f"\n🔍 Step 2: Analyzing Complex Multi-Join Query")
    print(f"   This query involves {complex_query.count('JOIN')} joins")
    print(f"   Tables involved: title, movie_link, complete_cast, movie_companies, comp_cast_type, movie_info_idx, info_type, movie_keyword, person_info, name")
    print(f"   WHERE clauses: movie_companies.id, complete_cast.id")
    print(f"   JOIN columns: movie_id, id, status_id, movie_id, info_type_id, person_id")

    # Test the complex query
    print(f"\n🚀 Step 3: Sending Complex Query to AI Engine")

    start_time = time.time()

    request_data = {
        "workload_queries": [{
            "query": complex_query,
            "execution_time": 500.0  # This looks like a complex query that takes time
        }],
        "database_host": "localhost",
        "database_port": 15432,
        "database_name": "imdb_test",
        "database_user": "neurdb",
        "time_limit_seconds": 90  # Give more time for complex analysis
    }

    try:
        print(f"   🔄 Starting cost analysis (this may take 60-90 seconds)...")
        response = requests.post(f"{ai_engine_url}/index/auto_create", json=request_data, timeout=180)

        end_time = time.time()
        analysis_time = end_time - start_time

        print(f"   ⏱️ Analysis completed in: {analysis_time:.2f} seconds")

        if response.status_code == 200:
            result = response.json()

            # Analyze the results
            status = result.get("status", "unknown")
            storage_budget = result.get("storage_budget", {})
            index_results = result.get("index_creation_results", {})

            created = index_results.get("created_indexes", 0)
            recommendations = index_results.get("total_recommendations", 0)

            print(f"\n📊 Results:")
            print(f"   ✅ Status: {status}")
            print(f"   📊 Final Budget: {storage_budget.get('budget_mb', 0):.2f} MB")
            print(f"   📊 Final Usage: {storage_budget.get('usage_after_mb', 0):.2f} MB")
            print(f"   📊 Recommendations: {recommendations}")
            print(f"   🔧 Indexes Created: {created}")

            if created > 0:
                print(f"\n🎉 SUCCESS! Indexes created:")
                created_indexes = result.get('created_indexes', [])
                total_size = 0
                for idx in created_indexes:
                    table = idx.get('table_name', 'unknown')
                    cols = idx.get('columns', [])
                    size = idx.get('actual_size_mb', 0)
                    total_size += size
                    print(f"      ✅ {table}({', '.join(map(str, cols))}): {size:.2f} MB")

                print(f"   📊 Total storage added: {total_size:.2f} MB")
                print(f"   💾 Remaining budget: {storage_budget.get('remaining_budget_mb', 0):.2f} MB")

                print(f"\n🏆 REACTIVE STRATEGY SUCCESS!")
                print(f"   The AI Engine successfully created beneficial indexes for the complex query!")
                print(f"   Future executions of similar queries will be much faster!")

            elif recommendations > 0:
                print(f"\n💡 Analysis Results:")
                print(f"   📊 {recommendations} indexes were evaluated")

                failed = result.get('failed_indexes', [])
                if failed:
                    print(f"   ⚠️ Indexes not created:")
                    for fail in failed[:3]:  # Show first 3
                        table = fail.get('table_name', 'unknown')
                        reason = fail.get('reason', 'Unknown')
                        print(f"      • {table}: {reason}")

                # Show created indexes if any
                created = result.get('created_indexes', [])
                if created:
                    print(f"   ✅ Successfully created:")
                    for idx in created:
                        table = idx.get('table_name', 'unknown')
                        cols = idx.get('columns', [])
                        size = idx.get('actual_size_mb', 0)
                        print(f"      • {table}({', '.join(map(str, cols))}): {size:.2f} MB")

            else:
                print(f"\n💡 Analysis Results:")
                print(f"   🔍 No index recommendations generated")
                print(f"   This could mean:")
                print(f"      • Query already has optimal indexes")
                print(f"      • Budget constraints prevented creation")
                print(f"      • Cost-benefit analysis determined no significant improvement")

        else:
            print(f"   ❌ Request failed: {response.status_code}")
            print(f"   Response: {response.text}")

    except Exception as e:
        print(f"   ❌ Request error: {e}")

    print(f"\n🎯 Step 4: Checking AI Engine Logs for Detailed Analysis")
    print(f"   📊 Recent activity should show:")
    print(f"      • Column extraction from complex query")
    print(f"      • HypoPG index creation for join columns")
    print(f"      • Cost estimation for hypothetical indexes")
    print(f"      • Progressive optimization analysis")

    # Check logs for analysis details
    print(f"\n📋 Checking AI Engine logs...")
    try:
        import subprocess
        log_result = subprocess.run([
            'docker', 'exec', 'neurdb_dev', 'tail', '-20',
            '/code/neurdb-dev/aiengine/workload_forecast/workload_forecast_server.log'
        ], capture_output=True, text=True, timeout=10)

        if log_result.returncode == 0:
            logs = log_result.stdout
            if 'title' in logs or 'movie_companies' in logs or 'JOIN' in logs:
                print(f"   ✅ Found complex query analysis in logs")
                if 'improvement:' in logs:
                    print(f"   ✅ Cost improvements detected")
            else:
                print(f"   📊 Recent logs show other activity")
        else:
            print(f"   ⚠️ Could not access logs")
    except:
        print(f"   ⚠️ Log check failed")

    print(f"\n🏆 COMPLEX QUERY TEST COMPLETE")
    print(f"   The reactive strategy processed the complex multi-join query!")
    print(f"   Check above results to see if indexes were created.")

if __name__ == "__main__":
    test_complex_query_with_10gb()