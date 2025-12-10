#!/bin/bash
# Fixed start script for workload forecast AI engine server
# This script uses the existing moqoe miniconda3 environment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}Starting NeurDB AI Engine Server...${NC}"

# Check if running from correct directory
if [[ ! -f "$SCRIPT_DIR/run_server.py" ]]; then
    echo -e "Error: run_server.py not found"
    echo "Please run this script from the aiengine/workload_forecast directory"
    exit 1
fi

# Use moqoe environment directly
MOQOE_PYTHON="/home/neurdb/miniconda3/envs/moqoe/bin/python"

if [[ -f "$MOQOE_PYTHON" ]]; then
    echo -e "${GREEN}✓ Using moqoe environment: $MOQOE_PYTHON${NC}"
    PYTHON_CMD="$MOQOE_PYTHON"
else
    echo -e "${YELLOW}Warning: moqoe environment not found, using current Python${NC}"
    PYTHON_CMD="python"
fi

# Set environment variables
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"
export WORKLOAD_FORECAST_LOG="/code/neurdb-dev/aiengine/workload_forecast/workload_forecast.log"

# Create log directory
mkdir -p "$(dirname "$WORKLOAD_FORECAST_LOG")"

echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  NeurDB Workload Forecast AI Engine${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "  Python: $PYTHON_CMD"
echo -e "  Python version: $($PYTHON_CMD --version)"
echo -e "  PYTHONPATH: $PYTHONPATH"
echo -e "  Log file: $WORKLOAD_FORECAST_LOG"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""

# Set default host and port if not provided
HOST=${HOST:-"0.0.0.0"}
PORT=${PORT:-8777}

echo -e "${BLUE}Starting server with host=$HOST, port=$PORT${NC}"

# Start server with default parameters
cd "$SCRIPT_DIR"
exec "$PYTHON_CMD" run_server.py --host "$HOST" --port "$PORT" "$@" 2>&1 | tee -a "$WORKLOAD_FORECAST_LOG"