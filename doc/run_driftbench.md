# DriftBench Runner for NeurDB

A comprehensive benchmark system for evaluating NeurDB's automatic index adaptation capabilities under workload drift scenarios for both TPC-H SF1 and IMDb databases.

## 🚀 Quick Start

### Prerequisites

1. **PostgreSQL with NeurDB extensions** running on localhost:15432
2. **TPC-H SF1 database** loaded (use `load_tpch_sf1_postgres.sh`)
3. **IMDb test database** (if testing with IMDb)
4. **NRIM extension installed** in target databases

### Installation

1. **Install Python dependencies**:
   ```bash
   pip install pandas numpy psycopg2-binary PyYAML tqdm
   ```

2. **Setup TPC-H SF1 database**:
   ```bash
   cd /Volumes/data/DB/tpch-dbgen
   ./load_tpch_sf1_postgres.sh
   ```

3. **Install NRIM extension** (required for both databases):
   ```bash
   # For TPC-H SF1
   psql -h localhost -p 15432 -U neurdb -d tpch_sf1 -c "CREATE EXTENSION IF NOT EXISTS nr_index_management;"

   # For IMDb test (if exists)
   psql -h localhost -p 15432 -U neurdb -d imdb_test -c "CREATE EXTENSION IF NOT EXISTS nr_index_management;"
   ```

## 📊 Usage Examples

### Basic Usage

```bash
# Run TPC-H SF1 benchmark with 1000 queries
python test/run_driftbench.py --database tpch_sf1 --query_num 1000

# Run IMDb benchmark with quick mode (100 queries)
python test/run_driftbench.py --database imdb_test --quick

# Generate larger TPC-H workload and run benchmark
python test/run_driftbench.py --database tpch_sf1 --query_num 10000
```

### Advanced Usage

```bash
# Custom database connection
python test/run_driftbench.py \
  --database tpch_sf1 \
  --host localhost \
  --port 15432 \
  --user neurdb \
  --query_num 5000

# Generate workload only
python test/run_driftbench.py --database tpch_sf1 --generate_workload --query_num 2000

# Custom output directory
python test/run_driftbench.py --database tpch_sf1 --query_num 1000 --output_dir ./my_results

# Enable debugging
python test/run_driftbench.py --database tpch_sf1 --query_num 500 --nrim_debug

# Set query timeout
python test/run_driftbench.py --database tpch_sf1 --query_num 1000 --timeout 30
```

## 🎯 Command Line Options

### Database Configuration
- `--database`: Database name (`tpch_sf1`, `imdb_test`) **[Required]**
- `--host`: PostgreSQL host (default: localhost)
- `--port`: PostgreSQL port (default: 15432)
- `--user`: PostgreSQL user (default: neurdb)
- `--password`: PostgreSQL password (default: empty)

### Benchmark Configuration
- `--query_num`: Total number of queries to execute (default: 100)
- `--quick`: Run in quick mode (100 queries, ignores --query_num)
- `--generate_workload`: Generate TPC-H drift workload if not exists
- `--seed`: Random seed for workload shuffling (default: 42)
- `--timeout`: Query timeout in seconds (default: 0 = no timeout)

### Output and Debugging
- `--output_dir`: Custom output directory (default: auto-generated)
- `--driftbench_path`: Path to DriftBench repository (default: /Volumes/data/DB/DriftBench)
- `--nrim_debug`: Enable NRIM debug mode (default: enabled)
- `--no_reset_nrim`: Skip NRIM table reset (default: reset)

## 📋 Database Support

### TPC-H SF1

- **Data Size**: 1GB SF1 TPC-H dataset
- **Tables**: 8 TPC-H tables (nation, region, supplier, part, partsupp, customer, orders, lineitem)
- **Drift Scenarios**: 5 realistic TPC-H drift patterns
  - `lineitem_quantity`: Price/quantity analysis queries
  - `lineitem_price`: Extended price range queries
  - `orders_date`: Customer order date patterns
  - `customer_balance`: Customer balance queries
  - `supplier_nation`: Supplier geography queries
  - `part_size`: Part size queries
  - `orders_status`: Order status queries
  - `part_price`: Part price range queries
  - `nation_region`: Geographic queries

### IMDb Test

- **Data Size**: Large IMDb movie database
- **Tables**: Focus on cast_info table with related joins
- **Drift Scenarios**: 5 IMDb-specific drift patterns
  - `popular_actor_drift`: Trending actor searches
  - `movie_cast_drift`: Movie detail page loads
  - `role_search_drift`: Character role searches
  - `multi_criteria_drift`: Complex search queries
  - `award_season_drift`: Award season traffic patterns

## 🏗️ Benchmark Architecture

### Two-Phase Execution

The benchmark runs each workload in two phases:

1. **Phase 1: Baseline (Reactive ON, Auto-index OFF)**
   - NeurDB reactive interception enabled
   - Automatic index creation disabled
   - Measures baseline query performance

2. **Phase 2: Adaptive (Reactive ON, Auto-index ON)**
   - NeurDB reactive interception enabled
   - Automatic index creation enabled
   - Measures performance with automatic adaptation

### Performance Metrics

For each phase, the system measures:
- **Average query execution time**
- **Median query execution time**
- **P95/P99 query execution time**
- **Success rate** (successful/total queries)
- **Speedup comparison** (Phase 1 vs Phase 2)

### Output Analysis

- **JSON Summary**: Overall performance comparison and speedup metrics
- **Per-Query CSV**: Detailed execution times for each query
- **Regressions**: Queries that got slower in Phase 2
- **Improvements**: Queries that got faster in Phase 2
- **NRIM Events**: Index creation/deletion logs

## 📁 Output Structure

```
output/driftbench_{database}_benchmark_{timestamp}/
├── mixed_workload.csv                 # Combined workload
├── noauto_queries.csv                 # Phase 1 results
├── auto_queries.csv                   # Phase 2 results
├── comparison.json                    # Performance summary
├── per_query_comparison.csv           # Detailed per-query comparison
├── top_regressions.csv                # Queries that got slower
├── top_improvements.csv               # Queries that got faster
├── noauto_nrim_events.csv             # NRIM events Phase 1
└── auto_nrim_events.csv               # NRIM events Phase 2
```

## 🔧 Example Output

```
DRIFTBENCH SUMMARY (TPC-H SF1)
========================================================================
Mode: no-auto  avg=0.0028s  median=0.0026s  p95=0.0040s  ok=1000/1000
Mode: auto     avg=0.0026s  median=0.0025s  p95=0.0032s  ok=1000/1000

Speedup (no-auto / auto):
  avg:    1.077x
  median: 1.040x
  p95:    1.250x
```

## 🐛 Troubleshooting

### Common Issues

1. **Database Connection Failed**
   ```bash
   # Verify database exists and is accessible
   psql -h localhost -p 15432 -U neurdb -d tpch_sf1
   ```

2. **NRIM Extension Not Found**
   ```bash
   # Install NRIM extension
   psql -h localhost -p 15432 -U neurdb -d tpch_sf1 -c "CREATE EXTENSION IF NOT EXISTS nr_index_management;"
   ```

3. **Missing TPC-H Workloads**
   ```bash
   # Generate TPC-H workloads
   python test/generate_tpch_workload.py 1000
   ```

4. **Permission Issues**
   ```bash
   # Make scripts executable
   chmod +x test/run_driftbench.py
   chmod +x ../tpch-dbgen/load_tpch_sf1_postgres.sh
   ```

### Debug Mode

```bash
# Enable verbose logging
python test/run_driftbench.py --database tpch_sf1 --nrim_debug --query_num 50
```

### Performance Tips

- **For initial testing**: Use `--quick` or smaller `--query_num`
- **For production benchmarking**: Use `--query_num 10000` or higher
- **For debugging**: Use `--nrim_debug` and check NRIM event logs
- **For reproducibility**: Set a specific `--seed` value

## 📚 Integration with CI/CD

```yaml
# Example GitHub Actions workflow
- name: Run NeurDB Benchmark
  run: |
    python test/run_driftbench.py --database tpch_sf1 --query_num 1000
    python test/run_driftbench.py --database imdb_test --query_num 500

- name: Validate Performance
  run: |
    # Check that NeurDB improvement exceeds threshold
    python scripts/validate_performance.py --threshold 15
```

## 🎯 Performance Recommendations

1. **Start Small**: Begin with `--quick` mode to verify setup
2. **Scale Up**: Gradually increase query count to find optimal size
3. **Monitor**: Check NRIM event logs for index creation patterns
4. **Compare**: Use results to tune NeurDB configuration
5. **Repeat**: Run multiple times with different seeds for statistical significance

## 📄 File Locations

- **Main Runner**: `test/run_driftbench.py`
- **TPC-H Benchmark**: `test/benchmark_tpch_sf1.py`
- **IMDb Benchmark**: `test/benchmark_imdb.py`
- **TPC-H Workload Generator**: `test/generate_tpch_workload.py`
- **TPC-H Data Loader**: `../tpch-dbgen/load_tpch_sf1_postgres.sh`
- **DriftBench**: `/Volumes/data/DB/DriftBench/`

## 🔍 References

- **DriftBench**: `/Volumes/data/DB/DriftBench/`
- **TPC-H Specification**: http://www.tpc.org/tpch/
- **NeurDB Documentation**: Available in the main NeurDB repository

---

**Last Updated**: December 2025
**Version**: 1.0
**Compatibility**: NeurDB 0.1+, PostgreSQL 13+