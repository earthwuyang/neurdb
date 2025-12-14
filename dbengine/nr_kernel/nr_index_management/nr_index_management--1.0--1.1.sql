/* nr_index_management extension upgrade: 1.0 -> 1.1 */

-- Internal schema for v2 bookkeeping (reactive management v2)
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

