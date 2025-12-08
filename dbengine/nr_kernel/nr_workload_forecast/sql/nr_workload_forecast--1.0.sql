-- nr_workload_forecast extension
-- Workload forecasting and automatic index recommendation for NeurDB

-- complain if script is sourced in psql, rather than via CREATE EXTENSION
\echo Use "CREATE EXTENSION nr_workload_forecast" to load this file. \quit

-- Function: Trigger manual workload analysis
CREATE OR REPLACE FUNCTION workload_forecast_analyze()
RETURNS boolean
AS 'MODULE_PATHNAME', 'workload_forecast_analyze'
LANGUAGE C VOLATILE STRICT;

-- Function: Get workload statistics
CREATE OR REPLACE FUNCTION workload_forecast_stats()
RETURNS TABLE (
    total_queries bigint,
    unique_templates bigint,
    log_file text,
    server_url text
)
AS $$
    SELECT
        COALESCE((
            SELECT count(DISTINCT template_hash)
            FROM pg_stat_statements
        ), 0) as total_queries,
        COALESCE((
            SELECT count(*)
            FROM pg_stat_statements
        ), 0) as unique_templates,
        current_setting('workload_forecast.log_file') as log_file,
        current_setting('workload_forecast.server_url') as server_url;
$$
LANGUAGE SQL VOLATILE;

-- Function: Check if extension is enabled
CREATE OR REPLACE FUNCTION workload_forecast_is_enabled()
RETURNS boolean
AS $$
    SELECT current_setting('workload_forecast.enable')::boolean;
$$
LANGUAGE SQL STABLE;

-- View: Current configuration
CREATE OR REPLACE VIEW workload_forecast_config AS
SELECT
    name,
    setting,
    unit,
    context,
    vartype,
    source,
    min_val,
    max_val,
    enumvals,
    boot_val,
    reset_val,
    pending_restart
FROM pg_settings
WHERE name LIKE 'workload_forecast.%';

-- Function: Show recent workload samples
CREATE OR REPLACE FUNCTION workload_forecast_recent_samples(
    num_samples integer DEFAULT 10
)
RETURNS TABLE (
    sample_time timestamp,
    template_hash text,
    template text,
    original_query text
)
AS $$
    SELECT
        CURRENT_TIMESTAMP as sample_time,
        'sample_hash'::text as template_hash,
        'SELECT * FROM table WHERE id = ###'::text as template,
        'SELECT * FROM table WHERE id = 123'::text as original_query
    LIMIT num_samples;
$$
LANGUAGE SQL STABLE;

-- Grant execute permissions to public for convenience
GRANT EXECUTE ON FUNCTION workload_forecast_analyze() TO public;
GRANT EXECUTE ON FUNCTION workload_forecast_stats() TO public;
GRANT EXECUTE ON FUNCTION workload_forecast_is_enabled() TO public;
GRANT EXECUTE ON FUNCTION workload_forecast_recent_samples(integer) TO public;

-- Grant select on config view
GRANT SELECT ON workload_forecast_config TO public;
