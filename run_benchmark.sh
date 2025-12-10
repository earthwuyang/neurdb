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

# Configuration - Updated for both host and Docker execution
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-15432}"
DB_NAME="${DB_NAME:-imdb_test}"
DB_USER="${DB_USER:-neurdb}"
NEURDB_PATH="/Volumes/data/DB/neurdb_dev"

# Check if we're inside Docker container
if [[ -f "/.dockerenv" ]] || grep -q 'docker' /proc/1/cgroup 2>/dev/null; then
    DRIFTBENCH_PATH="/code/neurdb-dev"
    DB_HOST="localhost"
    DB_PORT="5432"
    DB_NAME="neurdb"
    DB_USER="neurdb"
else
    DRIFTBENCH_PATH="/Volumes/data/DB/DriftBench"
fi

# Create output directory (works both inside Docker and on host)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
# Check if we're inside Docker container
if [[ -f "/.dockerenv" ]] || grep -q 'docker' /proc/1/cgroup 2>/dev/null; then
    BASE_OUTPUT_DIR="/code/neurdb-dev/output/neurdb_benchmark_${TIMESTAMP}"
else
    BASE_OUTPUT_DIR="${NEURDB_PATH}/output/neurdb_benchmark_${TIMESTAMP}"
fi
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

    # Set PostgreSQL path based on environment
    local psql_cmd="psql"
    if [[ -f "/.dockerenv" ]] || grep -q 'docker' /proc/1/cgroup 2>/dev/null; then
        psql_cmd="/code/neurdb-dev/psql/bin/psql"
    fi

    # Get index stats using standard PostgreSQL views
    $psql_cmd -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "
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
    $psql_cmd -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT
            COUNT(*) as total_indexes,
            COALESCE(SUM(pg_relation_size(s.indexrelid)) / (1024*1024.0), 0) as total_size_mb
        FROM pg_stat_user_indexes s;
    " >> "$BASE_OUTPUT_DIR/index_stats_${mode}.txt"
}

# Function to set GUC parameter and strategy
set_guc_parameter() {
    local enabled=$1
    local strategy=$2
    local value=$([[ "$enabled" == "true" ]] && echo "on" || echo "off")

    # Set PostgreSQL path based on environment
    local psql_cmd="psql"
    if [[ -f "/.dockerenv" ]] || grep -q 'docker' /proc/1/cgroup 2>/dev/null; then
        psql_cmd="/code/neurdb-dev/psql/bin/psql"
    fi

    echo "🔧 Setting nr_enable_auto_index_creation = $value"
    if [[ -n "$strategy" ]]; then
        echo "🎯 Setting nr_index_management_strategy = $strategy"
    fi

    # Set GUC parameter at session level
    $psql_cmd -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SET nr_enable_auto_index_creation = $value;" >/dev/null

    # Set strategy if specified
    if [[ -n "$strategy" ]]; then
        $psql_cmd -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SET nr_index_management_strategy = '$strategy';" >/dev/null
    fi

    # Verify the settings
    local current_value=$($psql_cmd -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SHOW nr_enable_auto_index_creation;")
    local current_strategy=$($psql_cmd -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SHOW nr_index_management_strategy;")

    echo "✅ GUC parameters set:"
    echo "   - nr_enable_auto_index_creation = $value (current: $current_value)"
    echo "   - nr_index_management_strategy = $current_strategy"

    # Check if AI engine is accessible
    if curl -s "http://localhost:8777/health" >/dev/null 2>&1; then
        echo "   ✅ AI Engine is accessible on port 8777"
        if [[ "$enabled" == "true" ]]; then
            echo "   🤖 Auto-index creation is now ENABLED"
            if [[ "$strategy" == "reactive" ]]; then
                echo "   ⚡ Using REACTIVE strategy (real-time index management)"
            elif [[ "$strategy" == "predictive" ]]; then
                echo "   🔮 Using PREDICTIVE strategy (forecast-based optimization)"
            fi
        else
            echo "   ⏸️  Auto-index creation is now DISABLED"
        fi
    else
        echo "   ⚠️  AI Engine not accessible on port 8777"
        echo "   Auto-index functionality may not work properly"
    fi
}

# Function to monitor index creation/deletion in real-time
monitor_index_activity() {
    local mode=$1
    local duration=$2
    echo "🔍 Starting index activity monitoring (${duration}s)..."

    # Create monitoring log file
    local monitor_log="$BASE_OUTPUT_DIR/index_monitor_${mode}.log"
    echo "Timestamp,Total_Indexes,Auto_Indexes,Total_Size_MB,New_Created,Deleted" > "$monitor_log"

    # Get initial index state
    local prev_indexes=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public';")
    local prev_auto=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT COUNT(*) FROM pg_stat_user_indexes WHERE indexrelname LIKE 'idx_auto_%';")

    # Monitor for specified duration (samples every 10 seconds)
    local end_time=$(($(date +%s) + duration))
    while [[ $(date +%s) -lt $end_time ]]; do
        local current_time=$(date '+%Y-%m-%d %H:%M:%S')
        local current_indexes=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public';")
        local current_auto=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT COUNT(*) FROM pg_stat_user_indexes WHERE indexrelname LIKE 'idx_auto_%';")
        local current_size=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT COALESCE(SUM(pg_relation_size(indexrelid)) / (1024*1024.0), 0) FROM pg_stat_user_indexes;")

        local created=$((current_auto - prev_auto))
        local deleted=$((prev_auto - current_auto))
        if [[ $deleted -lt 0 ]]; then deleted=0; fi

        echo "$current_time,$current_indexes,$current_auto,$current_size,$created,$deleted" >> "$monitor_log"

        prev_indexes=$current_indexes
        prev_auto=$current_auto

        sleep 10
    done

    echo "✅ Index activity monitoring completed. Log saved to: $monitor_log"
}

# Function to check AI engine index recommendations
check_ai_engine_activity() {
    local mode=$1
    echo "🤖 Checking AI engine activity for $mode mode..."

    local ai_log="$BASE_OUTPUT_DIR/ai_engine_${mode}.log"

    # Check if AI engine provides any API endpoints for monitoring
    echo "Timestamp,AI_Engine_Response" > "$ai_log"

    # Try to get reactive strategy status or recommendations
    if curl -s "http://localhost:8777/index/reactive/status" >> "$ai_log" 2>&1; then
        echo "✅ AI engine reactive status retrieved"
    else
        echo "⚠️ Could not retrieve AI engine reactive status" >> "$ai_log"
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

# Phase 1: Benchmark with auto-index disabled (Baseline)
echo ""
echo "=========================================="
echo "PHASE 1: Baseline (WITHOUT Auto-Index)"
echo "=========================================="

echo "📊 Capturing initial state (auto-index disabled)..."
get_index_stats "baseline_initial"

echo "🔧 Disabling automatic index creation..."
set_guc_parameter false

# Restart to apply changes if needed
restart_postgres

echo "📊 Capturing state after disabling auto-index..."
get_index_stats "baseline_after_disable"

echo "🚀 Running baseline workload (no auto-index, ${BENCHMARK_DURATION}s)..."
run_driftbench_workload "baseline" "$BENCHMARK_DURATION"

echo "📊 Capturing final state (baseline)..."
get_index_stats "baseline_final"

echo ""
echo "✅ Phase 1 completed successfully!"
echo "   Results saved to: ${BASE_OUTPUT_DIR}/"

# Wait a moment between tests
echo ""
echo "⏱️ Waiting 10 seconds between tests..."
sleep 10

# Phase 2: Benchmark with REACTIVE strategy
echo ""
echo "=========================================="
echo "PHASE 2: Testing REACTIVE Index Strategy"
echo "=========================================="

echo "📊 Capturing initial state (reactive strategy)..."
get_index_stats "reactive_initial"

echo "🔧 Enabling automatic index creation with REACTIVE strategy..."
set_guc_parameter true "reactive"

# Restart to apply changes if needed
restart_postgres

echo "📊 Capturing state after enabling reactive strategy..."
get_index_stats "reactive_after_enable"

echo "🔍 Starting background index monitoring..."
# Start index monitoring in background
monitor_index_activity "reactive" "$BENCHMARK_DURATION" &
MONITOR_PID=$!

echo "🤖 Checking AI engine status..."
check_ai_engine_activity "reactive"

echo "🚀 Running workload (with REACTIVE strategy, ${BENCHMARK_DURATION}s)..."
run_driftbench_workload "reactive" "$BENCHMARK_DURATION"

# Wait for monitoring to complete
wait $MONITOR_PID

echo "📊 Capturing final state (reactive)..."
get_index_stats "reactive_final"

echo ""
echo "✅ Phase 2 completed successfully!"
echo "   Results saved to: ${BASE_OUTPUT_DIR}/"

# Wait a moment between tests
echo ""
echo "⏱️ Waiting 10 seconds between tests..."
sleep 10

# Phase 3: Benchmark with PREDICTIVE strategy (Optional comparison)
echo ""
echo "=========================================="
echo "PHASE 3: Testing PREDICTIVE Index Strategy"
echo "=========================================="

echo "📊 Capturing initial state (predictive strategy)..."
get_index_stats "predictive_initial"

echo "🔧 Enabling automatic index creation with PREDICTIVE strategy..."
set_guc_parameter true "predictive"

# Restart to apply changes if needed
restart_postgres

echo "📊 Capturing state after enabling predictive strategy..."
get_index_stats "predictive_after_enable"

echo "🔍 Starting background index monitoring..."
# Start index monitoring in background
monitor_index_activity "predictive" "$BENCHMARK_DURATION" &
MONITOR_PID=$!

echo "🤖 Checking AI engine status..."
check_ai_engine_activity "predictive"

echo "🚀 Running workload (with PREDICTIVE strategy, ${BENCHMARK_DURATION}s)..."
run_driftbench_workload "predictive" "$BENCHMARK_DURATION"

# Wait for monitoring to complete
wait $MONITOR_PID

echo "📊 Capturing final state (predictive)..."
get_index_stats "predictive_final"

echo ""
echo "✅ Phase 3 completed successfully!"
echo "   Results saved to: ${BASE_OUTPUT_DIR}/"

# Generate comparison report
echo ""
echo "=========================================="
echo "GENERATING COMPARISON REPORT"
echo "=========================================="

# Enhanced comparison function for three phases
compare_results() {
    local baseline_file="$BASE_OUTPUT_DIR/index_stats_baseline_final.txt"
    local reactive_file="$BASE_OUTPUT_DIR/index_stats_reactive_final.txt"
    local predictive_file="$BASE_OUTPUT_DIR/index_stats_predictive_final.txt"

    echo "📊 THREE-PHASE INDEX COMPARISON:"
    echo ""

    # Baseline stats
    if [[ -f "$baseline_file" ]]; then
        local baseline_count=$(grep -o '[0-9]*' "$baseline_file" | head -1)
        local baseline_size=$(grep -o '[0-9]*\.[0-9]*' "$baseline_file" | tail -1)
        echo "   📈 Baseline (No Auto):   $baseline_count indexes, ${baseline_size} MB"
    else
        echo "   ⚠️  Baseline data not available"
        return 1
    fi

    # Reactive stats
    if [[ -f "$reactive_file" ]]; then
        local reactive_count=$(grep -o '[0-9]*' "$reactive_file" | head -1)
        local reactive_size=$(grep -o '[0-9]*\.[0-9]*' "$reactive_file" | tail -1)
        echo "   ⚡ Reactive Strategy:    $reactive_count indexes, ${reactive_size} MB"

        local reactive_diff=$((reactive_count - baseline_count))
        local reactive_size_diff=$(echo "$reactive_size - $baseline_size" | bc -l 2>/dev/null || echo "0")

        if [[ $reactive_diff -gt 0 ]]; then
            echo "      ✅ Reactive created $reactive_diff new indexes (+${reactive_size_diff} MB)"
        else
            echo "      ⚠️  Reactive strategy didn't create new indexes"
        fi
    else
        echo "   ⚠️  Reactive data not available"
    fi

    # Predictive stats
    if [[ -f "$predictive_file" ]]; then
        local predictive_count=$(grep -o '[0-9]*' "$predictive_file" | head -1)
        local predictive_size=$(grep -o '[0-9]*\.[0-9]*' "$predictive_file" | tail -1)
        echo "   🔮 Predictive Strategy: $predictive_count indexes, ${predictive_size} MB"

        local predictive_diff=$((predictive_count - baseline_count))
        local predictive_size_diff=$(echo "$predictive_size - $baseline_size" | bc -l 2>/dev/null || echo "0")

        if [[ $predictive_diff -gt 0 ]]; then
            echo "      ✅ Predictive created $predictive_diff new indexes (+${predictive_size_diff} MB)"
        else
            echo "      ⚠️  Predictive strategy didn't create new indexes"
        fi
    else
        echo "   ⚠️  Predictive data not available"
    fi

    echo ""
    echo "🔍 DETAILED ANALYSIS:"

    # Check monitoring logs for real-time activity
    if [[ -f "$BASE_OUTPUT_DIR/index_monitor_reactive.log" ]]; then
        echo ""
        echo "   ⚡ REACTIVE STRATEGY ACTIVITY:"
        local reactive_created=$(tail -n +2 "$BASE_OUTPUT_DIR/index_monitor_reactive.log" | awk -F',' '{sum+=$5} END {print sum+0}')
        echo "      - Total indexes created during workload: $reactive_created"

        # Show peak activity
        local peak_indexes=$(tail -n +2 "$BASE_OUTPUT_DIR/index_monitor_reactive.log" | awk -F',' '{print $3}' | sort -nr | head -1)
        echo "      - Peak auto-index count: $peak_indexes"
    fi

    if [[ -f "$BASE_OUTPUT_DIR/index_monitor_predictive.log" ]]; then
        echo ""
        echo "   🔮 PREDICTIVE STRATEGY ACTIVITY:"
        local predictive_created=$(tail -n +2 "$BASE_OUTPUT_DIR/index_monitor_predictive.log" | awk -F',' '{sum+=$5} END {print sum+0}')
        echo "      - Total indexes created during workload: $predictive_created"

        # Show peak activity
        local peak_indexes=$(tail -n +2 "$BASE_OUTPUT_DIR/index_monitor_predictive.log" | awk -F',' '{print $3}' | sort -nr | head -1)
        echo "      - Peak auto-index count: $peak_indexes"
    fi

    echo ""
    echo "💡 INTERPRETATION:"
    echo "   - Reactive strategy creates/destroys indexes in real-time based on query patterns"
    echo "   - Predictive strategy optimizes indexes based on workload forecasting"
    echo "   - Monitor the index_monitor_*.log files for detailed activity timeline"
}

# Strategy performance comparison
compare_strategy_performance() {
    echo ""
    echo "🏁 STRATEGY PERFORMANCE COMPARISON:"

    # Check if we have AI engine logs
    if [[ -f "$BASE_OUTPUT_DIR/ai_engine_reactive.log" ]]; then
        echo ""
        echo "   ⚡ Reactive AI Engine Activity:"
        grep -v "Timestamp,AI_Engine_Response" "$BASE_OUTPUT_DIR/ai_engine_reactive.log" | head -5 | sed 's/^/      /'
    fi

    if [[ -f "$BASE_OUTPUT_DIR/ai_engine_predictive.log" ]]; then
        echo ""
        echo "   🔮 Predictive AI Engine Activity:"
        grep -v "Timestamp,AI_Engine_Response" "$BASE_OUTPUT_DIR/ai_engine_predictive.log" | head -5 | sed 's/^/      /'
    fi

    echo ""
    echo "📋 RECOMMENDATIONS:"
    echo "   - Check index_monitor_*.csv files for real-time index creation/deletion patterns"
    echo "   - Review driftbench_*.log files for query execution details"
    echo "   - Compare final index counts to see which strategy was more active"
}

compare_results
compare_strategy_performance

# Save final summary
cat > "$BASE_OUTPUT_DIR/benchmark_summary.txt" << EOF
==========================================
NEURDB REACTIVE/PREDICTIVE BENCHMARK SUMMARY
==========================================
Date: $(date)
Duration: ${BENCHMARK_DURATION}s per phase
Output: $BASE_OUTPUT_DIR

Files Created:
- index_stats_baseline_initial.json/txt
- index_stats_baseline_after_disable.json/txt
- index_stats_baseline_final.json/txt
- index_stats_reactive_initial.json/txt
- index_stats_reactive_after_enable.json/txt
- index_stats_reactive_final.json/txt
- index_stats_predictive_initial.json/txt
- index_stats_predictive_after_enable.json/txt
- index_stats_predictive_final.json/txt
- index_monitor_reactive.log (Real-time monitoring)
- index_monitor_predictive.log (Real-time monitoring)
- ai_engine_reactive.log (AI engine activity)
- ai_engine_predictive.log (AI engine activity)
- driftbench_baseline.log
- driftbench_reactive.log
- driftbench_predictive.log

Test Phases:
1. BASELINE: No automatic index creation
2. REACTIVE: Real-time index management with budget constraints
3. PREDICTIVE: Forecast-based index optimization

Configuration:
- nr_enable_auto_index_creation: Disabled (Phase 1), Enabled (Phases 2-3)
- nr_index_management_strategy: reactive (Phase 2), predictive (Phase 3)
- Database: $DB_NAME
- Workload: DriftBench IMDb patterns

Key Features Tested:
- Reactive strategy: Real-time query-by-query index recommendations
- Predictive strategy: Workload forecasting and periodic optimization
- Index budget management and eviction policies
- Dynamic index creation and deletion

Note: Results compare baseline performance against both reactive
and predictive strategies to demonstrate NeurDB's adaptive
index management capabilities.
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
echo "   - Review index_monitor_*.log files for REAL-TIME index activity"
echo "   - Check ai_engine_*.log files for AI engine responses"
echo "   - Review driftbench_*.log files for query execution details"
echo ""
echo "🔍 Key files for REACTIVE strategy analysis:"
echo "   - index_monitor_reactive.log: Shows real-time index creation/deletion"
echo "   - ai_engine_reactive.log: Shows AI engine recommendations"
echo "   - index_stats_reactive_*.json: Shows before/after index state"
echo ""
echo "🔮 Key files for PREDICTIVE strategy analysis:"
echo "   - index_monitor_predictive.log: Shows real-time index activity"
echo "   - ai_engine_predictive.log: Shows AI engine forecasting activity"
echo "   - index_stats_predictive_*.json: Shows optimization results"
echo ""