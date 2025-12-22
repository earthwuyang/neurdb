#!/usr/bin/env python3
"""
Benchmark NeurDB index management with DriftBench for TPC-H SF1.

Runs DriftBench twice:
1) Reactive interception ON, auto-index creation OFF
2) Reactive interception ON, auto-index creation ON

Reports average/median/P95 query latency for each run and prints a comparison.
"""

import argparse
import json
import shutil
import subprocess
import time
import sys
from datetime import datetime
import os
import re
from dataclasses import dataclass
from datetime import timezone

import pandas as pd
import psycopg2


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Benchmark NeurDB index management with DriftBench for TPC-H SF1 (no-auto vs auto)"
    )
    parser.add_argument(
        "--driftbench_path",
        type=str,
        default='/Volumes/data/DB/DriftBench',
        help="Path to DriftBench repo (default: /Volumes/data/DB/DriftBench)"
    )
    parser.add_argument(
        "--query_num",
        type=int,
        default=100,
        help="Total number of DriftBench queries to execute (default: 100)"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run DriftBench in quick mode (ignores --query_num)"
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Output directory (default: output/driftbench_tpch_benchmark_TIMESTAMP)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for DriftBench mixed workload shuffle (default: 42)"
    )
    parser.add_argument(
        "--pg_logfile",
        type=str,
        default=os.path.join("psql", "data", "logfile"),
        help="Path to Postgres logfile for NRIM event extraction (default: psql/data/logfile)"
    )
    parser.add_argument(
        "--nrim_debug",
        action="store_true",
        default=True,
        help="Enable nr_reactive.debug during DriftBench execution (more NRIM logs, more overhead)"
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
        default="tpch_sf1",
        help="PostgreSQL database"
    )
    parser.add_argument(
        "--password",
        type=str,
        default="",
        help="PostgreSQL password (default: empty)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="psql statement timeout in seconds (0 means no timeout)"
    )
    parser.add_argument(
        "--no_reset_nrim",
        action="store_false",
        dest="reset_nrim",
        default=True,
        help="Do not reset NRIM bookkeeping tables (default: reset)"
    )
    parser.add_argument(
        "--generate_workload",
        action="store_true",
        help="Generate TPC-H drift workload if not exists"
    )
    return parser.parse_args()


def detect_driftbench_path() -> str:
    candidates = [
        "/Volumes/data/DB/DriftBench",
        "/code/neurdb-dev",
    ]
    for p in candidates:
        if os.path.isfile(os.path.join(p, "test", "test_neurdb_time_series_execution.py")):
            return p
    raise FileNotFoundError(
        "Could not find DriftBench. Pass --driftbench_path pointing to a repo containing "
        "test/test_neurdb_time_series_execution.py"
    )


def run_psql(args, sql: str) -> None:
    env = {**os.environ, "PGPASSWORD": args.password}
    cmd = [
        "psql",
        "-h",
        args.host,
        "-p",
        str(args.port),
        "-U",
        args.user,
        "-d",
        args.database,
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
        "-c",
        sql,
    ]
    if args.timeout and args.timeout > 0:
        # Apply per-session statement_timeout in seconds.
        cmd[-1] = f"SET statement_timeout = {int(args.timeout) * 1000}; {sql}"
    subprocess.run(cmd, check=True, text=True, capture_output=True, env=env)


def fetch_psql_lines(args, sql: str) -> list[str]:
    env = {**os.environ, "PGPASSWORD": args.password}
    cmd = [
        "psql",
        "-h",
        args.host,
        "-p",
        str(args.port),
        "-U",
        args.user,
        "-d",
        args.database,
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-t",
        "-q",
        "-c",
        sql,
    ]
    res = subprocess.run(cmd, check=True, text=True, capture_output=True, env=env)
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    return lines


def drop_reactive_indexes(args) -> None:
    idx_rows = fetch_psql_lines(
        args,
        "SELECT schemaname || '.' || indexname "
        "FROM pg_indexes "
        "WHERE indexname LIKE 'idx_reactive\\_%' ESCAPE '\\' "
        "ORDER BY 1",
    )
    if not idx_rows:
        print("No idx_reactive_* indexes found to drop.")
        return

    print(f"Dropping {len(idx_rows)} idx_reactive_* indexes (CONCURRENTLY)...")
    for ident in idx_rows:
        run_psql(args, f"DROP INDEX CONCURRENTLY IF EXISTS {ident};")


def reset_nrim_tables(args) -> None:
    # Check if NRIM schema exists before attempting to reset tables
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
            "RESTART IDENTITY;",
        )
        print("✅ NRIM tables reset successfully")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Could not reset NRIM tables (skipping): {e}", file=sys.stderr)


def configure_mode(args, *, auto_create: bool) -> None:
    try:
        # Database-level configuration for background worker
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_index_management_strategy = 'reactive';")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_reactive.enable = on;")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_enable_auto_index_creation = {'on' if auto_create else 'off'};")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_max_index_storage_mb = 0;")  # Remove storage constraint
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_reactive.use_concurrently = on;")
        run_psql(args, f"ALTER DATABASE {args.database} SET nr_reactive.debug = {'on' if args.nrim_debug else 'off'};")

        # Reload configuration to apply changes
        run_psql(args, "SELECT pg_reload_conf();")
        time.sleep(1.0)

        print(f"✅ NeurDB reactive mode configured (auto-create: {'on' if auto_create else 'off'}, 50% database size storage)")

    except subprocess.CalledProcessError as e:
        print(f"⚠️  Could not configure NeurDB mode: {e}", file=sys.stderr)
        # Continue anyway with basic configuration


def write_driftbench_pg_info(driftbench_path: str, args) -> tuple[str, str]:
    pg_info_path = os.path.join(driftbench_path, "data", "PG_info.json")
    with open(pg_info_path, "r", encoding="utf-8") as f:
        original = f.read()

    updated = {
        "dbname": args.database,
        "user": args.user,
        "password": args.password,
        "host": args.host,
        "port": int(args.port),
    }
    tmp_path = None
    try:
        with open(pg_info_path, "w", encoding="utf-8") as f:
            json.dump(updated, f, indent=4)
            f.write("\n")
        tmp_path = pg_info_path
    except Exception:
        # Restore on failure.
        with open(pg_info_path, "w", encoding="utf-8") as f:
            f.write(original)
        raise

    return tmp_path, original


def run_driftbench(driftbench_path: str, *, quick: bool, query_num: int, log_path: str) -> None:
    # Remove previous results so we don't accidentally parse stale data.
    results_path = os.path.join(
        driftbench_path,
        "output",
        "neurdb_benchmark",
        "evaluation",
        "time_series_execution_results.json",
    )
    try:
        os.remove(results_path)
    except FileNotFoundError:
        pass

    cmd = [sys.executable, "-m", "test.test_neurdb_time_series_execution"]
    if quick:
        cmd.append("--quick")
    else:
        cmd.extend(["--query-num", str(query_num)])

    with open(log_path, "w", encoding="utf-8") as logf:
        res = subprocess.run(
            cmd,
            cwd=driftbench_path,
            check=False,
            text=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": f"{driftbench_path}{os.pathsep}{os.environ.get('PYTHONPATH','')}"},
        )
    if res.returncode != 0:
        print(f"Warning: DriftBench exited with code {res.returncode}; logs at {log_path}", file=sys.stderr)


def load_driftbench_metrics(driftbench_path: str) -> dict:
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
    }


def snapshot_driftbench_outputs(driftbench_path: str, out_dir: str, prefix: str) -> None:
    src_eval_dir = os.path.join(driftbench_path, "output", "neurdb_benchmark", "evaluation")
    if not os.path.isdir(src_eval_dir):
        return
    dst_eval_dir = os.path.join(out_dir, f"{prefix}_driftbench_evaluation")
    if os.path.exists(dst_eval_dir):
        shutil.rmtree(dst_eval_dir)
    shutil.copytree(src_eval_dir, dst_eval_dir)


def load_mixed_workload(driftbench_path: str, *, max_queries: int, seed: int) -> pd.DataFrame:
    workload_dir = os.path.join(driftbench_path, "output", "neurdb_benchmark", "workloads")

    # First try to load the simple mixed workload we generated
    workload_file = os.path.join(workload_dir, "tpch_mixed_workload.csv")
    if os.path.exists(workload_file):
        df = pd.read_csv(workload_file)
        if max_queries and len(df) > max_queries:
            df = df.head(max_queries)
        # Shuffle with seed
        df = df.sample(frac=1, random_state=seed).reset_index(drop=True)
        print(f"✅ Loaded {len(df)} TPC-H queries from mixed workload")
        return df

    # Fallback: try to find any individual workloads from DriftSpec generation
    scenarios = [
        ("tpch_lineitem_price_drift.csv", "lineitem_price"),
        ("tpch_orders_customer_drift.csv", "orders_customer"),
        ("tpch_part_supplier_drift.csv", "part_supplier"),
        ("tpch_nation_region_drift.csv", "nation_region"),
        ("tpch_revenue_analytics_drift.csv", "revenue_analytics"),
    ]

    dfs = []
    for fn, scenario in scenarios:
        path = os.path.join(workload_dir, fn)
        if not os.path.isfile(path):
            print(f"Warning: TPC-H workload not found: {path}, will be generated if --generate_workload is used")
            continue
        df = pd.read_csv(path)
        if "query" not in df.columns:
            raise ValueError(f"Workload file missing 'query' column: {path}")
        df = df.copy()
        df["scenario"] = scenario
        dfs.append(df)

    if not dfs:
        if max_queries > 0:  # Only generate if we actually want queries
            return generate_simple_tpch_workload(driftbench_path, max_queries, seed)
        else:
            raise FileNotFoundError("No TPC-H workloads found and generation not requested")

    mixed = pd.concat(dfs, ignore_index=True)
    mixed = mixed.sample(frac=1, random_state=seed).reset_index(drop=True)
    if max_queries and len(mixed) > max_queries:
        mixed = mixed.head(max_queries)
    return mixed


def generate_simple_tpch_workload(driftbench_path: str, query_num: int, seed: int) -> pd.DataFrame:
    """Generate simple TPC-H workload using our existing generator"""
    print("Generating TPC-H workload using simple generator...")

    # Use our simple workload generator
    workload_script = os.path.join(os.path.dirname(__file__), "generate_tpch_workload.py")

    # Run the workload generator
    cmd = [sys.executable, workload_script, str(query_num)]
    res = subprocess.run(cmd, capture_output=True, text=True)

    if res.returncode != 0:
        print(f"❌ Workload generation failed: {res.stderr}")
        raise RuntimeError("TPC-H workload generation failed")

    print("TPC-H workload generation completed!")

    # Load and return the generated workloads
    return load_mixed_workload(driftbench_path, max_queries=query_num, seed=seed)


@dataclass
class QueryResult:
    query_idx: int
    scenario: str
    start_ts_utc: str
    end_ts_utc: str
    duration_s: float
    success: bool
    error: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_workload_with_psycopg2(args, workload_df: pd.DataFrame, out_csv: str, *, application_name: str) -> dict:
    conn = psycopg2.connect(
        dbname=args.database,
        user=args.user,
        password=args.password,
        host=args.host,
        port=args.port,
        application_name=application_name,
    )
    conn.autocommit = False
    results: list[QueryResult] = []

    start_run = utc_now_iso()
    try:
        with conn.cursor() as cur:
            cur.execute("SET client_min_messages = warning;")
            cur.execute("SET statement_timeout = %s;", (int(args.timeout) * 1000,))
        conn.commit()

        for i, row in enumerate(workload_df.itertuples(index=False), 1):
            q = getattr(row, "query")
            scenario = getattr(row, "scenario")
            start_ts = utc_now_iso()
            t0 = time.perf_counter()
            ok = True
            err = ""
            try:
                with conn.cursor() as cur:
                    cur.execute(q)
                conn.commit()
            except Exception as e:
                conn.rollback()
                ok = False
                err = str(e).replace("\n", " ").strip()
            t1 = time.perf_counter()
            end_ts = utc_now_iso()
            results.append(
                QueryResult(
                    query_idx=i,
                    scenario=scenario,
                    start_ts_utc=start_ts,
                    end_ts_utc=end_ts,
                    duration_s=float(t1 - t0),
                    success=ok,
                    error=err,
                )
            )
    finally:
        conn.close()

    end_run = utc_now_iso()
    df = pd.DataFrame([r.__dict__ for r in results])
    df.to_csv(out_csv, index=False)

    ok_df = df[df["success"] == True]
    lat = ok_df["duration_s"].tolist()
    metrics = {
        "run_start_utc": start_run,
        "run_end_utc": end_run,
        "total_queries": int(len(df)),
        "successful_queries": int(ok_df.shape[0]),
        "failed_queries": int((df["success"] == False).sum()),
        "avg_s": float(ok_df["duration_s"].mean()) if not ok_df.empty else 0.0,
        "median_s": float(ok_df["duration_s"].median()) if not ok_df.empty else 0.0,
        "p95_s": float(ok_df["duration_s"].quantile(0.95)) if not ok_df.empty else 0.0,
        "p99_s": float(ok_df["duration_s"].quantile(0.99)) if not ok_df.empty else 0.0,
        "min_s": float(ok_df["duration_s"].min()) if not ok_df.empty else 0.0,
        "max_s": float(ok_df["duration_s"].max()) if not ok_df.empty else 0.0,
    }
    if lat:
        metrics["std_s"] = float(ok_df["duration_s"].std(ddof=0))
    else:
        metrics["std_s"] = 0.0
    return metrics


def parse_pg_log_ts(line: str) -> datetime | None:
    m = re.match(r"^(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}\\.\\d{3}) ([A-Z]+) ", line)
    if not m:
        return None
    ts_s = m.group(1)
    tz = m.group(2)
    if tz != "UTC" and tz != "GMT":
        return None
    # Example: 2025-12-14 07:25:23.710 UTC
    return datetime.strptime(ts_s, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)


def extract_nrim_events(pg_logfile: str, start_utc: str, end_utc: str, out_csv: str) -> None:
    if not os.path.isfile(pg_logfile):
        return
    start_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))

    patterns = [
        "NRIM v2 worker: attempting CREATE INDEX",
        "NRIM v2 worker: CREATE INDEX succeeded",
        "NRIM v2 worker: eviction dropping",
        "NRIM v2 worker: eviction DROP INDEX succeeded",
        "NRIM v2 worker: eviction DROP INDEX failed",
        "NRIM v2 worker: post-create budget exceeded",
    ]

    events = []
    with open(pg_logfile, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ts = parse_pg_log_ts(line)
            if ts is None:
                continue
            if ts < start_dt or ts > end_dt:
                continue
            if any(p in line for p in patterns):
                events.append(
                    {
                        "ts_utc": ts.isoformat(),
                        "line": line.strip(),
                    }
                )

    if not events:
        return
    pd.DataFrame(events).to_csv(out_csv, index=False)


def compare_query_runs(noauto_csv: str, auto_csv: str, out_csv: str) -> pd.DataFrame:
    a = pd.read_csv(noauto_csv)
    b = pd.read_csv(auto_csv)
    merged = a.merge(b, on=["query_idx", "scenario"], suffixes=("_noauto", "_auto"))
    merged["delta_s"] = merged["duration_s_auto"] - merged["duration_s_noauto"]
    merged["ratio_auto_over_noauto"] = merged["duration_s_auto"] / merged["duration_s_noauto"].replace(0, pd.NA)
    merged.to_csv(out_csv, index=False)
    return merged


def main():
    """Main benchmark function."""
    args = parse_args()

    if args.driftbench_path is None:
        args.driftbench_path = detect_driftbench_path()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_dir is None:
        args.output_dir = os.path.join("output", f"driftbench_tpch_benchmark_{timestamp}")
    os.makedirs(args.output_dir, exist_ok=True)

    driftbench_path = args.driftbench_path
    print(f"Using DriftBench at: {driftbench_path}")
    print(f"Output dir: {args.output_dir}")

    # Ensure DriftBench connects to the correct DB by updating DriftBench's PG_info.json.
    pg_info_path, pg_info_original = write_driftbench_pg_info(driftbench_path, args)
    try:
        workload_df = load_mixed_workload(
            driftbench_path,
            max_queries=(100 if args.quick else args.query_num),
            seed=args.seed,
        )
        workload_path = os.path.join(args.output_dir, "mixed_workload.csv")
        workload_df.to_csv(workload_path, index=False)

        # Clean slate before baseline run.
        drop_reactive_indexes(args)
        if args.reset_nrim:
            reset_nrim_tables(args)

        # Phase 1: reactive enabled, auto-create disabled.
        print("\n=== Phase 1: reactive ON, auto-create OFF ===")
        configure_mode(args, auto_create=False)
        noauto_csv = os.path.join(args.output_dir, "noauto_queries.csv")
        metrics_noauto = run_workload_with_psycopg2(
            args,
            workload_df,
            noauto_csv,
            application_name="driftbench_tpch_noauto_client",
        )
        extract_nrim_events(
            args.pg_logfile,
            metrics_noauto["run_start_utc"],
            metrics_noauto["run_end_utc"],
            os.path.join(args.output_dir, "noauto_nrim_events.csv"),
        )

        # Clean slate before auto run (reactive indexes from phase 1 should not exist, but ensure anyway).
        drop_reactive_indexes(args)
        if args.reset_nrim:
            reset_nrim_tables(args)

        # Phase 2: reactive enabled, auto-create enabled.
        print("\n=== Phase 2: reactive ON, auto-create ON ===")
        configure_mode(args, auto_create=True)
        auto_csv = os.path.join(args.output_dir, "auto_queries.csv")
        metrics_auto = run_workload_with_psycopg2(
            args,
            workload_df,
            auto_csv,
            application_name="driftbench_tpch_auto_client",
        )
        extract_nrim_events(
            args.pg_logfile,
            metrics_auto["run_start_utc"],
            metrics_auto["run_end_utc"],
            os.path.join(args.output_dir, "auto_nrim_events.csv"),
        )

    finally:
        # Restore DriftBench config file.
        if pg_info_path:
            with open(pg_info_path, "w", encoding="utf-8") as f:
                f.write(pg_info_original)

    # Print summary + save comparison.
    print("\n" + "=" * 72)
    print("DRIFTBENCH SUMMARY (TPC-H SF1)")
    print("=" * 72)
    print(f"Mode: no-auto  avg={metrics_noauto['avg_s']:.4f}s  median={metrics_noauto['median_s']:.4f}s  p95={metrics_noauto['p95_s']:.4f}s  ok={metrics_noauto['successful_queries']}/{metrics_noauto['total_queries']}")
    print(f"Mode: auto     avg={metrics_auto['avg_s']:.4f}s  median={metrics_auto['median_s']:.4f}s  p95={metrics_auto['p95_s']:.4f}s  ok={metrics_auto['successful_queries']}/{metrics_auto['total_queries']}")

    def ratio(a: float, b: float) -> float:
        return (a / b) if b > 0 else float("inf")

    print("\nSpeedup (no-auto / auto):")
    print(f"  avg:    {ratio(metrics_noauto['avg_s'], metrics_auto['avg_s']):.3f}x")
    print(f"  median: {ratio(metrics_noauto['median_s'], metrics_auto['median_s']):.3f}x")
    print(f"  p95:    {ratio(metrics_noauto['p95_s'], metrics_auto['p95_s']):.3f}x")

    out_json = os.path.join(args.output_dir, "comparison.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "database": args.database,
                "query_num": None if args.quick else args.query_num,
                "quick": bool(args.quick),
                "seed": args.seed,
                "noauto": metrics_noauto,
                "auto": metrics_auto,
            },
            f,
            indent=2,
        )
        f.write("\n")
    print(f"\nSaved: {out_json}")

    # Per-query comparison report (regressions/improvements).
    comparison_csv = os.path.join(args.output_dir, "per_query_comparison.csv")
    merged = compare_query_runs(
        os.path.join(args.output_dir, "noauto_queries.csv"),
        os.path.join(args.output_dir, "auto_queries.csv"),
        comparison_csv,
    )
    print(f"Saved: {comparison_csv}")

    ok = merged[(merged["success_noauto"] == True) & (merged["success_auto"] == True)]
    if not ok.empty:
        top_reg = ok.sort_values("delta_s", ascending=False).head(20)
        top_imp = ok.sort_values("delta_s", ascending=True).head(20)
        top_reg.to_csv(os.path.join(args.output_dir, "top_regressions.csv"), index=False)
        top_imp.to_csv(os.path.join(args.output_dir, "top_improvements.csv"), index=False)
        print(f"Saved: {os.path.join(args.output_dir, 'top_regressions.csv')}")
        print(f"Saved: {os.path.join(args.output_dir, 'top_improvements.csv')}")


if __name__ == "__main__":
    main()