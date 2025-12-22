#!/usr/bin/env python3
"""
Manually generate a working TPC-H drift spec that matches the format of the IMDB spec
"""

import os

tpch_spec = """pattern_id: neurdb-tpch-index-benchmark
seed: 42

type:
  family: workload
  category: templates
  subtype: selection_payload

data_source:
  kind: postgres
  db_config_path: ./data/PG_info.json
  schema_name: public
  # Use the largest TPC-H table for comprehensive index testing
  physical_table: lineitem
  schema_extractor:
    source_type: postgres
    sample_size: 100000
    schema_output_path: ./output/neurdb_benchmark/schema/tpch_lineitem_schema.json

variables:
  base_table: lineitem

  # Define column groups for index recommendation testing based on TPC-H schema
  index_candidate_columns:
    # High-cardinality columns (excellent for B-tree indexes)
    high_cardinality:
      - l_orderkey     # Foreign key to orders
      - l_partkey      # Foreign key to part
      - l_suppkey      # Foreign key to supplier
      - l_linenumber   # Line number in order

    # Medium-cardinality columns
    medium_cardinality:
      - l_shipdate     # Shipping date
      - l_commitdate   # Commit date
      - l_receiptdate  # Receipt date
      - l_shipmode     # Shipping mode

    # Low-cardinality columns
    low_cardinality:
      - l_returnflag   # Return status
      - l_linestatus   # Line status

runs:
  - name: tpch_baseline
    num_templates: 50
    templates_per_run: 25
    output_dir: ./output/neurdb_benchmark/templates
    output_file: tpch_baseline_templates.json

  # Phase 2: TPC-H-specific workload drift scenarios
query_runs:
    # Scenario 1: Lineitem Price/Quantity Drift (simulating price analysis queries)
    - name: lineitem_price_drift
      template: tpch_baseline
      queries_per_template: 100
      dist_config:
        lineitem.l_quantity: { distribution: normal, mean: 25, std: 10 }
        lineitem.l_extendedprice: { distribution: zipf, a: 1.5, min: 100, max: 10000 }
        lineitem.l_discount: { distribution: uniform, min: 0, max: 0.1 }
      outputs:
        - type: workload
          path: ./output/neurdb_benchmark/workloads/tpch_lineitem_price_drift.csv
        - type: temporal
          path: ./output/neurdb_benchmark/temporal/tpch_lineitem_price_timestamp.csv
          timestamp: { pattern: periodic, start_time: "2025-01-01T00:00:00", queries_per_minute: 100 }

    # Scenario 2: Orders Customer Drift (simulating customer order analysis)
    - name: orders_customer_drift
      template: tpch_baseline
      queries_per_template: 100
      dist_config:
        orders.o_orderdate: { distribution: zipf, a: 1.2, min: "1995-01-01", max: "1998-12-31" }
        orders.o_custkey: { distribution: normal, mean: 75000, std: 25000 }
      outputs:
        - type: workload
          path: ./output/neurdb_benchmark/workloads/tpch_orders_customer_drift.csv
        - type: temporal
          path: ./output/neurdb_benchmark/temporal/tpch_orders_customer_timestamp.csv
          timestamp: { pattern: periodic, start_time: "2025-01-01T02:00:00", queries_per_minute: 100 }

    # Scenario 3: Part Supplier Drift (simulating supply chain queries)
    - name: part_supplier_drift
      template: tpch_baseline
      queries_per_template: 100
      dist_config:
        part.p_partkey: { distribution: uniform, min: 1, max: 200000 }
        partsupp.ps_suppkey: { distribution: zipf, a: 1.3, min: 1, max: 10000 }
      outputs:
        - type: workload
          path: ./output/neurdb_benchmark/workloads/tpch_part_supplier_drift.csv
        - type: temporal
          path: ./output/neurdb_benchmark/temporal/tpch_part_supplier_timestamp.csv
          timestamp: { pattern: periodic, start_time: "2025-01-01T04:00:00", queries_per_minute: 100 }

    # Scenario 4: Nation Region Drift (simulating geographic queries)
    - name: nation_region_drift
      template: tpch_baseline
      queries_per_template: 100
      dist_config:
        nation.n_nationkey: { distribution: uniform, min: 0, max: 24 }
        region.r_regionkey: { distribution: uniform, min: 0, max: 4 }
      outputs:
        - type: workload
          path: ./output/neurdb_benchmark/workloads/tpch_nation_region_drift.csv
        - type: temporal
          path: ./output/neurdb_benchmark/temporal/tpch_nation_region_timestamp.csv
          timestamp: { pattern: periodic, start_time: "2025-01-01T06:00:00", queries_per_minute: 100 }

    # Scenario 5: Revenue Analytics Drift (simulating business intelligence queries)
    - name: revenue_analytics_drift
      template: tpch_baseline
      queries_per_template: 100
      dist_config:
        lineitem.l_shipdate: { distribution: zipf, a: 1.1, min: "1996-01-01", max: "1998-12-31" }
        orders.o_orderpriority: { distribution: choice, choices: ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED"] }
      outputs:
        - type: workload
          path: ./output/neurdb_benchmark/workloads/tpch_revenue_analytics_drift.csv
        - type: temporal
          path: ./output/neurdb_benchmark/temporal/tpch_revenue_analytics_timestamp.csv
          timestamp: { pattern: periodic, start_time: "2025-01-01T08:00:00", queries_per_minute: 100 }
"""

# Write the corrected spec
spec_path = "/Volumes/data/DB/DriftBench/driftspec/examples/neurdb_tpch_benchmark.yaml"
with open(spec_path, "w") as f:
    f.write(tpch_spec)

print(f"✅ Created corrected TPC-H spec at: {spec_path}")