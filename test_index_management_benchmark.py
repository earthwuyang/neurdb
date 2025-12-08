#!/usr/bin/env python3
"""
NeurDB Automatic Index Management Benchmark

Compares query performance with and without automatic index management
using realistic IMDb workload patterns.
"""

import psycopg2
import time
import random
import statistics
import json
import argparse
from datetime import datetime
import sys
import os

# Test queries based on typical IMDb access patterns
TEST_QUERIES = [
    # Title searches
    {
        "name": "title_by_year",
        "pattern": "SELECT title, production_year FROM title WHERE production_year = {}",
        "params": [2018, 2019, 2020, 2021, 2022],
        "would_benefit_from": "title_production_year_idx"
    },
    {
        "name": "title_by_keyword",
        "pattern": "SELECT title FROM title WHERE title ILIKE '%{}%' LIMIT 20",
        "params": ["Star", "Love", "War", "Action", "Comedy"],
        "would_benefit_from": "test_title_idx"
    },
    # Cast info queries
    {
        "name": "cast_by_person",
        "pattern": "SELECT COUNT(*) FROM cast_info WHERE person_id = {}",
        "params": list(range(100, 500, 50)),
        "would_benefit_from": "person_id_cast_info"
    },
    # Movie info queries
    {
        "name": "movie_info_by_type",
        "pattern": "SELECT COUNT(*) FROM movie_info WHERE info_type_id = {}",
        "params": [1, 2, 3, 4, 5, 8, 10],
        "would_benefit_from": "info_type_id_movie_info"
    },
    # Complex joins
    {
        "name": "title_with_cast",
        "pattern": """SELECT t.title, COUNT(ci.id) as cast_count
                     FROM title t
                     JOIN cast_info ci ON t.id = ci.movie_id
                     WHERE t.production_year BETWEEN {} AND {}
                     GROUP BY t.title
                     LIMIT 20""",
        "params": [(2000, 2010), (2010, 2020), (1990, 2000)],
        "would_benefit_from": "movie_id_cast_info"
    }
]

class IndexManagementBenchmark:
    """Benchmarks NeurDB with and without automatic index management"""

    def __init__(self, db_config, iterations=100, verbose=False):
        self.db_config = db_config
        self.iterations = iterations
        self.verbose = verbose
        self.conn = None

    def connect(self):
        """Connect to database"""
        self.conn = psycopg2.connect(**self.db_config)
        print("✅ Connected to NeurDB")

    def disconnect(self):
        """Disconnect from database"""
        if self.conn:
            self.conn.close()
            print("🔌 Disconnected from NeurDB")

    def set_auto_index(self, enabled):
        """Enable or disable automatic index management"""
        with self.conn.cursor() as cursor:
            value = 'on' if enabled else 'off'
            cursor.execute(f"SET nr_enable_auto_index_creation = {value}")
            self.conn.commit()
            status = "ENABLED" if enabled else "DISABLED"
            print(f"🔧 Automatic index management: {status}")

    def get_index_stats(self):
        """Get current index statistics"""
        with self.conn.cursor() as cursor:
            # Total indexes
            cursor.execute("""
                SELECT COUNT(*), SUM(pg_relation_size(indexrelid)) / (1024*1024)
                FROM pg_stat_user_indexes
            """)
            result = cursor.fetchone()

            # Auto-created indexes
            cursor.execute("""
                SELECT indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
                FROM pg_stat_user_indexes
                WHERE indexrelname LIKE 'idx_auto_%'
                ORDER BY pg_relation_size(indexrelid) DESC
            """)
            auto_indexes = cursor.fetchall()

            return {
                'total_indexes': result[0],
                'total_size_mb': float(result[1]) if result[1] else 0,
                'auto_indexes': auto_indexes
            }

    def clear_auto_indexes(self):
        """Drop all auto-created indexes"""
        with self.conn.cursor() as cursor:
            cursor.execute("""
                SELECT indexrelname
                FROM pg_stat_user_indexes
                WHERE indexrelname LIKE 'idx_auto_%'
            """)
            indexes = cursor.fetchall()

            for (index_name,) in indexes:
                cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
                print(f"   Dropped {index_name}")

            if indexes:
                self.conn.commit()
                print(f"🗑️  Dropped {len(indexes)} auto-created indexes")

    def run_query(self, query):
        """Execute a query and return execution time"""
        with self.conn.cursor() as cursor:
            start_time = time.perf_counter()
            cursor.execute(query)
            cursor.fetchall()
            end_time = time.perf_counter()
            return (end_time - start_time) * 1000  # Convert to milliseconds

    def run_benchmark(self, scenario_name):
        """Run benchmark for a specific scenario"""
        print(f"\n📊 Running {scenario_name} benchmark...")

        results = {}
        total_queries = 0

        for query_def in TEST_QUERIES:
            query_name = query_def['name']
            pattern = query_def['pattern']
            params = query_def['params']

            if self.verbose:
                print(f"\n   Testing {query_name}...")

            execution_times = []

            for i in range(self.iterations):
                # Select random parameter
                param = random.choice(params)
                if isinstance(param, tuple):
                    query = pattern.format(*param)
                else:
                    query = pattern.format(param)

                try:
                    exec_time = self.run_query(query)
                    execution_times.append(exec_time)
                    total_queries += 1

                    if self.verbose and i % 10 == 0:
                        print(f"     Query {i}: {exec_time:.2f}ms", end='\r')

                except Exception as e:
                    print(f"\n   Error on {query_name}: {e}")
                    continue

            # Calculate statistics
            if execution_times:
                results[query_name] = {
                    'mean': statistics.mean(execution_times),
                    'median': statistics.median(execution_times),
                    'stdev': statistics.stdev(execution_times) if len(execution_times) > 1 else 0,
                    'min': min(execution_times),
                    'max': max(execution_times),
                    'count': len(execution_times)
                }

                if self.verbose:
                    print(f"     {query_name}: {results[query_name]['mean']:.2f}ms avg")

        # Calculate overall metrics
        all_times = [r['mean'] for r in results.values()]
        overall = {
            'queries_tested': len(results),
            'total_executions': total_queries,
            'overall_mean': statistics.mean(all_times),
            'overall_median': statistics.median(all_times),
            'query_results': results
        }

        return overall

    def run_comparison(self):
        """Run comparison between auto-index on and off"""

        print("=" * 70)
        print("NEURDB AUTOMATIC INDEX MANAGEMENT BENCHMARK")
        print("=" * 70)

        results = {}

        # === SCENARIO 1: No Automatic Index Management ===
        print("\n" + "=" * 70)
        print("SCENARIO 1: No Automatic Index Management")
        print("=" * 70)

        # Clear any existing auto indexes
        print("\n🗑️  Clearing any existing auto-indexes...")
        self.clear_auto_indexes()

        # Disable auto index management
        self.set_auto_index(False)

        # Record baseline stats
        stats_before = self.get_index_stats()
        print(f"\n📊 Baseline:")
        print(f"   Total indexes: {stats_before['total_indexes']}")
        print(f"   Total size: {stats_before['total_size_mb']:.2f} MB")

        # Run benchmark
        results['no_auto_index'] = self.run_benchmark("No Auto Index")
        results['no_auto_index']['index_stats'] = stats_before

        # === SCENARIO 2: With Automatic Index Management ===
        print("\n" + "=" * 70)
        print("SCENARIO 2: With Automatic Index Management")
        print("=" * 70)

        # Enable auto index management
        self.set_auto_index(True)

        # Record initial stats
        stats_before = self.get_index_stats()
        print(f"\n📊 Before adaptation:")
        print(f"   Total indexes: {stats_before['total_indexes']}")
        print(f"   Total size: {stats_before['total_size_mb']:.2f} MB")

        # Run benchmark (indexes will be created automatically as needed)
        print("\n🔍 Running workload to trigger index creation...")
        results['with_auto_index'] = self.run_benchmark("With Auto Index")

        # Record final stats
        stats_after = self.get_index_stats()
        print(f"\n📊 After adaptation:")
        print(f"   Total indexes: {stats_after['total_indexes']}")
        print(f"   Total size: {stats_after['total_size_mb']:.2f} MB")
        print(f"   New auto-indexes: {stats_after['total_indexes'] - stats_before['total_indexes']}")

        if stats_after['auto_indexes']:
            print(f"\n🆕 New auto-created indexes:")
            for name, scans, size in stats_after['auto_indexes']:
                print(f"   - {name} ({size}, {scans} scans)")

        results['with_auto_index']['index_stats_before'] = stats_before
        results['with_auto_index']['index_stats_after'] = stats_after

        # === COMPARISON ===
        print("\n" + "=" * 70)
        print("COMPARISON RESULTS")
        print("=" * 70)

        self.print_comparison(results)

        return results

    def print_comparison(self, results):
        """Print comparison between scenarios"""

        no_auto = results['no_auto_index']
        with_auto = results['with_auto_index']

        print(f"\n📈 Performance Summary:")
        print(f"{'Query Type':<25} {'No Auto (ms)':<15} {'With Auto (ms)':<15} {'Improvement':<15}")
        print("-" * 70)

        improvements = []
        for query_name in no_auto['query_results']:
            if query_name in with_auto['query_results']:
                no_auto_time = no_auto['query_results'][query_name]['mean']
                with_auto_time = with_auto['query_results'][query_name]['mean']
                improvement = ((no_auto_time - with_auto_time) / no_auto_time * 100)
                improvements.append(improvement)

                print(f"{query_name:<25} {no_auto_time:<15.2f} {with_auto_time:<15.2f} {improvement:<15.1f}%")

        # Overall statistics
        avg_improvement = statistics.mean(improvements)
        print("-" * 70)
        print(f"{'Average':<25} {'':<15} {'':<15} {avg_improvement:<15.1f}%")
        print(f"{'Median':<25} {'':<15} {'':<15} {statistics.median(improvements):<15.1f}%")

        # Storage overhead
        storage_before = with_auto['index_stats_before']['total_size_mb']
        storage_after = with_auto['index_stats_after']['total_size_mb']
        storage_overhead = storage_after - storage_before
        overhead_percent = (storage_overhead / storage_before * 100) if storage_before > 0 else 0

        print(f"\n💾 Storage Impact:")
        print(f"   Before: {storage_before:.2f} MB")
        print(f"   After: {storage_after:.2f} MB")
        print(f"   Overhead: {storage_overhead:.2f} MB ({overhead_percent:.1f}%)")

        # Recommendation
        print(f"\n💡 Recommendation:")
        if avg_improvement > 20:
            recommendation = "✅ Strongly recommend enabling automatic index management"
        elif avg_improvement > 10:
            recommendation = "✅ Recommend enabling automatic index management"
        elif avg_improvement > 5:
            recommendation = "🤔 Consider enabling automatic index management"
        else:
            recommendation = "⚠️ Automatic index management shows minimal benefit"

        print(f"{recommendation}")
        print(f"   ({storage_overhead:.2f} MB storage overhead for {avg_improvement:.1f}% performance gain)")


def main():
    parser = argparse.ArgumentParser(description='Benchmark NeurDB automatic index management')
    parser.add_argument('--host', default='localhost', help='Database host')
    parser.add_argument('--port', default=5432, type=int, help='Database port')
    parser.add_argument('--database', default='imdb_ori', help='Database name')
    parser.add_argument('--user', default='neurdb', help='Database user')
    parser.add_argument('--password', default='', help='Database password')
    parser.add_argument('--iterations', default=50, type=int, help='Number of query iterations')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    args = parser.parse_args()

    db_config = {
        'host': args.host,
        'port': args.port,
        'database': args.database,
        'user': args.user
    }
    if args.password:
        db_config['password'] = args.password

    benchmark = IndexManagementBenchmark(db_config, iterations=args.iterations, verbose=args.verbose)

    try:
        benchmark.connect()
        results = benchmark.run_comparison()

        # Save results to JSON
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = "/Volumes/data/DB/neurdb_dev/output/index_benchmark"
        os.makedirs(output_dir, exist_ok=True)

        results_file = os.path.join(output_dir, f"results_{timestamp}.json")
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)

        print(f"\n✅ Results saved to: {results_file}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        benchmark.disconnect()


if __name__ == "__main__":
    main()
