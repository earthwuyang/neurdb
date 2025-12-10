# GDB script to debug NeurDB HypoPG crashes
set pagination off
set confirm off

# Attach to the running process
attach 47726

# Set up breakpoints to catch crashes
catch signal
catch signal SIGSEGV
catch signal SIGABRT
catch signal SIGBUS

# Enable logging
set logging file gdb_debug.log
set logging on

# Print backtrace when any signal is caught
define bt_all
  thread apply all bt
  continue
end

# Set breakpoints in HypoPG-related functions (if symbols are available)
# breakpoint hypopg_create_index
# breakpoint hypopg_explain
# breakpoint explain_one_query

# Also break on common crash locations
break pg_fdw_report_error
break elog_finish
break ereport

# Show breakpoints
info breakpoints

# Print current stack
bt

# Continue execution and wait for crash
continue

# When crash occurs, print full backtrace
define handle_crash
  echo "\n=== CRASH DETECTED ===\n"
  thread apply all bt full
  echo "\n=== REGISTERS ===\n"
  info registers
  echo "\n=== LOCAL VARIABLES ===\n"
  thread apply all info locals
  echo "\n=== THREAD INFO ===\n"
  thread apply all info thread
  quit
end

# Continue after crash
commands
  handle_crash
end