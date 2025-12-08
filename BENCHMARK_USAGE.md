# NeurDB DriftBench Benchmarking Guide - Quick Start

## Prerequisites

1. **Running NeurDB Container**
```bash
docker exec neurdb_dev ps aux | grep postgres
```

2. **Database Ready**
```bash
docker exec neurdb_dev psql -d imdb_ori -c "SELECT COUNT(*) FROM title;"
```

3. **Scripts Ready**
```bash
ls -la /Volumes/data/DB/neurdb_dev/run_benchmark.sh
docker exec neurdb_dev ls /code/neurdb-dev/neurdb_triggers.py 2>/dev/null || echo "Triggers script required"
```

---

## Quick Benchmark Run

### 1. Run Baseline (No Auto-Index)

```bash
# Start in one terminal (inside Docker)
docker exec -it neurdb_dev bash
cd /code/neurdb-dev

# Run baseline test (5 minutes)
./run_benchmark.sh noauto 300
```

**Expected Output:**
```
==========================================
📊 BENCHMARK COMPLETE
==========================================
Mode: noauto
Queries executed: 1234
Average time: 45.3ms
Min time: 12.1ms
Max time: 234.5ms
Output: output/benchmark_noauto_20241208_150000
==========================================
```

### 2. Run With Auto-Index

```bash
# In same Docker terminal
./run_benchmark.sh auto 300
```

**Expected Output:**
```
==========================================
📊 BENCHMARK COMPLETE
==========================================
Mode: auto
Queries executed: 1234
Average time: 8.7ms
Min time: 0.3ms
Max time: 45.2ms
Output: output/benchmark_auto_20241208_150500
==========================================
```

### 3. Compare Results

```bash
# After both runs complete
./compare_results.sh \
    output/benchmark_noauto_20241208_150000 \
    output/benchmark_auto_20241208_150500

# Results saved to:
# output/comparison_20241208_151000/
```

**Sample Comparison Output:**
```
==========================================
📊 BENCHMARK COMPARISON REPORT
==========================================

┌──────────────┬────────────────┬────────────────┬─────────────┐
│ Metric       │ Baseline       │ Optimized      │ Improvement │
├──────────────┼────────────────┼────────────────┼─────────────┤
│ Average      │ 45.3 ms        │ 8.7 ms         │ ✅ 80.8%    │
│ Min          │ 12.1 ms        │ 0.3 ms         │ 97.5%       │
│ Max          │ 234.5 ms       │ 45.2 ms        │ 80.7%       │
├──────────────┼────────────────┼────────────────┼─────────────┤
│ Indexes      │ 47 indexes     │ 52 indexes     │ +5          │
│ Size         │ 3908.7 MB      │ 3954.2 MB      │ +45.5 MB    │
└──────────────┴────────────────┴────────────────┴─────────────┘

Summary: ✅ EXCELLENT (average 80.8% improvement)

💡 RECOMMENDATION:
   ✅ Strongly recommend enabling automatic index management
   (80.8% performance improvement with 1.2% storage overhead)

💾 Results saved to:
   output/comparison_20241208_151000/comparison_results.json
```

---

## Important Notes

1. **Database Connection**: Scripts must be run **inside Docker container**:
   ```bash
   docker exec -it neurdb_dev bash
   cd /code/neurdb-dev
   ```

2. **Time Required**:
   - Quick test: 5 minutes per run
   - Full benchmark: 1 hour per run
   - Comparison: instant

3. **Disk Space**: Each benchmark creates ~10-50MB of logs

4. **GUC Parameters**: Currently set at session level. For production:
   ```bash
   # Edit postgresql.conf (requires restart)
   nr_max_index_storage_mb = 0  # Auto-calculate (50% of DB size)
   nr_enable_auto_index_creation = on
   ```

---

## Troubleshooting

### Benchmark Fails

1. **Check database connection:**
   ```bash
   psql -c "SELECT version();"
   ```

2. **Verify imdb_ori database exists:**
   ```bash
   psql -l | grep imdb_ori
   ```

3. **Check write permissions:**
   ```bash
   touch /Volumes/data/DB/neurdb_dev/output/test
   ```

### No Performance Improvement

Possible reasons:

1. **Workload already optimized**: Try different query patterns
2. **Queries too simple**: Add more complex joins or aggregations
3. **Storage full**: Check `SELECT nr_get_current_index_storage_mb()`
4. **GUC not set**: Verify auto-index is enabled in script

### Excessive Storage Usage

1. **Check current usage:**
   ```bash
   psql -c "SELECT nr_get_current_index_storage_mb()"
   ```

2. **Drop unused indexes:**
   ```bash
   psql -c "
     SELECT 'DROP INDEX ' || indexrelname || ';'
     FROM pg_stat_user_indexes
     WHERE indexrelname LIKE 'idx_auto_%'
       AND idx_scan = 0
   "
   ```

3. **Run with eviction demo:**
   ```bash
   python3 test_with_eviction_demo.py
   ```

---

## Next Steps

After confirming benefits:

1. **Adjust GUC parameters** for production:
   ```bash
   # In postgresql.conf
   nr_max_index_storage_mb = 8192  # 8GB limit
   nr_enable_auto_index_creation = on
   ```

2. **Monitor in production:**
   ```bash
   # Add to monitoring dashboard
   SELECT
     nr_get_current_index_storage_mb() as current,
     nr_calculate_index_budget_mb() as budget,
     nr_get_current_index_storage_mb() / nr_calculate_index_budget_mb() * 100 as pct_used;
   ```

3. **Set up alerts** for budget threshold (e.g., 90%)

4. **Review auto-created indexes** weekly:
   ```bash
   psql -c "
     SELECT
       indexrelname,
       idx_scan,
       pg_size_pretty(pg_relation_size(indexrelid))
     FROM pg_stat_user_indexes
     WHERE indexrelname LIKE 'idx_auto_%'
     ORDER BY idx_scan DESC;
   "
   ```

For complete documentation, see: `/Volumes/data/DB/neurdb_dev/BENCHMARK_USAGE.md`
