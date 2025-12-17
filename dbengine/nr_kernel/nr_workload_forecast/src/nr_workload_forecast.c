/*
 * nr_workload_forecast.c
 *      PostgreSQL extension for workload forecasting and index recommendation
 */

#include "postgres.h"
#include "fmgr.h"
#include "miscadmin.h"
#include "optimizer/planner.h"
#include "utils/guc.h"
#include "utils/timestamp.h"
#include "utils/memutils.h"
#include "tcop/utility.h"
#include "parser/analyze.h"
#include "commands/extension.h"
#include "nodes/queryjumble.h"
#include "utils/builtins.h"
#include "lib/stringinfo.h"
#include "executor/spi.h"
#include "nr_workload_forecast.h"

PG_MODULE_MAGIC;

/* Saved hook values */
static post_parse_analyze_hook_type prev_post_parse_analyze_hook = NULL;

/* GUC variables */
WorkloadForecastConfig workload_forecast_config = {
    .enable = false,
    .server_url = "http://localhost:8777",
    .log_file = NULL,
    .analysis_interval = 60,
    .auto_apply_indexes = false,
    .log_rotation_size = 100 * 1024 * 1024  /* 100MB */
};

/* Local variables */
static bool extension_initialized = false;

/* Function declarations */
static bool validate_log_directory(char **newval, void **extra, GucSource source);

/* Extension initialization */
void
_PG_init(void)
{
    char *default_log_file = NULL;
    MemoryContext oldcontext;

    oldcontext = MemoryContextSwitchTo(TopMemoryContext);
    if (DataDir != NULL && DataDir[0] != '\0')
        default_log_file = psprintf("%s/%s", DataDir, "neurdb_workload.csv");
    else
        default_log_file = pstrdup("/tmp/neurdb_workload.csv");
    MemoryContextSwitchTo(oldcontext);

    /* Define custom GUC variables */
    DefineCustomBoolVariable(
        "workload_forecast.enable",
        "Enable workload forecasting and index recommendation",
        NULL,
        &workload_forecast_config.enable,
        false,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomStringVariable(
        "workload_forecast.server_url",
        "URL of the workload forecasting AI engine",
        NULL,
        &workload_forecast_config.server_url,
        "http://localhost:8777",
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomStringVariable(
        "workload_forecast.log_file",
        "Path to workload log file",
        NULL,
        &workload_forecast_config.log_file,
        default_log_file,
        PGC_SIGHUP,
        0,
        validate_log_directory,
        NULL,
        NULL
    );

    DefineCustomIntVariable(
        "workload_forecast.analysis_interval",
        "Analysis interval in minutes",
        NULL,
        &workload_forecast_config.analysis_interval,
        60,
        1,
        1440,
        PGC_SIGHUP,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomBoolVariable(
        "workload_forecast.auto_apply_indexes",
        "Automatically apply recommended indexes",
        NULL,
        &workload_forecast_config.auto_apply_indexes,
        false,
        PGC_SUSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomIntVariable(
        "workload_forecast.log_rotation_size",
        "Log rotation size in MB",
        NULL,
        &workload_forecast_config.log_rotation_size,
        100,
        10,
        10000,
        PGC_SIGHUP,
        0,
        NULL,
        NULL,
        NULL
    );

    /* Install hooks */
    prev_post_parse_analyze_hook = post_parse_analyze_hook;
    post_parse_analyze_hook = workload_forecast_post_parse_analyze;

    /* Ensure log directory exists */
    if (!ensure_log_directory(workload_forecast_config.log_file))
    {
        ereport(WARNING,
                (errmsg("Failed to create log directory for workload_forecast")));
    }

    extension_initialized = true;

    ereport(LOG,
            (errmsg("nr_workload_forecast extension initialized")));
}

/* Extension cleanup */
void
_PG_fini(void)
{
    /* Restore original hook */
    if (post_parse_analyze_hook == workload_forecast_post_parse_analyze)
    {
        post_parse_analyze_hook = prev_post_parse_analyze_hook;
    }

    /* Close workload log file if open */
    workload_log_cleanup();

    ereport(LOG,
            (errmsg("nr_workload_forecast extension unloaded")));
}

/* Hook function to intercept and log queries */
void
workload_forecast_post_parse_analyze(ParseState *pstate, Query *query, JumbleState *jstate)
{
    TimestampTz timestamp;
    const char *query_text;

    /* Call the previous hook if any */
    if (prev_post_parse_analyze_hook)
    {
        prev_post_parse_analyze_hook(pstate, query, jstate);
    }

    /* Log hook entry for debugging */
    ereport(DEBUG1,
            (errmsg("workload_forecast: hook called, enable is %s, commandType=%d",
                    workload_forecast_config.enable ? "true" : "false",
                    query->commandType)));

    /* Check if extension is enabled */
    if (!workload_forecast_config.enable)
    {
        return;
    }

    /* Only process SELECT queries */
    if (query->commandType != CMD_SELECT)
    {
        return;
    }

    /* Get query text */
    query_text = pstate->p_sourcetext;
    if (!query_text || strlen(query_text) == 0)
    {
        return;
    }

    /* Get current timestamp */
    timestamp = GetCurrentTimestamp();

    /* Log the query */
    query_logger_log_query(query_text, timestamp);

    /* Log debug info */
    ereport(DEBUG1,
            (errmsg("workload_forecast: logged query: %s", query_text)));
}

/* GUC check hook to validate log directory */
static bool
validate_log_directory(char **newval, void **extra, GucSource source)
{
    if (!ensure_log_directory(*newval))
    {
        GUC_check_errdetail("Cannot create parent directory for log file");
        return false;
    }
    return true;
}

/* SQL-callable function to trigger manual analysis */
PG_FUNCTION_INFO_V1(workload_forecast_analyze);
Datum
workload_forecast_analyze(PG_FUNCTION_ARGS)
{
    char *response;
    StringInfoData cmd;
    const char *url = psprintf("%s/analyze_workload", workload_forecast_config.server_url);

    if (!workload_forecast_config.enable)
    {
        ereport(WARNING,
                (errmsg("workload_forecast is disabled")));
        PG_RETURN_BOOL(false);
    }

    /* Prepare request payload */
    initStringInfo(&cmd);
    appendStringInfo(&cmd,
        "{\"workload_data\": \"%s\", "
        "\"forecast_horizon_minutes\": %d, "
        "\"perform_index_analysis\": true}",
        workload_forecast_config.log_file,
        workload_forecast_config.analysis_interval
    );

    /* Send request to AI engine */
    response = send_http_request(url, cmd.data);

    if (response == NULL)
    {
        ereport(WARNING,
                (errmsg("Failed to communicate with AI engine at %s", url)));
        PG_RETURN_BOOL(false);
    }

    ereport(LOG,
            (errmsg("workload analysis completed: %s", response)));

    pfree(cmd.data);
    pfree(response);

    PG_RETURN_BOOL(true);
}
