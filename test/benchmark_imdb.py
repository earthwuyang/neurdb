#!/usr/bin/env python3
"""
Benchmark script for NeurDB query optimizer.
Compares two optimizer methods: default vs molqo
"""

import argparse
import subprocess
import time
import random
import csv
import sys
import statistics
from datetime import datetime
from typing import List, Tuple, Dict
import os


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Benchmark NeurDB query optimizer against default and molqo methods"
    )
    parser.add_argument(
        "--num_queries",
        type=int,
        default=100,
        help="Number of queries to sample (default: 100)"
    )
    parser.add_argument(
        "--query_file",
        type=str,
        default="/Volumes/data/DB/pg_mem_pred/query_generation/generated_workloads/imdb_ori/workload_100k_s1_group_order_by_more_complex.sql",
        help="Path to SQL query file"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV file (default: benchmark_results_TIMESTAMP.csv)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="PostgreSQL host"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=15432,
        help="PostgreSQL port"
    )
    parser.add_argument(
        "--user",
        type=str,
        default="neurdb",
        help="PostgreSQL user"
    )
    parser.add_argument(
        "--database",
        type=str,
        default="imdb_ori",
        help="PostgreSQL database"
    )
    parser.add_argument(
        "--molqo_url",
        type=str,
        default="http://localhost:8666/optimize",
        help="MoLQO server URL"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Query timeout in seconds (default: 300)"
    )
    return parser.parse_args()


def read_queries(query_file: str) -> List[str]:
    """Read queries from SQL file."""
    try:
        with open(query_file, 'r') as f:
            queries = [line.strip() for line in f if line.strip()]
        print(f"Loaded {len(queries)} queries from {query_file}")
        return queries
    except FileNotFoundError:
        print(f"Error: Query file not found: {query_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading query file: {e}", file=sys.stderr)
        sys.exit(1)


def sample_queries(queries: List[str], num_queries: int) -> List[str]:
    """Randomly sample queries."""
    if num_queries > len(queries):
        print(f"Warning: Requested {num_queries} queries but only {len(queries)} available")
        num_queries = len(queries)

    sampled = random.sample(queries, num_queries)
    print(f"Sampled {num_queries} queries")
    return sampled


def execute_query_psql(query: str, optimizer_settings: List[str], args) -> Tuple[float, bool, str]:
    """
    Execute a single query using psql with given optimizer settings.

    Returns:
        - execution_time: float (seconds)
        - success: bool
        - error_msg: str (empty if success)
    """
    # Build psql command
    psql_cmd = [
        "psql",
        f"-h", args.host,
        f"-p", str(args.port),
        f"-U", args.user,
        f"-d", args.database,
        f"--tuples-only",
        f"--quiet",
        f"--command", f"\\timing on"
    ]

    # Build the full command with settings and query
    settings_sql = "; ".join(optimizer_settings) + "; "
    full_query = settings_sql + query

    try:
        start_time = time.time()
        result = subprocess.run(
            psql_cmd + ["--command", full_query],
            capture_output=True,
            text=True,
            timeout=args.timeout,
            env={**os.environ, "PGPASSWORD": ""}  # Add password if needed
        )
        end_time = time.time()

        execution_time = end_time - start_time

        if result.returncode == 0:
            return execution_time, True, ""
        else:
            error_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
            return execution_time, False, error_msg

    except subprocess.TimeoutExpired:
        return args.timeout, False, f"Query timed out after {args.timeout} seconds"
    except Exception as e:
        return 0.0, False, str(e)


def run_benchmark(queries: List[str], args) -> List[Dict]:
    """Run benchmark for all queries with both optimizer methods."""
    results = []

    print(f"\nStarting benchmark with {len(queries)} queries")
    print("=" * 80)

    for idx, query in enumerate(queries, 1):
        print(f"\nQuery {idx}/{len(queries)}:")
        print(f"{'='*80}")

        # Test with default optimizer
        print("Testing DEFAULT optimizer...")
        default_settings = [
            "set enable_molqo=off"
        ]
        default_time, default_success, default_error = execute_query_psql(
            query, default_settings, args
        )

        if default_success:
            print(f"  ✓ Success: {default_time:.4f} seconds")
        else:
            print(f"  ✗ Failed: {default_error}")

        # Test with molqo optimizer
        print("Testing MOLQO optimizer...")
        molqo_settings = [
            "set enable_molqo=on",
            f"set molqo.server_url = '{args.molqo_url}'"
        ]
        molqo_time, molqo_success, molqo_error = execute_query_psql(
            query, molqo_settings, args
        )

        if molqo_success:
            print(f"  ✓ Success: {molqo_time:.4f} seconds")
        else:
            print(f"  ✗ Failed: {molqo_error}")

        # Calculate speedup
        if default_success and molqo_success and default_time > 0:
            speedup = default_time / molqo_time
            improvement = ((default_time - molqo_time) / default_time) * 100
        else:
            speedup = None
            improvement = None

        # Store results
        result = {
            'query_id': idx,
            'query': query[:200] + "..." if len(query) > 200 else query,
            'default_time': default_time if default_success else None,
            'default_success': default_success,
            'default_error': default_error if not default_success else "",
            'molqo_time': molqo_time if molqo_success else None,
            'molqo_success': molqo_success,
            'molqo_error': molqo_error if not molqo_success else "",
            'speedup': speedup,
            'improvement_pct': improvement
        }
        results.append(result)

    return results


def print_summary(results: List[Dict]):
    """Print benchmark summary statistics."""
    print("\n" + "=" * 80)
    print("BENCHMARK SUMMARY")
    print("=" * 80)

    successful_defaults = [r for r in results if r['default_success']]
    successful_molqo = [r for r in results if r['molqo_success']]
    successful_both = [r for r in results if r['default_success'] and r['molqo_success']]

    print(f"\nTotal queries: {len(results)}")
    print(f"Default optimizer successful: {len(successful_defaults)}")
    print(f"MoLQO optimizer successful: {len(successful_molqo)}")
    print(f"Both successful (comparable): {len(successful_both)}")

    if successful_both:
        default_times = [r['default_time'] for r in successful_both]
        molqo_times = [r['molqo_time'] for r in successful_both]
        speedups = [r['speedup'] for r in successful_both if r['speedup'] is not None]
        improvements = [r['improvement_pct'] for r in successful_both if r['improvement_pct'] is not None]

        print(f"\nPerformance comparison (on successful queries):")
        print(f"  Default optimizer:")
        print(f"    Makespan (total time): {sum(default_times):.4f}s")
        print(f"    Avg latency: {sum(default_times)/len(default_times):.4f}s")
        print(f"    P95 latency: {statistics.quantiles(default_times, n=100)[94]:.4f}s")
        print(f"    Min time: {min(default_times):.4f}s")
        print(f"    Max time: {max(default_times):.4f}s")

        print(f"  MoLQO optimizer:")
        print(f"    Makespan (total time): {sum(molqo_times):.4f}s")
        print(f"    Avg latency: {sum(molqo_times)/len(molqo_times):.4f}s")
        print(f"    P95 latency: {statistics.quantiles(molqo_times, n=100)[94]:.4f}s")
        print(f"    Min time: {min(molqo_times):.4f}s")
        print(f"    Max time: {max(molqo_times):.4f}s")

        if speedups:
            avg_speedup = sum(speedups) / len(speedups)
            avg_improvement = sum(improvements) / len(improvements)
            print(f"\n  Speedup:")
            print(f"    Average speedup: {avg_speedup:.2f}x")
            print(f"    Average improvement: {avg_improvement:.2f}%")
            print(f"    Min speedup: {min(speedups):.2f}x")
            print(f"    Max speedup: {max(speedups):.2f}x")

            # Count improvements vs regressions
            improvements_count = sum(1 for i in improvements if i > 0)
            regressions_count = sum(1 for i in improvements if i < 0)
            neutral_count = sum(1 for i in improvements if i == 0)

            print(f"\n  Query performance distribution:")
            print(f"    Improved: {improvements_count} ({improvements_count/len(improvements)*100:.1f}%)")
            print(f"    Regressed: {regressions_count} ({regressions_count/len(improvements)*100:.1f}%)")
            print(f"    Neutral: {neutral_count} ({neutral_count/len(improvements)*100:.1f}%)")


def save_results(results: List[Dict], output_file: str):
    """Save results to CSV file."""
    try:
        with open(output_file, 'w', newline='') as csvfile:
            if not results:
                print("No results to save")
                return

            fieldnames = results[0].keys()
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()
            for result in results:
                writer.writerow(result)

        print(f"\nResults saved to: {output_file}")
    except Exception as e:
        print(f"Error saving results: {e}", file=sys.stderr)


def main():
    """Main benchmark function."""
    args = parse_args()

    # Set output filename if not specified
    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"benchmark_results_{timestamp}.csv"

    # Read queries
    print("Reading queries...")
    all_queries = read_queries(args.query_file)

    # Sample queries
    sampled_queries = sample_queries(all_queries, args.num_queries)

    # Run benchmark
    results = run_benchmark(sampled_queries, args)

    # Print summary
    print_summary(results)

    # Save results
    save_results(results, args.output)


if __name__ == "__main__":
    main()
