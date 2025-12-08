-- Schema for NeurDB Workload Forecast Metrics
-- Based on QueryBot5000 schema with enhanced clustering support

-- Drop existing schema if it exists (for development)
DROP SCHEMA IF EXISTS neurdb_metrics CASCADE;

-- Create schema
CREATE SCHEMA IF NOT EXISTS neurdb_metrics;

-- ============================================================================
-- Query Templates
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.query_templates (
    template_hash VARCHAR(32) PRIMARY KEY,
    template_text TEXT NOT NULL,
    first_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    total_executions BIGINT DEFAULT 0,
    avg_execution_time_ms FLOAT DEFAULT 0.0,
    max_execution_time_ms FLOAT DEFAULT 0.0,
    min_execution_time_ms FLOAT DEFAULT 0.0,
    -- Enhanced template features
    query_type VARCHAR(50) DEFAULT 'SELECT',
    complexity_score INTEGER DEFAULT 0,
    has_aggregation BOOLEAN DEFAULT FALSE,
    has_subquery BOOLEAN DEFAULT FALSE,
    has_cte BOOLEAN DEFAULT FALSE,
    tables_used TEXT[],  -- Array of table names
    columns_accessed TEXT[],  -- Array of column names
    join_types TEXT[],  -- Types of joins used
    logical_features JSONB,  -- Additional logical features
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes for query templates
CREATE INDEX idx_query_templates_first_seen ON neurdb_metrics.query_templates(first_seen);
CREATE INDEX idx_query_templates_last_seen ON neurdb_metrics.query_templates(last_seen);
CREATE INDEX idx_query_templates_query_type ON neurdb_metrics.query_templates(query_type);
CREATE INDEX idx_query_templates_tables_used ON neurdb_metrics.query_templates USING GIN(tables_used);
CREATE INDEX idx_query_templates_complexity ON neurdb_metrics.query_templates(complexity_score);

-- ============================================================================
-- Workload Time Series
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.workload_timeseries (
    id BIGSERIAL PRIMARY KEY,
    template_hash VARCHAR(32) NOT NULL REFERENCES neurdb_metrics.query_templates(template_hash) ON DELETE CASCADE,
    timestamp TIMESTAMP NOT NULL,
    execution_count INTEGER NOT NULL DEFAULT 1,
    total_duration_ms FLOAT DEFAULT 0.0,
    avg_duration_ms FLOAT DEFAULT 0.0,
    p95_duration_ms FLOAT DEFAULT 0.0,
    p99_duration_ms FLOAT DEFAULT 0.0,
    error_count INTEGER DEFAULT 0,
    time_window_minutes INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Partitioning by month for performance (optional, comment out if not needed)
-- Uncomment the following for partitioned table setup:
/*
CREATE TABLE workload_timeseries_y2024m01 PARTITION OF neurdb_metrics.workload_timeseries
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE workload_timeseries_y2024m02 PARTITION OF neurdb_metrics.workload_timeseries
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
-- Add more partitions as needed
*/

-- Indexes for time series
CREATE INDEX idx_workload_ts_timestamp ON neurdb_metrics.workload_timeseries(timestamp);
CREATE INDEX idx_workload_ts_template_timestamp ON neurdb_metrics.workload_timeseries(template_hash, timestamp);
CREATE INDEX idx_workload_ts_execution_count ON neurdb_metrics.workload_timeseries(execution_count);

-- ============================================================================
-- Template Clusters
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.template_clusters (
    cluster_id SERIAL PRIMARY KEY,
    cluster_name VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_updated TIMESTAMP NOT NULL DEFAULT NOW(),
    cluster_status VARCHAR(20) DEFAULT 'ACTIVE',  -- ACTIVE, INACTIVE, MERGED
    parent_cluster_id INTEGER REFERENCES neurdb_metrics.template_clusters(cluster_id),
    similarity_threshold DECIMAL(3,2) DEFAULT 0.80,
    min_cluster_size INTEGER DEFAULT 3,
    clustering_algorithm VARCHAR(50) DEFAULT 'ONLINE_COSINE',
    clustering_parameters JSONB,
    -- Cluster statistics
    total_templates INTEGER DEFAULT 0,
    total_queries BIGINT DEFAULT 0,
    avg_queries_per_interval FLOAT DEFAULT 0.0,
    cluster_quality_score DECIMAL(3,2) DEFAULT 0.0,
    stability_score DECIMAL(3,2) DEFAULT 0.0,
    -- Cluster metadata
    dominant_query_type VARCHAR(50),
    common_tables TEXT[],
    complexity_distribution JSONB,
    created_from_template_hash VARCHAR(32)
);

-- Indexes for clusters
CREATE INDEX idx_template_clusters_status ON neurdb_metrics.template_clusters(cluster_status);
CREATE INDEX idx_template_clusters_created_at ON neurdb_metrics.template_clusters(created_at);
CREATE INDEX idx_template_clusters_parent ON neurdb_metrics.template_clusters(parent_cluster_id);

-- ============================================================================
-- Template Cluster Assignments
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.template_cluster_assignments (
    assignment_id BIGSERIAL PRIMARY KEY,
    template_hash VARCHAR(32) NOT NULL REFERENCES neurdb_metrics.query_templates(template_hash) ON DELETE CASCADE,
    cluster_id INTEGER NOT NULL REFERENCES neurdb_metrics.template_clusters(cluster_id) ON DELETE CASCADE,
    assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    unassigned_at TIMESTAMP,
    similarity_score DECIMAL(5,4),
    confidence_score DECIMAL(3,2),
    assignment_reason VARCHAR(100),  -- NEW_CLUSTER, SIMILARITY, MERGE
    -- Historical tracking
    is_current BOOLEAN DEFAULT TRUE,
    previous_cluster_id INTEGER REFERENCES neurdb_metrics.template_clusters(cluster_id),
    assignment_metadata JSONB
);

-- Indexes for assignments
CREATE INDEX idx_cluster_assignments_template ON neurdb_metrics.template_cluster_assignments(template_hash);
CREATE INDEX idx_cluster_assignments_cluster ON neurdb_metrics.template_cluster_assignments(cluster_id);
CREATE INDEX idx_cluster_assignments_current ON neurdb_metrics.template_cluster_assignments(is_current) WHERE is_current = TRUE;
CREATE INDEX idx_cluster_assignments_assigned_at ON neurdb_metrics.template_cluster_assignments(assigned_at);

-- ============================================================================
-- Cluster Time Series
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.cluster_timeseries (
    id BIGSERIAL PRIMARY KEY,
    cluster_id INTEGER NOT NULL REFERENCES neurdb_metrics.template_clusters(cluster_id) ON DELETE CASCADE,
    timestamp TIMESTAMP NOT NULL,
    total_query_count BIGINT DEFAULT 0,
    active_template_count INTEGER DEFAULT 0,
    avg_similarity_score DECIMAL(5,4),
    cluster_quality_score DECIMAL(3,2),
    predicted_query_count BIGINT,
    prediction_error DECIMAL(5,2),
    time_window_minutes INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes for cluster time series
CREATE INDEX idx_cluster_ts_timestamp ON neurdb_metrics.cluster_timeseries(timestamp);
CREATE INDEX idx_cluster_ts_cluster_timestamp ON neurdb_metrics.cluster_timeseries(cluster_id, timestamp);

-- ============================================================================
-- Clustering Sessions
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.clustering_sessions (
    session_id BIGSERIAL PRIMARY KEY,
    session_timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    clustering_algorithm VARCHAR(50) DEFAULT 'ONLINE_COSINE',
    parameters JSONB,
    input_templates_count INTEGER,
    output_clusters_count INTEGER,
    unassigned_templates_count INTEGER,
    merged_clusters_count INTEGER,
    new_clusters_count INTEGER,
    session_duration_ms INTEGER,
    quality_metrics JSONB,
    status VARCHAR(20) DEFAULT 'COMPLETED',  -- RUNNING, COMPLETED, FAILED
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes for sessions
CREATE INDEX idx_clustering_sessions_timestamp ON neurdb_metrics.clustering_sessions(session_timestamp);
CREATE INDEX idx_clustering_sessions_status ON neurdb_metrics.clustering_sessions(status);

-- ============================================================================
-- Cluster Similarity Matrix
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.cluster_similarity_matrix (
    similarity_id BIGSERIAL PRIMARY KEY,
    cluster1_id INTEGER NOT NULL REFERENCES neurdb_metrics.template_clusters(cluster_id) ON DELETE CASCADE,
    cluster2_id INTEGER NOT NULL REFERENCES neurdb_metrics.template_clusters(cluster_id) ON DELETE CASCADE,
    similarity_score DECIMAL(5,4) NOT NULL,
    common_templates_count INTEGER DEFAULT 0,
    computed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    similarity_algorithm VARCHAR(50) DEFAULT 'COSINE',
    time_period_days INTEGER DEFAULT 30,
    CHECK (cluster1_id < cluster2_id)  -- Prevent duplicates
);

-- Indexes for similarity matrix
CREATE INDEX idx_cluster_similarity_cluster1 ON neurdb_metrics.cluster_similarity_matrix(cluster1_id);
CREATE INDEX idx_cluster_similarity_cluster2 ON neurdb_metrics.cluster_similarity_matrix(cluster2_id);
CREATE INDEX idx_cluster_similarity_score ON neurdb_metrics.cluster_similarity_matrix(similarity_score);

-- ============================================================================
-- Template Evolution Tracking
-- ============================================================================

CREATE TABLE IF NOT EXISTS neurdb_metrics.template_evolution (
    evolution_id BIGSERIAL PRIMARY KEY,
    template_hash VARCHAR(32) NOT NULL REFERENCES neurdb_metrics.query_templates(template_hash) ON DELETE CASCADE,
    event_timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,  -- CREATED, MODIFIED, CLUSTER_ASSIGNED, CLUSTER_UNASSIGNED, CLUSTER_MERGED
    old_cluster_id INTEGER REFERENCES neurdb_metrics.template_clusters(cluster_id),
    new_cluster_id INTEGER REFERENCES neurdb_metrics.template_clusters(cluster_id),
    similarity_score DECIMAL(5,4),
    reason TEXT,
    event_metadata JSONB
);

-- Indexes for evolution tracking
CREATE INDEX idx_template_evolution_template ON neurdb_metrics.template_evolution(template_hash);
CREATE INDEX idx_template_evolution_timestamp ON neurdb_metrics.template_evolution(event_timestamp);
CREATE INDEX idx_template_evolution_type ON neurdb_metrics.template_evolution(event_type);

-- ============================================================================
-- Views for Common Queries
-- ============================================================================

-- Current cluster assignments view
CREATE OR REPLACE VIEW neurdb_metrics.current_cluster_assignments AS
SELECT
    tca.template_hash,
    tca.cluster_id,
    tc.cluster_name,
    tca.assigned_at,
    tca.similarity_score,
    tca.confidence_score,
    qt.template_text,
    qt.query_type,
    qt.complexity_score,
    qt.tables_used
FROM neurdb_metrics.template_cluster_assignments tca
JOIN neurdb_metrics.template_clusters tc ON tca.cluster_id = tc.cluster_id
JOIN neurdb_metrics.query_templates qt ON tca.template_hash = qt.template_hash
WHERE tca.is_current = TRUE AND tc.cluster_status = 'ACTIVE';

-- Cluster summary view
CREATE OR REPLACE VIEW neurdb_metrics.cluster_summary AS
SELECT
    tc.cluster_id,
    tc.cluster_name,
    tc.created_at,
    tc.total_templates,
    tc.total_queries,
    tc.avg_queries_per_interval,
    tc.cluster_quality_score,
    tc.dominant_query_type,
    tc.common_tables,
    COUNT(tca.template_hash) as current_template_count,
    SUM(qt.total_executions) as actual_total_queries
FROM neurdb_metrics.template_clusters tc
LEFT JOIN neurdb_metrics.template_cluster_assignments tca
    ON tc.cluster_id = tca.cluster_id AND tca.is_current = TRUE
LEFT JOIN neurdb_metrics.query_templates qt
    ON tca.template_hash = qt.template_hash
WHERE tc.cluster_status = 'ACTIVE'
GROUP BY tc.cluster_id, tc.cluster_name, tc.created_at, tc.total_templates,
         tc.total_queries, tc.avg_queries_per_interval, tc.cluster_quality_score,
         tc.dominant_query_type, tc.common_tables;

-- Recent workload activity view
CREATE OR REPLACE VIEW neurdb_metrics.recent_workload_activity AS
SELECT
    wt.timestamp,
    wt.template_hash,
    wt.execution_count,
    wt.avg_duration_ms,
    qt.template_text,
    qt.query_type,
    COALESCE(tca.cluster_id, -1) as cluster_id,
    COALESCE(tc.cluster_name, 'UNASSIGNED') as cluster_name
FROM neurdb_metrics.workload_timeseries wt
JOIN neurdb_metrics.query_templates qt ON wt.template_hash = qt.template_hash
LEFT JOIN neurdb_metrics.template_cluster_assignments tca
    ON wt.template_hash = tca.template_hash AND tca.is_current = TRUE
LEFT JOIN neurdb_metrics.template_clusters tc
    ON tca.cluster_id = tc.cluster_id
WHERE wt.timestamp >= NOW() - INTERVAL '24 hours'
ORDER BY wt.timestamp DESC;

-- ============================================================================
-- Functions and Procedures
-- ============================================================================

-- Function to update template statistics
CREATE OR REPLACE FUNCTION neurdb_metrics.update_template_stats(
    p_template_hash VARCHAR(32),
    p_execution_count INTEGER,
    p_avg_duration_ms FLOAT
) RETURNS VOID AS $$
BEGIN
    UPDATE neurdb_metrics.query_templates
    SET
        total_executions = total_executions + p_execution_count,
        avg_execution_time_ms = (avg_execution_time_ms * total_executions + p_avg_duration_ms * p_execution_count) / (total_executions + p_execution_count),
        max_execution_time_ms = GREATEST(max_execution_time_ms, p_avg_duration_ms),
        last_seen = NOW(),
        updated_at = NOW()
    WHERE template_hash = p_template_hash;
END;
$$ LANGUAGE plpgsql;

-- Function to get cluster evolution over time
CREATE OR REPLACE FUNCTION neurdb_metrics.get_cluster_evolution(
    p_days_back INTEGER DEFAULT 7
) RETURNS TABLE (
    cluster_id INTEGER,
    date_trunc TIMESTAMP,
    template_count INTEGER,
    query_count BIGINT,
    avg_similarity DECIMAL(5,4)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        tca.cluster_id,
        DATE_TRUNC('day', tca.assigned_at) as date_trunc,
        COUNT(*) as template_count,
        COALESCE(SUM(qt.total_executions), 0) as query_count,
        AVG(tca.similarity_score) as avg_similarity
    FROM neurdb_metrics.template_cluster_assignments tca
    LEFT JOIN neurdb_metrics.query_templates qt ON tca.template_hash = qt.template_hash
    WHERE tca.assigned_at >= NOW() - INTERVAL '1 day' * p_days_back
    GROUP BY tca.cluster_id, DATE_TRUNC('day', tca.assigned_at)
    ORDER BY date_trunc DESC, cluster_id;
END;
$$ LANGUAGE plpgsql;

-- Function to clean up old data (maintenance)
CREATE OR REPLACE FUNCTION neurdb_metrics.cleanup_old_data(
    p_days_to_keep INTEGER DEFAULT 90
) RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Delete old workload time series
    DELETE FROM neurdb_metrics.workload_timeseries
    WHERE timestamp < NOW() - INTERVAL '1 day' * p_days_to_keep;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    -- Delete old cluster time series
    DELETE FROM neurdb_metrics.cluster_timeseries
    WHERE timestamp < NOW() - INTERVAL '1 day' * p_days_to_keep;

    -- Delete old similarity matrix entries
    DELETE FROM neurdb_metrics.cluster_similarity_matrix
    WHERE computed_at < NOW() - INTERVAL '1 day' * p_days_to_keep;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Triggers for Automatic Updates
-- ============================================================================

-- Trigger to update updated_at timestamp on query_templates
CREATE OR REPLACE FUNCTION neurdb_metrics.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_query_templates_updated_at
    BEFORE UPDATE ON neurdb_metrics.query_templates
    FOR EACH ROW
    EXECUTE FUNCTION neurdb_metrics.update_updated_at_column();

-- Trigger to update cluster statistics
CREATE OR REPLACE FUNCTION neurdb_metrics.update_cluster_stats()
RETURNS TRIGGER AS $$
BEGIN
    -- Update cluster template count
    UPDATE neurdb_metrics.template_clusters
    SET
        total_templates = (
            SELECT COUNT(*)
            FROM neurdb_metrics.template_cluster_assignments
            WHERE cluster_id = NEW.cluster_id AND is_current = TRUE
        ),
        last_updated = NOW()
    WHERE cluster_id = NEW.cluster_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_cluster_assignments_stats
    AFTER INSERT OR UPDATE ON neurdb_metrics.template_cluster_assignments
    FOR EACH ROW
    EXECUTE FUNCTION neurdb_metrics.update_cluster_stats();

-- ============================================================================
-- Initial Data and Setup
-- ============================================================================

-- Create default clustering session record
INSERT INTO neurdb_metrics.clustering_sessions (
    clustering_algorithm,
    parameters,
    status,
    created_at
) VALUES (
    'ONLINE_COSINE',
    '{"similarity_threshold": 0.8, "min_cluster_size": 3, "time_window_hours": 24}',
    'COMPLETED',
    NOW()
);

-- Grant permissions (adjust as needed)
-- GRANT ALL PRIVILEGES ON SCHEMA neurdb_metrics TO neurdb;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA neurdb_metrics TO neurdb;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA neurdb_metrics TO neurdb;

-- Comments
COMMENT ON SCHEMA neurdb_metrics IS 'NeurDB Workload Forecast Metrics Schema';
COMMENT ON TABLE neurdb_metrics.query_templates IS 'Stores normalized SQL query templates with execution statistics';
COMMENT ON TABLE neurdb_metrics.workload_timeseries IS 'Time series data for query execution patterns';
COMMENT ON TABLE neurdb_metrics.template_clusters IS 'Cluster definitions for query template grouping';
COMMENT ON TABLE neurdb_metrics.template_cluster_assignments IS 'Historical and current template-to-cluster assignments';
COMMENT ON TABLE neurdb_metrics.cluster_timeseries IS 'Aggregated time series data for clusters';
COMMENT ON TABLE neurdb_metrics.clustering_sessions IS 'Metadata for clustering algorithm runs';

-- Schema setup complete
SELECT 'NeurDB metrics schema created successfully' as status;