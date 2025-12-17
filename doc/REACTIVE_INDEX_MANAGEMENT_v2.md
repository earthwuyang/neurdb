# Reactive Index Management v2 (DB-Engine Only)

This document proposes a v2 design where reactive index management is implemented entirely inside the NeurDB/Postgres extension `nr_index_management`, without relying on AI Engine (`aiengine`) for candidate generation, costing, eviction, or creation. It is written as an implementation plan intended to be reviewed before coding.

---

## Goals

1. **Zero query-latency impact** from reactive index management:
   - Intercept user queries, but do **not block** query execution on networking, analysis, or index creation.
2. **Robust candidate generation** using PostgreSQL’s internal parse/analyze structures:
   - Extract referenced tables/columns from the analyzed `Query` tree (not SQL text parsing).
   - Generate candidates for:
     - Single-column indexes (filters/join keys/order/group).
     - Multi-column indexes derived from query structure (AND filters, join clauses, ORDER BY / GROUP BY keys).
3. **Budget-aware automatic creation**:
   - Respect `nr_max_index_storage_mb` (or computed fallback budget).
4. **Reactive eviction and replacement**:
   - Track auto-created reactive indexes.
   - Maintain a continuously updated value metric per index.
   - If a new candidate has higher value than existing ones, evict less useful indexes until the new one fits.
5. **Safety filters**:
   - Intercept only “user workload” queries (exclude system schemas, exclude AI-engine sessions via `application_name`).

---

## Non-goals (for v2 initial cut)

- Full-blown cost-based search (e.g., exploring permutations of multi-column order with EXPLAIN-based scoring per candidate).
- Functional indexes / expression indexes / partial indexes (can be added later).
- Cross-query global optimization beyond value tracking and replacement heuristics.

---

## Current State (v1 summary)

The current reactive flow sends SQL text to AI engine, which runs additional SQL (EXPLAIN/HypoPG/storage stats). This created:
- query blocking due to synchronous HTTP;
- feedback loops when AI’s own SQL is intercepted;
- candidate generation limitations from string parsing.

v2 removes AI engine reactive responsibilities entirely.

---

## High-level Architecture (v2)

### 1) Interception Hook (fast path)

- Hook: `post_parse_analyze_hook` (already installed in `nr_index_management`).
- Responsibilities (fast, non-blocking):
  1. Filter: only handle SELECT, reactive strategy, user tables, not AI sessions.
  2. Extract query features + generate candidate index definitions **synchronously**, but cheaply.
  3. Enqueue a “work item” to a background pipeline (shared memory queue).
  4. Return immediately (no I/O, no index creation inline).

### 2) Background Worker (slow path)

- A `BackgroundWorker` runs continuously:
  - consumes queued work items,
  - merges candidates,
  - scores them using index-value heuristics,
  - checks budget,
  - performs **CREATE INDEX CONCURRENTLY** / **DROP INDEX CONCURRENTLY** to minimize impact on ongoing workload.

### 3) Persistent Tracking Tables

Use extension-owned tables (in `public` or a dedicated schema like `nrim`) to persist:
- known reactive indexes + metadata,
- per-index value metrics,
- per-index usage snapshots (decay over time),
- last creation status/errors.

---

## Filters: “Only user queries”

Interception should **not** enqueue work for:

1. **System schema relations**:
   - any RTE relation in `pg_catalog`, `information_schema`, or `pg_*` (excluding `pg_temp*`).
2. **AI engine sessions**:
   - skip when `application_name = 'neurdb_ai'` (already implemented in v1; keep it).
3. (Optional) **superuser/internal roles**:
   - optionally skip for role names used by maintenance tasks.
4. **Non-SELECT**:
   - ignore DDL/DML to avoid side effects.

Implementation detail:
- Walk `Query->rtable` and any nested subqueries (`RTE_SUBQUERY`) and reject if any referenced relation belongs to a system schema.

---

## Query Feature Extraction (AST-based)

### Inputs available to `post_parse_analyze_hook`

- `ParseState *pstate` and `Query *query` are available and already analyzed.
- `query->rtable` provides relation OIDs for base relations.
- Expression trees contain `Var` nodes with:
  - `varno` (RTE index),
  - `varattno` (attribute number),
  - `vartype` etc.

### Walkers

Use internal walkers:
- `expression_tree_walker()` to traverse expressions and collect referenced `Var`s.
- custom walkers for specific clause kinds:
  - WHERE/JOIN quals: `query->jointree->quals`
  - JOIN conditions: inside `FromExpr` / `JoinExpr` nodes
  - ORDER BY: `query->sortClause` + `get_sortgroupclause_expr()`
  - GROUP BY: `query->groupClause` + `get_sortgroupclause_expr()`

### Data extracted per query (minimal payload to queue)

For each base relation (table OID):
- `where_cols_eq`: columns used with equality operators (e.g., `a = const`, `a = b`)
- `where_cols_range`: columns used with range-like operators (`<`, `>`, `BETWEEN`, `LIKE` heuristics)
- `join_cols`: columns participating in join clauses
- `order_cols`: ordered list (if order by references table)
- `group_cols`: group-by key list
- also optionally track co-occurrence groups from AND conjunctions.

Notes:
- Multi-table queries: keep data per table.
- For `Var` resolution: map `Var->varno` to `RangeTblEntry` and `relid`.

---

## Candidate Generation Rules (single + multi-column)

Candidates are generated per table.

### Single-column candidates

1. Columns in equality WHERE predicates.
2. Join key columns.
3. Leading ORDER BY columns.
4. GROUP BY columns (lower priority).
5. Range predicate columns (lower priority than equality).

Index type:
- default `btree` for now.

### Multi-column candidates

Generate multi-column candidates using ordered heuristics:

1. **Equality conjunction groups**:
   - For `WHERE a = ? AND b = ? AND c = ?`, create candidate prefixes:
     - `(a,b)`, `(a,b,c)` (order decision below).
2. **Join + filter**:
   - If join key exists plus a highly used filter column:
     - `(join_key, filter_col)`
3. **Filter + ORDER BY**:
   - If `WHERE a = ? ORDER BY b, c`:
     - `(a,b)` or `(a,b,c)`
4. **GROUP BY composites**:
   - `GROUP BY a, b`:
     - `(a,b)` (usually lower priority than filter/order patterns).

Column order heuristics (btree):
- Put **equality** columns first, then **range** columns, then **order-by** columns.
- If multiple equality columns, order them by estimated selectivity proxy:
  - if stats are available cheaply: use `pg_stats.n_distinct` / `pg_statistic` lookup in background worker (not in hook).
  - otherwise: stable deterministic order (e.g., attnum ascending) to reduce churn.

Limit combinatorics:
- Cap multi-column width (e.g., max 3 columns in v2).
- Cap per-query per-table candidates (e.g., max 10).

---

## Candidate Merge / Prune Phase

Given a set of candidates for a table, perform merge/prune:

### 1) Deduplicate
- Normalize signature: `(table_oid, index_type, columns[], order)` into a canonical string key.

### 2) Prefix dominance prune (“merge”)
- If candidate `(A,B)` exists, drop candidate `(A)` **only if**:
  - `(A,B)` uses the same index method,
  - and `(A)` is purely redundant under the leftmost-prefix property.

Important nuance:
- Even if `(A,B)` exists, a standalone `(A)` can still be useful if:
  - `(A,B)` is extremely large and `(A)` would be much smaller, or
  - `(A,B)` is not chosen by planner for some patterns.

So v2 should implement a conservative rule:
- Prefer dropping `(A)` only when:
  - `(A,B)` already exists *and* size ratio is not huge, or
  - budget pressure is high.

Implementation detail:
- The background worker should consult existing index sizes.

### 3) Existing index coverage
- If an equivalent or stronger existing index already exists on the table (including non-reactive user indexes), avoid proposing duplicates.
- Use system catalogs (`pg_index`, `pg_class`, `pg_am`, `pg_attribute`) to compare:
  - index method,
  - key columns order.

---

## Index Value Model (continuous update)

v2 needs a value metric to decide creation/eviction/replacement. The v2 value model is **HypoPG-driven**:

- Use **HypoPG hypothetical indexes** + **EXPLAIN (FORMAT JSON)** to estimate **marginal utility** of candidate and existing reactive indexes.
- Combine this with real **touch/usage** signals and **cost** (storage/maintenance) into a holistic `value_score`.
- Update continuously with time decay (EWMA), in the **background worker only**.

### Track reactive indexes only

We track only indexes created by v2, with a naming pattern:
- `idx_reactive_<table>_<col1>_<col2>_..._<timestamp>`

Budget/eviction only applies to these reactive indexes (never drop user-created indexes).

### Persistent state (tables)

**1) Query facts**

```sql
CREATE TABLE nrim_query_facts (
  query_id bigint PRIMARY KEY,               -- stable hash of normalized statement
  sample_query_text text NOT NULL,           -- representative text for EXPLAIN
  frequency_ewma double precision NOT NULL,  -- decayed frequency
  last_seen_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

**2) Reactive index registry**

```sql
CREATE TABLE nrim_index_registry (
  index_oid oid PRIMARY KEY,
  index_name text NOT NULL,
  table_oid oid NOT NULL,
  table_name text NOT NULL,
  columns int2[] NOT NULL,                   -- key columns (attnums)
  index_method text NOT NULL,                -- btree for v2
  created_at timestamptz NOT NULL DEFAULT now(),
  state text NOT NULL DEFAULT 'created',     -- created|creating|dropping|failed

  size_bytes bigint,
  creation_cost_ms bigint,

  -- usage signals (periodically refreshed, store deltas + EWMA)
  idx_scan bigint,
  idx_tup_read bigint,
  idx_tup_fetch bigint,
  touch_score_ewma double precision NOT NULL DEFAULT 0,

  -- HypoPG/EXPLAIN benefit signals
  benefit_score_ewma double precision NOT NULL DEFAULT 0,
  value_score double precision NOT NULL DEFAULT 0,

  last_refreshed_at timestamptz,
  last_error text
);
```

**3) Query ↔ Index attribution (marginal benefit)**

```sql
CREATE TABLE nrim_query_index_attribution (
  query_id bigint NOT NULL,
  index_oid oid NOT NULL,
  marginal_benefit_ewma double precision NOT NULL DEFAULT 0,
  last_estimated_at timestamptz,
  PRIMARY KEY (query_id, index_oid)
);
```

### HypoPG-based marginal benefit estimation

All HypoPG/EXPLAIN work runs in the background worker session with `application_name='neurdb_reactive_worker'` and must be excluded from interception.

For a query `q` and a candidate (or existing reactive) index `i`:

1. Baseline plan cost with real indexes:
   - `C0 = cost(EXPLAIN (FORMAT JSON) q)`
2. Plan cost with `i` present (HypoPG):
   - `hypoid = hypopg_create_index('CREATE INDEX ...')`
   - `Ci = cost(EXPLAIN (FORMAT JSON) q)`
   - `hypopg_drop_index(hypoid)`
3. Marginal benefit:
   - `Δ(q,i) = max(0, C0 - Ci)`

Optionally normalize:
- `Δn(q,i) = Δ(q,i) / max(C0, ε)`

This approach remains robust for complex queries because it uses Postgres’s own planner.

### Preventing double counting (attribution)

When multiple indexes help the same query, summing individual `Δ(q,i)` overcounts.

Use a greedy marginal-set attribution per query:

1. Choose a small candidate set `K` (e.g., top 3–5 candidates for that query).
2. Start with `S = ∅`, `C(S) = C0`.
3. Iterate candidates in descending individual benefit estimate:
   - Create HypoPG indexes for `S ∪ {i}`, compute `C(S∪{i})`.
   - `marginal_i = max(0, C(S) - C(S∪{i}))`
   - If `marginal_i` is tiny, stop early.
   - Add `i` to `S`.

Store `marginal_i` in `nrim_query_index_attribution` (EWMA-updated).

### Touch / “index being touched”

We track both:

1) **Stats-based touches** (real reactive indexes only):
- Periodically read `pg_stat_user_indexes` for the reactive index oids and compute deltas.
- Update `touch_score_ewma` from deltas of `idx_scan`, optionally weighted by `idx_tup_read/fetch`.

2) **Plan evidence touches** (optional but useful):
- When worker runs `EXPLAIN (FORMAT JSON)`, parse plan nodes.
- If the plan uses an index scan referencing a reactive index name, increment a per-index plan-touch counter.

### Holistic value score (v2)

Compute in worker periodically:

**Benefit aggregation**

- For each index `i`:
  - `B_i = Σ_q [ frequency_ewma(q) * marginal_benefit_ewma(q,i) ]`

**Usage aggregation**

- `U_i = touch_score_ewma(i)` (from stats and optional plan evidence).

**Cost terms**

- `S_i = size_bytes(i)`
- `M_i = maintenance_proxy(i)` (optional: table update rate × index width)

**Final score (example)**

```
value_i = (wB * log1p(B_i) + wU * log1p(U_i))
          / (wS * log1p(S_i) + wM * log1p(M_i) + 1)
```

All components use EWMA so old workload decays and stale indexes naturally lose value.

### Rate limiting / evaluation budget

HypoPG + EXPLAIN can be expensive. v2 must enforce:

- Max HypoPG evaluations per minute.
- Max candidates evaluated per query (`K`).
- Only evaluate queries with `frequency_ewma >= threshold` or sampled (e.g., 1/N per query template).
- Stop early when additional marginal improvements are tiny.

---

## Budgeting & Eviction Logic

### Budget source

- Use `nr_max_index_storage_mb` if `> 0`.
- Otherwise budget = `50% of current DB size` (same semantics as existing helper).

### Current usage

- Sum sizes of **reactive indexes** only (or optionally all indexes, depending on desired semantics).
  - v2 recommendation: budget applies to reactive indexes only; do not delete user-managed indexes.

### Creation decision

For a candidate index with estimated size `cand_size`:

1. If `(current_usage + cand_size) <= budget`: create.
2. Else attempt eviction:
   - select a **victim set** `V` of reactive indexes to drop until enough space is freed,
   - prefer evicting indexes with the lowest “utility per MB” first (to minimize harm).
3. Replacement rule (victim-set total-utility gate):
   - define an index “utility” term as the **numerator** of `value_score`:
     - `utility(i) = wB*log1p(benefit_score_ewma(i)) + wU*log1p(touch_score_ewma(i))`
   - estimate candidate utility `utility(cand)` using HypoPG benefit on the triggering query (or a small set of frequent queries).
   - only evict `V` if:
     - `utility(cand) >= (1 + min_improvement) * Σ_{v in V} utility(v)`
   - otherwise, do nothing (budget is exceeded but the replacement is not worth it).

This victim-set gate avoids the common pitfall of comparing a large new index to only the single “worst” victim: if freeing enough space requires dropping multiple low-value indexes, the decision should consider the **total** value being traded away.

### Candidate utility/value estimation (before creation)

We estimate candidate utility (and optional value) using HypoPG marginal benefit on a limited set of high-frequency queries:

1. Choose affected queries for candidate’s table:
   - top-N by `frequency_ewma`.
2. Compute `Δ(q,cand)` using HypoPG.
3. Aggregate to `B_cand = Σ_q freq(q) * Δ(q,cand)`.
4. Convert to candidate utility:
   - `utility_cand ≈ wB*log1p(B_cand) + wU*log1p(U_pred)` (with `U_pred` defaulting to `0` until the index is real and has stats)
5. If needed for ranking candidates of different sizes, convert to a size-aware score:
   - `value_cand ≈ utility_cand / (wS*log1p(size_est)+1)`

After creation, the candidate becomes a real reactive index and starts accumulating touch/benefit EWMA in `nrim_index_registry`.

---

## Truly Non-blocking Index Creation

### Requirement

“Non-blocking” means the user query must not wait on:
- network I/O,
- candidate generation heavy work,
- index build.

### Proposed approach

1. Hook enqueues work item only.
2. Background worker performs index build with **CONCURRENTLY**:
   - `CREATE INDEX CONCURRENTLY ...`
   - `DROP INDEX CONCURRENTLY ...`

Why CONCURRENTLY:
- avoids blocking reads/writes as much as regular CREATE INDEX (still takes some locks, but generally safe for production-like workloads).

### Technical detail: executing CONCURRENTLY

In Postgres, `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block.

Design options:

**Option A (preferred): background worker uses libpq to issue SQL**
- The background worker establishes a libpq connection to the local server (`localhost` / unix socket) with `application_name='neurdb_reactive_worker'`.
- It issues `CREATE INDEX CONCURRENTLY ...` via `PQexec` as standalone statements (no explicit BEGIN).
- This avoids SPI transaction issues.

**Option B: spawn `psql` subprocess**
- Similar to the async curl dispatch approach:
  - fork + exec `psql -c "CREATE INDEX CONCURRENTLY ..."`
- Simpler, but less controllable (parsing output/errors, auth, environment).

For v2, Option A is recommended for control and robustness.

---

## Queueing: Work Item Format

Work item should be compact and stable.

Suggested structure (serialized to JSON or a custom binary):
- query_id (hash),
- timestamp,
- per-table extracted features:
  - table_oid + schema/table name,
  - eq_cols (attnums),
  - range_cols,
  - join_cols,
  - order_cols,
  - group_cols.

The hook should avoid expensive catalog lookups:
- store OIDs + attnums; background worker can resolve names later.

Queue implementation options:
- `shmem_mq` (shared memory message queue)
- ring buffer in shared memory with LWLocks

---

## Extension Lifecycle & GUCs

Existing GUCs:
- `nr_index_management_strategy` = `reactive` enables v2.
- `nr_enable_auto_index_creation` controls whether worker performs creation or only logs candidates.
- `nr_max_index_storage_mb` budget.

New v2 GUCs (proposed):
- `nr_reactive.enable` (bool) — explicit master switch.
- `nr_reactive.max_candidates_per_query` (int).
- `nr_reactive.max_columns_per_index` (int).
- `nr_reactive.worker_interval_ms` (int).
- `nr_reactive.min_value_improvement` (float) — required improvement before replacement.
- `nr_reactive.use_concurrently` (bool, default on).

---

## Removing AI Engine Reactive Responsibilities

Once v2 is implemented in dbengine:
- delete / disable these routes from `aiengine/workload_forecast/run_server.py`:
  - `/index/reactive/manage`
  - `/index/reactive/recommend`
  - `/index/reactive/status` (optional)
- keep predictive endpoints if still needed.

Also remove:
- `ReactiveIndexManager` use from AI server (or leave module unused).

---

## Implementation Plan (stepwise)

1. Add v2 design GUCs and an enable flag.
2. Add shared memory queue + background worker skeleton.
3. Implement AST feature extraction in hook:
   - collect per-table columns for WHERE/JOIN/ORDER/GROUP.
4. Implement candidate generator (single + limited multi-column).
5. Implement merge/prune logic (dedupe + prefix dominance).
6. Implement registry table + periodic stats refresh (idx_scan, size_mb, value_score).
7. Implement budget check + eviction policy + replacement thresholds.
8. Implement concurrent index build/drop in worker (libpq-based).
9. Add safety filters (system schemas + application_name).
10. Remove reactive logic from AI engine.

---

## Open Questions / Review Items

### Decisions (confirmed)

1. **Budget scope**: budget applies to **all indexes in `public`** (reactive indexes compete with user indexes for the same global budget).
2. **Cold queries**: v2 does **not** avoid cold queries initially (no frequency threshold gate). Rate limiting still applies for HypoPG evaluations.
3. **Multi-column width**: default max width is **3**, exposed as a configurable GUC (e.g., `nr_reactive.max_columns_per_index`).
4. **Value function**: use the **holistic HypoPG + EXPLAIN marginal benefit model** described above (with attribution + touch + cost + EWMA).



## Files
in output/:
mixed_workload.csv
noauto_queries.csv / auto_queries.csv
per_query_comparison.csv
top_regressions.csv
top_improvements.csv
noauto_nrim_events.csv / auto_nrim_events.csv