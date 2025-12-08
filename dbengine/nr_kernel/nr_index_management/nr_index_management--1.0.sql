/* NeurDB Index Management Extension */

-- complain if script is sourced in psql, rather than via CREATE EXTENSION
\echo Use "CREATE EXTENSION nr_index_management" to load this file. \quit

-- Index management functions
CREATE FUNCTION nr_get_current_index_storage_mb()
RETURNS double precision
AS 'MODULE_PATHNAME', 'nr_get_current_index_storage_mb'
LANGUAGE C STRICT;

CREATE FUNCTION nr_get_database_size_mb()
RETURNS double precision
AS 'MODULE_PATHNAME', 'nr_get_database_size_mb'
LANGUAGE C STRICT;

CREATE FUNCTION nr_calculate_index_budget_mb()
RETURNS double precision
AS 'MODULE_PATHNAME', 'nr_calculate_index_budget_mb'
LANGUAGE C STRICT;

CREATE FUNCTION nr_create_index_if_budget_allows(
    index_name text,
    table_name text,
    columns text[],
    index_type text DEFAULT 'btree'
)
RETURNS boolean
AS 'MODULE_PATHNAME', 'nr_create_index_if_budget_allows'
LANGUAGE C;

CREATE FUNCTION nr_drop_index_if_exists(
    index_name text
)
RETURNS boolean
AS 'MODULE_PATHNAME', 'nr_drop_index_if_exists'
LANGUAGE C;

CREATE FUNCTION nr_get_index_storage_stats()
RETURNS TABLE(
    index_name text,
    table_name text,
    size_mb double precision,
    usage_count bigint
)
AS 'MODULE_PATHNAME', 'nr_get_index_storage_stats'
LANGUAGE C;

-- View for current index storage usage
CREATE VIEW nr_index_storage_usage AS
SELECT * FROM nr_get_index_storage_stats();

-- Function for AI engine to automatically create indexes
CREATE FUNCTION nr_auto_create_indexes(
    index_definitions JSONB
)
RETURNS TABLE(
    index_name text,
    table_name text,
    created boolean,
    size_mb double precision,
    error_message text
)
AS 'MODULE_PATHNAME', 'nr_auto_create_indexes'
LANGUAGE C;

-- Grant necessary permissions
GRANT EXECUTE ON FUNCTION nr_get_current_index_storage_mb() TO PUBLIC;
GRANT EXECUTE ON FUNCTION nr_get_database_size_mb() TO PUBLIC;
GRANT EXECUTE ON FUNCTION nr_calculate_index_budget_mb() TO PUBLIC;
GRANT EXECUTE ON FUNCTION nr_get_index_storage_stats() TO PUBLIC;
GRANT SELECT ON nr_index_storage_usage TO PUBLIC;

-- Only superusers can create/drop indexes automatically
GRANT EXECUTE ON FUNCTION nr_create_index_if_budget_allows(text, text, text[], text) TO PUBLIC;
GRANT EXECUTE ON FUNCTION nr_drop_index_if_exists(text) TO PUBLIC;
GRANT EXECUTE ON FUNCTION nr_auto_create_indexes(JSONB) TO PUBLIC;