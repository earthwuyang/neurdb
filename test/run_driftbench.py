#!/usr/bin/env python3
"""
Comprehensive DriftBench Runner for NeurDB Benchmarking

This script provides a unified interface to run DriftBench benchmarks
against different databases with various workload configurations.

Usage:
    python run_driftbench.py --database tpch_sf1 --query_num 1000
    python run_driftbench.py --database tpch_sf10 --query_num 1000
    python run_driftbench.py --database imdb_test --quick
"""

import argparse
import json
import os
import sys
import subprocess
import tempfile
import time
import shutil
import re
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

import pandas as pd
import psycopg2

# Core data structures
@dataclass
class Metrics:
    avg_s: float
    median_s: float
    p95_s: float
    successful_queries: int
    total_queries: int
    failed_queries: int
    run_start_utc: str
    run_end_utc: str


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run DriftBench benchmark against specified database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_driftbench.py --database tpch_sf1 --query_num 1000
  python run_driftbench.py --database tpch_sf10 --query_num 1000
  python run_driftbench.py --database imdb_test --quick
  python run_driftbench.py --database imdb_test --query_num 500 --output_dir ./results
        """
    )

    # Database configuration
    parser.add_argument(
        "--database",
        type=str,
        required=True,
        choices=["tpch_sf1", "tpch_sf10", "imdb_test"],
        help="Database to benchmark against (tpch_sf1, tpch_sf10, or imdb_test)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="PostgreSQL host (default: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=15432,
        help="PostgreSQL port (default: 15432)"
    )
    parser.add_argument(
        "--user",
        type=str,
        default="neurdb",
        help="PostgreSQL user (default: neurdb)"
    )
    parser.add_argument(
        "--password",
        type=str,
        default="",
        help="PostgreSQL password (default: empty)"
    )

    # Benchmark configuration
    parser.add_argument(
        "--query_num",
        type=int,
        default=100,
        help="Total number of queries to execute (default: 100)"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run in quick mode (ignores --query_num, uses 100 queries)"
    )
    
    # Output and debugging
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Output directory (default: auto-generated)"
    )
    parser.add_argument(
        "--driftbench_path",
        type=str,
        default="/Volumes/data/DB/DriftBench",
        help="Path to DriftBench repository"
    )
    parser.add_argument(
        "--nrim_debug",
        action="store_true",
        default=True,
        help="Enable NRIM debug mode"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Query timeout in seconds (0 = no timeout)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for workload shuffling"
    )
    parser.add_argument(
        "--no_reset_nrim",
        action="store_false",
        dest="reset_nrim",
        default=True,
        help="Do not reset NRIM bookkeeping tables (default: reset)"
    )

    return parser.parse_args()


def verify_database_connection(args):
    """Verify that we can connect to the specified database."""
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database
        )

        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()

        conn.close()
        return True
    except Exception as e:
        print(f"❌ Cannot connect to database {args.database}: {e}")
        return False


def install_required_extensions(args):
    """Install required PostgreSQL extensions for NRIM functionality."""
    print(f"🔧 Installing required extensions for database: {args.database}")

    try:
        import psycopg2

        conn = psycopg2.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database
        )

        with conn.cursor() as cur:
            # Install hypopg extension for hypothetical index evaluation
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS hypopg;")
                print("✅ HypoPG extension installed successfully")
            except Exception as e:
                print(f"⚠️  Could not install HypoPG extension: {e}")

            # Install NRIM extension for reactive index management
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS nr_index_management;")
                print("✅ NRIM extension installed successfully")
            except Exception as e:
                print(f"⚠️  Could not install NRIM extension: {e}")

            # Configure index storage budget for reactive indexing (0 = 50% of database size)
            try:
                cur.execute(f"ALTER DATABASE {args.database} SET nr_max_index_storage_mb = 0;")
                print("✅ Index storage budget configured (nr_max_index_storage_mb = 0, 50% of database size)")
            except Exception as e:
                print(f"⚠️  Could not configure index storage budget: {e}")

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        print(f"❌ Failed to install extensions: {e}")
        return False


def update_driftbench_config(driftbench_path, args):
    """Update DriftBench PG_info.json with database configuration."""
    config_path = os.path.join(driftbench_path, "data", "PG_info.json")

    config = {
        "dbname": args.database,
        "user": args.user,
        "password": args.password,
        "host": args.host,
        "port": args.port
    }

    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

    print(f"✅ Updated DriftBench config for database: {args.database}")


def run_psql(args, sql, timeout=None):
    """Execute a psql command with the provided database connection args."""
    cmd = [
        "psql",
        f"-h{args.host}",
        f"-p{args.port}",
        f"-U{args.user}",
        f"-d{args.database}",
        "-vON_ERROR_STOP=1",
        "-c", sql
    ]

    if args.password:
        cmd.insert(-2, f"-W{args.password}")
    if timeout:
        cmd.insert(-2, f"--set=statement_timeout={timeout}s")

    env = os.environ.copy()
    env["PGPASSWORD"] = args.password

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env
    )

def fetch_psql_lines(args, sql):
    """Execute psql command and return output lines."""
    result = run_psql(args, sql)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr}")
    return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

def drop_reactive_indexes(args):
    """Drop all idx_reactive_* indexes with progress indicator."""
    indexes_result = fetch_psql_lines(
        args,
        "SELECT indexname FROM pg_indexes WHERE indexname LIKE 'idx_reactive_%' ORDER BY indexname"
    )

    if not indexes_result:
        print("No idx_reactive_* indexes found to drop.")
        return

    reactive_indexes = [idx for idx in indexes_result if idx.startswith('idx_reactive_')]

    if not reactive_indexes:
        print("No idx_reactive_* indexes found to drop.")
        return

    print(f"Dropping {len(reactive_indexes)} idx_reactive_* indexes (CONCURRENTLY)...")
    for idx in reactive_indexes:
        try:
            result = run_psql(args, f"DROP INDEX CONCURRENTLY IF EXISTS {idx}")
            print(f"  Dropped {idx}")
        except Exception as e:
            print(f"  Failed to drop {idx}: {e}")

def reset_nrim_tables(args):
    """Reset NRIM bookkeeping tables."""
    try:
        result = fetch_psql_lines(
            args,
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'nrim'"
        )
        if not result:
            print("ℹ️  NRIM extension not found - NRIM table reset skipped")
            return

        run_psql(
            args,
            "TRUNCATE nrim.nrim_work_queue, "
            "nrim.nrim_query_index_attribution, "
            "nrim.nrim_index_registry, "
            "nrim.nrim_query_facts "
            "RESTART IDENTITY;"
        )
        print("✅ NRIM tables reset successfully")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Could not reset NRIM tables (skipping): {e}", file=sys.stderr)

def configure_mode(args, *, auto_create: bool) -> None:
    """Configure NRIM mode for the database."""
    try:
        # Database-level configuration for background worker
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_index_management_strategy = 'reactive';")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_reactive.enable = on;")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_enable_auto_index_creation = {'on' if auto_create else 'off'};")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_max_index_storage_mb = 0;")  # 50% database size storage
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_reactive.use_concurrently = on;")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_reactive.debug = {'on' if args.nrim_debug else 'off'};")

        # Reload configuration to apply changes
        run_psql(args, "SELECT pg_reload_conf();")
        time.sleep(1.0)

        print(f"✅ NeurDB reactive mode configured (auto-create: {'on' if auto_create else 'off'}, 50% database size storage)")

    except subprocess.CalledProcessError as e:
        print(f"⚠️  Could not configure NeurDB mode: {e}", file=sys.stderr)

def run_workload_with_psycopg2(args, workload_df, csv_path, application_name):
    """Execute workload queries using psycopg2 and collect metrics."""
    print(f"Executing {len(workload_df)} queries ({application_name})...")

    timestamps = []
    latencies = []
    errors = []

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        connect_timeout=10
    )
    conn.autocommit = False

    run_start_utc = datetime.now(timezone.utc).isoformat()

    for _, row in workload_df.iterrows():
        query = row['query']
        start_ts = time.time()

        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = %s", (args.timeout if args.timeout > 0 else 0,))
                cur.execute("SET application_name = %s", (application_name,))
                cur.execute(query)

                # Consume all results
                cur.fetchall()

        except Exception as e:
            errors.append(str(e))
            latencies.append(float('nan'))
        else:
            latencies.append(time.time() - start_ts)
            errors.append(None)

        timestamps.append(start_ts)

    conn.commit()
    conn.close()

    run_end_utc = datetime.now(timezone.utc).isoformat()

    # Calculate metrics
    valid_latencies = [l for l in latencies if not pd.isna(l)]
    successful = len(valid_latencies)
    failed = len(latencies) - successful

    if successful > 0:
        avg_s = sum(valid_latencies) / successful
        sorted_latencies = sorted(valid_latencies)
        median_s = sorted_latencies[successful // 2]
        p95_s = sorted_latencies[int(successful * 0.95)]
    else:
        avg_s = median_s = p95_s = float('nan')

    # Save detailed results
    results_df = pd.DataFrame({
        'timestamp_utc': timestamps,
        'latency_s': latencies,
        'error': errors
    })
    results_df.to_csv(csv_path, index=False)

    return Metrics(
        avg_s=avg_s,
        median_s=median_s,
        p95_s=p95_s,
        successful_queries=successful,
        total_queries=len(workload_df),
        failed_queries=failed,
        run_start_utc=run_start_utc,
        run_end_utc=run_end_utc
    )

def run_driftbench_time_series(args, *, quick: bool, query_num: int, log_path: str) -> None:
    """Run the DriftBench time series execution script."""
    # Remove previous results so we don't accidentally parse stale data.
    results_path = os.path.join(
        args.driftbench_path,
        "output",
        "neurdb_benchmark",
        "evaluation",
        "time_series_execution_results.json",
    )
    try:
        os.remove(results_path)
    except FileNotFoundError:
        pass

    # Determine workload type based on database
    workload_type = "tpch" if args.database.startswith("tpch") else "imdb"

    cmd = [sys.executable, "-m", "test.test_neurdb_time_series_execution"]
    cmd.append("--workload-type")
    cmd.append(workload_type)

    if quick:
        cmd.append("--quick")
    else:
        cmd.extend(["--query-num", str(query_num)])

    with open(log_path, "w", encoding="utf-8") as logf:
        res = subprocess.run(
            cmd,
            cwd=args.driftbench_path,
            check=False,
            text=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": f"{args.driftbench_path}{os.pathsep}{os.environ.get('PYTHONPATH','')}"},
        )
    if res.returncode != 0:
        print(f"Warning: DriftBench exited with code {res.returncode}; logs at {log_path}", file=sys.stderr)

def run_benchmark_imdb(args):
    """Run benchmark against IMDB database using DriftBench time series execution."""
    print(f"\n{'='*60}")
    print("RUNNING IMDB BENCHMARK")
    print(f"{'='*60}")

    # Load IMDb mixed workload
    workload_path = "/Volumes/data/DB/DriftBench/output/neurdb_benchmark/workloads/imdb_multi_criteria_drift.csv"

    if not os.path.exists(workload_path):
        print(f"❌ DriftBench workload not found at {workload_path}")
        print("   Please generate the workload first using DriftBench")
        sys.exit(1)

    workload_df = pd.read_csv(workload_path)
    if len(workload_df) > args.query_num:
        workload_df = workload_df.sample(n=args.query_num, random_state=args.seed)

    workload_path = os.path.join(args.output_dir, "mixed_workload.csv")
    workload_df.to_csv(workload_path, index=False)
    print(f"✅ Loaded {len(workload_df)} IMDb queries from mixed workload")

    # Clean slate before baseline run
    drop_reactive_indexes(args)
    if args.reset_nrim:
        reset_nrim_tables(args)

    # Phase 1: reactive enabled, auto-create disabled
    print("\n=== Phase 1: reactive ON, auto-create OFF ===")
    configure_mode(args, auto_create=False)
    log_path = os.path.join(args.output_dir, "noauto_driftbench.log")
    run_driftbench_time_series(args, quick=args.quick, query_num=args.query_num, log_path=log_path)
    noauto_metrics = load_driftbench_metrics(args.driftbench_path)

    # Clean slate before auto run
    drop_reactive_indexes(args)
    if args.reset_nrim:
        reset_nrim_tables(args)

    # Phase 2: reactive enabled, auto-create enabled
    print("\n=== Phase 2: reactive ON, auto-create ON ===")
    configure_mode(args, auto_create=True)
    log_path = os.path.join(args.output_dir, "auto_driftbench.log")
    run_driftbench_time_series(args, quick=args.quick, query_num=args.query_num, log_path=log_path)
    auto_metrics = load_driftbench_metrics(args.driftbench_path)

    # Generate comparison results
    comparison = {
        "no_auto": {
            "avg_execution_time": noauto_metrics["avg_s"],
            "median_execution_time": noauto_metrics["median_s"],
            "p95_execution_time": noauto_metrics["p95_s"],
            "successful_queries": noauto_metrics["successful_queries"],
            "total_queries": noauto_metrics["total_queries"],
            "failed_queries": noauto_metrics["failed_queries"],
            "run_start_utc": noauto_metrics["run_start_utc"],
            "run_end_utc": noauto_metrics["run_end_utc"],
        },
        "auto": {
            "avg_execution_time": auto_metrics["avg_s"],
            "median_execution_time": auto_metrics["median_s"],
            "p95_execution_time": auto_metrics["p95_s"],
            "successful_queries": auto_metrics["successful_queries"],
            "total_queries": auto_metrics["total_queries"],
            "failed_queries": auto_metrics["failed_queries"],
            "run_start_utc": auto_metrics["run_start_utc"],
            "run_end_utc": auto_metrics["run_end_utc"],
        },
    }

    comparison_path = os.path.join(args.output_dir, "comparison.json")
    with open(comparison_path, "w") as f:
        json.dump(comparison, f, indent=2)

    print(f"\n{'='*60}")
    print(f"DRIFTBENCH SUMMARY (IMDB)")
    print(f"{'='*60}")
    print(f"Mode: no-auto  avg={comparison['no_auto']['avg_execution_time']:.4f}s  median={comparison['no_auto']['median_execution_time']:.4f}s  p95={comparison['no_auto']['p95_execution_time']:.4f}s  ok={comparison['no_auto']['successful_queries']}/{comparison['no_auto']['total_queries']}")
    print(f"Mode: auto     avg={comparison['auto']['avg_execution_time']:.4f}s  median={comparison['auto']['median_execution_time']:.4f}s  p95={comparison['auto']['p95_execution_time']:.4f}s  ok={comparison['auto']['successful_queries']}/{comparison['auto']['total_queries']}")

    # Calculate speedup
    if comparison['no_auto']['avg_execution_time'] > 0:
        avg_speedup = comparison['no_auto']['avg_execution_time'] / comparison['auto']['avg_execution_time']
        median_speedup = comparison['no_auto']['median_execution_time'] / comparison['auto']['median_execution_time']
        p95_speedup = comparison['no_auto']['p95_execution_time'] / comparison['auto']['p95_execution_time']

        print(f"\nSpeedup (no-auto / auto):")
        print(f"  avg:    {avg_speedup:.3f}x")
        print(f"  median: {median_speedup:.3f}x")
        print(f"  p95:    {p95_speedup:.3f}x")

    print("✅ IMDB benchmark completed successfully")
    return True


def load_mixed_workload(args):
    """Load TPC-H mixed workload."""
    workload_path = "/Volumes/data/DB/DriftBench/output/neurdb_benchmark/workloads/tpch_mixed_workload.csv"

    if not os.path.exists(workload_path):
        print(f"❌ DriftBench workload not found at {workload_path}")
        print("   Please generate the workload first using DriftBench")
        sys.exit(1)

    workload_df = pd.read_csv(workload_path)

    # TPC-H workload format: query,scenario - extract only the query column
    if 'query' in workload_df.columns:
        # Create new dataframe with only 'query' column for DriftBench compatibility
        workload_df = workload_df[['query']].copy()

        # Remove trailing semicolons from queries (PostgreSQL doesn't need them)
        workload_df['query'] = workload_df['query'].str.rstrip(';')

        # Validate queries after cleaning
        sample_queries = workload_df['query'].head(3).tolist()
        print(f"📝 Sample queries after cleaning:")
        for i, q in enumerate(sample_queries, 1):
            print(f"   {i}: {q[:80]}{'...' if len(q) > 80 else ''}")
    else:
        print(f"❌ TPC-H workload missing 'query' column. Found columns: {list(workload_df.columns)}")
        sys.exit(1)

    # Sample to requested size
    if len(workload_df) > args.query_num:
        workload_df = workload_df.sample(n=args.query_num, random_state=args.seed)

    print(f"✅ Loaded {len(workload_df)} TPC-H queries from mixed workload")
    return workload_df


def load_driftbench_metrics(driftbench_path) -> dict:
    """Load metrics from DriftBench time series execution results."""
    results_path = os.path.join(
        driftbench_path,
        "output",
        "neurdb_benchmark",
        "evaluation",
        "time_series_execution_results.json",
    )
    if not os.path.isfile(results_path):
        raise FileNotFoundError(f"DriftBench results not found at {results_path}")
    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mixed = data.get("mixed_execution") or {}
    return {
        "avg_s": float(mixed.get("avg_execution_time", 0.0)),
        "median_s": float(mixed.get("median_execution_time", 0.0)),
        "p95_s": float(mixed.get("p95_execution_time", 0.0)),
        "successful_queries": int(mixed.get("successful_queries", 0)),
        "total_queries": int(mixed.get("total_queries", 0)),
        "failed_queries": int(mixed.get("failed_queries", 0)),
        "run_start_utc": datetime.now(timezone.utc).isoformat(),
        "run_end_utc": datetime.now(timezone.utc).isoformat(),
    }

def run_benchmark_tpch(args):
    """Run benchmark against TPC-H database (SF1 or SF10) using DriftBench time series execution."""
    print(f"\n{'='*60}")
    print(f"RUNNING TPC-H {args.database.upper()} BENCHMARK")
    print(f"{'='*60}")

    # Load TPC-H mixed workload
    workload_df = load_mixed_workload(args)
    workload_path = os.path.join(args.output_dir, "mixed_workload.csv")
    workload_df.to_csv(workload_path, index=False)

    # Clean slate before baseline run
    drop_reactive_indexes(args)
    if args.reset_nrim:
        reset_nrim_tables(args)

    # Phase 1: reactive enabled, auto-create disabled
    print("\n=== Phase 1: reactive ON, auto-create OFF ===")
    configure_mode(args, auto_create=False)
    log_path = os.path.join(args.output_dir, "noauto_driftbench.log")
    run_driftbench_time_series(args, quick=args.quick, query_num=args.query_num, log_path=log_path)
    noauto_metrics = load_driftbench_metrics(args.driftbench_path)

    # Clean slate before auto run
    drop_reactive_indexes(args)
    if args.reset_nrim:
        reset_nrim_tables(args)

    # Phase 2: reactive enabled, auto-create enabled
    print("\n=== Phase 2: reactive ON, auto-create ON ===")
    configure_mode(args, auto_create=True)
    log_path = os.path.join(args.output_dir, "auto_driftbench.log")
    run_driftbench_time_series(args, quick=args.quick, query_num=args.query_num, log_path=log_path)
    auto_metrics = load_driftbench_metrics(args.driftbench_path)

    # Generate comparison results
    comparison = {
        "no_auto": {
            "avg_execution_time": noauto_metrics["avg_s"],
            "median_execution_time": noauto_metrics["median_s"],
            "p95_execution_time": noauto_metrics["p95_s"],
            "successful_queries": noauto_metrics["successful_queries"],
            "total_queries": noauto_metrics["total_queries"],
            "failed_queries": noauto_metrics["failed_queries"],
            "run_start_utc": noauto_metrics["run_start_utc"],
            "run_end_utc": noauto_metrics["run_end_utc"],
        },
        "auto": {
            "avg_execution_time": auto_metrics["avg_s"],
            "median_execution_time": auto_metrics["median_s"],
            "p95_execution_time": auto_metrics["p95_s"],
            "successful_queries": auto_metrics["successful_queries"],
            "total_queries": auto_metrics["total_queries"],
            "failed_queries": auto_metrics["failed_queries"],
            "run_start_utc": auto_metrics["run_start_utc"],
            "run_end_utc": auto_metrics["run_end_utc"],
        },
    }

    comparison_path = os.path.join(args.output_dir, "comparison.json")
    with open(comparison_path, "w") as f:
        json.dump(comparison, f, indent=2)

    print(f"\n{'='*60}")
    print(f"DRIFTBENCH SUMMARY (TPC-H SF1)")
    print(f"{'='*60}")
    print(f"Mode: no-auto  avg={comparison['no_auto']['avg_execution_time']:.4f}s  median={comparison['no_auto']['median_execution_time']:.4f}s  p95={comparison['no_auto']['p95_execution_time']:.4f}s  ok={comparison['no_auto']['successful_queries']}/{comparison['no_auto']['total_queries']}")
    print(f"Mode: auto     avg={comparison['auto']['avg_execution_time']:.4f}s  median={comparison['auto']['median_execution_time']:.4f}s  p95={comparison['auto']['p95_execution_time']:.4f}s  ok={comparison['auto']['successful_queries']}/{comparison['auto']['total_queries']}")

    # Calculate speedup
    if comparison['no_auto']['avg_execution_time'] > 0:
        avg_speedup = comparison['no_auto']['avg_execution_time'] / comparison['auto']['avg_execution_time']
        median_speedup = comparison['no_auto']['median_execution_time'] / comparison['auto']['median_execution_time']
        p95_speedup = comparison['no_auto']['p95_execution_time'] / comparison['auto']['p95_execution_time']

        print(f"\nSpeedup (no-auto / auto):")
        print(f"  avg:    {avg_speedup:.3f}x")
        print(f"  median: {median_speedup:.3f}x")
        print(f"  p95:    {p95_speedup:.3f}x")

    print(f"✅ TPC-H {args.database.upper()} benchmark completed successfully")
    return True


def verify_prerequisites(driftbench_path):
    """Verify that DriftBench and required dependencies are available."""

    # Check DriftBench directory
    if not os.path.exists(driftbench_path):
        print(f"❌ DriftBench directory not found: {driftbench_path}")
        return False

    # Check key DriftBench files
    required_files = [
        os.path.join(driftbench_path, "test", "test_neurdb_time_series_execution.py"),
        os.path.join(driftbench_path, "data", "PG_info.json"),
    ]

    for file_path in required_files:
        if not os.path.exists(file_path):
            print(f"❌ Required DriftBench file not found: {file_path}")
            return False

    # Check Python dependencies
    required_packages = ['psycopg2', 'pandas', 'numpy', 'tqdm']
    missing_packages = []

    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)

    if missing_packages:
        print(f"❌ Missing required Python packages: {', '.join(missing_packages)}")
        print("Install with: pip install " + " ".join(missing_packages))
        return False

    print("✅ All prerequisites verified")
    return True


def main():
    """Main runner function."""
    args = parse_args()

    print(f"🚀 DriftBench Runner")
    print(f"Database: {args.database}")
    print(f"Query count: {args.query_num if not args.quick else 'quick mode'}")
    print(f"DriftBench path: {args.driftbench_path}")

    # Verify prerequisites
    if not verify_prerequisites(args.driftbench_path):
        sys.exit(1)

    # Verify database connection
    if not verify_database_connection(args):
        sys.exit(1)

    # Create output directory
    if not args.output_dir:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output_dir = f"output/driftbench_{args.database}_benchmark_{timestamp}"

    os.makedirs(args.output_dir, exist_ok=True)

    # Install required extensions
    if not install_required_extensions(args):
        print("⚠️  Extension installation failed, but continuing with benchmark...")
        print("   Note: Some NRIM features may not work without required extensions")

    # Update DriftBench configuration
    update_driftbench_config(args.driftbench_path, args)

    # Run appropriate benchmark
    success = False
    if args.database == "imdb_test":
        success = run_benchmark_imdb(args)
    elif args.database in ["tpch_sf1", "tpch_sf10"]:
        success = run_benchmark_tpch(args)
    else:
        print(f"❌ Unsupported database: {args.database}")
        sys.exit(1)

    if success:
        print(f"\n{'='*60}")
        print("🎉 BENCHMARK COMPLETED SUCCESSFULLY")
        print(f"{'='*60}")

        if args.output_dir:
            print(f"📁 Results saved to: {args.output_dir}")
        else:
            print("📁 Results saved to auto-generated output directory")

        print(f"\nTo analyze results:")
        if args.database == "imdb_test":
            print(f"  - Check the generated comparison.json for performance metrics")
        else:
            print(f"  - Check the generated comparison.json for performance metrics")

        print(f"  - Review the detailed CSV files for per-query analysis")
    else:
        print(f"\n❌ Benchmark failed")
        sys.exit(1)


if __name__ == "__main__":
    main()