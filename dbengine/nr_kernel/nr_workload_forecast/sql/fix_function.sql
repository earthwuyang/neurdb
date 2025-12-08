-- Fix the get_workload_stats function
DROP FUNCTION IF EXISTS neurdb_metrics.get_workload_stats(integer);

CREATE OR REPLACE FUNCTION neurdb_metrics.get_workload_stats(
    p_hours_back INTEGER DEFAULT 24
) RETURNS TABLE (
    total_queries BIGINT,
    unique_templates INTEGER,
    avg_execution_time_ms FLOAT,
    top_templates TEXT[]
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(wts.id)::BIGINT as total_queries,
        COUNT(DISTINCT wts.template_hash)::INTEGER as unique_templates,
        AVG(wts.avg_duration_ms) as avg_execution_time_ms,
        ARRAY_AGG(
            COALESCE(qt.template_text || ' (' || wts.template_hash || ')', 'Unknown template')
            ORDER BY COALESCE(wts.execution_count, 1) DESC
        ) as top_templates
    FROM neurdb_metrics.workload_timeseries wts
    LEFT JOIN neurdb_metrics.query_templates qt ON wts.template_hash = qt.template_hash
    WHERE wts.timestamp >= NOW() - (p_hours_back || ' hours')::INTERVAL;
END;
$$ LANGUAGE plpgsql;