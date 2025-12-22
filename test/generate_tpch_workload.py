#!/usr/bin/env python3
"""
Generate TPC-H drift workload for DriftBench testing
"""

import os
import sys
import pandas as pd
import json
import yaml
import random
from datetime import datetime, timedelta

# Add DriftBench to path
driftbench_path = "/Volumes/data/DB/DriftBench"
sys.path.insert(0, driftbench_path)

def generate_simple_tpch_queries(num_queries=100):
    """Generate simple TPC-H style queries for testing"""

    templates = [
        "SELECT * FROM lineitem WHERE l_quantity > {value} LIMIT 100;",
        "SELECT * FROM lineitem WHERE l_extendedprice < {value} LIMIT 100;",
        "SELECT * FROM orders WHERE o_orderdate > '{date}' LIMIT 100;",
        "SELECT * FROM customer WHERE c_acctbal > {value} LIMIT 100;",
        "SELECT * FROM part WHERE p_retailprice BETWEEN {min} AND {max} LIMIT 100;",
        "SELECT l_orderkey, SUM(l_quantity) FROM lineitem GROUP BY l_orderkey HAVING SUM(l_quantity) > {value} LIMIT 100;",
        "SELECT * FROM nation WHERE n_regionkey = {value};",
        "SELECT * FROM supplier WHERE s_nationkey = {value} LIMIT 100;",
        "SELECT o_orderkey, o_totalprice FROM orders WHERE o_orderstatus = '{status}' LIMIT 100;",
        "SELECT p_partkey, p_name FROM part WHERE p_size = {value} LIMIT 100;"
    ]

    queries = []
    scenarios = []

    for i in range(num_queries):
        template = random.choice(templates)

        if 'quantity' in template:
            value = random.uniform(10, 50)
            query = template.format(value=value)
            scenario = "lineitem_quantity"
        elif 'extendedprice' in template:
            value = random.uniform(1000, 5000)
            query = template.format(value=value)
            scenario = "lineitem_price"
        elif 'orderdate' in template:
            date = (datetime(1995, 1, 1) + timedelta(days=random.randint(0, 1095))).strftime('%Y-%m-%d')
            query = template.format(date=date)
            scenario = "orders_date"
        elif 'acctbal' in template:
            value = random.uniform(0, 1000)
            query = template.format(value=value)
            scenario = "customer_balance"
        elif 'retailprice' in template:
            min_val = random.uniform(100, 500)
            max_val = min_val + random.uniform(100, 500)
            query = template.format(min=min_val, max=max_val)
            scenario = "part_price"
        elif 'regionkey' in template:
            value = random.randint(0, 4)
            query = template.format(value=value)
            scenario = "nation_region"
        elif 'nationkey' in template:
            value = random.randint(0, 24)
            query = template.format(value=value)
            scenario = "supplier_nation"
        elif 'status' in template:
            status = random.choice(['O', 'F', 'P'])
            query = template.format(status=status)
            scenario = "orders_status"
        else:  # size
            value = random.randint(1, 50)
            query = template.format(value=value)
            scenario = "part_size"

        queries.append(query)
        scenarios.append(scenario)

    return queries, scenarios


def main():
    """Generate TPC-H drift workload"""

    print("🏗️  Generating TPC-H drift workload...")

    # Create output directories
    output_dir = "/Volumes/data/DB/DriftBench/output/neurdb_benchmark/workloads"
    os.makedirs(output_dir, exist_ok=True)

    # Generate queries
    num_queries = 500  # Default number
    if len(sys.argv) > 1:
        num_queries = int(sys.argv[1])

    queries, scenarios = generate_simple_tpch_queries(num_queries)

    # Create DataFrame
    df = pd.DataFrame({
        'query': queries,
        'scenario': scenarios
    })

    # Save to CSV
    output_file = os.path.join(output_dir, "tpch_mixed_workload.csv")
    df.to_csv(output_file, index=False)

    print(f"✅ Generated {len(queries)} TPC-H queries")
    print(f"📁 Saved to: {output_file}")
    print(f"📊 Scenarios: {df['scenario'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()