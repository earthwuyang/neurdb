#!/bin/bash
# Docker setup script for testing index management strategies

echo "=== NeurDB Index Management Strategy Testing in Docker ==="
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

# Check if required Python packages are available
echo "Checking dependencies..."
python -c "import flask, psycopg2, sqlparse" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error: Required Python packages not found."
    echo "Installing missing packages..."
    pip install flask psycopg2-binary sqlparse requests numpy scikit-learn
fi

echo "✓ Dependencies check passed"
echo

# Check database connectivity
echo "Testing database connectivity..."
python -c "
import psycopg2
try:
    conn = psycopg2.connect(
        host='localhost',
        port=5432,
        database='neurdb',
        user='neurdb_user',
        password='neurdb_pass'
    )
    print('✓ Database connection successful')
    conn.close()
except Exception as e:
    print(f'✗ Database connection failed: {e}')
    exit(1)
"

if [ $? -ne 0 ]; then
    echo "Database connectivity test failed. Please check PostgreSQL is running."
    exit 1
fi

echo
echo "=== Starting Index Management Strategy Server ==="
echo

# Start the server in background
echo "Starting server on port 8777..."
python run_server.py --host 0.0.0.0 --port 8777 --debug > server.log 2>&1 &
SERVER_PID=$!

# Wait for server to start
echo "Waiting for server to start..."
sleep 5

# Check if server is running
if ! curl -s http://localhost:8777/health > /dev/null; then
    echo "✗ Server failed to start. Check server.log for details."
    kill $SERVER_PID 2>/dev/null
    cat server.log
    exit 1
fi

echo "✓ Server started successfully (PID: $SERVER_PID)"
echo

# Run the test script
echo "=== Running Index Management Strategy Tests ==="
echo

python test_index_strategies.py

# Clean up
echo
echo "=== Cleaning up ==="
echo "Stopping server..."
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null

echo "✓ Server stopped"
echo
echo "Test completed. Check the output above for results."