# NeurDB Auto-Index Benchmarking Quick Start

## Running Benchmarks

### Prerequisites
- NeurDB Docker container running
- Scripts in `/Volumes/data/DB/neurdb_dev/`
- Run from the Docker container

### Quick Commands

```bash
# Inside Docker:
docker exec -it neurdb_dev bash
cd /code/neurdb-dev

# Test benchmark scripts
./run_benchmark.sh --help
./compare_results.sh --help

# Run 30-second baseline (no auto-index)
./run_benchmark.sh noauto 30

# Run 30-second optimized (with auto-index)
./run_benchmark.sh auto 30

# Compare results
./compare_results.sh \
    output/benchmark_noauto_* \
    output/benchmark_auto_*
```

### What Gets Measured

**Performance Metrics:**
- Query execution time (avg/min/max)
- Number of queries executed
- Individual query performance

**Index Metrics:**
- Total indexes before/after
- Index storage usage
- Auto-created indexes
- Usage statistics (scans)

**Comparison Output:**
- Performance improvement percentage
- Storage overhead
- Recommendations

### Example Output

```
📊 BENCHMARK COMPLETE
Mode: noauto
Queries: 270
Average: 96.8ms
Output: output/benchmark_noauto_20251208_182602
```

### Comparison Results

```
📊 BENCHMARK COMPARISON REPORT
┌──────────────┬────────────────┬────────────────┬─────────────┐
│ Metric       │ Baseline       │ Optimized      │ Improvement │
├──────────────┼────────────────┼────────────────┼─────────────┤
│ Average      │ 96.8 ms        │ 92.5 ms        │ 4.4%        │
│ Queries      │ 270            │ 270            │ --          │
└──────────────┴────────────────┴────────────────┴─────────────┘
```

### Troubleshooting

**Error: `psql: command not found`**
- Solution: Run inside Docker container

**Error: `connection refused`**
- Solution: Check DB_PORT (use 5432 inside Docker, 15432 from host)

**Error: `syntax error at or near`\`"**\`
- Solution: Script is already fixed, re-download if needed

### Next Steps

1. **Run longer benchmarks** (300+ seconds) for more accurate results
2. **Test different workloads** by modifying queries in the script
3. **Monitor storage** with `nr_get_current_index_storage_mb()`
4. **Enable in production** if benchmarks show >10% improvement

For detailed documentation, see: `/Volumes/data/DB/neurdb_dev/BENCHMARK_USAGE.md`
