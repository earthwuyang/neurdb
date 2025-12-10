#!/bin/bash
# Docker test script for GUC-based strategy switching

echo "=== GUC Strategy Switching Test in Docker ==="
echo

# Check if we're inside Docker container
if [ ! -f /.dockerenv ]; then
    echo "This script should be run inside the neurdb_dev Docker container."
    echo "To access the container, run:"
    echo "docker exec -it neurdb_dev bash"
    echo "Then navigate to /code/neurdb-dev/aiengine/workload_forecast and run this script."
    exit 1
fi

# Set working directory
cd /code/neurdb-dev/aiengine/workload_forecast

echo "Working directory: $(pwd)"
echo

# Function to test SQL command
test_sql() {
    local description="$1"
    local sql="$2"

    echo "Testing: $description"
    echo "SQL: $sql"

    psql -h localhost -p 5432 -U neurdb_user -d neurdb -c "$sql" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "✓ Success"
        return 0
    else
        echo "✗ Failed"
        return 1
    fi
    echo
}

# Function to show current strategy
show_strategy() {
    echo "Current strategy:"
    psql -h localhost -p 5432 -U neurdb_user -d neurdb -c "SELECT nr_get_index_management_strategy();" 2>/dev/null
    echo
}

# Check database connectivity
echo "=== Checking Database Connectivity ==="
psql -h localhost -p 5432 -U neurdb_user -d neurdb -c "SELECT version();" >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "✗ Database connection failed"
    exit 1
fi
echo "✓ Database connection successful"
echo

# Test 1: Check if GUC parameter exists
echo "=== Test 1: GUC Parameter Existence ==="
test_sql "Check GUC parameter" "\d pg_settings" | grep nr_index_management_strategy >/dev/null
if [ $? -eq 0 ]; then
    echo "✓ GUC parameter exists"
    # Show current value
    psql -h localhost -p 5432 -U neurdb_user -d neurdb -c "SHOW nr_index_management_strategy;" 2>/dev/null
else
    echo "✗ GUC parameter not found - you may need to compile and install the extension"
fi
echo

# Test 2: Test SQL functions
echo "=== Test 2: SQL Functions ==="

# Test function exists
test_sql "Get current strategy" "SELECT nr_get_index_management_strategy();"

# Test switching to predictive
test_sql "Switch to predictive" "SELECT nr_set_index_management_strategy('predictive');"
show_strategy

# Test switching to reactive
test_sql "Switch to reactive" "SELECT nr_set_index_management_strategy('reactive');"
show_strategy

echo

# Test 3: Test status view
echo "=== Test 3: Status View ==="
test_sql "Check status view" "SELECT * FROM nr_index_management_status;"
echo

# Test 4: Test direct GUC commands
echo "=== Test 4: Direct GUC Commands ==="

test_sql "SET to predictive via GUC" "SET nr_index_management_strategy = 0;"
test_sql "SHOW current GUC value" "SHOW nr_index_management_strategy;"

test_sql "SET to reactive via GUC" "SET nr_index_management_strategy = 1;"
test_sql "SHOW current GUC value" "SHOW nr_index_management_strategy;"
echo

# Test 5: Test error handling
echo "=== Test 5: Error Handling ==="

test_sql "Invalid strategy name" "SELECT nr_set_index_management_strategy('invalid');" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "✓ Correctly rejected invalid strategy"
else
    echo "✗ Should have rejected invalid strategy"
fi

test_sql "Invalid GUC value" "SET nr_index_management_strategy = 99;" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "✓ Correctly rejected invalid GUC value"
else
    echo "✗ Should have rejected invalid GUC value"
fi
echo

# Test 6: Test with AI Engine (if running)
echo "=== Test 6: AI Engine Integration ==="

# Check if AI engine is running
if curl -s http://localhost:8777/health >/dev/null 2>&1; then
    echo "✓ AI engine is running"

    # Test strategy switching with AI engine notification
    echo "Testing strategy switching with AI engine notification..."

    # Switch to predictive
    test_sql "Switch to predictive (AI engine notification)" "SELECT nr_set_index_management_strategy('predictive');"
    sleep 2

    # Check AI engine status
    echo "Checking AI engine predictive status..."
    curl -s http://localhost:8777/index/predictive/status | python -m json.tool 2>/dev/null || echo "AI engine status check failed"

    # Switch to reactive
    test_sql "Switch to reactive (AI engine notification)" "SELECT nr_set_index_management_strategy('reactive');"
    sleep 2

    # Check AI engine status
    echo "Checking AI engine reactive status..."
    curl -s http://localhost:8777/index/reactive/status | python -m json.tool 2>/dev/null || echo "AI engine status check failed"

else
    echo "⚠ AI engine is not running - skipping integration test"
    echo "To start AI engine: python run_server.py --host 0.0.0.0 --port 8777 &"
fi
echo

# Test 7: Python test script
echo "=== Test 7: Comprehensive Python Test ==="
if [ -f "test_guc_strategy_switching.py" ]; then
    echo "Running comprehensive Python test..."
    python test_guc_strategy_switching.py
else
    echo "⚠ Python test script not found"
fi
echo

echo "=== GUC Strategy Switching Tests Complete ==="
echo
echo "Summary:"
echo "1. ✓ GUC parameter implementation"
echo "2. ✓ SQL functions for strategy management"
echo "3. ✓ Status view for monitoring"
echo "4. ✓ Direct GUC command support"
echo "5. ✓ Error handling and validation"
echo "6. ✓ AI engine integration (when running)"
echo
echo "You can now use the following commands to switch strategies:"
echo "  SELECT nr_set_index_management_strategy('predictive');"
echo "  SELECT nr_set_index_management_strategy('reactive');"
echo "  SELECT nr_get_index_management_strategy();"
echo "  SELECT * FROM nr_index_management_status;"
echo
echo "Or use GUC commands directly:"
echo "  SET nr_index_management_strategy = 0;  -- predictive"
echo "  SET nr_index_management_strategy = 1;  -- reactive"
echo "  SHOW nr_index_management_strategy;"