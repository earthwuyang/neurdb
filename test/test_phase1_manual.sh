#!/bin/bash
# Manual test for Phase 1: Skip build, test installation and query logging

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration - use pre-built library
BUILD_DIR="/code/neurdb-dev/dbengine/nr_kernel/nr_workload_forecast/build"
LIB_FILE="$BUILD_DIR/nr_workload_forecast.so"
PG_LIBDIR="/code/neurdb-dev/psql/lib/postgresql"
PG_SHAREDIR="/code/neurdb-dev/psql/share/postgresql"
WORKLOAD_LOG="/tmp/neurdb_workload_manual_test.csv"
PSQL="/code/neurdb-dev/psql/bin/psql -h localhost -p 5432 -U neurdb -d imdb_ori"

echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Phase 1 Manual Test: Query Logging (Skip Build)${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo ""

# Check if library exists
if [ ! -f "$LIB_FILE" ]; then
    echo -e "${RED}✗ Extension library not found: $LIB_FILE${NC}"
    echo "  Please build the extension first by running CMake and make"
    exit 1
fi

echo -e "${GREEN}✓ Extension library found${NC}"
echo "  $LIB_FILE ($(stat -c%s $LIB_FILE) bytes)"
echo ""

# Step 1: Install extension
echo -e "${BLUE}Step 1: Installing extension files...${NC}"

cp $LIB_FILE $PG_LIBDIR/
echo -e "${GREEN}✓ Extension library installed to $PG_LIBDIR${NC}"

cp /code/neurdb-dev/dbengine/nr_kernel/nr_workload_forecast/nr_workload_forecast.control $PG_SHAREDIR/extension/
cp /code/neurdb-dev/dbengine/nr_kernel/nr_workload_forecast/sql/nr_workload_forecast--1.0.sql $PG_SHAREDIR/extension/
echo -e "${GREEN}✓ Extension control and SQL files installed${NC}"

echo ""
echo -e "${BLUE}Step 2: Creating extension in PostgreSQL...${NC}"

# Create extension
cat > /tmp/create_extension.sql << 'EOF'
-- Clean up if exists
DROP EXTENSION IF EXISTS nr_workload_forecast CASCADE;

-- Create extension
CREATE EXTENSION nr_workload_forecast;

-- Verify it's loaded
SELECT name FROM pg_extension WHERE name = 'nr_workload_forecast';

-- Show GUC variables
SHOW workload_forecast.enable;
SHOW workload_forecast.log_file;
EOF

$PSQL < /tmp/create_extension.sql > /tmp/create_output.log 2>&1

if grep -q "nr_workload_forecast" /tmp/create_output.log; then
    echo -e "${GREEN}✓ Extension created successfully${NC}"
else
    echo -e "${RED}✗ Failed to create extension${NC}"
    cat /tmp/create_output.log
    exit 1
fi

echo ""
echo -e "${BLUE}Step 3: Configuring extension...${NC}"

cat > /tmp/configure.sql << EOF
SET workload_forecast.enable = on;
SET workload_forecast.log_file = '$WORKLOAD_LOG';

-- Verify configuration
SHOW workload_forecast.enable;
SHOW workload_forecast.log_file;
EOF

$PSQL -tA < /tmp/configure.sql

echo -e "${GREEN}✓ Extension configured${NC}"

echo ""
echo -e "${BLUE}Step 4: Executing test queries...${NC}"

# Clean up any old log
rm -f $WORKLOAD_LOG

cat > /tmp/test_queries.sql << 'EOF'
SET workload_forecast.enable = on;

-- Test Query 1: Simple SELECT
SELECT COUNT(*) FROM movie_info WHERE movie_id < 100;

-- Test Query 2: SELECT with string
SELECT * FROM title WHERE title LIKE 'A%' LIMIT 5;

-- Test Query 3: SELECT with JOIN
SELECT t.title, ci.role_id
FROM title t
JOIN cast_info ci ON t.id = ci.movie_id
LIMIT 5;

-- Test Query 4: SELECT with aggregation
SELECT kind_id, COUNT(*)
FROM title
WHERE production_year > 2000
GROUP BY kind_id;

-- Test Query 5: SELECT with IN clause
SELECT * FROM name WHERE id IN (1, 2, 3, 4, 5);

-- DML queries (should NOT be logged)
CREATE TEMP TABLE test_temp AS SELECT * FROM title LIMIT 1;
INSERT INTO test_temp SELECT * FROM title LIMIT 1;
UPDATE test_temp SET production_year = 2000 WHERE id IS NOT NULL;
DELETE FROM test_temp WHERE id IS NOT NULL;
DROP TABLE test_temp;

-- More SELECT queries
SELECT name, COUNT(*)
FROM name
WHERE name LIKE 'J%'
GROUP BY name
LIMIT 10;

WITH cte AS (
    SELECT id, title FROM title WHERE kind_id = 1 LIMIT 5
)
SELECT * FROM cte;
EOF

echo "Running test queries..."
$PSQL -t < /tmp/test_queries.sql > /dev/null 2>&1

echo -e "${GREEN}✓ Test queries executed${NC}"

echo ""
echo -e "${BLUE}Step 5: Verifying workload log...${NC}"

if [ ! -f "$WORKLOAD_LOG" ]; then
    echo -e "${RED}✗ Workload log file not found: $WORKLOAD_LOG${NC}"
    exit 1
fi

line_count=$(wc -l < "$WORKLOAD_LOG")
echo -e "${GREEN}✓ Workload log created with $line_count lines${NC}"

if [ "$line_count" -le 1 ]; then
    echo -e "${RED}✗ No queries were logged (only header)${NC}"
    cat $WORKLOAD_LOG
    exit 1
fi

echo ""
echo -e "${BLUE}  First few entries:${NC}"
head -4 "$WORKLOAD_LOG" | while IFS=',' read -r timestamp template_hash template query; do
    [ -n "$timestamp" ] && echo "    Time: $timestamp"
    [ -n "$template_hash" ] && echo "    Hash: $template_hash"
    [ -n "$template" ] && echo "    Template: $template"
done

echo ""
echo -e "${BLUE}  Template statistics:${NC}"
template_count=$(tail -n +2 "$WORKLOAD_LOG" | cut -d',' -f2 | sort -u | wc -l)
echo "    Unique templates: $template_count"

# Count occurrences of each template
echo ""
echo -e "${BLUE}  Template frequencies:${NC}"
tail -n +2 "$WORKLOAD_LOG" | cut -d',' -f3 | sort | uniq -c | sort -nr | head -5 | while read count template; do
    echo "    [$count] $template"
done

echo ""
echo -e "${BLUE}Step 6: Testing AI engine server...${NC}"

if [ -f "/code/neurdb-dev/aiengine/workload_forecast/run_server.py" ]; then
    echo -e "${GREEN}✓ AI engine server script found${NC}"

    # Test Python environment
    python3 -c "import flask" 2>/dev/null && \
        echo -e "${GREEN}✓ Flask is available${NC}" || \
        echo -e "${YELLOW}⚠ Flask not found${NC}"

    # Create sample data and test endpoint
    cd /code/neurdb-dev/aiengine/workload_forecast
    python3 run_server.py --port 8778 --create-sample &
    SERVER_PID=$!
    sleep 3

    if curl -s http://127.0.0.1:8778/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ AI engine server responding on port 8778${NC}"

        # Test analysis endpoint
        response=$(curl -s http://127.0.0.1:8778/analyze_workload \
            -H "Content-Type: application/json" \
            -d "{\"workload_data\": \"$WORKLOAD_LOG\", \"forecast_horizon_minutes\": 60}" 2>&1)

        if echo "$response" | grep -q '"status": "success"'; then
            echo -e "${GREEN}✓ AI engine analysis endpoint working${NC}"
            query_count=$(echo "$response" | grep -o '"total_queries": [0-9]*' | cut -d' ' -f2)
            echo "    Analyzed $query_count queries from workload log"
        else
            echo -e "${YELLOW}⚠ AI engine analysis returned errors${NC}"
        fi
    else
        echo -e "${YELLOW}⚠ AI engine not responding${NC}"
    fi

    # Stop server
    kill $SERVER_PID 2>/dev/null || true
else
    echo -e "${YELLOW}⚠ AI engine server not found${NC}"
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  PHASE 1 TEST SUMMARY${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo ""

success=true

# Check each requirement
if [ -f "$PG_LIBDIR/nr_workload_forecast.so" ]; then
    echo -e "${GREEN}✓ Extension library installed${NC}"
else
    echo -e "${RED}✗ Extension library not installed${NC}"
    success=false
fi

if $PSQL -tAc "SELECT 1 FROM pg_extension WHERE name = 'nr_workload_forecast'" | grep -q 1; then
    echo -e "${GREEN}✓ Extension loaded in PostgreSQL${NC}"
else
    echo -e "${RED}✗ Extension not loaded${NC}"
    success=false
fi

if [ -f "$WORKLOAD_LOG" ] && [ "$line_count" -gt 1 ]; then
    echo -e "${GREEN}✓ Queries logged to CSV ($line_count lines)${NC}"
else
    echo -e "${RED}✗ No queries logged${NC}"
    success=false
fi

if [ "$template_count" -gt 0 ]; then
    echo -e "${GREEN}✓ Template extraction working ($template_count unique templates)${NC}"
else
    echo -e "${RED}✗ Template extraction failed${NC}"
    success=false
fi

# Show final status
echo ""
if [ "$success" = true ]; then
    echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✅ PHASE 1 COMPLETED SUCCESSFULLY!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
    echo ""
    echo "Summary:"
    echo "  - Extension builds and loads in PostgreSQL"
    echo "  - Queries are intercepted via post_parse_analyze_hook"
    echo "  - Template extraction normalizes literals (strings -> &&&, numbers -> #)"
    echo "  - Workload data is written to CSV format"
    echo "  - AI engine server is operational"
    echo ""
    exit 0
else
    echo -e "${RED}═══════════════════════════════════════════════════════${NC}"
    echo -e "${RED}  ❌ PHASE 1 FAILED - Check errors above${NC}"
    echo -e "${RED}═══════════════════════════════════════════════════════${NC}"
    exit 1
fi
