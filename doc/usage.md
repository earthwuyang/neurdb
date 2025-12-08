# NeurDB Workload Forecasting and Index Management Usage Guide

## Overview

This document provides comprehensive usage instructions for NeurDB's AI-powered workload forecasting and automatic index management system. The system consists of two main components:

1. **AI Engine** (`aiengine/workload_forecast/`) - Provides REST API endpoints for workload analysis and index recommendations
2. **Database Extensions** (`dbengine/nr_kernel/nr_index_management/`) - PostgreSQL extension for storage budget management and index operations

## Architecture and Planning

For detailed architecture and implementation plans, see:
- [Workload Forecast Plan](../plans/workload_forecast_plan.md) - Complete technical roadmap and implementation details
- [Phase 4 & 5 Implementation](../plans/workload_forecast_plan.md#phase-4-index-recommendation-and-generation) - Index recommendation and automatic creation strategies

## Quick Start

### Starting the AI Engine

```bash
cd /code/neurdb-dev/aiengine/workload_forecast
python run_server.py --port 8777
```

Check health status:
```bash
curl http://localhost:8777/health
```

### Enabling Database Extensions

Connect to your PostgreSQL database:

```sql
-- Install the index management extension
CREATE EXTENSION nr_index_management;

-- Verify GUC parameters are available
SHOW nr_max_index_storage_mb;
SHOW nr_enable_auto_index_creation;
```

## AI Engine API Endpoints

### 1. Storage Statistics Endpoint

Get real-time index storage statistics and budget information.

**Endpoint**: `GET /index/storage_stats`

**Parameters**:
- `host` - Database host (default: localhost)
- `port` - Database port (default: 5432)
- `database` - Database name
- `user` - Database user
- `password` - Database password (optional)

**Example**:
```bash
curl "http://localhost:8777/index/storage_stats?database=imdb_ori&user=neurdb&host=localhost&port=5432"
```

**Response**:
```json
{
    "status": "success",
    "storage_summary": {
        "current_usage_mb": 3908.69,
        "budget_mb": 4586.42,
        "budget_utilization_percent": 85.2,
        "remaining_budget_mb": 677.73,
        "total_indexes": 47
    },
    "index_details": [
        {
            "index_name": "cast_info_pkey",
            "table_name": "cast_info",
            "size_mb": 776.40,
            "usage_count": 124
        }
    ]
}
```

### 2. Automatic Index Creation Endpoint

Automatically analyze workload and create beneficial indexes within storage budget.

**Endpoint**: `POST /index/auto_create`

**Request Body**:
```json
{
    "workload_queries": [
        "SELECT * FROM title WHERE production_year = 2020",
        "SELECT * FROM movie_info WHERE info_type_id = 3"
    ],
    "database_host": "localhost",
    "database_port": 5432,
    "database_name": "imdb_ori",
    "database_user": "neurdb",
    "time_limit_seconds": 30
}
```

**Alternative with Direct Recommendations**:
```json
{
    "database_host": "localhost",
    "database_port": 5432,
    "database_name": "imdb_ori",
    "database_user": "neurdb",
    "recommended_indexes": [
        {
            "table_name": "title",
            "columns": ["production_year", "kind_id"],
            "index_type": "btree",
            "estimated_size_mb": 50,
            "benefit_score": 100
        }
    ]
}
```

**Example**:
```bash
curl -X POST http://localhost:8777/index/auto_create \
  -H "Content-Type: application/json" \
  -d '{
    "workload_queries": ["SELECT * FROM title WHERE production_year BETWEEN 2020 AND 2022"],
    "database_name": "imdb_ori",
    "database_user": "neurdb"
  }'
```

**Response**:
```json
{
    "status": "success",
    "message": "Successfully created 1 out of 2 recommended indexes (45.50 MB)",
    "storage_budget": {
        "budget_mb": 4586.42,
        "usage_before_mb": 3908.69,
        "usage_after_mb": 3954.19,
        "size_added_mb": 45.50,
        "remaining_budget_mb": 632.23
    },
    "index_creation_results": {
        "total_recommendations": 2,
        "created_indexes": 1,
        "failed_indexes": 1,
        "success_rate": 0.5
    },
    "created_indexes": [
        {
            "index_name": "idx_auto_title_production_year_0",
            "table_name": "title",
            "columns": ["production_year"],
            "actual_size_mb": 45.50,
            "status": "created"
        }
    ],
    "failed_indexes": [
        {
            "table_name": "movie_info",
            "columns": ["info_type_id"],
            "reason": "Index already exists"
        }
    ]
}
```

### 3. Cost-Based Index Recommendations

Generate index recommendations based on query costs and benefits.

**Endpoint**: `POST /index/recommendations/cost_based`

**Request Body**:
```json
{
    "workload_queries": [
        "SELECT * FROM title WHERE production_year = 2020",
        "SELECT * FROM cast_info WHERE person_id = 12345"
    ],
    "schema_info": {
        "tables": ["title", "cast_info"],
        "workload_type": "mixed"
    },
    "time_limit_seconds": 30
}
```

**Example**:
```bash
curl -X POST http://localhost:8777/index/recommendations/cost_based \
  -H "Content-Type: application/json" \
  -d '{
    "workload_queries": ["SELECT * FROM title WHERE production_year = 2020"],
    "database_name": "imdb_ori"
  }'
```

## Database Extension Functions

### Storage Management Functions

#### Get Current Index Storage
```sql
-- Returns total size of all indexes in MB
SELECT nr_get_current_index_storage_mb();

-- Example output: 3908.6875
```

#### Calculate Index Budget
```sql
-- Returns maximum allowed index storage (50% of database size)
SELECT nr_calculate_index_budget_mb();

-- Example output: 4586.42015171051
```

#### Get Database Size
```sql
-- Returns total database size in MB
SELECT nr_get_database_size_mb();
```

### Index Management Functions

#### Create Index with Budget Check
```sql
-- Attempts to create an index if within budget
SELECT nr_create_index_if_budget_allows(
    'idx_title_year',           -- index name
    'title',                    -- table name
    ARRAY['production_year'],   -- columns
    'btree'                     -- index type
);

-- Returns: true if created, false if budget exceeded or auto-creation disabled
```

#### Drop Index If Exists
```sql
-- Safely drops an index if it exists
SELECT nr_drop_index_if_exists('idx_title_year');

-- Returns: true if dropped, false if didn't exist
```

#### Get Index Storage Statistics
```sql
-- Returns table of all indexes with sizes and usage
SELECT * FROM nr_get_index_storage_stats();

-- Or query the view directly
SELECT * FROM nr_index_storage_usage ORDER BY size_mb DESC;
```

#### Auto-Create Multiple Indexes
```sql
-- Auto-create multiple indexes from JSON definition
SELECT * FROM nr_auto_create_indexes(
    '[{"table_name": "title", "columns": ["production_year"], "index_type": "btree"}]'::jsonb
);
```

## SQL Query Examples

### 1. Check Index Storage Usage

```sql
-- Top 20 largest indexes
SELECT
    schemaname,
    indexrelname as index_name,
    pg_size_pretty(pg_relation_size(indexrelid)) as index_size
FROM pg_stat_user_indexes
ORDER BY pg_relation_size(indexrelid) DESC
LIMIT 20;
```

### 2. Find Missing Indexes (Based on Sequential Scans)

```sql
-- Tables with high sequential scan counts might need indexes
SELECT
    schemaname,
    tablename,
    seq_scan,
    seq_tup_read,
    idx_scan
FROM pg_stat_user_tables
WHERE seq_scan > 1000
ORDER BY seq_scan DESC;
```

### 3. Check Index Usage Effectiveness

```sql
-- Indexes with low usage might be candidates for removal
SELECT
    schemaname,
    indexrelname,
    idx_scan,
    pg_size_pretty(pg_relation_size(indexrelid)) as size
FROM pg_stat_user_indexes
WHERE idx_scan < 10
ORDER BY pg_relation_size(indexrelid) DESC;
```

### 4. Calculate Index Size Distribution

```sql
-- Distribution of index sizes across tables
SELECT
    tablename,
    COUNT(*) as index_count,
    SUM(pg_relation_size(indexrelid)) / (1024*1024) as total_size_mb
FROM pg_stat_user_indexes
GROUP BY tablename
ORDER BY total_size_mb DESC;
```

### 5. Find Redundant Indexes

```sql
-- Find indexes on same table with similar column sets
SELECT
    tablename,
    indexrelname,
    pg_get_indexdef(indexrelid)
FROM pg_stat_user_indexes
WHERE tablename IN (
    SELECT tablename
    FROM pg_stat_user_indexes
    GROUP BY tablename
    HAVING COUNT(*) > 3
)
ORDER BY tablename, indexrelname;
```

## Workload Analysis Queries

### 1. Capture Slow Queries for Analysis

```sql
-- Find queries taking more than 1 second
SELECT
    query,
    calls,
    total_time / 1000 as total_seconds,
    mean_time / 1000 as mean_seconds
FROM pg_stat_statements
WHERE mean_time > 1000
ORDER BY mean_time DESC
LIMIT 10;
```

### 2. Identify Frequently Accessed Tables

```sql
-- Tables with highest access frequency
SELECT
    schemaname,
    tablename,
    n_tup_ins + n_tup_upd + n_tup_del as total_modifications,
    n_live_tup as row_count
FROM pg_stat_user_tables
ORDER BY total_modifications DESC
LIMIT 10;
```

### 3. Analyze Join Patterns

```sql
-- Common join patterns from query logs (requires query logging)
SELECT
    query,
    calls,
    total_time
FROM pg_stat_statements
WHERE query LIKE '%JOIN%'
ORDER BY calls DESC
LIMIT 10;
```

## Complete Workflow Example

### Step 1: Analyze Current Storage

```sql
-- Check current storage usage
SELECT
    nr_get_current_index_storage_mb() as current_mb,
    nr_calculate_index_budget_mb() as budget_mb,
    nr_get_database_size_mb() as database_size_mb;
```

### Step 2: Identify Slow Queries

```sql
-- Find queries that might benefit from indexes
SELECT query, mean_time, calls
FROM pg_stat_statements
WHERE query LIKE '%WHERE%' AND mean_time > 100
ORDER BY mean_time DESC;
```

### Step 3: Test AI Engine Recommendations

```bash
# Send slow queries to AI engine
curl -X POST http://localhost:8777/index/recommendations/cost_based \
  -d '{
    "workload_queries": ["SLOW_QUERY_1", "SLOW_QUERY_2"],
    "database_name": "imdb_ori"
  }'
```

### Step 4: Auto-Create Recommended Indexes

```bash
# Let AI engine create indexes within budget
curl -X POST http://localhost:8777/index/auto_create \
  -d '{
    "workload_queries": ["SLOW_QUERY_1", "SLOW_QUERY_2"],
    "database_name": "imdb_ori"
  }'
```

### Step 5: Verify Performance Improvement

```sql
-- Check if queries are faster
SELECT query, mean_time
FROM pg_stat_statements
WHERE query = 'SLOW_QUERY_1';

-- Verify storage usage is within budget
SELECT nr_get_current_index_storage_mb();
```

## Configuration

### GUC Parameters

Set these parameters in `postgresql.conf`:

```conf
# Maximum index storage in MB (0 = auto-calculate as 50% of DB size)
nr_max_index_storage_mb = 0

# Enable automatic index creation
nr_enable_auto_index_creation = off

# Preload libraries (required)
shared_preload_libraries = 'pg_hint_plan,nr_index_management'
```

Or set at session level:
```sql
SET nr_max_index_storage_mb = 5000.0;
SET nr_enable_auto_index_creation = on;
```

### Environment Variables

```bash
# AI engine configuration
export MOQOE_ENV_PATH=/path/to/moqoe/environment
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=imdb_ori
export DB_USER=neurdb
```

## Troubleshooting

### Common Issues

1. **Extension not found**
   ```sql
   -- Check if extension is installed
   SELECT * FROM pg_available_extensions WHERE name = 'nr_index_management';

   -- If not found, check library path
   SHOW shared_preload_libraries;
   ```

2. **Budget calculation returning 0**
   ```sql
   -- This is normal for empty databases
   -- Budget = 50% of database size, so new databases have small budgets
   SELECT nr_get_database_size_mb();
   ```

3. **AI engine connection refused**
   ```bash
   # Check if AI engine is running
   curl http://localhost:8777/health

   # Check PostgreSQL is accepting connections
   psql -h localhost -p 5432 -U neurdb -d imdb_ori
   ```

4. **No recommendations generated**
   ```bash
   # This usually means:
   # 1. Queries already have optimal indexes
   # 2. Workload patterns are not clear
   # 3. HypoPG extension is not available

   -- Check if hypopg is available
   SELECT * FROM pg_available_extensions WHERE name = 'hypopg';
   ```

## Performance Tips

1. **Regular Monitoring**: Check storage stats weekly
   ```bash
   curl "http://localhost:8777/index/storage_stats?database=imdb_ori&user=neurdb"
   ```

2. **Seasonal Analysis**: Run workload analysis during peak hours
   ```bash
   # Capture queries during peak load
   curl -X POST http://localhost:8777/ingest_query \
     -d '{"query": "SELECT ...", "timestamp": "2025-12-08T10:00:00Z"}'
   ```

3. **Budget Alerts**: Set up alerts when budget usage exceeds 80%
   ```sql
   -- In your monitoring system
   SELECT nr_get_current_index_storage_mb() / nr_calculate_index_budget_mb() > 0.8;
   ```

4. **Index Cleanup**: Regularly review low-usage indexes
   ```sql
   -- Find indexes with zero scans
   SELECT * FROM pg_stat_user_indexes WHERE idx_scan = 0;
   ```

## References

- [Workload Forecast Plan](../plans/workload_forecast_plan.md) - Complete implementation roadmap
- [PostgreSQL Documentation](https://www.postgresql.org/docs/current/indexes.html) - Index concepts
- [HypoPG Documentation](https://hypopg.readthedocs.io/) - Hypothetical indexes for testing

## Support

For issues and questions:
1. Check logs at `/tmp/neurdb_logs/workload_forecast.log`
2. Review query plans with `EXPLAIN (ANALYZE, BUFFERS) your_query;`
3. Test recommendations with HypoPG before applying
4. Monitor storage with both AI engine and extension functions
