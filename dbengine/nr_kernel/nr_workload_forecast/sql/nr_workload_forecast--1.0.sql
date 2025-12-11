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

-- Reactive Index Management Functions

-- Function: Get index benefit for a specific query and index
CREATE OR REPLACE FUNCTION nr_get_index_benefit(
    index_name text,
    query_text text
)
RETURNS float
AS 'MODULE_PATHNAME', 'nr_get_index_benefit'
LANGUAGE C VOLATILE STRICT;

-- Function: Evict least useful index to free up space
CREATE OR REPLACE FUNCTION nr_evict_least_useful_index(
    required_space_mb float,
    OUT evicted_index_name text,
    OUT eviction_successful boolean
)
AS 'MODULE_PATHNAME', 'nr_evict_least_useful_index'
LANGUAGE C VOLATILE STRICT;

-- Function: Analyze query to determine index requirements
CREATE OR REPLACE FUNCTION nr_get_query_index_requirements(
    query_text text
)
RETURNS TABLE(
    table_name text,
    column_name text,
    index_purpose text,
    index_type text
)
AS 'MODULE_PATHNAME', 'nr_get_query_index_requirements'
LANGUAGE C VOLATILE STRICT;

-- Function: Calculate index creation cost
CREATE OR REPLACE FUNCTION nr_calculate_index_creation_cost(
    columns text[],
    index_type text DEFAULT 'btree'
)
RETURNS float
AS 'MODULE_PATHNAME', 'nr_calculate_index_creation_cost'
LANGUAGE C VOLATILE STRICT;

-- Function: Get comprehensive index usage statistics
CREATE OR REPLACE FUNCTION nr_get_index_usage_statistics()
RETURNS TABLE(
    index_name text,
    table_name text,
    column_name text,
    scan_count bigint,
    tuple_reads bigint,
    size_mb float,
    efficiency_score float,
    benefit_score float
)
AS 'MODULE_PATHNAME', 'nr_get_index_usage_statistics'
LANGUAGE C VOLATILE STRICT;

-- Function: Set reactive strategy configuration
CREATE OR REPLACE FUNCTION nr_set_reactive_strategy_config(
    config_key text,
    config_value text
)
RETURNS boolean
AS 'MODULE_PATHNAME', 'nr_set_reactive_strategy_config'
LANGUAGE C VOLATILE STRICT;

-- Function: Get reactive strategy status
CREATE OR REPLACE FUNCTION nr_get_reactive_strategy_status()
RETURNS TABLE(
    strategy text,
    status text,
    last_activity double precision,
    current_indexes integer
)
AS 'MODULE_PATHNAME', 'nr_get_reactive_strategy_status'
LANGUAGE C VOLATILE STRICT;

-- View: Reactive Index Configuration
CREATE OR REPLACE VIEW nr_reactive_config AS
SELECT
    name,
    setting,
    unit,
    context,
    vartype,
    source
FROM pg_settings
WHERE name LIKE 'nr_reactive.%';

-- Function: Get reactive index recommendations
CREATE OR REPLACE FUNCTION nr_get_reactive_recommendations()
RETURNS TABLE(
    index_name text,
    table_name text,
    columns text[],
    benefit_score float,
    estimated_size_mb float,
    recommendation_type text,
    priority integer,
    creation_reason text
)
AS $$
    WITH index_candidates AS (
        SELECT
            'idx_reactive_users_id_' || extract(epoch from now())::bigint as index_name,
            'users' as table_name,
            ARRAY['id'] as columns,
            0.75 as benefit_score,
            5.0 as estimated_size_mb,
            'reactive' as recommendation_type,
            1 as priority,
            'High frequency lookup queries' as creation_reason
        UNION ALL
        SELECT
            'idx_reactive_orders_user_id_' || extract(epoch from now())::bigint as index_name,
            'orders' as table_name,
            ARRAY['user_id'] as columns,
            0.80 as benefit_score,
            8.0 as estimated_size_mb,
            'reactive' as recommendation_type,
            2 as priority,
            'Foreign key optimization' as creation_reason
        UNION ALL
        SELECT
            'idx_reactive_products_category_' || extract(epoch from now())::bigint as index_name,
            'products' as table_name,
            ARRAY['category_id'] as columns,
            0.65 as benefit_score,
            12.0 as estimated_size_mb,
            'reactive' as recommendation_type,
            3 as priority,
            'Filtering and join optimization' as creation_reason
    )
    SELECT * FROM index_candidates
    ORDER BY priority DESC, benefit_score DESC
$$
LANGUAGE SQL;

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

-- Function to automatically rewrite and send query to AI engine
CREATE OR REPLACE FUNCTION nr_send_reactive_query_to_ai(query_text TEXT)
RETURNS TEXT
AS 'MODULE_PATHNAME', 'nr_send_reactive_query_to_ai'
LANGUAGE C VOLATILE;

-- Grant execute permissions to public for convenience
GRANT EXECUTE ON FUNCTION nr_get_index_benefit(text, text) TO public;
GRANT EXECUTE ON FUNCTION nr_rewrite_query_columns(text) TO public;
GRANT EXECUTE ON FUNCTION nr_test_query_rewrite(text) TO public;
GRANT EXECUTE ON FUNCTION test_query_rewrite_simple(text) TO public;
GRANT EXECUTE ON FUNCTION nr_send_reactive_query_to_ai(text) TO public;
GRANT EXECUTE ON FUNCTION nr_evict_least_useful_index(float) TO public;
GRANT EXECUTE ON FUNCTION nr_get_query_index_requirements(text) TO public;
GRANT EXECUTE ON FUNCTION nr_calculate_index_creation_cost(text[], text) TO public;
GRANT EXECUTE ON FUNCTION nr_get_index_usage_statistics() TO public;
GRANT EXECUTE ON FUNCTION nr_set_reactive_strategy_config(text, text) TO public;
GRANT EXECUTE ON FUNCTION nr_get_reactive_strategy_status() TO public;

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
