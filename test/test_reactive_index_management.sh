#!/bin/bash

# Test script for reactive index management v2 (DB-engine only)
# Verifies: hook enqueues work, background worker scores via HypoPG, and creates reactive indexes asynchronously.

echo "=========================================="
echo "Testing Reactive Index Management Architecture"
echo "=========================================="

# Use Unix socket connection
PSQL_CMD="/code/neurdb-dev/dbengine/src/bin/psql/psql"
CONN_OPTS="-h /tmp -U neurdb -d imdb_test"

echo "1. Setting up reactive index management mode..."
$PSQL_CMD $CONN_OPTS -c "
CREATE EXTENSION IF NOT EXISTS hypopg;
SET nr_index_management_strategy = 'reactive';
SET nr_enable_auto_index_creation = true;
SET nr_reactive.enable = true;
SET nr_reactive.use_concurrently = true;
SET nr_reactive.max_columns_per_index = 3;
SET nr_reactive.max_candidates_per_query = 20;
SET nr_reactive.debug = true;
SELECT 'Reactive index management enabled' as status;
" 2>&1

echo -e "\n2. Testing reactive query interception..."
echo "Running SELECT query - should enqueue a work item (non-blocking)..."

$PSQL_CMD $CONN_OPTS -c "
SET client_min_messages = WARNING;
SELECT count(*) FROM cast_info WHERE person_id = 77777 LIMIT 5;
" 2>&1

echo -e "\n3. Checking the v2 queue/registry tables..."
$PSQL_CMD $CONN_OPTS -c "
SELECT status, count(*) AS n
FROM nrim.nrim_work_queue
GROUP BY status
ORDER BY status;
" 2>&1

echo -e "\n4. Waiting briefly for the worker to process the queue..."
for i in {1..10}; do
  sleep 1
  DONE=$($PSQL_CMD $CONN_OPTS -Atc "SELECT count(*) FROM nrim.nrim_work_queue WHERE status IN ('done','error');" 2>/dev/null)
  if [[ \"$DONE\" != \"\" && \"$DONE\" -gt 0 ]]; then
    break
  fi
done

$PSQL_CMD $CONN_OPTS -c "
SELECT id, status, left(coalesce(last_error,''), 120) AS last_error
FROM nrim.nrim_work_queue
ORDER BY id DESC
LIMIT 5;
" 2>&1

echo -e "\n5. Checking for created reactive indexes..."
$PSQL_CMD $CONN_OPTS -c "
SELECT indexname, tablename
FROM pg_indexes
WHERE schemaname NOT IN ('pg_catalog','information_schema')
  AND indexname LIKE 'idx_reactive_%'
ORDER BY indexname
LIMIT 20;
" 2>&1

$PSQL_CMD $CONN_OPTS -c "
SELECT index_name, table_name, state, value_score, benefit_score_ewma, touch_score_ewma
FROM nrim.nrim_index_registry
ORDER BY created_at DESC
LIMIT 20;
" 2>&1

echo -e "\n6. Testing predictive mode (should not enqueue work items)..."
echo "Switching to predictive mode..."
$PSQL_CMD $CONN_OPTS -c "
SET nr_index_management_strategy = 'predictive';
SET workload_forecast.enable = true;
SELECT 'Switched to predictive mode' as status;
SELECT count(*) FROM title WHERE kind_id = 1 LIMIT 5;
" 2>&1

echo -e "\n7. Testing reactive mode with auto-creation disabled..."
echo "Switching back to reactive but disabling auto-creation..."
$PSQL_CMD $CONN_OPTS -c "
SET nr_index_management_strategy = 'reactive';
SET nr_enable_auto_index_creation = false;
SELECT 'Reactive mode with auto-creation disabled' as status;
SELECT count(*) FROM movie_info WHERE info_type_id = 3 LIMIT 5;
" 2>&1

echo -e "\n8. Testing non-SELECT queries (should not be intercepted)..."
$PSQL_CMD $CONN_OPTS -c "
SET nr_index_management_strategy = 'reactive';
SET nr_enable_auto_index_creation = true;
CREATE TEMP TABLE test_reactive (id int);
INSERT INTO test_reactive VALUES (1);
DROP TABLE test_reactive;
" 2>&1

echo -e "\n=========================================="
echo "Test completed!"
echo "=========================================="
echo ""
echo "Expected behavior:"
echo "1. Reactive mode + SELECT queries → enqueued into nrim.nrim_work_queue (no user-query blocking)"
echo "2. Background worker → uses HypoPG+EXPLAIN to score and may CREATE INDEX CONCURRENTLY"
echo "3. Predictive mode → no reactive enqueueing"
echo "4. Auto-creation disabled → worker may score/log but will not create indexes"
echo "5. Non-SELECT queries → never intercepted"
echo ""
echo "To monitor logs in real-time:"
echo "docker exec neurdb_dev tail -f /code/neurdb-dev/psql/data/logfile"
echo ""
echo "Look for log entries starting with 'REACTIVE INDEX:'"
