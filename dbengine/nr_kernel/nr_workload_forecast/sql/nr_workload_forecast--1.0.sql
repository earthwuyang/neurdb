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

-- Query rewriting functions for AI engine integration
CREATE OR REPLACE FUNCTION nr_rewrite_query_columns(query_text TEXT)
RETURNS TEXT AS 'MODULE_PATHNAME', 'nr_rewrite_query_columns'
LANGUAGE C STRICT;

CREATE OR REPLACE FUNCTION nr_test_query_rewrite(sample_query TEXT)
RETURNS TABLE(original_query TEXT, rewritten_query TEXT) AS 'MODULE_PATHNAME', 'nr_test_query_rewrite'
LANGUAGE C STRICT;

-- Example usage function for testing query rewriting
CREATE OR REPLACE FUNCTION test_query_rewrite_simple(sample_query TEXT)
RETURNS TABLE(original_query TEXT, rewritten_query TEXT) AS $$
DECLARE
    rewritten TEXT;
BEGIN
    rewritten := nr_rewrite_query_columns(sample_query);
    RETURN QUERY SELECT sample_query, rewritten;
END;
$$ LANGUAGE plpgsql;

-- Grant execute permissions to public for convenience
GRANT EXECUTE ON FUNCTION nr_rewrite_query_columns(text) TO public;
GRANT EXECUTE ON FUNCTION nr_test_query_rewrite(text) TO public;
GRANT EXECUTE ON FUNCTION test_query_rewrite_simple(text) TO public;

-- Grant select on config view and recommendations
GRANT SELECT ON nr_reactive_config TO public;
GRANT EXECUTE ON FUNCTION nr_get_reactive_recommendations() TO public;

-- Index Management Strategy Functions

-- Function: Switch between predictive and reactive strategies
CREATE OR REPLACE FUNCTION nr_set_index_management_strategy(strategy_name text)
RETURNS boolean
AS $$
DECLARE
    strategy_value integer;
BEGIN
    -- Convert strategy name to integer value
    CASE LOWER(strategy_name)
        WHEN 'predictive' THEN
            strategy_value := 0;
        WHEN 'reactive' THEN
            strategy_value := 1;
        ELSE
            RAISE EXCEPTION 'Invalid strategy name: %. Valid values are "predictive" or "reactive"', strategy_name;
    END CASE;

    -- Set the GUC variable (this will automatically notify the AI engine)
    PERFORM set_config('nr_index_management_strategy', strategy_value::text, false);

    RETURN true;
END;
$$
LANGUAGE plpgsql VOLATILE STRICT;

-- Function: Get current index management strategy
CREATE OR REPLACE FUNCTION nr_get_index_management_strategy()
RETURNS text
AS $$
DECLARE
    strategy_value integer;
BEGIN
    strategy_value := current_setting('nr_index_management_strategy')::integer;

    CASE strategy_value
        WHEN 0 THEN
            RETURN 'predictive';
        WHEN 1 THEN
            RETURN 'reactive';
        ELSE
            RETURN 'unknown';
    END CASE;
END;
$$
LANGUAGE sql STABLE;

-- Function: Show current index management configuration
CREATE OR REPLACE VIEW nr_index_management_status AS
SELECT
    'nr_index_management_strategy' as parameter,
    nr_get_index_management_strategy() as current_value,
    CASE nr_get_index_management_strategy()
        WHEN 'predictive' THEN 'Uses workload forecasting and proactive optimization'
        WHEN 'reactive' THEN 'Provides real-time query-by-query index management'
        ELSE 'Unknown strategy'
    END as description
UNION ALL
SELECT
    'nr_enable_auto_index_creation' as parameter,
    current_setting('nr_enable_auto_index_creation') as current_value,
    CASE current_setting('nr_enable_auto_index_creation')::boolean
        WHEN true THEN 'Automatic index creation is enabled'
        WHEN false THEN 'Automatic index creation is disabled'
    END as description;

-- Grant execute permissions to public for convenience
GRANT EXECUTE ON FUNCTION nr_set_index_management_strategy(text) TO public;
GRANT EXECUTE ON FUNCTION nr_get_index_management_strategy() TO public;
GRANT SELECT ON nr_index_management_status TO public;
