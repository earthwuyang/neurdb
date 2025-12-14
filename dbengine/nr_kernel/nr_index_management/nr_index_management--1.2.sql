/* NeurDB Index Management Extension */

-- complain if script is sourced in psql, rather than via CREATE EXTENSION
\echo Use "CREATE EXTENSION nr_index_management" to load this file. \quit

-- Load base objects from 1.0 and then apply v1.1/v1.2 additions via upgrade scripts.
-- (Kept for completeness; deployments should use CREATE EXTENSION which installs default_version)

-- Index management functions (1.0)
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

-- Function for AI engine to automatically create indexes (legacy v1)
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
GRANT EXECUTE ON FUNCTION nr_create_index_if_budget_allows(text, text, text[], text) TO PUBLIC;
GRANT EXECUTE ON FUNCTION nr_drop_index_if_exists(text) TO PUBLIC;
GRANT EXECUTE ON FUNCTION nr_auto_create_indexes(JSONB) TO PUBLIC;

-- Function to enable reactive query interception (legacy v1)
CREATE FUNCTION nr_enable_reactive_query_interception()
RETURNS boolean
AS 'MODULE_PATHNAME', 'nr_enable_reactive_query_interception'
LANGUAGE C;

GRANT EXECUTE ON FUNCTION nr_enable_reactive_query_interception() TO PUBLIC;

-- v2 bookkeeping (reactive index management v2)
CREATE SCHEMA IF NOT EXISTS nrim;

CREATE TABLE IF NOT EXISTS nrim.nrim_query_facts (
  query_id bigint PRIMARY KEY,
  sample_query_text text NOT NULL,
  frequency_ewma double precision NOT NULL,
  last_seen_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nrim.nrim_index_registry (
  index_oid oid PRIMARY KEY,
  index_name text NOT NULL,
  table_oid oid NOT NULL,
  table_name text NOT NULL,
  columns int2[] NOT NULL,
  index_method text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  state text NOT NULL DEFAULT 'created',

  size_bytes bigint,
  creation_cost_ms bigint,

  idx_scan bigint,
  idx_tup_read bigint,
  idx_tup_fetch bigint,
  touch_score_ewma double precision NOT NULL DEFAULT 0,

  benefit_score_ewma double precision NOT NULL DEFAULT 0,
  value_score double precision NOT NULL DEFAULT 0,

  last_refreshed_at timestamptz,
  last_error text
);

CREATE TABLE IF NOT EXISTS nrim.nrim_query_index_attribution (
  query_id bigint NOT NULL,
  index_oid oid NOT NULL,
  marginal_benefit_ewma double precision NOT NULL DEFAULT 0,
  last_estimated_at timestamptz,
  PRIMARY KEY (query_id, index_oid)
);

-- Work queue (reactive worker)
CREATE TABLE IF NOT EXISTS nrim.nrim_work_queue (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'pending',
  query_text text NOT NULL,
  candidates text,
  last_error text
);

CREATE INDEX IF NOT EXISTS nrim_work_queue_status_created_at_idx
ON nrim.nrim_work_queue(status, created_at);
