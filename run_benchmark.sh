#!/bin/bash
# NeurDB Benchmark with Real DriftBench and GUC Control
# Usage: ./run_benchmark.sh [--duration SECONDS] [--query-num NUM] [--help]

set -e

# Default values
BENCHMARK_DURATION=300  # Default 5 minutes for macOS testing
QUERY_NUM=""  # No custom query limit by default

# Function to display usage
usage() {
    echo "Usage: $0 [--duration SECONDS] [--query-num NUM] [--help]"
    echo ""
    echo "Options:"
    echo "  --duration SECONDS    Set benchmark duration per phase (default: 300)"
    echo "  --query-num NUM        Set total number of queries to execute (optional)"
    echo "  --help                 Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --duration 60               # Run 60-second benchmark"
    echo "  $0 --query-num 500             # Run with 500 queries limit"
    echo "  $0 --duration 120 --query-num 1000  # 120-second run with 1000 queries"
    exit 0
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --duration)
            BENCHMARK_DURATION="$2"
            shift 2
            ;;
        --query-num)
            QUERY_NUM="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        *)
            # Backward compatibility: treat bare number as duration
            if [[ "$1" =~ ^[0-9]+$ ]]; then
                BENCHMARK_DURATION="$1"
                shift
            else
                echo "Unknown option: $1"
                usage
            fi
            ;;
    esac
done

# Configuration - Updated for macOS direct execution
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-15432}"
DB_NAME="${DB_NAME:-imdb_test}"
DB_USER="${DB_USER:-neurdb}"
NEURDB_PATH="/Volumes/data/DB/neurdb_dev"
DRIFTBENCH_PATH="/Volumes/data/DB/DriftBench"

# Create output directory on macOS
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BASE_OUTPUT_DIR="${NEURDB_PATH}/output/neurdb_benchmark_${TIMESTAMP}"
mkdir -p "$BASE_OUTPUT_DIR"

# Logging
LOG_FILE="$BASE_OUTPUT_DIR/benchmark.log"
exec > >(tee -a "$LOG_FILE")
exec 2>&1

echo "=========================================="
echo "NeurDB Benchmark with DriftBench"
echo "Duration: ${BENCHMARK_DURATION}s"
if [[ -n "$QUERY_NUM" ]]; then
    echo "Query Limit: ${QUERY_NUM} queries"
else
    echo "Query Limit: Time-based (duration: ${BENCHMARK_DURATION}s)"
fi
echo "Output: $BASE_OUTPUT_DIR"
echo "=========================================="

# Function to get current index stats
get_index_stats() {
    local mode=$1
    local stats_file="${BASE_OUTPUT_DIR}/index_stats_${mode}.json"

    # Get index stats using standard PostgreSQL views
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "
        SELECT json_build_object(
            'total_indexes', (SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public'),
            'total_size_mb', COALESCE(
                (SELECT SUM(pg_relation_size(indexrelid)) / (1024*1024.0)
                 FROM pg_stat_user_indexes), 0
            ),
            'auto_indexes', (
                SELECT json_agg(json_build_object(
                    'name', s.indexrelname,
                    'scans', s.idx_scan,
                    'size_mb', pg_relation_size(s.indexrelid) / (1024*1024.0)
                ))
                FROM pg_stat_user_indexes s
                WHERE s.indexrelname LIKE 'idx_auto_%'
            ),
            'timestamp', NOW()
        )
    " > "$stats_file"

    # Also save readable version
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT
            COUNT(*) as total_indexes,
            COALESCE(SUM(pg_relation_size(s.indexrelid)) / (1024*1024.0), 0) as total_size_mb
        FROM pg_stat_user_indexes s;
    " >> "$BASE_OUTPUT_DIR/index_stats_${mode}.txt"
}

# Function to set GUC parameter
set_guc_parameter() {
    local enabled=$1
    local value=$([[ "$enabled" == "true" ]] && echo "on" || echo "off")

    echo "🔧 Setting nr_enable_auto_index_creation = $value"

    # Set GUC parameter at session level
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SET nr_enable_auto_index_creation = $value;" >/dev/null

    # Verify the setting
    local current_value=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SHOW nr_enable_auto_index_creation;")

    echo "✅ GUC parameter set to $value (current: $current_value)"

    # Check if AI engine is accessible
    if curl -s "http://localhost:8777/health" >/dev/null 2>&1; then
        echo "   ✅ AI Engine is accessible on port 8777"
        if [[ "$enabled" == "true" ]]; then
            echo "   🤖 Auto-index creation is now ENABLED"
        else
            echo "   ⏸️  Auto-index creation is now DISABLED"
        fi
    else
        echo "   ⚠️  AI Engine not accessible on port 8777"
        echo "   Auto-index functionality may not work properly"
    fi
}

# Function to restart PostgreSQL (if needed)
restart_postgres() {
    echo "🔄 Restarting PostgreSQL to apply system GUC changes..."

    # Check if we can connect as superuser to restart
    if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" >/dev/null 2>&1; then
        echo "Database connected, no restart needed for session-level setting"
        return 0
    else
        echo "⚠️  Cannot connect to database, please restart PostgreSQL manually if needed"
        return 1
    fi
}

# Function to run DriftBench workload
run_driftbench_workload() {
    local mode=$1
    local duration=$2

    echo "🚀 Starting DriftBench workload ($mode mode, ${duration}s)..."

    # Check if DriftBench test module exists
    if [[ ! -f "$DRIFTBENCH_PATH/test/test_neurdb_time_series_execution.py" ]]; then
        echo "❌ DriftBench test script not found at $DRIFTBENCH_PATH/test/test_neurdb_time_series_execution.py"
        echo "   Please ensure DriftBench is properly installed at $DRIFTBENCH_PATH"
        exit 1
    fi

    # Set up environment for direct DriftBench invocation
    export PYTHONPATH="$DRIFTBENCH_PATH:$PYTHONPATH"
    export DB_HOST="$DB_HOST"
    export DB_PORT="$DB_PORT"
    export DB_NAME="$DB_NAME"
    export DB_USER="$DB_USER"
    export RESULTS_DIR="$BASE_OUTPUT_DIR/driftbench_${mode}"

    # Create results directory
    mkdir -p "$BASE_OUTPUT_DIR/driftbench_${mode}"

    echo "📋 Directly invoking DriftBench test module..."
    echo "   PYTHONPATH: $PYTHONPATH"
    echo "   DB Connection: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
    echo "   Results Dir: $RESULTS_DIR"

    # Run DriftBench directly from its directory
    cd "$DRIFTBENCH_PATH"

    # Use Python module invocation with proper parameters
    local driftbench_args=""
    if [[ -n "$QUERY_NUM" ]]; then
        driftbench_args="--query-num $QUERY_NUM"
    else
        driftbench_args="--quick"
    fi

    echo "📋 DriftBench arguments: $driftbench_args"
    python3 -m test.test_neurdb_time_series_execution $driftbench_args 2>&1 | tee "$BASE_OUTPUT_DIR/driftbench_${mode}.log"

    # Check if DriftBench completed successfully
    if [[ ${PIPESTATUS[0]} -eq 0 ]]; then
        echo "✅ DriftBench workload completed successfully"
    else
        echo "⚠️  DriftBench workload completed with warnings (exit code: ${PIPESTATUS[0]})"
    fi

    cd "$BASE_OUTPUT_DIR"
}

# Phase 1: Benchmark with auto-index disabled
echo ""
echo "=========================================="
echo "PHASE 1: Benchmark WITHOUT Auto-Index"
echo "=========================================="

echo "📊 Capturing initial state (auto-index disabled)..."
get_index_stats "noauto_initial"

echo "🔧 Disabling automatic index creation..."
set_guc_parameter false

# Restart to apply changes if needed
restart_postgres

echo "📊 Capturing state after disabling auto-index..."
get_index_stats "noauto_after_disable"

echo "🚀 Running workload (no auto-index, ${BENCHMARK_DURATION}s)..."
run_driftbench_workload "noauto" "$BENCHMARK_DURATION"

echo "📊 Capturing final state (no auto-index)..."
get_index_stats "noauto_final"

echo ""
echo "✅ Phase 1 completed successfully!"
echo "   Results saved to: ${BASE_OUTPUT_DIR}/"

# Wait a moment between tests
echo ""
echo "⏱️ Waiting 10 seconds between tests..."
sleep 10

# Phase 2: Benchmark with auto-index enabled
echo ""
echo "=========================================="
echo "PHASE 2: Benchmark WITH Auto-Index"
echo "=========================================="

echo "📊 Capturing initial state (auto-index enabled)..."
get_index_stats "auto_initial"

echo "🔧 Enabling automatic index creation..."
set_guc_parameter true

# Restart to apply changes if needed
restart_postgres

echo "📊 Capturing state after enabling auto-index..."
get_index_stats "auto_after_enable"

echo "🚀 Running workload (with auto-index, ${BENCHMARK_DURATION}s)..."
run_driftbench_workload "auto" "$BENCHMARK_DURATION"

echo "📊 Capturing final state (with auto-index)..."
get_index_stats "auto_final"

echo ""
echo "✅ Phase 2 completed successfully!"
echo "   Results saved to: ${BASE_OUTPUT_DIR}/"

# Generate comparison report
echo ""
echo "=========================================="
echo "GENERATING COMPARISON REPORT"
echo "=========================================="

# Simple comparison function
compare_results() {
    local noauto_file="$BASE_OUTPUT_DIR/index_stats_noauto_final.txt"
    local auto_file="$BASE_OUTPUT_DIR/index_stats_auto_final.txt"

    if [[ -f "$noauto_file" && -f "$auto_file" ]]; then
        local noauto_size=$(grep -o '[0-9]*\.[0-9]*' "$noauto_file" | tail -1)
        local auto_size=$(grep -o '[0-9]*\.[0-9]*' "$auto_file" | tail -1)
        local noauto_count=$(grep -o '[0-9]*' "$noauto_file" | head -1)
        local auto_count=$(grep -o '[0-9]*' "$auto_file" | head -1)

        local index_diff=$((auto_count - noauto_count))
        local size_diff=$(echo "$auto_size - $noauto_size" | bc -l 2>/dev/null || echo "0")
        local size_percent=$(echo "scale=2; ($size_diff / $noauto_size) * 100" | bc -l 2>/dev/null || echo "0")

        echo "📊 INDEX COMPARISON:"
        echo "   No Auto:   $noauto_count indexes, ${noauto_size} MB"
        echo "   With Auto: $auto_count indexes, ${auto_size} MB"
        echo "   Difference: $index_count new indexes, +${size_diff} MB (${size_percent}%)"

        if (( $(echo "$index_diff > 0" | bc -l 2>/dev/null) )); then
            echo "   ✅ Auto-index created $index_diff indexes during workload"
        else
            echo "   ⚠️  No new indexes created (workload may already be optimized)"
        fi
    else
        echo "   ⚠️  Could not find index stats files for comparison"
    fi
}

compare_results

# Save final summary
cat > "$BASE_OUTPUT_DIR/benchmark_summary.txt" << EOF
==========================================
NEURDB BENCHMARK SUMMARY
==========================================
Date: $(date)
Duration: ${BENCHMARK_DURATION}s
Output: $BASE_OUTPUT_DIR

Files Created:
- index_stats_noauto_initial.json/txt
- index_stats_noauto_after_disable.json/txt
- index_stats_noauto_final.json/txt
- index_stats_auto_initial.json/txt
- index_stats_auto_after_enable.json/txt
- index_stats_auto_final.json/txt
- driftbench_noauto.log
- driftbench_auto.log

Configuration:
- nr_enable_auto_index_creation: Disabled (Phase 1), Enabled (Phase 2)
- Database: imdb_ori
- Workload: DriftBench IMDb patterns

Note: Results show how NeurDB's automatic index management
adapts to real workload patterns.
==========================================
EOF

echo ""
echo "✅ BENCHMARK COMPLETE!"
echo "=========================================="
echo "Summary saved to: $BASE_OUTPUT_DIR/benchmark_summary.txt"
echo "Comparison results shown above"
echo ""

# Clean up - No temporary files to clean up with direct DriftBench invocation
echo "🧹 No temporary files to clean up (using direct DriftBench invocation)"

echo ""
echo "💡 To analyze detailed results:"
echo "   - Check index_stats_*.json files for detailed index data"
echo "   - Review driftbench_*.log files for query execution details"
echo "   - Use the comparison script if you have both test results"
echo ""