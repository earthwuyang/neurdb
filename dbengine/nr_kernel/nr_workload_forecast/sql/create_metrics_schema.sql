-- NeurDB Metrics Schema
-- Schema for storing workload and performance metrics

-- Create metrics schema
CREATE SCHEMA IF NOT EXISTS neurdb_metrics;

-- Template definitions
CREATE TABLE IF NOT EXISTS neurdb_metrics.query_templates (
    template_hash VARCHAR(32) PRIMARY KEY,
    template_text TEXT NOT NULL,
    first_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    total_executions BIGINT DEFAULT 0,
    avg_execution_time_ms FLOAT DEFAULT 0.0,
    max_execution_time_ms FLOAT DEFAULT 0.0,
    min_execution_time_ms FLOAT DEFAULT 0.0
);

-- Index for template lookups
CREATE INDEX IF NOT EXISTS idx_query_templates_last_seen
ON neurdb_metrics.query_templates(last_seen);

-- Workload time series
CREATE TABLE IF NOT EXISTS neurdb_metrics.workload_timeseries (
    id SERIAL PRIMARY KEY,
    template_hash VARCHAR(32) NOT NULL REFERENCES neurdb_metrics.query_templates(template_hash),
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    execution_count INTEGER NOT NULL DEFAULT 1,
    avg_duration_ms FLOAT,
    p95_duration_ms FLOAT,
    total_duration_ms FLOAT
);

-- Indexes for time series queries
CREATE INDEX IF NOT EXISTS idx_workload_ts_timestamp
ON neurdb_metrics.workload_timeseries(timestamp);
CREATE INDEX IF NOT EXISTS idx_workload_ts_template
ON neurdb_metrics.workload_timeseries(template_hash, timestamp);

-- Cluster assignments
CREATE TABLE IF NOT EXISTS neurdb_metrics.template_clusters (
    cluster_id INTEGER NOT NULL,
    template_hash VARCHAR(32) PRIMARY KEY REFERENCES neurdb_metrics.query_templates(template_hash),
    similarity_score FLOAT NOT NULL,
    cluster_created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    cluster_updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Index for cluster queries
CREATE INDEX IF NOT EXISTS idx_cluster_id
ON neurdb_metrics.template_clusters(cluster_id);

-- Index recommendations
CREATE TABLE IF NOT EXISTS neurdb_metrics.index_recommendations (
    rec_id SERIAL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    table_schema VARCHAR(64) NOT NULL DEFAULT 'public',
    table_name VARCHAR(64) NOT NULL,
    columns TEXT[] NOT NULL,
    index_type VARCHAR(20) NOT NULL DEFAULT 'btree',
    benefit_score FLOAT NOT NULL,
    estimated_size_mb FLOAT,
    creation_cost FLOAT DEFAULT 0.0,
    maintenance_cost FLOAT DEFAULT 0.0,
    sql_statement TEXT NOT NULL,
    recommendation_source VARCHAR(32) DEFAULT 'workload_forecast', -- 'workload_forecast', 'cost_based', 'manual'
    confidence_score FLOAT DEFAULT 0.5,
    risk_level VARCHAR(10) DEFAULT 'MEDIUM', -- 'LOW', 'MEDIUM', 'HIGH'
    applied_at TIMESTAMP,
    performance_improvement FLOAT,
    status VARCHAR(20) DEFAULT 'PENDING' -- 'PENDING', 'APPROVED', 'APPLIED', 'REJECTED', 'FAILED'
);

-- Indexes for recommendations
CREATE INDEX IF NOT EXISTS idx_index_rec_created
ON neurdb_metrics.index_recommendations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_index_rec_table
ON neurdb_metrics.index_recommendations(table_schema, table_name);
CREATE INDEX IF NOT EXISTS idx_index_rec_status
ON neurdb_metrics.index_recommendations(status);

-- Applied indexes tracking
CREATE TABLE IF NOT EXISTS neurdb_metrics.applied_indexes (
    index_name VARCHAR(128) PRIMARY KEY,
    table_schema VARCHAR(64) NOT NULL DEFAULT 'public',
    table_name VARCHAR(64) NOT NULL,
    columns TEXT[] NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW(),
    recommended_by VARCHAR(32),  -- 'workload_forecast' or 'cost_based' or manual
    recommendation_id INTEGER REFERENCES neurdb_metrics.index_recommendations(rec_id),
    usage_count BIGINT DEFAULT 0,
    last_used TIMESTAMP,
    size_mb FLOAT DEFAULT 0.0,
    index_type VARCHAR(20) DEFAULT 'btree',
    performance_impact FLOAT DEFAULT 0.0,
    status VARCHAR(20) DEFAULT 'ACTIVE' -- 'ACTIVE', 'UNUSED', 'DROPPED'
);

-- Indexes for applied indexes
CREATE INDEX IF NOT EXISTS idx_applied_idx_table
ON neurdb_metrics.applied_indexes(table_schema, table_name);
CREATE INDEX IF NOT EXISTS idx_applied_idx_usage
ON neurdb_metrics.applied_indexes(usage_count DESC);
CREATE INDEX IF NOT EXISTS idx_applied_idx_status
ON neurdb_metrics.applied_indexes(status);

-- Workload aggregation table for faster queries
CREATE TABLE IF NOT EXISTS neurdb_metrics.workload_summary (
    hour_bucket TIMESTAMP PRIMARY KEY,
    total_queries BIGINT DEFAULT 0,
    unique_templates INTEGER DEFAULT 0,
    avg_execution_time_ms FLOAT DEFAULT 0.0,
    slow_queries_count INTEGER DEFAULT 0,
    top_template_hash VARCHAR(32),
    top_template_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Index for summary queries
CREATE INDEX IF NOT EXISTS idx_workload_summary_hour
ON neurdb_metrics.workload_summary(hour_bucket DESC);

-- Functions for updating aggregated data

-- Function to update template statistics
CREATE OR REPLACE FUNCTION neurdb_metrics.update_template_stats(
    p_template_hash VARCHAR(32),
    p_template_text TEXT,
    p_execution_time_ms FLOAT
) RETURNS void AS $$
BEGIN
    INSERT INTO neurdb_metrics.query_templates
        (template_hash, template_text, last_seen, total_executions, avg_execution_time_ms,
         max_execution_time_ms, min_execution_time_ms)
    VALUES
        (p_template_hash, p_template_text, NOW(), 1, p_execution_time_ms,
         p_execution_time_ms, p_execution_time_ms)
    ON CONFLICT (template_hash) DO UPDATE SET
        last_seen = NOW(),
        total_executions = query_templates.total_executions + 1,
        avg_execution_time_ms = (
            (query_templates.avg_execution_time_ms * query_templates.total_executions + p_execution_time_ms) /
            (query_templates.total_executions + 1)
        ),
        max_execution_time_ms = GREATEST(query_templates.max_execution_time_ms, p_execution_time_ms),
        min_execution_time_ms = LEAST(query_templates.min_execution_time_ms, p_execution_time_ms);
END;
$$ LANGUAGE plpgsql;

-- Function to record workload time series data
CREATE OR REPLACE FUNCTION neurdb_metrics.record_workload_data(
    p_template_hash VARCHAR(32),
    p_execution_time_ms FLOAT
) RETURNS void AS $$
BEGIN
    INSERT INTO neurdb_metrics.workload_timeseries
        (template_hash, execution_count, avg_duration_ms, total_duration_ms)
    VALUES
        (p_template_hash, 1, p_execution_time_ms, p_execution_time_ms)
    ON CONFLICT (id) DO NOTHING; -- Let this fail naturally as we have SERIAL PK
END;
$$ LANGUAGE plpgsql;

-- Function to get workload statistics
CREATE OR REPLACE FUNCTION neurdb_metrics.get_workload_stats(
    p_hours_back INTEGER DEFAULT 24
) RETURNS TABLE (
    total_queries BIGINT,
    unique_templates INTEGER,
    avg_execution_time_ms FLOAT,
    top_templates TEXT[]
) AS $$
DECLARE
    v_start_time TIMESTAMP := NOW() - (p_hours_back || ' hours')::INTERVAL;
BEGIN
    RETURN QUERY
    SELECT
        COUNT(wts.id)::BIGINT as total_queries,
        COUNT(DISTINCT wts.template_hash)::INTEGER as unique_templates,
        AVG(wts.avg_duration_ms) as avg_execution_time_ms,
        ARRAY_AGG(
            qt.template_text || ' (' || wts.template_hash || ')'
            ORDER BY wts.execution_count DESC
        ) as top_templates
    FROM neurdb_metrics.workload_timeseries wts
    JOIN neurdb_metrics.query_templates qt ON wts.template_hash = qt.template_hash
    WHERE wts.timestamp >= v_start_time;
END;
$$ LANGUAGE plpgsql;

-- Function to recommend an index
CREATE OR REPLACE FUNCTION neurdb_metrics.recommend_index(
    p_table_name VARCHAR(64),
    p_columns TEXT[],
    p_benefit_score FLOAT,
    p_sql_statement TEXT,
    p_table_schema VARCHAR(64) DEFAULT 'public',
    p_index_type VARCHAR(20) DEFAULT 'btree',
    p_source VARCHAR(32) DEFAULT 'workload_forecast',
    p_confidence_score FLOAT DEFAULT 0.5,
    p_estimated_size_mb FLOAT DEFAULT NULL
) RETURNS INTEGER AS $$
DECLARE
    v_rec_id INTEGER;
BEGIN
    INSERT INTO neurdb_metrics.index_recommendations
        (table_schema, table_name, columns, index_type, benefit_score,
         sql_statement, recommendation_source, confidence_score, estimated_size_mb)
    VALUES
        (p_table_schema, p_table_name, p_columns, p_index_type, p_benefit_score,
         p_sql_statement, p_source, p_confidence_score, p_estimated_size_mb)
    RETURNING rec_id INTO v_rec_id;

    RETURN v_rec_id;
END;
$$ LANGUAGE plpgsql;

-- Function to mark index as applied
CREATE OR REPLACE FUNCTION neurdb_metrics.apply_index_recommendation(
    p_rec_id INTEGER,
    p_index_name VARCHAR(128),
    p_actual_size_mb FLOAT DEFAULT NULL
) RETURNS BOOLEAN AS $$
DECLARE
    v_rec RECORD;
BEGIN
    -- Get recommendation details
    SELECT * INTO v_rec
    FROM neurdb_metrics.index_recommendations
    WHERE rec_id = p_rec_id AND status = 'PENDING';

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    -- Update recommendation status
    UPDATE neurdb_metrics.index_recommendations
    SET status = 'APPLIED', applied_at = NOW()
    WHERE rec_id = p_rec_id;

    -- Insert into applied indexes
    INSERT INTO neurdb_metrics.applied_indexes
        (index_name, table_schema, table_name, columns, recommended_by,
         recommendation_id, size_mb, index_type)
    VALUES
        (p_index_name, v_rec.table_schema, v_rec.table_name, v_rec.columns,
         v_rec.recommendation_source, p_rec_id,
         COALESCE(p_actual_size_mb, v_rec.estimated_size_mb), v_rec.index_type);

    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Function to get applied indexes performance
CREATE OR REPLACE FUNCTION neurdb_metrics.get_applied_indexes_performance()
RETURNS TABLE (
    index_name VARCHAR(128),
    table_name VARCHAR(64),
    usage_count BIGINT,
    size_mb FLOAT,
    performance_impact FLOAT,
    days_since_creation INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ai.index_name,
        ai.table_name,
        ai.usage_count,
        ai.size_mb,
        ai.performance_impact,
        EXTRACT(DAYS FROM NOW() - ai.applied_at)::INTEGER as days_since_creation
    FROM neurdb_metrics.applied_indexes ai
    WHERE ai.status = 'ACTIVE'
    ORDER BY ai.usage_count DESC;
END;
$$ LANGUAGE plpgsql;

-- Function to clean up old data
CREATE OR REPLACE FUNCTION neurdb_metrics.cleanup_old_data(
    p_days_to_keep INTEGER DEFAULT 90
) RETURNS INTEGER AS $$
DECLARE
    v_cutoff_date TIMESTAMP := NOW() - (p_days_to_keep || ' days')::INTERVAL;
    v_deleted_count INTEGER := 0;
BEGIN
    -- Clean up old time series data
    DELETE FROM neurdb_metrics.workload_timeseries
    WHERE timestamp < v_cutoff_date;
    GET DIAGNOSTICS v_deleted_count = ROW_COUNT;

    -- Clean up old workload summaries
    DELETE FROM neurdb_metrics.workload_summary
    WHERE hour_bucket < v_cutoff_date;

    RETURN v_deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Grant permissions to neurdb user
GRANT USAGE ON SCHEMA neurdb_metrics TO neurdb;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA neurdb_metrics TO neurdb;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA neurdb_metrics TO neurdb;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA neurdb_metrics TO neurdb;

-- Set default privileges for future objects
ALTER DEFAULT PRIVILEGES IN SCHEMA neurdb_metrics GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO neurdb;
ALTER DEFAULT PRIVILEGES IN SCHEMA neurdb_metrics GRANT EXECUTE ON FUNCTIONS TO neurdb;
ALTER DEFAULT PRIVILEGES IN SCHEMA neurdb_metrics GRANT USAGE, SELECT ON SEQUENCES TO neurdb;