# GDB script to catch backend crash
set pagination off
set confirm off

# Wait for a postgres backend with "imdb_test" in its command line
# We'll attach when we find one doing EXPLAIN

# Define a function to find and attach to backend process
define attach_to_imdb_backend
    set $found = 0
    shell /bin/bash -c "ps aux | grep 'postgres: neurdb imdb_test' | grep -v grep | awk '{print \$2}'" > /tmp/backend_pids.txt
    shell while read pid; do echo "Checking PID $pid"; gdb -batch -ex "attach $pid" -ex "continue" -ex "quit" --pid=$pid 2>&1 | grep -q "No such process" && continue || echo $pid; done < /tmp/backend_pids.txt | head -1 > /tmp/found_pid.txt
    shell if [ -s /tmp/found_pid.txt ]; then echo "Found backend PID: $(cat /tmp/found_pid.txt)"; attach $(cat /tmp/found_pid.txt); else echo "No backend found"; exit; fi
end

# Set breakpoints for common crash scenarios
catch signal SIGSEGV
catch signal SIGABRT
catch signal SIGBUS
catch signal SIGFPE

# When a crash occurs, show full backtrace
commands
    echo "\n=== BACKEND CRASH DETECTED ===\n"
    thread apply all bt full
    echo "\n=== REGISTERS ===\n"
    info registers
    echo "\n=== CURRENT THREAD ===\n"
    thread info
    echo "\n=== LOCAL VARIABLES ===\n"
    info locals
    quit
end

# Enable logging
set logging file backend_crash.log
set logging on
set verbose on

echo "Waiting for backend crash..."
attach_to_imdb_backend