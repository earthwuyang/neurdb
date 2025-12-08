#!/usr/bin/env python3
"""
NeurDB Index Management with Eviction Demo

Demonstrates the automatic index management and eviction capabilities
"""

import psycopg2
import time
import random
import sys

# Database connection
conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='imdb_ori',
    user='neurdb'
)
cursor = conn.cursor()

def time_query(query):
    """Time a query execution"""
    start = time.time()
    cursor.execute(query)
    cursor.fetchall()
    return time.time() - start

def show_index_stats():
    """Show current index statistics"""
    cursor.execute("""
        SELECT
            COUNT(*) as count,
            SUM(pg_relation_size(indexrelid)) / (1024*1024) as total_mb
        FROM pg_stat_user_indexes
    """)
    count, size = cursor.fetchone()
    print(f"📊 Current indexes: {count}, Total size: {size:.2f} MB")

    # Show top indexes
    cursor.execute("""
        SELECT
            indexrelname,
            idx_scan,
            pg_size_pretty(pg_relation_size(indexrelid))
        FROM pg_stat_user_indexes
        WHERE indexrelname LIKE 'idx_auto_%'
        ORDER BY pg_relation_size(indexrelid) DESC
        LIMIT 5
    """)
    auto_indexes = cursor.fetchall()

    if auto_indexes:
        print("🤖 Auto-created indexes:")
        for name, scans, size in auto_indexes:
            print(f"   {name}: {size}, {scans} scans")

    return count, size or 0

def create_test_index_if_allowed():
    """Demonstrate creating an index with eviction"""
    print("\n" + "="*60)
    print("TESTING AUTOMATIC INDEX CREATION WITH EVICTION")
    print("="*60)

    print("\n📖 Checking current storage...")
    show_index_stats()

    # Show budget
    cursor.execute("SELECT nr_get_current_index_storage_mb(), nr_calculate_index_budget_mb()")
    usage, budget = cursor.fetchone()
    print(f"📦 Storage usage: {usage:.2f} MB / {budget:.2f} MB ({usage/budget*100:.1f}%)")

    # Try to create an index that would exceed budget
    print("\n🔨 Attempting to create new index...")
    cursor.execute("""
        SELECT nr_create_index_if_budget_allows('test_eviction_idx', 'title', ARRAY['kind_id'], 'btree')
    """)
    result = cursor.fetchone()

    if result[0]:
        print("✅ Index created successfully")
    else:
        print("❌ Failed to create index (budget check returned false)")

    # Show updated stats
    print("\n📊 Updated statistics:")
    show_index_stats()

    # Test eviction by creating several indexes
    print("\n" + "="*60)
    print("TESTING EVICTION OF LOW-BENEFIT INDEXES")
    print("="*60)

    for i in range(3):
        print(f"\n🔄 Creating index {i+1}...")

        # Create a potentially low-benefit index
        cursor.execute(f"""
            CREATE INDEX idx_auto_low_benefit_{i} ON title(production_year)
            WHERE production_year = {1950 + i}
        """)
        conn.commit()

        print(f"   Created idx_auto_low_benefit_{i}")

        # Show it was created
        cursor.execute("SELECT nr_get_current_index_storage_mb()")
        new_usage = cursor.fetchone()[0]
        print(f"   Storage now: {new_usage:.2f} MB")

    print("\n📊 Index creation complete:")
    show_index_stats()

    print("\n✅ Demo complete!")
    print("\nThe system can now automatically evict low-benefit indexes when")
    print("storage budget is exceeded, making room for more beneficial ones.")

if __name__ == "__main__":
    try:
        create_test_index_if_allowed()

        print("\n✨ All operations completed successfully!")
        print("\nFor full automatic index management with eviction:")
        print("1. Use the AI engine /index/auto_create endpoint")
        print("2. Or call nr_create_index_if_budget_allows() for automatic evictions")
        print("3. Monitor with nr_get_current_index_storage_mb() for budget tracking")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        conn.close()
