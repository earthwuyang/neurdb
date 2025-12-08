# Phase 1 Verification Checklist

This document lists what needs to be verified in the Docker environment before marking Phase 1 as complete.

## Pre-Docker Verification Status

### ✅ Completed (Code Implementation)
- [x] Directory structure created
- [x] PostgreSQL extension skeleton implemented
- [x] Query templatization implemented
- [x] Workload logging to CSV implemented
- [x] HTTP client in C implemented
- [x] AI engine HTTP server implemented in Python
- [x] Build system (CMake) configured
- [x] SQL and control files created
- [x] Start script created (using existing miniconda environment)
- [x] Test script created for Docker environment

### ⏳ Pending (Docker Testing Required)

These items MUST be verified by running the test script in Docker:

#### 1. Compilation
- [ ] Extension compiles without errors in Docker
  - Command: `cd /code/neurdb-dev/dbengine/nr_kernel/nr_workload_forecast && ./script/build.sh`
  - Expected: `libnr_workload_forecast.so` built successfully
  - Check for: Compiler errors, missing headers, linking issues

#### 2. Installation
- [ ] Extension installs correctly in PostgreSQL
  - Library copied to `$(pg_config --pkglibdir)/`
  - Control and SQL files copied to `$(pg_config --sharedir)/extension/`
  - No permission errors

#### 3. Extension Loading
- [ ] Extension can be created in PostgreSQL
  - Command: `psql -h localhost -p 15432 -U neurdb -d imdb_ori -c "CREATE EXTENSION nr_workload_forecast"`
  - Expected: No errors
  - Check: `SELECT name FROM pg_extension WHERE name = 'nr_workload_forecast'`

#### 4. GUC Variables
- [ ] GUC variables are recognized
  - Command: `SHOW workload_forecast.enable`
  - Command: `SHOW workload_forecast.log_file`
  - Expected: Shows default values

#### 5. Query Interception
- [ ] `post_parse_analyze_hook` is installed
  - Enable: `SET workload_forecast.enable = on`
  - Should activate the hook for SELECT queries
  - Check: Queries appear in workload log

#### 6. Template Extraction
- [ ] Query templatization works correctly
  - Test queries with:
    - String literals
    - Numeric literals
    - Hex/binary literals
    - CTEs
    - Subqueries
  - Expected outcome: Literals replaced with placeholders in log

#### 7. Workload Logging
- [ ] CSV log file created
  - Location: `/tmp/neurdb_workload_test.csv`
  - Contains: timestamp, template_hash, template, original_query
  - Format: Properly escaped CSV

#### 8. SELECT Query Filtering
- [ ] Only SELECT queries are logged
  - INSERT/UPDATE/DELETE should NOT appear in log
  - Test: Run mix of DML and SELECT, verify only SELECT logged

#### 9. AI Engine Server
- [ ] Python server starts successfully
  - Command: `cd /code/neurdb-dev/aiengine/workload_forecast && ./run_server.sh`
  - Check: No import errors
  - Test: `curl http://127.0.0.1:8777/health` returns 200

#### 10. End-to-End Integration
- [ ] Complete test script runs without errors
  - Command: `./test/test_phase1_docker.sh`
  - Expected: All 10 steps pass
  - Final output shows "Phase 1 Complete"

## Known Potential Issues to Watch For

### Compilation Issues
1. Missing PostgreSQL headers: Ensure `postgresql-server-dev` is installed
2. Missing OpenSSL: Required for MD5 functions
3. CMake version: Need 3.10+

### Runtime Issues
1. Hook not called: Verify `post_parse_analyze_hook` assignment in `_PG_init`
2. Queries not logged: Check `is_select_query` function logic
3. Log file permissions: Ensure `/tmp` is writable
4. GUC not recognized: Check DefineCustom*Variable calls

### Integration Issues
1. Python dependencies: Verify Flask is available
2. HTTP connection: Check curl is available for HTTP client
3. Port conflicts: Ensure port 8777 is not in use

## How to Run Verification

```bash
# Enter Docker container
docker exec -it neurdb_dev bash

# Navigate to project directory
cd /code/neurdb-dev

# Run verification test
./test/test_phase1_docker.sh

# Check individual components if needed
psql -h localhost -p 15432 -U neurdb -d imdb_ori
```

## Success Criteria

Phase 1 is **ONLY** complete when ALL items in the "Pending (Docker Testing Required)" section are checked off.

## Debugging Commands

```bash
# Check extension library
cd /code/neurdb-dev/dbengine/nr_kernel/nr_workload_forecast/build
ldd libnr_workload_forecast.so
nm -D libnr_workload_forecast.so | grep workload_forecast

# Check PostgreSQL logs
pg_ctl -D /var/lib/postgresql/data logfile

# Check workload log
tail -f /tmp/neurdb_workload_test.csv

# Test template extraction manually
psql -c "SELECT workload_forecast_analyze()"

# Check if hook is active (debug mode)
psql -c "SHOW workload_forecast.enable"
```

## Report Template

After running tests in Docker, fill out:

```
Date of Test:
Docker Image Version:
Test Script Version:

Compilation: [PASS/FAIL]
Installation: [PASS/FAIL]
Extension Loading: [PASS/FAIL]
GUC Variables: [PASS/FAIL]
Query Interception: [PASS/FAIL]
Template Extraction: [PASS/FAIL]
Workload Logging: [PASS/FAIL]
SELECT Filtering: [PASS/FAIL]
AI Engine Server: [PASS/FAIL]
End-to-End Test: [PASS/FAIL]

Issues Found:
-
-

Phase 1 Status: [COMPLETE/INCOMPLETE]
```

**DO NOT** mark Phase 1 as complete until this checklist is fully verified in Docker.
