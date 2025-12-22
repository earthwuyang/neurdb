# DriftBench NeurDB Benchmark System

A comprehensive benchmark system for evaluating NeurDB's automatic index adaptation capabilities under workload drift scenarios for both IMDb and TPC-H SF1 databases.

## 🚀 Quick Start

### Prerequisites

1. **PostgreSQL with NeurDB extensions** running on localhost:15432
2. **TPC-H SF1 database** loaded (use `load_tpch_sf1_postgres.sh`)
3. **Required Python packages**:
   ```bash
   pip install pandas numpy scipy matplotlib seaborn psycopg2-binary PyYAML tqdm
   ```

### Basic Usage

```bash
# Run benchmark against TPC-H SF1 with 1000 queries
cd /Volumes/data/DB/neurdb_dev/test
python run_driftbench.py --database tpch_sf1 --query_num 1000

# Run quick benchmark against IMDb test database
python run_driftbench.py --database imdb_test --quick

# Generate TPC-H workload and run benchmark
python run_driftbench.py --database tpch_sf1 --generate_workload --query_num 500
```

## 📁 Files Overview

### Core Benchmark Files

1. **`run_driftbench.py`** - Main runner script with CLI interface
2. **`benchmark_imdb.py`** - IMDb-specific benchmark (existing)
3. **`benchmark_tpch_sf1.py`** - TPC-H SF1 benchmark (new)
4. **`generate_tpch_workload.py`** - Simple TPC-H workload generator

### Database Setup

5. **`../tpch-dbgen/load_tpch_sf1_postgres.sh`** - TPC-H SF1 data loading script

## 🎯 Usage Examples

### TPC-H SF1 Benchmarking

```bash
# Full benchmark with 1000 queries
python run_driftbench.py --database tpch_sf1 --query_num 1000

# Quick test (100 queries)
python run_driftbench.py --database tpch_sf1 --quick

# Generate workload first, then run
python run_driftbench.py --database tpch_sf1 --generate_workload --query_num 500

# Custom output directory
python run_driftbench.py --database tpch_sf1 --query_num 200 --output_dir ./tpch_results
```

### IMDb Test Database Benchmarking

```bash
# Full benchmark with default settings
python run_driftbench.py --database imdb_test --query_num 100

# Quick test mode
python run_driftbench.py --database imdb_test --quick

# Custom configuration
python run_driftbench.py --database imdb_test --query_num 500 --seed 123 --timeout 30
```

## ⚙️ Configuration Options

### Database Connection
- `--database`: Database name (`tpch_sf1`, `imdb_test`)
- `--host`: PostgreSQL host (default: localhost)
- `--port`: PostgreSQL port (default: 15432)
- `--user`: PostgreSQL user (default: neurdb)
- `--password`: PostgreSQL password (default: empty)

### Benchmark Configuration
- `--query_num`: Total number of queries to execute (default: 100)
- `--quick`: Run in quick mode (100 queries total)
- `--generate_workload`: Generate drift workload for TPC-H if not exists
- `--seed`: Random seed for workload shuffling (default: 42)
- `--timeout`: Query timeout in seconds (default: 0 = no timeout)

### Output and Debugging
- `--output_dir`: Custom output directory
- `--driftbench_path`: Path to DriftBench repository
- `--nrim_debug`: Enable NRIM debug mode

## 📊 Benchmark Modes

The benchmark runs two phases for each database:

### Phase 1: Baseline (Reactive ON, Auto-index OFF)
- NeurDB reactive interception enabled
- Automatic index creation disabled
- Measures baseline query performance

### Phase 2: Adaptive (Reactive ON, Auto-index ON)
- NeurDB reactive interception enabled
- Automatic index creation enabled
- Measures performance with automatic adaptation

### Results Comparison
- **Speedup metrics**: avg, median, p95 improvements
- **Per-query analysis**: individual query comparisons
- **Regressions and improvements**: top performance changes
- **NRIM events**: index creation/dropping logs

## 🎯 TPC-H SF1 Drift Scenarios

The TPC-H benchmark includes 5 drift scenarios:

1. **Lineitem Price Drift** - Queries focusing on price ranges and quantities
2. **Orders Customer Drift** - Customer order pattern changes
3. **Part Supplier Drift** - Part-supplier relationship queries
4. **Nation Region Drift** - Geographic distribution queries
5. **Revenue Analytics Drift** - Business intelligence queries

## 📈 Output Structure

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

## 🔧 Advanced Usage

### Custom Workload Generation

```bash
# Generate custom TPC-H workload
python generate_tpch_workload.py 1000

# This creates workloads in:
# /Volumes/data/DB/DriftBench/output/neurdb_benchmark/workloads/
```

### Direct Benchmark Execution

```bash
# Run IMDb benchmark directly
python benchmark_imdb.py --query_num 500

# Run TPC-H benchmark directly
python benchmark_tpch_sf1.py --query_num 500 --generate_workload
```

### Database-Specific Configuration

For TPC-H SF1:
```bash
python run_driftbench.py \
  --database tpch_sf1 \
  --host localhost \
  --port 15432 \
  --user neurdb \
  --query_num 1000 \
  --generate_workload
```

For IMDb test:
```bash
python run_driftbench.py \
  --database imdb_test \
  --host localhost \
  --port 15432 \
  --user neurdb \
  --query_num 500
```

## 📋 Example Output

```
DRIFTBENCH SUMMARY (TPC-H SF1)
========================================================================
Mode: no-auto  avg=0.0234s  median=0.0198s  p95=0.0456s  ok=950/1000
Mode: auto     avg=0.0156s  median=0.0132s  p95=0.0298s  ok=950/1000

Speedup (no-auto / auto):
  avg:    1.500x
  median: 1.500x
  p95:    1.530x

Saved: output/driftbench_tpch_benchmark_20250317_143022/comparison.json
Saved: output/driftbench_tpch_benchmark_20250317_143022/per_query_comparison.csv
```

## 🐛 Troubleshooting

### Common Issues

1. **Database Connection Failed**
   ```bash
   # Verify database exists and is accessible
   psql -h localhost -p 15432 -U neurdb -d tpch_sf1
   ```

2. **Missing TPC-H Workloads**
   ```bash
   # Generate TPC-H workloads
   python run_driftbench.py --database tpch_sf1 --generate_workload
   ```

3. **DriftBench Path Issues**
   ```bash
   # Verify DriftBench installation
   ls /Volumes/data/DB/DriftBench/test/test_neurdb_time_series_execution.py
   ```

4. **Permission Issues**
   ```bash
   # Make scripts executable
   chmod +x run_driftbench.py
   chmod +x ../tpch-dbgen/load_tpch_sf1_postgres.sh
   ```

### Debug Mode

```bash
# Enable verbose logging
python run_driftbench.py --database tpch_sf1 --nrim_debug --query_num 50
```

## 🎯 Next Steps

1. **Load TPC-H SF1 data**: Use the provided load script
2. **Run initial benchmark**: Start with quick mode to verify setup
3. **Scale up**: Run full benchmarks with 1000+ queries
4. **Analyze results**: Review generated CSV files and JSON summaries
5. **Customize**: Modify workload generation for specific use cases

## 📚 References

- **DriftBench**: `/Volumes/data/DB/DriftBench/`
- **TPC-H Specification**: http://www.tpc.org/tpch/
- **NeurDB Documentation**: Available in the main NeurDB repository