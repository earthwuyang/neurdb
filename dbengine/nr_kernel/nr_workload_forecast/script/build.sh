#!/bin/bash
# Build script for nr_workload_forecast extension

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_ROOT/build"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Building nr_workload_forecast extension${NC}"
echo -e "${BLUE}========================================${NC}"

# Create build directory
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Configure with CMake
echo -e "${GREEN}Configuring with CMake...${NC}"
cmake .. -DCMAKE_BUILD_TYPE=Release

# Build
echo -e "${GREEN}Building extension...${NC}"
make -j$(nproc)

# Check if built successfully
if [ -f "libnr_workload_forecast.so" ]; then
    echo -e "${GREEN}✓ Build successful!${NC}"
    echo -e "${GREEN}  Output: libnr_workload_forecast.so${NC}"
else
    echo -e "${RED}✗ Build failed${NC}"
    exit 1
fi

# Run basic tests if requested
if [ "$1" == "--test" ]; then
    echo -e "${BLUE}Running basic tests...${NC}"

    # Test 1: Check if library has required symbols
    if command -v nm >/dev/null 2>&1; then
        if nm -D libnr_workload_forecast.so | grep -q "workload_forecast_post_parse_analyze"; then
            echo -e "${GREEN}✓ Hook symbol found${NC}"
        else
            echo -e "${RED}✗ Hook symbol not found${NC}"
            exit 1
        fi
    fi

    # Test 2: Check if library loads
    if command -v ldd >/dev/null 2>&1; then
        if ldd libnr_workload_forecast.so | grep -q "not found"; then
            echo -e "${RED}✗ Missing dependencies${NC}"
            ldd libnr_workload_forecast.so
            exit 1
        else
            echo -e "${GREEN}✓ All dependencies resolved${NC}"
        fi
    fi

    echo -e "${GREEN}✓ All tests passed!${NC}"
fi

# Show next steps
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Next steps:${NC}"
echo -e "  1. Install PostgreSQL development headers:"
echo -e "     sudo apt-get install postgresql-server-dev-$(pg_config --version | cut -d' ' -f2 | cut -d. -f1)"
echo -e ""
echo -e "  2. Install the extension:"
echo -e "     sudo make install"
echo -e ""
echo -e "  3. In PostgreSQL, run:"
echo -e "     CREATE EXTENSION nr_workload_forecast;"
echo -e ""
echo -e "  4. Configure in postgresql.conf:"
echo -e "     workload_forecast.enable = on"
echo -e "     workload_forecast.log_file = '/tmp/neurdb_workload.csv'"
echo -e "${BLUE}========================================${NC}"
