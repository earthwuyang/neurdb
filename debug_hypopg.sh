#!/bin/bash

# Debugging script for NeurDB hypopg crash
# This script sets up gdb debugging for backend crashes

echo "=== NeurDB Debugging Setup ==="
echo "This script will help you debug the hypopg crash using gdb"
echo ""

# Check if NeurDB is running
echo "1. Checking if NeurDB is running..."
if ! pgrep -f "/code/neurdb-dev/dbengine/src/backend/postgres" > /dev/null; then
    echo "ERROR: NeurDB is not running!"
    exit 1
fi
echo "   ✓ NeurDB is running"
echo ""

# Function to start psql and get backend PID
start_debug_session() {
    echo "2. Starting psql session to get backend PID..."
    echo "   Run this command in psql to get the backend PID:"
    echo "   SELECT pg_backend_pid();"
    echo ""
    echo "3. In another terminal, attach gdb to that PID:"
    echo "   gdb /code/neurdb-dev/dbengine/src/backend/postgres <PID>"
    echo ""
    echo "4. In gdb, run these commands:"
    echo "   handle SIGPIPE nostop noprint"
    echo "   continue"
    echo ""
    echo "5. Back in psql, reproduce the crash:"
    echo "   SELECT hypopg_create_index('create index idx_large_test on cast_info(person_id)');"
    echo "   EXPLAIN (FORMAT JSON) SELECT * FROM cast_info WHERE person_id = 12345;"
    echo ""
    echo "6. When gdb stops at the crash, run:"
    echo "   bt full"
    echo "   info locals"
    echo "   list"
    echo ""

    # Start psql session
    echo "Starting psql session now..."
    /code/neurdb-dev/psql/bin/psql -h localhost -p 5432 -d imdb_test
}

# Function to enable core dumps
setup_core_dumps() {
    echo "Setting up core dumps for offline debugging..."
    echo "Run these commands to enable core dumps:"
    echo "   ulimit -c unlimited"
    echo "   sudo sysctl -w kernel.core_pattern=core.%e.%p.%t"
    echo ""
    echo "Then reproduce the crash and find the core file."
    echo "Open it with: gdb /code/neurdb-dev/dbengine/src/backend/postgres core.<e>.<p>.<t>"
}

# Main menu
case "${1:-menu}" in
    "attach")
        start_debug_session
        ;;
    "core")
        setup_core_dumps
        ;;
    "menu"|*)
        echo "Choose debugging method:"
        echo "1) $0 attach    - Start psql session for gdb attachment"
        echo "2) $0 core      - Set up core dump debugging"
        echo ""
        echo "For core dumps, you can also manually run:"
        echo "   ulimit -c unlimited"
        echo "   Then reproduce the crash"
        ;;
esac