/*
 * NeurDB Index Management Extension
 * Provides automatic index creation and storage budget management
 */

#include "postgres.h"
#include "fmgr.h"
#include "miscadmin.h"
#include "access/xact.h"
#include "catalog/pg_type.h"
#include "utils/builtins.h"
#include "utils/lsyscache.h"
#include "utils/timestamp.h"
#include "funcapi.h"
#include "utils/jsonb.h"
#include "executor/spi.h"
#include "utils/snapmgr.h"
#include "utils/syscache.h"
#include "utils/acl.h"
#include "catalog/pg_class.h"
#include "catalog/pg_index.h"
#include "utils/guc.h"  /* PostgreSQL GUC API */
#include "utils/fmgrprotos.h"

#include "neurdb/guc.h"

/* Hook function declarations */
#include "parser/analyze.h"
#include "tcop/utility.h"
#include "nodes/queryjumble.h"
#include "utils/ps_status.h"
#include "catalog/namespace.h"
#include "utils/relcache.h"
#include "optimizer/optimizer.h"
#include "nodes/nodeFuncs.h"
#include "nodes/bitmapset.h"
#include "utils/typcache.h"
#include "parser/parsetree.h"
#include "postmaster/bgworker.h"
#include "storage/latch.h"
#include "storage/procsignal.h"
#include "storage/ipc.h"
#include "storage/lock.h"
#include "storage/lmgr.h"
#include "tcop/tcopprot.h"
#include "common/hashfn.h"
#include "utils/wait_event.h"
#include "commands/dbcommands.h"
#include <unistd.h>
#include <fcntl.h>
#include <sys/wait.h>

/* Global variables for hooks */
static post_parse_analyze_hook_type prev_post_parse_analyze_hook = NULL;

/* v2 GUCs (extension-local) */
static bool nr_reactive_enable = true;
static int nr_reactive_max_columns_per_index = 3;
static int nr_reactive_max_candidates_per_query = 20;
static int nr_reactive_worker_interval_ms = 200;
static int nr_reactive_max_hypopg_evals_per_minute = 30;
static double nr_reactive_min_value_improvement = 0.20;
static bool nr_reactive_use_concurrently = true;
static bool nr_reactive_debug = false;

/* Worker state (Phase 5: best-effort, table-backed queue) */
static volatile sig_atomic_t nrim_worker_sigterm = false;
static bool nrim_worker_started = false;
static volatile sig_atomic_t nrim_worker_got_sighup = false;

/* Function prototypes */
static const char *get_current_statement_text(ParseState *pstate, Query *query);
static bool is_select_statement_text(const char *stmt);
static bool query_references_only_user_tables(Query *query);
static bool is_system_schema_name(const char *nspname);
static bool is_ai_engine_session(void);
static char *nrim_build_candidates_json(Query *query);
static bool nrim_enqueue_work_item(const char *query_text, const char *candidates_text);
static void nrim_start_worker_if_needed(void);
PGDLLEXPORT void nrim_worker_main(Datum main_arg);
static void nrim_worker_sigterm_handler(SIGNAL_ARGS);
static void nrim_worker_sighup_handler(SIGNAL_ARGS);
static bool nrim_process_one_work_item(Oid dboid);
static uint64 nrim_query_id_from_text(const char *query_text);
static bool nrim_worker_update_query_facts(uint64 query_id, const char *query_text);
static bool nrim_worker_refresh_registry_stats(void);
static bool nrim_worker_explain_total_cost(const char *query_text, double *total_cost_out, char **errmsg_out);
static bool nrim_extract_total_cost_from_explain_json(const char *plan_json, double *total_cost_out);
static bool nrim_worker_hypopg_available(void);
static bool nrim_worker_index_exists(Oid relid, AttrNumber *cols, int ncols);
static bool nrim_worker_ensure_registry_for_index(Oid index_oid,
                                                  Oid relid,
                                                  AttrNumber *cols,
                                                  int ncols,
                                                  const char *index_method,
                                                  double benefit_score);
static bool nrim_worker_update_attribution(uint64 query_id, Oid index_oid, double marginal_benefit);
static bool nrim_worker_evict_until_fits(double needed_mb, double candidate_value, double *freed_mb_out);
static bool nrim_worker_run_psql_utility(Oid dboid, const char *sql);
static char *nrim_worker_build_index_name(Oid relid, AttrNumber *cols, int ncols, uint64 query_id);
static bool nrim_parse_candidates_json(const char *candidates_json, List **out_cands);
static bool nrim_worker_has_default_btree_opclass(Oid typoid);
static bool collect_var_attnum(Query *query, Var *var, Oid *relid_out, AttrNumber *attnum_out);
static bool is_equality_op(Oid opno);
static bool is_range_op(Oid opno);

#define NRIM_ELOG(fmt, ...) \
    ereport(nr_reactive_debug ? LOG : DEBUG1, (errmsg(fmt, ##__VA_ARGS__)))

static char *nrim_preview_text(const char *s, int max_bytes);
void index_management_post_parse_analyze(ParseState *pstate, Query *query, JumbleState *jstate);

PG_MODULE_MAGIC;

/* Helper function to get index size in MB */
static double
get_index_size_mb(Oid indexOid)
{
    StringInfoData buf;
    double size_mb = 0.0;

    initStringInfo(&buf);
    appendStringInfo(&buf, "SELECT pg_relation_size(%u)::double precision / 1024.0 / 1024.0", indexOid);

    if (SPI_execute(buf.data, true, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        bool isnull;
        Datum size_datum = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
        if (!isnull)
            size_mb = DatumGetFloat8(size_datum);
    }

    return size_mb;
}

/* Calculate current total index storage usage */
PG_FUNCTION_INFO_V1(nr_get_current_index_storage_mb);

Datum
nr_get_current_index_storage_mb(PG_FUNCTION_ARGS)
{
    double total_size_mb = 0.0;

    if (SPI_connect() != SPI_OK_CONNECT)
        elog(ERROR, "SPI_connect failed");

    if (SPI_execute(
        "SELECT sum(pg_relation_size(pg_index.indexrelid))::double precision / 1024.0 / 1024.0 "
        "FROM pg_index JOIN pg_class ON pg_class.oid = pg_index.indexrelid "
        "WHERE pg_class.relkind = 'i' AND pg_class.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')",
        true, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        bool isnull;
        Datum total_datum = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
        if (!isnull)
            total_size_mb = DatumGetFloat8(total_datum);
    }

    SPI_finish();

    PG_RETURN_FLOAT8(total_size_mb);
}

/* Get database size in MB */
PG_FUNCTION_INFO_V1(nr_get_database_size_mb);

Datum
nr_get_database_size_mb(PG_FUNCTION_ARGS)
{
    double db_size_mb = 0.0;

    if (SPI_connect() != SPI_OK_CONNECT)
        elog(ERROR, "SPI_connect failed");

    if (SPI_execute("SELECT pg_database_size(current_database())::double precision / 1024.0 / 1024.0", true, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        bool isnull;
        Datum size_datum = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
        if (!isnull)
            db_size_mb = DatumGetFloat8(size_datum);
    }

    SPI_finish();

    PG_RETURN_FLOAT8(db_size_mb);
}

/* Calculate index budget (half of database size if not explicitly set) */
PG_FUNCTION_INFO_V1(nr_calculate_index_budget_mb);

Datum
nr_calculate_index_budget_mb(PG_FUNCTION_ARGS)
{
    double budget_mb;

    /* Primary method: use PostgreSQL's GUC API to get the current setting */
    const char *guc_value = GetConfigOption("nr_max_index_storage_mb", false, false);
    if (guc_value != NULL)
    {
        budget_mb = atof(guc_value);
        if (budget_mb > 0.0)
        {
            elog(DEBUG1, "Using GUC nr_max_index_storage_mb = %.2f MB", budget_mb);
            PG_RETURN_FLOAT8(budget_mb);
        }
    }

    /* If GUC is 0 or not set, calculate as half of database size */
    if (SPI_connect() != SPI_OK_CONNECT)
        elog(ERROR, "SPI_connect failed");

    if (SPI_execute("SELECT pg_database_size(current_database())::double precision / 1024.0 / 1024.0", true, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        bool isnull;
        Datum size_datum = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
        if (!isnull)
        {
            double db_size_mb = DatumGetFloat8(size_datum);
            budget_mb = db_size_mb / 2.0;  /* Half of database size */
            SPI_finish();
            elog(DEBUG1, "Using calculated budget (50%% of DB size) = %.2f MB", budget_mb);
            PG_RETURN_FLOAT8(budget_mb);
        }
    }

    SPI_finish();

    /* Ultimate fallback */
    elog(DEBUG1, "Using default budget of 1057.10 MB");
    PG_RETURN_FLOAT8(1057.10);
}

/* Create index if budget allows */
PG_FUNCTION_INFO_V1(nr_create_index_if_budget_allows);

Datum
nr_create_index_if_budget_allows(PG_FUNCTION_ARGS)
{
    text *index_name_text = PG_GETARG_TEXT_P(0);
    text *table_name_text = PG_GETARG_TEXT_P(1);
    ArrayType *columns_array = PG_GETARG_ARRAYTYPE_P(2);
    text *index_type_text = PG_ARGISNULL(3) ? NULL : PG_GETARG_TEXT_P(3);

    char *index_name = text_to_cstring(index_name_text);
    char *table_name = text_to_cstring(table_name_text);
    char *index_type = index_type_text ? text_to_cstring(index_type_text) : "btree";

    bool created = false;
    double current_usage = 0.0;
    double budget = 0.0;
    double new_index_size = 0.0;

    /* Check if auto-creation is enabled */
    if (!nr_enable_auto_index_creation)
    {
        elog(WARNING, "Automatic index creation is disabled (nr_enable_auto_index_creation = false)");
        PG_RETURN_BOOL(false);
    }

    /* Check if index already exists */
    if (SPI_connect() != SPI_OK_CONNECT)
        elog(ERROR, "SPI_connect failed");

    StringInfoData check_query;
    initStringInfo(&check_query);
    appendStringInfo(&check_query, "SELECT 1 FROM pg_class WHERE relname = '%s' AND relkind = 'i'", index_name);

    if (SPI_execute(check_query.data, true, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        SPI_finish();
        elog(INFO, "Index '%s' already exists", index_name);
        PG_RETURN_BOOL(false);
    }

    /* Get current index usage */
    if (SPI_execute("SELECT nr_get_current_index_storage_mb()", true, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        bool isnull;
        Datum usage_datum = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
        if (!isnull)
            current_usage = DatumGetFloat8(usage_datum);
    }

    /* Get budget using nr_calculate_index_budget_mb */
    if (SPI_execute("SELECT nr_calculate_index_budget_mb()", true, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        bool isnull;
        Datum budget_datum = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
        if (!isnull)
            budget = DatumGetFloat8(budget_datum);
    }

    SPI_finish();

    /* Check if we have budget */
    if (current_usage >= budget)
    {
        elog(INFO, "Index storage budget exceeded: current %.2f MB, budget %.2f MB", current_usage, budget);
        PG_RETURN_BOOL(false);
    }
    else
    {
        elog(INFO, "Index storage budget does not exceed: current %.2f MB, budget %.2f MB", current_usage, budget);
    }

    /* Create index (estimate size first) */
    StringInfoData create_query;
    initStringInfo(&create_query);
    appendStringInfo(&create_query, "CREATE INDEX %s ON %s USING %s (",
                     quote_identifier(index_name),
                     quote_identifier(table_name),
                     index_type);

    /* Add columns */
    if (ARR_NDIM(columns_array) == 1 && !ARR_HASNULL(columns_array))
    {
        int nelems = ARR_DIMS(columns_array)[0];
        Datum *elems;
        bool *nulls;

        deconstruct_array(columns_array, TEXTOID, -1, false, 'i', &elems, &nulls, &nelems);

        for (int i = 0; i < nelems; i++)
        {
            if (nulls[i])
                elog(ERROR, "Column name cannot be NULL");

            if (i > 0)
                appendStringInfo(&create_query, ", ");

            appendStringInfoString(&create_query, quote_identifier(text_to_cstring(DatumGetTextP(elems[i]))));
        }
    }
    else
    {
        elog(ERROR, "Invalid columns array");
    }

    appendStringInfo(&create_query, ")");

    /* Execute CREATE INDEX */
    if (SPI_connect() != SPI_OK_CONNECT)
        elog(ERROR, "SPI_connect failed");

    if (SPI_execute(create_query.data, false, 0) == SPI_OK_UTILITY)
    {
        /* Get the size of the newly created index */
        Oid indexOid = get_relname_relid(index_name, get_namespace_oid("public", false));
        if (OidIsValid(indexOid))
            new_index_size = get_index_size_mb(indexOid);

        created = true;
        elog(INFO, "Created index '%s' (%.2f MB) - Total index usage: %.2f MB / %.2f MB",
             index_name, new_index_size, current_usage + new_index_size, budget);
    }
    else
    {
        elog(WARNING, "Failed to create index '%s'", index_name);
    }

    SPI_finish();

    PG_RETURN_BOOL(created);
}

/* Drop index if exists */
PG_FUNCTION_INFO_V1(nr_drop_index_if_exists);

Datum
nr_drop_index_if_exists(PG_FUNCTION_ARGS)
{
    text *index_name_text = PG_GETARG_TEXT_P(0);
    char *index_name = text_to_cstring(index_name_text);
    bool dropped = false;

    if (SPI_connect() != SPI_OK_CONNECT)
        elog(ERROR, "SPI_connect failed");

    StringInfoData drop_query;
    initStringInfo(&drop_query);
    appendStringInfo(&drop_query, "DROP INDEX IF EXISTS %s", quote_identifier(index_name));

    if (SPI_execute(drop_query.data, false, 0) == SPI_OK_UTILITY)
    {
        dropped = true;
        elog(INFO, "Dropped index '%s'", index_name);
    }

    SPI_finish();

    PG_RETURN_BOOL(dropped);
}

/* Get index storage statistics */
PG_FUNCTION_INFO_V1(nr_get_index_storage_stats);

Datum
nr_get_index_storage_stats(PG_FUNCTION_ARGS)
{
    FuncCallContext *funcctx;
    TupleDesc tupdesc;

    if (SRF_IS_FIRSTCALL())
    {
        MemoryContext oldcontext;
        funcctx = SRF_FIRSTCALL_INIT();

        oldcontext = MemoryContextSwitchTo(funcctx->multi_call_memory_ctx);

        if (SPI_connect() != SPI_OK_CONNECT)
            elog(ERROR, "SPI_connect failed");

        /* Execute query to get index stats */
        if (SPI_execute(
            "SELECT indexrel.relname as index_name, "
            "       heaprel.relname as table_name, "
            "       pg_relation_size(indexrel.oid)::double precision / 1024.0 / 1024.0 as size_mb, "
            "       pg_stat_user_indexes.idx_scan as usage_count "
            "FROM pg_index "
            "JOIN pg_class indexrel ON indexrel.oid = pg_index.indexrelid "
            "JOIN pg_class heaprel ON heaprel.oid = pg_index.indrelid "
            "LEFT JOIN pg_stat_user_indexes ON pg_stat_user_indexes.indexrelid = indexrel.oid "
            "WHERE indexrel.relkind = 'i' AND indexrel.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public') "
            "ORDER BY size_mb DESC",
            true, 0) != SPI_OK_SELECT)
        {
            elog(ERROR, "SPI_execute failed");
        }

        funcctx->user_fctx = (void *) SPI_processed;
        funcctx->max_calls = SPI_processed;

        if (get_call_result_type(fcinfo, NULL, &tupdesc) != TYPEFUNC_COMPOSITE)
            elog(ERROR, "return type must be a row type");

        funcctx->tuple_desc = BlessTupleDesc(tupdesc);

        MemoryContextSwitchTo(oldcontext);
    }

    funcctx = SRF_PERCALL_SETUP();

    if (funcctx->call_cntr < funcctx->max_calls)
    {
        Datum values[4];
        bool nulls[4] = {false, false, false, false};
        HeapTuple tuple;

        TupleDesc tupdesc = funcctx->tuple_desc;

        values[0] = PointerGetDatum(cstring_to_text(SPI_getvalue(SPI_tuptable->vals[funcctx->call_cntr], SPI_tuptable->tupdesc, 1)));
        values[1] = PointerGetDatum(cstring_to_text(SPI_getvalue(SPI_tuptable->vals[funcctx->call_cntr], SPI_tuptable->tupdesc, 2)));
        values[2] = Float8GetDatum(atof(SPI_getvalue(SPI_tuptable->vals[funcctx->call_cntr], SPI_tuptable->tupdesc, 3)));

        char *usage_str = SPI_getvalue(SPI_tuptable->vals[funcctx->call_cntr], SPI_tuptable->tupdesc, 4);
        values[3] = usage_str ? Int64GetDatum(atoll(usage_str)) : Int64GetDatum(0);

        tuple = heap_form_tuple(tupdesc, values, nulls);

        SRF_RETURN_NEXT(funcctx, HeapTupleGetDatum(tuple));
    }
    else
    {
        SPI_finish();
        SRF_RETURN_DONE(funcctx);
    }
}

/* Auto-create indexes from JSONB definitions */
PG_FUNCTION_INFO_V1(nr_auto_create_indexes);

Datum
nr_auto_create_indexes(PG_FUNCTION_ARGS)
{
    FuncCallContext *funcctx;
    Jsonb *jsonb = PG_GETARG_JSONB_P(0);
    JsonbIterator *it;
    JsonbValue v;
    JsonbIteratorToken r;

    if (SRF_IS_FIRSTCALL())
    {
        MemoryContext oldcontext;
        funcctx = SRF_FIRSTCALL_INIT();

        oldcontext = MemoryContextSwitchTo(funcctx->multi_call_memory_ctx);

        /* Parse JSONB and prepare results */
        funcctx->user_fctx = (void *) jsonb;

        TupleDesc tupdesc;
        if (get_call_result_type(fcinfo, NULL, &tupdesc) != TYPEFUNC_COMPOSITE)
            elog(ERROR, "return type must be a row type");

        funcctx->tuple_desc = BlessTupleDesc(tupdesc);
        funcctx->max_calls = 1;  /* Simplified for testing */
        funcctx->call_cntr = 0;

        MemoryContextSwitchTo(oldcontext);
    }

    funcctx = SRF_PERCALL_SETUP();

    /* This is a simplified implementation - in practice, you'd parse the JSONB more carefully */
    if (funcctx->call_cntr < funcctx->max_calls)
    {
        Datum values[5];
        bool nulls[5] = {false};
        HeapTuple tuple;
        TupleDesc tupdesc = funcctx->tuple_desc;

        /* For demonstration, return placeholder data */
        values[0] = PointerGetDatum(cstring_to_text("example_index"));
        values[1] = PointerGetDatum(cstring_to_text("example_table"));
        values[2] = BoolGetDatum(true);
        values[3] = Float8GetDatum(10.5);
        values[4] = PointerGetDatum(cstring_to_text(""));

        tuple = heap_form_tuple(tupdesc, values, nulls);

        SRF_RETURN_NEXT(funcctx, HeapTupleGetDatum(tuple));
    }
    else
    {
        SRF_RETURN_DONE(funcctx);
    }
}

PG_FUNCTION_INFO_V1(nr_enable_reactive_query_interception);
Datum
nr_enable_reactive_query_interception(PG_FUNCTION_ARGS)
{
    /* Register the reactive query interception hook */
    prev_post_parse_analyze_hook = post_parse_analyze_hook;
    post_parse_analyze_hook = index_management_post_parse_analyze;

    ereport(LOG,
            (errmsg("REACTIVE INDEX: Query interception hook registered")));

    PG_RETURN_BOOL(true);
}

/*
 * Phase 5: enqueue a work item into the table-backed queue.
 *
 * Must be cheap and non-blocking.
 *
 * IMPORTANT: Do not use SPI here. This hook runs during parse/analyze, and
 * running SQL via SPI can re-enter other extensions' parse hooks and has been
 * observed to crash the backend. Enqueue using a short-lived external `psql`
 * subprocess instead.
 */
static bool
nrim_enqueue_work_item(const char *query_text, const char *candidates_text)
{
    pid_t pid;
    char *dbname;
    StringInfoData sql;
    char tag[64];
    uint32 h;
    char *user;
    char *sockdir = NULL;
    const char *sock_guc;

    if (query_text == NULL || query_text[0] == '\0')
        return false;

    dbname = get_database_name(MyDatabaseId);
    if (dbname == NULL)
        return false;

    user = GetUserNameFromId(GetUserId(), false);
    sock_guc = GetConfigOption("unix_socket_directories", true, false);
    if (sock_guc && sock_guc[0] != '\0')
    {
        const char *comma = strchr(sock_guc, ',');
        if (comma)
            sockdir = pnstrdup(sock_guc, (int) (comma - sock_guc));
        else
            sockdir = pstrdup(sock_guc);
    }
    else
        sockdir = pstrdup("/tmp");

    /* Build a dollar-quoted SQL command with a low-collision tag. */
    h = DatumGetUInt32(hash_any((const unsigned char *) query_text,
                                (int) strlen(query_text)));
    snprintf(tag, sizeof(tag), "nrimq_%08x", h);

    initStringInfo(&sql);
    appendStringInfo(&sql,
                     "INSERT INTO nrim.nrim_work_queue(query_text, candidates) VALUES "
                     "($%s$%s$%s$, ",
                     tag,
                     query_text,
                     tag);
    if (candidates_text && candidates_text[0] != '\0')
        appendStringInfo(&sql, "$%s$%s$%s$", tag, candidates_text, tag);
    else
        appendStringInfoString(&sql, "NULL");
    appendStringInfoString(&sql, ");");

    /*
     * Double-fork to avoid zombies without blocking the user query.
     * The grandchild runs `psql -c <insert>` in a separate session.
     */
    pid = fork();
    if (pid < 0)
    {
        pfree(dbname);
        pfree(user);
        pfree(sockdir);
        pfree(sql.data);
        return false;
    }

    if (pid == 0)
    {
        pid_t pid2 = fork();
        if (pid2 < 0)
            _exit(1);

        if (pid2 == 0)
        {
            int devnull = open("/dev/null", O_RDWR);
            if (devnull >= 0)
            {
                (void) dup2(devnull, STDOUT_FILENO);
                (void) dup2(devnull, STDERR_FILENO);
                if (devnull > STDERR_FILENO)
                    close(devnull);
            }

            (void) setenv("PGAPPNAME", "nrim_enqueue", 1);

            char *argv[] = {
                "psql",
                "-X",
                "-q",
                "-v", "ON_ERROR_STOP=1",
                "-h", sockdir,
                "-U", user,
                "-d", dbname,
                "-c", sql.data,
                NULL
            };

            execvp("psql", argv);
            execv("/code/neurdb-dev/psql/bin/psql", argv);
            _exit(127);
        }

        _exit(0);
    }

    /* Reap the intermediate child quickly */
    (void) waitpid(pid, NULL, 0);
    pfree(dbname);
    pfree(user);
    pfree(sockdir);
    pfree(sql.data);

    if (nr_reactive_debug)
    {
        char *qprev = nrim_preview_text(query_text, 200);
        char *cprev = nrim_preview_text(candidates_text ? candidates_text : "", 200);
        NRIM_ELOG("NRIM v2: enqueued work item (async psql) auto_create=%s query=\"%s\" candidates=\"%s\"",
                  nr_enable_auto_index_creation ? "true" : "false",
                  qprev,
                  cprev);
        pfree(qprev);
        pfree(cprev);
    }
    return true;
}

/*
 * Phase 5: start a background worker on demand (best-effort).
 * Uses a dynamic background worker so the extension does not need preloading.
 */
static void
nrim_start_worker_if_needed(void)
{
    BackgroundWorker worker;
    BackgroundWorkerHandle *handle = NULL;

    if (nrim_worker_started)
        return;

    PG_TRY();
    {
    MemSet(&worker, 0, sizeof(worker));
    worker.bgw_flags = BGWORKER_SHMEM_ACCESS | BGWORKER_BACKEND_DATABASE_CONNECTION;
    worker.bgw_start_time = BgWorkerStart_ConsistentState;
    worker.bgw_restart_time = BGW_NEVER_RESTART;
    snprintf(worker.bgw_name, BGW_MAXLEN, "nrim reactive worker");
    snprintf(worker.bgw_library_name, BGW_MAXLEN, "nr_index_management");
    snprintf(worker.bgw_function_name, BGW_MAXLEN, "nrim_worker_main");
    worker.bgw_main_arg = ObjectIdGetDatum(MyDatabaseId);
    worker.bgw_notify_pid = MyProcPid;

    if (!RegisterDynamicBackgroundWorker(&worker, &handle))
        return;

    nrim_worker_started = true;
    }
    PG_CATCH();
    {
        FlushErrorState();
        return;
    }
    PG_END_TRY();
}

static void
nrim_worker_sigterm_handler(SIGNAL_ARGS)
{
    int save_errno = errno;
    nrim_worker_sigterm = true;
    if (MyLatch)
        SetLatch(MyLatch);
    errno = save_errno;
}

static void
nrim_worker_sighup_handler(SIGNAL_ARGS)
{
    int save_errno = errno;
    nrim_worker_got_sighup = true;
    if (MyLatch)
        SetLatch(MyLatch);
    errno = save_errno;
}

static uint64
nrim_query_id_from_text(const char *query_text)
{
    uint32 h;
    uint64 qid;

    if (query_text == NULL)
        return 0;

    h = DatumGetUInt32(hash_any((const unsigned char *) query_text,
                                (int) strlen(query_text)));
    qid = ((((uint64) h) << 32) | (uint64) h);
    /* Keep within signed BIGINT range for nrim.nrim_query_facts.query_id */
    qid &= UINT64_C(0x7fffffffffffffff);
    if (qid == 0)
        qid = 1;
    return qid;
}

static bool
nrim_worker_update_query_facts(uint64 query_id, const char *query_text)
{
    StringInfoData sql;
    int rc;
    char *q_lit;

    if (query_id == 0 || query_text == NULL)
        return false;

    q_lit = quote_literal_cstr(query_text);
    initStringInfo(&sql);
    appendStringInfo(&sql,
                     "INSERT INTO nrim.nrim_query_facts(query_id, sample_query_text, frequency_ewma, last_seen_at) "
                     "VALUES (%llu, %s, 1.0, now()) "
                     "ON CONFLICT (query_id) DO UPDATE SET "
                     "  sample_query_text = EXCLUDED.sample_query_text, "
                     "  frequency_ewma = nrim.nrim_query_facts.frequency_ewma * 0.9 + 1.0, "
                     "  last_seen_at = now()",
                     (unsigned long long) query_id,
                     q_lit);

    rc = SPI_execute(sql.data, false, 0);
    pfree(q_lit);
    pfree(sql.data);

    return (rc == SPI_OK_INSERT || rc == SPI_OK_UPDATE);
}

static bool
nrim_worker_explain_total_cost(const char *query_text, double *total_cost_out, char **errmsg_out)
{
    StringInfoData sql;
    int rc;
    char *plan_json;
    char *p;
    double cost;
    bool ok = true;

    if (errmsg_out)
        *errmsg_out = NULL;
    if (total_cost_out)
        *total_cost_out = 0.0;

    if (query_text == NULL || query_text[0] == '\0')
        return false;

    initStringInfo(&sql);
    appendStringInfoString(&sql, "EXPLAIN (FORMAT JSON) ");
    appendStringInfoString(&sql, query_text);

    PG_TRY();
    {
        rc = SPI_execute(sql.data, false, 1);
    }
    PG_CATCH();
    {
        ErrorData *edata = CopyErrorData();
        FlushErrorState();
        ok = false;
        if (errmsg_out)
            *errmsg_out = pstrdup(edata->message ? edata->message : "EXPLAIN failed");
        FreeErrorData(edata);
        rc = SPI_ERROR_CONNECT;
    }
    PG_END_TRY();
    pfree(sql.data);

    /*
     * EXPLAIN is a utility statement, but when executed through SPI it may
     * report either SPI_OK_SELECT or SPI_OK_UTILITY depending on the build and
     * dest receiver. Accept both as long as we got at least one row.
     */
    if (!ok || !(rc == SPI_OK_SELECT || rc == SPI_OK_UTILITY) || SPI_processed < 1)
    {
        if (errmsg_out)
        {
            if (*errmsg_out == NULL)
                *errmsg_out = psprintf("EXPLAIN failed (SPI rc=%d)", rc);
        }
        return false;
    }

    plan_json = SPI_getvalue(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1);
    if (plan_json == NULL)
    {
        if (errmsg_out)
            *errmsg_out = pstrdup("EXPLAIN returned NULL");
        return false;
    }

    if (!nrim_extract_total_cost_from_explain_json(plan_json, &cost))
    {
        /* Fallback: best-effort string search */
        p = strstr(plan_json, "\"Total Cost\"");
        if (p == NULL)
        {
            pfree(plan_json);
            if (errmsg_out)
                *errmsg_out = pstrdup("could not find Total Cost in EXPLAIN JSON");
            return false;
        }

        p = strchr(p, ':');
        if (p == NULL)
        {
            pfree(plan_json);
            if (errmsg_out)
                *errmsg_out = pstrdup("malformed EXPLAIN JSON");
            return false;
        }

        p++;
        while (*p && isspace((unsigned char) *p))
            p++;

        cost = strtod(p, NULL);
    }
    pfree(plan_json);

    if (total_cost_out)
        *total_cost_out = cost;
    return true;
}

static bool
nrim_extract_total_cost_from_explain_json(const char *plan_json, double *total_cost_out)
{
    Jsonb *jb;
    JsonbIterator *it;
    JsonbValue v;
    JsonbIteratorToken tok;
    bool in_first_elem = false;
    bool in_top_object = false;
    bool want_plan = false;
    bool in_plan_object = false;
    bool want_total_cost = false;

    if (total_cost_out)
        *total_cost_out = 0.0;
    if (plan_json == NULL || total_cost_out == NULL)
        return false;

    jb = DatumGetJsonbP(DirectFunctionCall1(jsonb_in, CStringGetDatum(plan_json)));
    it = JsonbIteratorInit(&jb->root);

    while ((tok = JsonbIteratorNext(&it, &v, true)) != WJB_DONE)
    {
        if (tok == WJB_ELEM && !in_first_elem)
        {
            in_first_elem = true;
            continue;
        }

        if (tok == WJB_BEGIN_OBJECT && in_first_elem && !in_top_object)
        {
            in_top_object = true;
            continue;
        }

        if (tok == WJB_KEY && in_top_object && v.type == jbvString)
        {
            want_plan = (v.val.string.len == 4 &&
                         memcmp(v.val.string.val, "Plan", 4) == 0);
            if (in_plan_object)
                want_total_cost = (v.val.string.len == 10 &&
                                   memcmp(v.val.string.val, "Total Cost", 10) == 0);
            continue;
        }

        if (tok == WJB_BEGIN_OBJECT && want_plan)
        {
            in_plan_object = true;
            want_plan = false;
            continue;
        }

        if (tok == WJB_VALUE && in_plan_object && want_total_cost && v.type == jbvNumeric)
        {
            Datum d = DirectFunctionCall1(numeric_float8, NumericGetDatum(v.val.numeric));
            *total_cost_out = DatumGetFloat8(d);
            return true;
        }

        if (tok == WJB_END_OBJECT && in_plan_object)
        {
            /* Exiting plan object without finding Total Cost */
            in_plan_object = false;
        }
    }

    return false;
}
static bool
nrim_worker_hypopg_available(void)
{
    int rc = SPI_execute("SELECT 1 FROM pg_extension WHERE extname = 'hypopg'", true, 1);
    return (rc == SPI_OK_SELECT && SPI_processed > 0);
}

static char *
nrim_preview_text(const char *s, int max_bytes)
{
    int len;
    char *out;

    if (s == NULL)
        return pstrdup("");

    if (max_bytes <= 0)
        return pstrdup("");

    len = (int) strlen(s);
    if (len <= max_bytes)
        return pstrdup(s);

    out = palloc(max_bytes + 4);
    memcpy(out, s, max_bytes);
    memcpy(out + max_bytes, "...", 4);
    return out;
}

static bool
nrim_worker_index_exists(Oid relid, AttrNumber *cols, int ncols)
{
    StringInfoData sql;
    int rc;

    if (!OidIsValid(relid) || cols == NULL || ncols <= 0)
        return false;

    initStringInfo(&sql);
    appendStringInfo(&sql,
                     "SELECT 1 "
                     "FROM pg_index i "
                     "JOIN pg_class ic ON ic.oid = i.indexrelid "
                     "JOIN pg_am am ON am.oid = ic.relam "
                     "WHERE i.indrelid = %u "
                     "  AND am.amname = 'btree' "
                     "  AND i.indisvalid AND i.indisready "
                     "  AND i.indkey::int2[] = ARRAY[",
                     relid);
    for (int i = 0; i < ncols; i++)
    {
        if (i > 0)
            appendStringInfoChar(&sql, ',');
        appendStringInfo(&sql, "%d", cols[i]);
    }
    appendStringInfoString(&sql, "]::int2[] LIMIT 1");

    rc = SPI_execute(sql.data, true, 1);
    pfree(sql.data);

    return (rc == SPI_OK_SELECT && SPI_processed > 0);
}

/* Check if a type has a default btree operator class. */
static bool
nrim_worker_has_default_btree_opclass(Oid typoid)
{
    StringInfoData sql;
    int rc;

    if (!OidIsValid(typoid))
        return false;

    initStringInfo(&sql);
    appendStringInfo(&sql,
                     "SELECT 1 "
                     "FROM pg_opclass "
                     "WHERE opcmethod = (SELECT oid FROM pg_am WHERE amname='btree') "
                     "  AND opcintype = %u "
                     "  AND opcdefault "
                     "LIMIT 1",
                     typoid);

    rc = SPI_execute(sql.data, true, 1);
    pfree(sql.data);

    if (rc != SPI_OK_SELECT || SPI_processed < 1)
        return false;

    return true;
}

static char *
nrim_worker_build_index_name(Oid relid, AttrNumber *cols, int ncols, uint64 query_id)
{
    StringInfoData buf;
    char *relname;
    uint32 h = DatumGetUInt32(hash_any((const unsigned char *) &query_id, sizeof(query_id)));

    initStringInfo(&buf);
    relname = get_rel_name(relid);

    appendStringInfoString(&buf, "idx_reactive_");
    appendStringInfoString(&buf, relname ? relname : "table");

    for (int i = 0; i < ncols; i++)
    {
        char *attname = get_attname(relid, cols[i], false);
        appendStringInfoChar(&buf, '_');
        if (attname)
            appendStringInfoString(&buf, attname);
        else
            appendStringInfo(&buf, "a%d", cols[i]);
        if (attname)
            pfree(attname);
    }

    appendStringInfo(&buf, "_%08x", h);

    if (relname)
        pfree(relname);

    if (buf.len >= NAMEDATALEN)
        buf.data[NAMEDATALEN - 1] = '\0';

    return buf.data;
}

static bool
nrim_worker_run_psql_utility(Oid dboid, const char *sql)
{
    pid_t pid;
    int status;
    char *dbname;
    char *user;
    char *sockdir = NULL;
    const char *sock_guc;
    bool started_tx = false;
    MemoryContext oldcxt;

    if (sql == NULL || sql[0] == '\0')
        return false;

    /*
     * This helper is often called outside any transaction because
     * CREATE/DROP INDEX CONCURRENTLY must not run in a transaction block.
     * However, get_database_name() uses catalogs/syscache and requires a
     * transaction state. Start a short transaction if needed just to resolve
     * connection parameters, then commit before forking.
     */
    if (!IsTransactionState())
    {
        StartTransactionCommand();
        started_tx = true;
    }

    dbname = get_database_name(dboid);
    if (dbname == NULL)
    {
        if (started_tx)
            CommitTransactionCommand();
        return false;
    }

    user = GetUserNameFromId(GetUserId(), false);
    sock_guc = GetConfigOption("unix_socket_directories", true, false);
    if (sock_guc && sock_guc[0] != '\0')
    {
        const char *comma = strchr(sock_guc, ',');
        if (comma)
            sockdir = pnstrdup(sock_guc, (int) (comma - sock_guc));
        else
            sockdir = pstrdup(sock_guc);
    }
    else
        sockdir = pstrdup("/tmp");

    /* Copy to TopMemoryContext so they survive any commit we do here. */
    oldcxt = MemoryContextSwitchTo(TopMemoryContext);
    dbname = pstrdup(dbname);
    user = pstrdup(user);
    sockdir = pstrdup(sockdir);
    MemoryContextSwitchTo(oldcxt);

    if (started_tx)
        CommitTransactionCommand();

    if (nr_reactive_debug)
    {
        char *sqlprev = nrim_preview_text(sql, 400);
        NRIM_ELOG("NRIM v2 worker: psql utility sql=\"%s\"", sqlprev);
        pfree(sqlprev);
    }

    pid = fork();
    if (pid < 0)
    {
        pfree(dbname);
        pfree(user);
        pfree(sockdir);
        return false;
    }

    if (pid == 0)
    {
        if (!nr_reactive_debug)
        {
            int devnull = open("/dev/null", O_RDWR);
            if (devnull >= 0)
            {
                (void) dup2(devnull, STDOUT_FILENO);
                (void) dup2(devnull, STDERR_FILENO);
                if (devnull > STDERR_FILENO)
                    close(devnull);
            }
        }

        (void) setenv("PGAPPNAME", "nrim_reactive_worker", 1);

        char *argv[] = {
            "psql",
            "-X",
            "-q",
            "-v", "ON_ERROR_STOP=1",
            "-h", sockdir,
            "-U", user,
            "-d", dbname,
            "-c", (char *) sql,
            NULL
        };

        execvp("psql", argv);
        execv("/code/neurdb-dev/psql/bin/psql", argv);
        _exit(127);
    }

    if (waitpid(pid, &status, 0) < 0)
    {
        pfree(dbname);
        pfree(user);
        pfree(sockdir);
        return false;
    }

    pfree(dbname);
    pfree(user);
    pfree(sockdir);
    return (WIFEXITED(status) && WEXITSTATUS(status) == 0);
}

static bool
nrim_worker_ensure_registry_for_index(Oid index_oid,
                                      Oid relid,
                                      AttrNumber *cols,
                                      int ncols,
                                      const char *index_method,
                                      double benefit_score)
{
    StringInfoData sql;
    char *idxname;
    char *tblname;
    char *nspname;
    Oid nspid;
    char *idx_lit;
    char *tbl_lit;
    char *method_lit;
    int rc;

    if (!OidIsValid(index_oid) || !OidIsValid(relid) || cols == NULL || ncols <= 0)
        return false;

    idxname = get_rel_name(index_oid);
    tblname = get_rel_name(relid);
    nspid = get_rel_namespace(relid);
    nspname = get_namespace_name(nspid);

    if (idxname == NULL || tblname == NULL || nspname == NULL)
    {
        if (idxname) pfree(idxname);
        if (tblname) pfree(tblname);
        if (nspname) pfree(nspname);
        return false;
    }

    idx_lit = quote_literal_cstr(idxname);
    {
        char *tblq = psprintf("%s.%s", nspname, tblname);
        tbl_lit = quote_literal_cstr(tblq);
        pfree(tblq);
    }
    method_lit = quote_literal_cstr(index_method ? index_method : "btree");

    initStringInfo(&sql);
    appendStringInfo(&sql,
                     "INSERT INTO nrim.nrim_index_registry("
                     "  index_oid, index_name, table_oid, table_name, columns, index_method, "
                     "  state, size_bytes, benefit_score_ewma, value_score, last_refreshed_at"
                     ") VALUES ("
                     "  %u, %s, %u, %s, ARRAY[",
                     index_oid,
                     idx_lit,
                     relid,
                     tbl_lit);
    for (int i = 0; i < ncols; i++)
    {
        if (i > 0)
            appendStringInfoChar(&sql, ',');
        appendStringInfo(&sql, "%d", cols[i]);
    }
    appendStringInfo(&sql,
                     "]::int2[], %s, 'created', pg_relation_size(%u), %.6f, %.6f, now()) "
                     "ON CONFLICT (index_oid) DO UPDATE SET "
                     "  benefit_score_ewma = GREATEST(nrim.nrim_index_registry.benefit_score_ewma, EXCLUDED.benefit_score_ewma), "
                     "  last_refreshed_at = now()",
                     method_lit,
                     index_oid,
                     benefit_score,
                     benefit_score);

    rc = SPI_execute(sql.data, false, 0);

    pfree(sql.data);
    pfree(idxname);
    pfree(tblname);
    pfree(nspname);
    pfree(idx_lit);
    pfree(tbl_lit);
    pfree(method_lit);

    return (rc == SPI_OK_INSERT || rc == SPI_OK_UPDATE);
}

static bool
nrim_worker_update_attribution(uint64 query_id, Oid index_oid, double marginal_benefit)
{
    int rc;
    StringInfoData sql;

    if (query_id == 0 || !OidIsValid(index_oid) || marginal_benefit < 0.0)
        return false;

    initStringInfo(&sql);
    appendStringInfo(&sql,
                     "INSERT INTO nrim.nrim_query_index_attribution("
                     "  query_id, index_oid, marginal_benefit_ewma, last_estimated_at"
                     ") VALUES ("
                     "  %llu, %u, %.6f, now()"
                     ") "
                     "ON CONFLICT (query_id, index_oid) DO UPDATE SET "
                     "  marginal_benefit_ewma = nrim.nrim_query_index_attribution.marginal_benefit_ewma * 0.9 + EXCLUDED.marginal_benefit_ewma, "
                     "  last_estimated_at = now()",
                     (unsigned long long) query_id,
                     index_oid,
                     marginal_benefit);

    rc = SPI_execute(sql.data, false, 0);
    pfree(sql.data);

    if (!(rc == SPI_OK_INSERT || rc == SPI_OK_UPDATE))
        return false;

    /*
     * Refresh registry benefit score using query frequency weights:
     * benefit_score_ewma(i) = Σ_q [ freq_ewma(q) * marginal_benefit_ewma(q,i) ].
     */
    {
        char *q = psprintf(
            "UPDATE nrim.nrim_index_registry r "
            "SET benefit_score_ewma = COALESCE(("
            "    SELECT sum(f.frequency_ewma * a.marginal_benefit_ewma) "
            "    FROM nrim.nrim_query_index_attribution a "
            "    JOIN nrim.nrim_query_facts f ON f.query_id = a.query_id "
            "    WHERE a.index_oid = r.index_oid"
            "), 0), "
            "value_score = (ln(1 + COALESCE(("
            "    SELECT sum(f.frequency_ewma * a.marginal_benefit_ewma) "
            "    FROM nrim.nrim_query_index_attribution a "
            "    JOIN nrim.nrim_query_facts f ON f.query_id = a.query_id "
            "    WHERE a.index_oid = r.index_oid"
            "), 0)) + 0.1 * ln(1 + r.touch_score_ewma)) / (ln(1 + GREATEST(pg_relation_size(r.index_oid),0)) + 1), "
            "last_refreshed_at = now() "
            "WHERE r.index_oid = %u",
            index_oid);
        (void) SPI_execute(q, false, 0);
        pfree(q);
    }

    return true;
}

static bool
nrim_worker_refresh_registry_stats(void)
{
    int rc;

    StartTransactionCommand();
    PushActiveSnapshot(GetTransactionSnapshot());
    if (SPI_connect() != SPI_OK_CONNECT)
    {
        PopActiveSnapshot();
        AbortCurrentTransaction();
        return false;
    }

    rc = SPI_execute(
        "UPDATE nrim.nrim_index_registry r "
        "SET "
        "  size_bytes = pg_relation_size(r.index_oid), "
        "  idx_scan = s.idx_scan, "
        "  idx_tup_read = s.idx_tup_read, "
        "  idx_tup_fetch = s.idx_tup_fetch, "
        "  touch_score_ewma = r.touch_score_ewma * 0.9 + GREATEST(0, COALESCE(s.idx_scan,0) - COALESCE(r.idx_scan,0)), "
        "  value_score = (ln(1 + r.benefit_score_ewma) + 0.1 * ln(1 + r.touch_score_ewma)) / (ln(1 + GREATEST(pg_relation_size(r.index_oid),0)) + 1), "
        "  last_refreshed_at = now() "
        "FROM pg_stat_user_indexes s "
        "WHERE s.indexrelid = r.index_oid",
        false, 0);

    SPI_finish();
    PopActiveSnapshot();
    CommitTransactionCommand();
    return (rc == SPI_OK_UPDATE);
}

typedef struct NrimWorkerCand
{
    Oid relid;
    int ncols;
    AttrNumber cols[8];
} NrimWorkerCand;

static bool
nrim_parse_candidates_json(const char *candidates_json, List **out_cands)
{
    Jsonb *jb;
    JsonbIterator *it;
    JsonbValue v;
    JsonbIteratorToken tok;
    NrimWorkerCand *current = NULL;
    bool expect_relid = false;
    bool in_cols = false;
    int col_i = 0;
    bool any_object = false;

    if (out_cands)
        *out_cands = NIL;

    if (candidates_json == NULL || candidates_json[0] == '\0' || out_cands == NULL)
        return false;

    jb = DatumGetJsonbP(DirectFunctionCall1(jsonb_in, CStringGetDatum(candidates_json)));
    it = JsonbIteratorInit(&jb->root);

    /*
     * We need to walk into nested arrays/objects to see `cols` elements.
     * Using skipNested=true would skip `WJB_ELEM` tokens for the cols array.
     */
    while ((tok = JsonbIteratorNext(&it, &v, false)) != WJB_DONE)
    {
        if (tok == WJB_BEGIN_OBJECT)
        {
            current = palloc0(sizeof(*current));
            col_i = 0;
            expect_relid = false;
            in_cols = false;
            any_object = true;
        }
        else if (tok == WJB_END_OBJECT)
        {
            if (current && OidIsValid(current->relid) && current->ncols > 0)
                *out_cands = lappend(*out_cands, current);
            else if (current)
                pfree(current);
            current = NULL;
        }
        else if (tok == WJB_KEY)
        {
            if (current == NULL || v.type != jbvString)
                continue;

            expect_relid = (v.val.string.len == 5 &&
                            memcmp(v.val.string.val, "relid", 5) == 0);
            in_cols = (v.val.string.len == 4 &&
                       memcmp(v.val.string.val, "cols", 4) == 0);
        }
        else if (tok == WJB_VALUE)
        {
            if (current == NULL)
                continue;
            if (expect_relid && v.type == jbvNumeric)
            {
                int64 rel = DatumGetInt64(DirectFunctionCall1(numeric_int8, NumericGetDatum(v.val.numeric)));
                if (rel > 0)
                    current->relid = (Oid) rel;
            }
            expect_relid = false;
        }
        else if (tok == WJB_BEGIN_ARRAY)
        {
            /* wait for elems */
        }
        else if (tok == WJB_END_ARRAY)
        {
            if (current && in_cols)
                current->ncols = col_i;
            in_cols = false;
        }
        else if (tok == WJB_ELEM)
        {
            if (current == NULL || !in_cols)
                continue;
            if (col_i >= 8)
                continue;
            if (v.type == jbvNumeric)
            {
                int64 att = DatumGetInt64(DirectFunctionCall1(numeric_int8, NumericGetDatum(v.val.numeric)));
                if (att > 0 && att <= INT16_MAX)
                    current->cols[col_i++] = (AttrNumber) att;
            }
        }
    }

    if (*out_cands == NIL)
    {
        if (nr_reactive_debug)
        {
            char *cprev = nrim_preview_text(candidates_json, 300);
            NRIM_ELOG("NRIM v2 worker: candidate parse failed any_object=%s candidates=\"%s\"",
                      any_object ? "true" : "false",
                      cprev);
            pfree(cprev);
        }
        return false;
    }

    return true;
}

static bool
nrim_worker_evict_until_fits(double needed_mb, double candidate_value, double *freed_mb_out)
{
    double freed_mb = 0.0;

    if (freed_mb_out)
        *freed_mb_out = 0.0;

    if (needed_mb <= 0.0)
        return true;

    for (;;)
    {
        int rc;
        bool isnull;
        Oid index_oid = InvalidOid;
        double idx_mb = 0.0;
        double idx_value = 0.0;
        char *idx_relname = NULL;
        char *idx_nspname = NULL;
        char *drop_sql;

        rc = SPI_execute(
            "SELECT index_oid, "
            "       COALESCE(size_bytes, pg_relation_size(index_oid))::double precision / 1024.0 / 1024.0 AS size_mb, "
            "       value_score "
            "FROM nrim.nrim_index_registry "
            "WHERE state = 'created' "
            "ORDER BY value_score ASC, created_at ASC "
            "LIMIT 1",
            true, 1);

        if (rc != SPI_OK_SELECT || SPI_processed < 1)
            break;

        {
            Datum d_oid = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
            if (isnull)
                break;
            index_oid = DatumGetObjectId(d_oid);

            {
                Datum d_mb = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 2, &isnull);
                if (!isnull)
                    idx_mb = DatumGetFloat8(d_mb);
            }
            {
                Datum d_val = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 3, &isnull);
                if (!isnull)
                    idx_value = DatumGetFloat8(d_val);
            }
        }

        if (!OidIsValid(index_oid))
            break;

        if (candidate_value <= idx_value * (1.0 + nr_reactive_min_value_improvement))
            break;

        idx_relname = get_rel_name(index_oid);
        idx_nspname = get_namespace_name(get_rel_namespace(index_oid));
        if (idx_relname == NULL || idx_nspname == NULL)
        {
            if (idx_relname) pfree(idx_relname);
            if (idx_nspname) pfree(idx_nspname);
            break;
        }

        {
            char *q = psprintf("UPDATE nrim.nrim_index_registry SET state='dropping' WHERE index_oid=%u", index_oid);
            (void) SPI_execute(q, false, 0);
            pfree(q);
        }

        drop_sql = psprintf("DROP INDEX %s %s.%s",
                            nr_reactive_use_concurrently ? "CONCURRENTLY" : "",
                            quote_identifier(idx_nspname),
                            quote_identifier(idx_relname));

        /*
         * DROP INDEX CONCURRENTLY cannot run inside a transaction block.
         * Run it via external psql outside our SPI transaction, then reconnect.
         */
        {
            MemoryContext oldcxt;
            char *drop_sql_persist;

            oldcxt = MemoryContextSwitchTo(TopMemoryContext);
            drop_sql_persist = pstrdup(drop_sql);
            MemoryContextSwitchTo(oldcxt);
            pfree(drop_sql);
            drop_sql = drop_sql_persist;

            SPI_finish();
            PopActiveSnapshot();
            CommitTransactionCommand();

            if (!nrim_worker_run_psql_utility(MyDatabaseId, drop_sql))
            {
                StartTransactionCommand();
                PushActiveSnapshot(GetTransactionSnapshot());
                if (SPI_connect() != SPI_OK_CONNECT)
                {
                    PopActiveSnapshot();
                    AbortCurrentTransaction();
                    pfree(drop_sql);
                    pfree(idx_relname);
                    pfree(idx_nspname);
                    return false;
                }

                pfree(drop_sql);
                pfree(idx_relname);
                pfree(idx_nspname);
                break;
            }

            StartTransactionCommand();
            PushActiveSnapshot(GetTransactionSnapshot());
            if (SPI_connect() != SPI_OK_CONNECT)
            {
                PopActiveSnapshot();
                AbortCurrentTransaction();
                pfree(drop_sql);
                pfree(idx_relname);
                pfree(idx_nspname);
                return false;
            }

            pfree(drop_sql);
        }

        {
            char *q = psprintf("DELETE FROM nrim.nrim_index_registry WHERE index_oid=%u", index_oid);
            (void) SPI_execute(q, false, 0);
            pfree(q);
        }

        freed_mb += idx_mb;
        needed_mb -= idx_mb;

        pfree(idx_relname);
        pfree(idx_nspname);

        if (needed_mb <= 0.0)
            break;
    }

    if (freed_mb_out)
        *freed_mb_out = freed_mb;

    return (needed_mb <= 0.0);
}

static bool
nrim_process_one_work_item(Oid dboid)
{
    bool got_one = false;
    int64 work_id = 0;
    char *query_text = NULL;
    char *candidates_json = NULL;

    /* Claim one pending item */
    StartTransactionCommand();
    PushActiveSnapshot(GetTransactionSnapshot());
    if (SPI_connect() != SPI_OK_CONNECT)
    {
        PopActiveSnapshot();
        AbortCurrentTransaction();
        return false;
    }

    if (SPI_execute(
            "SELECT id, query_text, candidates "
            "FROM nrim.nrim_work_queue "
            "WHERE status = 'pending' "
            "ORDER BY id "
            "LIMIT 1 "
            "FOR UPDATE SKIP LOCKED",
            false, 1) == SPI_OK_SELECT && SPI_processed > 0)
    {
        bool isnull;
        Datum d_id = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
        if (!isnull)
            work_id = DatumGetInt64(d_id);

        /*
         * SPI_getvalue() allocates in the current transaction context; copy out
         * before we commit, otherwise pointers can go stale.
         */
        {
            char *tmp = SPI_getvalue(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 2);
            if (tmp)
            {
                MemoryContext old = MemoryContextSwitchTo(TopMemoryContext);
                query_text = pstrdup(tmp);
                MemoryContextSwitchTo(old);
                pfree(tmp);
            }
        }
        {
            char *tmp = SPI_getvalue(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 3);
            if (tmp)
            {
                MemoryContext old = MemoryContextSwitchTo(TopMemoryContext);
                candidates_json = pstrdup(tmp);
                MemoryContextSwitchTo(old);
                pfree(tmp);
            }
        }
        got_one = (work_id > 0 && query_text != NULL);

        if (got_one)
        {
            char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='processing' WHERE id=%lld",
                               (long long) work_id);
            (void) SPI_execute(q, false, 0);
            pfree(q);
        }
    }

    SPI_finish();
    PopActiveSnapshot();
    CommitTransactionCommand();

    if (!got_one)
    {
        if (query_text) pfree(query_text);
        if (candidates_json) pfree(candidates_json);
        return false;
    }

    /* Process */
    StartTransactionCommand();
    PushActiveSnapshot(GetTransactionSnapshot());
    if (SPI_connect() != SPI_OK_CONNECT)
    {
        PopActiveSnapshot();
        AbortCurrentTransaction();
        pfree(query_text);
        if (candidates_json) pfree(candidates_json);
        return true;
    }

    {
        uint64 query_id = nrim_query_id_from_text(query_text);
        double baseline_cost = 0.0;
        char *ex_err = NULL;
	        List *cand_list = NIL;
	        ListCell *lc;
	        NrimWorkerCand *best = NULL;
	        double best_benefit = 0.0;
	        double best_value = 0.0;
	        double best_size_mb = 0.0;
	        Oid best_relid = InvalidOid;
	        int best_ncols = 0;
	        AttrNumber best_cols[8];

        (void) nrim_worker_update_query_facts(query_id, query_text);

        if (!nrim_worker_hypopg_available())
        {
            char *err_lit = quote_literal_cstr("hypopg extension not installed");
            char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='error', last_error=%s WHERE id=%lld",
                               err_lit,
                               (long long) work_id);
            (void) SPI_execute(q, false, 0);
            pfree(q);
            pfree(err_lit);
            goto done_tx;
        }

        if (!nrim_worker_explain_total_cost(query_text, &baseline_cost, &ex_err))
        {
            char *err_lit = quote_literal_cstr(ex_err ? ex_err : "EXPLAIN failed");
            char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='error', last_error=%s WHERE id=%lld",
                               err_lit,
                               (long long) work_id);
            (void) SPI_execute(q, false, 0);
            pfree(q);
            pfree(err_lit);
            if (ex_err) pfree(ex_err);
            goto done_tx;
        }

        if (nr_reactive_debug)
        {
            char *qprev = nrim_preview_text(query_text, 300);
            char *cprev = nrim_preview_text(candidates_json ? candidates_json : "", 300);
            NRIM_ELOG("NRIM v2 worker: claimed work_id=%lld query_id=%llu baseline_cost=%.6f query=\"%s\" candidates=\"%s\"",
                      (long long) work_id,
                      (unsigned long long) query_id,
                      baseline_cost,
                      qprev,
                      cprev);
            pfree(qprev);
            pfree(cprev);
        }

        if (!nrim_parse_candidates_json(candidates_json, &cand_list))
        {
            char *err_lit = quote_literal_cstr("candidate parse failed");
            char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='done', last_error=%s WHERE id=%lld",
                               err_lit,
                               (long long) work_id);
            (void) SPI_execute(q, false, 0);
            pfree(q);
            pfree(err_lit);
            goto done_tx;
        }

        /* Evaluate and pick best by value (benefit / (size_mb + 1)) */
        int evals = 0;
        foreach(lc, cand_list)
        {
            NrimWorkerCand *c = (NrimWorkerCand *) lfirst(lc);
            char *nspname;
            char *relname;
            StringInfoData create_sql;
            char *create_lit;
            int rc;
            Oid hypoid = InvalidOid;
            double with_cost = 0.0;
            double benefit = 0.0;
            double size_mb = 0.0;

            if (evals >= nr_reactive_max_candidates_per_query)
                break;
            if (nr_reactive_max_hypopg_evals_per_minute > 0 &&
                evals >= nr_reactive_max_hypopg_evals_per_minute)
                break;

            if (!OidIsValid(c->relid) || c->ncols <= 0)
                continue;

            if (nrim_worker_index_exists(c->relid, c->cols, c->ncols))
            {
                if (nr_reactive_debug)
                    NRIM_ELOG("NRIM v2 worker: skip candidate relid=%u ncols=%d (matching real index exists)",
                              c->relid, c->ncols);
                continue;
            }

            nspname = get_namespace_name(get_rel_namespace(c->relid));
            relname = get_rel_name(c->relid);
            if (nspname == NULL || relname == NULL)
            {
                if (nspname) pfree(nspname);
                if (relname) pfree(relname);
                continue;
            }

            initStringInfo(&create_sql);
            appendStringInfo(&create_sql, "CREATE INDEX ON %s.%s USING btree (",
                             quote_identifier(nspname),
                             quote_identifier(relname));
            bool cols_ok = true;
            for (int i = 0; i < c->ncols; i++)
            {
                char *attname = get_attname(c->relid, c->cols[i], false);
                Oid atttyp = InvalidOid;
                if (attname == NULL)
                {
                    cols_ok = false;
                    break;
                }
                atttyp = get_atttype(c->relid, c->cols[i]);
                if (!nrim_worker_has_default_btree_opclass(atttyp))
                {
                    if (nr_reactive_debug)
                        NRIM_ELOG("NRIM v2 worker: skip candidate relid=%u attnum=%d (no btree opclass for typoid=%u)",
                                  c->relid, (int) c->cols[i], atttyp);
                    pfree(attname);
                    cols_ok = false;
                    break;
                }
                if (i > 0)
                    appendStringInfoString(&create_sql, ", ");
                appendStringInfoString(&create_sql, quote_identifier(attname));
                pfree(attname);
            }
            appendStringInfoChar(&create_sql, ')');

            if (!cols_ok)
            {
                pfree(create_sql.data);
                pfree(nspname);
                pfree(relname);
                continue;
            }

            create_lit = quote_literal_cstr(create_sql.data);
            pfree(create_sql.data);

            /*
             * HypoPG can ERROR for some types/opclasses; don't let a single
             * candidate crash the whole worker process.
             */
            hypoid = InvalidOid;
            rc = 0;
            BeginInternalSubTransaction(NULL);
            PG_TRY();
            {
                char *q = psprintf("SELECT indexrelid FROM hypopg_create_index(%s) LIMIT 1", create_lit);
                rc = SPI_execute(q, false, 1);
                pfree(q);
                ReleaseCurrentSubTransaction();
            }
            PG_CATCH();
            {
                ErrorData *edata;
                MemoryContext oldcxt = MemoryContextSwitchTo(TopMemoryContext);
                edata = CopyErrorData();
                FlushErrorState();
                MemoryContextSwitchTo(oldcxt);

                RollbackAndReleaseCurrentSubTransaction();

                if (nr_reactive_debug)
                    NRIM_ELOG("NRIM v2 worker: hypopg_create_index ERROR: %s", edata->message ? edata->message : "(unknown)");
                FreeErrorData(edata);

                pfree(create_lit);
                pfree(nspname);
                pfree(relname);
                evals++;
                continue;
            }
            PG_END_TRY();

            if (rc == SPI_OK_SELECT && SPI_processed > 0)
            {
                bool isnull;
                Datum d_oid = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
                if (!isnull)
                    hypoid = DatumGetObjectId(d_oid);
            }

            if (OidIsValid(hypoid))
            {
                char *qsz = psprintf("SELECT hypopg_relation_size(%u)::double precision / 1024.0 / 1024.0", hypoid);
                int rc2 = SPI_execute(qsz, false, 1);
                pfree(qsz);
                if (rc2 == SPI_OK_SELECT && SPI_processed > 0)
                {
                    bool isnull;
                    Datum d_sz = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
                    if (!isnull)
                        size_mb = DatumGetFloat8(d_sz);
                }

                if (nrim_worker_explain_total_cost(query_text, &with_cost, NULL))
                {
                    benefit = baseline_cost - with_cost;
                    if (benefit < 0.0)
                        benefit = 0.0;
                }

                if (nr_reactive_debug)
                {
                    char *ddls = nrim_preview_text(create_lit, 250);
                    NRIM_ELOG("NRIM v2 worker: cand ddl_lit=%s hypoid=%u size_mb=%.3f baseline=%.6f with=%.6f benefit=%.6f",
                              ddls, hypoid, size_mb, baseline_cost, with_cost, benefit);
                    pfree(ddls);
                }

                {
                    char *qd = psprintf("SELECT hypopg_drop_index(%u)", hypoid);
                    (void) SPI_execute(qd, false, 0);
                    pfree(qd);
                }
            }
            else if (nr_reactive_debug)
            {
                char *ddls = nrim_preview_text(create_lit, 250);
                NRIM_ELOG("NRIM v2 worker: hypopg_create_index failed (rc=%d) ddl_lit=%s", rc, ddls);
                pfree(ddls);
            }

            pfree(create_lit);
            pfree(nspname);
            pfree(relname);

            if (benefit > 0.0)
            {
                double value = benefit / (size_mb + 1.0);
                if (value > best_value)
                {
                    best_value = value;
                    best_benefit = benefit;
                    best_size_mb = size_mb;
                    best = c;
                }
            }

            evals++;
        }

	        if (best == NULL || best_benefit <= 0.0)
	        {
	            char *err_lit = quote_literal_cstr("no beneficial candidate (HypoPG delta_cost<=0)");
	            char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='done', last_error=%s WHERE id=%lld",
	                               err_lit,
	                               (long long) work_id);
            (void) SPI_execute(q, false, 0);
            pfree(q);
            pfree(err_lit);
	            goto done_tx;
	        }

	        best_relid = best->relid;
	        best_ncols = best->ncols;
	        memset(best_cols, 0, sizeof(best_cols));
	        for (int i = 0; i < best_ncols && i < 8; i++)
	            best_cols[i] = best->cols[i];

	        if (nr_reactive_debug)
	            NRIM_ELOG("NRIM v2 worker: selected best relid=%u ncols=%d benefit=%.6f size_mb=%.3f value=%.6f",
	                      best->relid, best->ncols, best_benefit, best_size_mb, best_value);

        if (!nr_enable_auto_index_creation)
        {
            char *err_lit = quote_literal_cstr("auto-creation disabled in worker session (set via ALTER SYSTEM SET nr_enable_auto_index_creation=on; SELECT pg_reload_conf())");
            char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='done', last_error=%s WHERE id=%lld",
                               err_lit,
                               (long long) work_id);
            (void) SPI_execute(q, false, 0);
            pfree(q);
            pfree(err_lit);
            if (nr_reactive_debug)
                NRIM_ELOG("NRIM v2 worker: skipping CREATE INDEX because nr_enable_auto_index_creation=false in worker session");
            goto done_tx;
        }

        /* Budget check (budget scope: all public indexes) */
        {
            double current_mb = 0.0;
            double budget_mb = 0.0;
            bool isnull;

            if (SPI_execute("SELECT nr_get_current_index_storage_mb()", false, 1) == SPI_OK_SELECT && SPI_processed > 0)
            {
                Datum d = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
                if (!isnull)
                    current_mb = DatumGetFloat8(d);
            }
            if (SPI_execute("SELECT nr_calculate_index_budget_mb()", false, 1) == SPI_OK_SELECT && SPI_processed > 0)
            {
                Datum d = SPI_getbinval(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1, &isnull);
                if (!isnull)
                    budget_mb = DatumGetFloat8(d);
            }

            if (budget_mb > 0.0)
            {
                double needed_mb = (current_mb + best_size_mb) - budget_mb;
                if (nr_reactive_debug)
                    NRIM_ELOG("NRIM v2 worker: budget current_mb=%.3f budget_mb=%.3f best_size_mb=%.3f needed_mb=%.3f",
                              current_mb, budget_mb, best_size_mb, needed_mb);
                if (needed_mb > 0.0)
                {
                    if (!nrim_worker_evict_until_fits(needed_mb, best_value, NULL))
                    {
                        char *err_lit = quote_literal_cstr("budget exceeded and eviction not beneficial/insufficient");
                        char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='done', last_error=%s WHERE id=%lld",
                                           err_lit,
                                           (long long) work_id);
                        (void) SPI_execute(q, false, 0);
                        pfree(q);
                        pfree(err_lit);
                        goto done_tx;
                    }
                }
            }
        }

        /* Create the best index concurrently via psql, outside this transaction */
        {
            char *nspname = get_namespace_name(get_rel_namespace(best_relid));
            char *relname = get_rel_name(best_relid);
            char *idxname;
            StringInfoData create;
            bool created_ok;

            if (nspname == NULL || relname == NULL)
            {
                if (nspname) pfree(nspname);
                if (relname) pfree(relname);
                {
                    char *err_lit = quote_literal_cstr("could not resolve relation name");
                    char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='error', last_error=%s WHERE id=%lld",
                                       err_lit,
                                       (long long) work_id);
                    (void) SPI_execute(q, false, 0);
                    pfree(q);
                    pfree(err_lit);
                }
                goto done_tx;
            }

	            idxname = nrim_worker_build_index_name(best_relid, best_cols, best_ncols, query_id);

            initStringInfo(&create);
            appendStringInfo(&create,
                             "CREATE INDEX %s %s ON %s.%s USING btree (",
                             nr_reactive_use_concurrently ? "CONCURRENTLY" : "",
                             quote_identifier(idxname),
                             quote_identifier(nspname),
                             quote_identifier(relname));
                    bool cols_ok = true;
            for (int i = 0; i < best_ncols; i++)
            {
                char *attname = get_attname(best_relid, best_cols[i], false);
                Oid atttyp = InvalidOid;
                if (attname == NULL)
                {
                    cols_ok = false;
                    break;
                }
                atttyp = get_atttype(best_relid, best_cols[i]);
	                if (!nrim_worker_has_default_btree_opclass(atttyp))
	                {
	                    if (nr_reactive_debug)
	                        NRIM_ELOG("NRIM v2 worker: cannot CREATE INDEX relid=%u attnum=%d (no btree opclass for typoid=%u)",
	                                  best_relid, (int) best_cols[i], atttyp);
	                    pfree(attname);
                    cols_ok = false;
                    break;
                }
	                if (i > 0)
	                    appendStringInfoString(&create, ", ");
	                appendStringInfoString(&create, quote_identifier(attname));
	                pfree(attname);
	            }
            appendStringInfoChar(&create, ')');

            if (!cols_ok)
            {
                char *err_lit = quote_literal_cstr("could not resolve column name");
                char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='error', last_error=%s WHERE id=%lld",
                                   err_lit,
                                   (long long) work_id);
                (void) SPI_execute(q, false, 0);
                pfree(q);
                pfree(err_lit);
                pfree(create.data);
                pfree(idxname);
                pfree(nspname);
                pfree(relname);
                goto done_tx;
            }

	            if (nr_reactive_debug)
	            {
	                char *sqlprev = nrim_preview_text(create.data, 400);
	                NRIM_ELOG("NRIM v2 worker: attempting CREATE INDEX idxname=%s sql=\"%s\"", idxname, sqlprev);
	                pfree(sqlprev);
	            }

	            if (nr_reactive_debug)
	                NRIM_ELOG("NRIM v2 worker: create path step=before_persist");

	            /*
	             * CommitTransactionCommand() will reset transaction memory contexts,
	             * so copy strings we still need across the commit into TopMemoryContext
	             * to avoid use-after-free crashes.
             */
            {
                MemoryContext oldcxt = MemoryContextSwitchTo(TopMemoryContext);
                char *create_sql_persist = pstrdup(create.data);
                char *idxname_persist = pstrdup(idxname);
                char *nspname_persist = pstrdup(nspname);
                char *relname_persist = pstrdup(relname);
                MemoryContextSwitchTo(oldcxt);

                create.data = create_sql_persist;
                idxname = idxname_persist;
                nspname = nspname_persist;
	                relname = relname_persist;
	            }

	            if (nr_reactive_debug)
	                NRIM_ELOG("NRIM v2 worker: create path step=before_spi_finish");

	            SPI_finish();
	            PopActiveSnapshot();
	            if (nr_reactive_debug)
	                NRIM_ELOG("NRIM v2 worker: create path step=before_commit");
	            CommitTransactionCommand();
	            if (nr_reactive_debug)
	                NRIM_ELOG("NRIM v2 worker: create path step=after_commit");

	            created_ok = nrim_worker_run_psql_utility(dboid, create.data);

            StartTransactionCommand();
            PushActiveSnapshot(GetTransactionSnapshot());
            if (SPI_connect() != SPI_OK_CONNECT)
            {
                PopActiveSnapshot();
                AbortCurrentTransaction();
                pfree(create.data);
                pfree(idxname);
                pfree(nspname);
                pfree(relname);
                pfree(query_text);
                if (candidates_json) pfree(candidates_json);
                return true;
            }

            if (!created_ok)
            {
                {
                    char *err_lit = quote_literal_cstr("CREATE INDEX failed");
                    char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='error', last_error=%s WHERE id=%lld",
                                       err_lit,
                                       (long long) work_id);
                    (void) SPI_execute(q, false, 0);
                    pfree(q);
                    pfree(err_lit);
                }
                pfree(create.data);
                pfree(idxname);
                pfree(nspname);
                pfree(relname);
                goto done_tx;
            }

            {
                Oid nspoid = get_namespace_oid(nspname, true);
                Oid index_oid = get_relname_relid(idxname, nspoid);
                if (nr_reactive_debug)
                    NRIM_ELOG("NRIM v2 worker: CREATE INDEX succeeded, index lookup nsp=%s idxname=%s oid=%u",
                              nspname, idxname, index_oid);
	                if (OidIsValid(index_oid))
	                {
	                    (void) nrim_worker_ensure_registry_for_index(index_oid, best_relid, best_cols, best_ncols, "btree", best_benefit);
	                    (void) nrim_worker_update_attribution(query_id, index_oid, best_benefit);
	                }
                else
                {
                    if (nr_reactive_debug)
                        NRIM_ELOG("NRIM v2 worker: CREATE INDEX succeeded but OID lookup failed (not registering)");
                }
            }

            {
                char *q = psprintf("UPDATE nrim.nrim_work_queue SET status='done' WHERE id=%lld",
                                   (long long) work_id);
                (void) SPI_execute(q, false, 0);
                pfree(q);
            }

            pfree(create.data);
            pfree(idxname);
            pfree(nspname);
            pfree(relname);
        }
    }

done_tx:
    SPI_finish();
    PopActiveSnapshot();
    CommitTransactionCommand();
    pfree(query_text);
    if (candidates_json) pfree(candidates_json);
    return true;
}

/*
 * Phase 5-7: background worker main loop.
 */
PGDLLEXPORT void
nrim_worker_main(Datum main_arg)
{
    Oid dboid = DatumGetObjectId(main_arg);

    pqsignal(SIGTERM, nrim_worker_sigterm_handler);
    pqsignal(SIGHUP, nrim_worker_sighup_handler);
    BackgroundWorkerUnblockSignals();

    BackgroundWorkerInitializeConnectionByOid(dboid, InvalidOid, 0);
    (void) SetConfigOption("application_name", "nrim_reactive_worker",
                           PGC_USERSET, PGC_S_OVERRIDE);

    ereport(LOG, (errmsg("NRIM v2 worker: started (dboid=%u)", dboid)));

    /* Ensure single active worker per database */
    {
        LOCKTAG tag;
        LockAcquireResult res;

        SET_LOCKTAG_ADVISORY(tag,
                             MyDatabaseId,
                             1313296973,
                             2147483647,
                             1);
        res = LockAcquire(&tag, ExclusiveLock, false, true);
        if (res != LOCKACQUIRE_OK)
        {
            ereport(LOG, (errmsg("NRIM v2 worker: exiting (another worker holds advisory lock)")));
            proc_exit(0);
        }
    }

    while (!nrim_worker_sigterm)
    {
        bool did_work = false;

        if (nrim_worker_got_sighup)
        {
            nrim_worker_got_sighup = false;
            ProcessConfigFile(PGC_SIGHUP);
            NRIM_ELOG("NRIM v2 worker: reloaded config (nr_reactive.debug=%s)",
                      nr_reactive_debug ? "on" : "off");
        }

        for (int i = 0; i < 5; i++)
        {
            if (nrim_worker_sigterm)
                break;
            if (!nrim_process_one_work_item(dboid))
                break;
            did_work = true;
        }

        if (did_work)
            (void) nrim_worker_refresh_registry_stats();

        {
            int rc = WaitLatch(MyLatch,
                               WL_LATCH_SET | WL_TIMEOUT | WL_POSTMASTER_DEATH,
                               nr_reactive_worker_interval_ms,
                               PG_WAIT_EXTENSION);
            ResetLatch(MyLatch);
            if (rc & WL_POSTMASTER_DEATH)
                proc_exit(1);
        }
    }

    ereport(LOG, (errmsg("NRIM v2 worker: stopping")));
    proc_exit(0);
}

/*
 * Hook function to intercept queries for reactive index management
 */
void
index_management_post_parse_analyze(ParseState *pstate, Query *query, JumbleState *jstate)
{
    const char *query_text;

    /* Call the previous hook if any */
    if (prev_post_parse_analyze_hook)
        prev_post_parse_analyze_hook(pstate, query, jstate);

    /* Only process SELECT queries */
    if (query->commandType != CMD_SELECT)
        return;

    /* Check if reactive strategy is enabled */
    if (strcmp(nr_index_management_strategy, "reactive") != 0)
        return;

    /* v2 master enable */
    if (!nr_reactive_enable)
        return;

    /* Skip queries issued by AI engine sessions (application_name filter) */
    if (is_ai_engine_session())
        return;

    /* Get text for the current statement, not the whole batch */
    query_text = get_current_statement_text(pstate, query);
    if (!query_text || query_text[0] == '\0')
        return;

    /* Defensive: only send if the statement text is a SELECT */
    if (!is_select_statement_text(query_text))
        return;

    /*
     * Only intercept user-table queries.
     * Skip queries that reference system schemas (pg_catalog, information_schema, etc),
     * including internal AI/extension helper queries on catalogs.
     */
    if (!query_references_only_user_tables(query))
        return;

    /*
     * v2: enqueue work for background worker (non-blocking).
     * Phase 5: queue stores query text + candidate summary text.
     */
    if (nr_reactive_enable)
    {
        char *candidates_json;

        nrim_start_worker_if_needed();
        candidates_json = nrim_build_candidates_json(query);
        if (candidates_json)
        {
            (void) nrim_enqueue_work_item(query_text, candidates_json);
            pfree(candidates_json);
        }

        return;
    }

    /* v2: no AI-engine reactive path */
    return;
}

/*
 * Extract the SQL text for the current statement.
 * When p_sourcetext includes multiple statements (e.g., "SET ...; SELECT ..."),
 * use stmt_location/stmt_len to slice out just the SELECT.
 */
static const char *
get_current_statement_text(ParseState *pstate, Query *query)
{
    const char *source;
    int loc;
    int len;

    if (pstate == NULL)
        return NULL;

    source = pstate->p_sourcetext;
    if (source == NULL)
        return NULL;

    loc = query ? query->stmt_location : -1;
    len = query ? query->stmt_len : -1;

    if (loc >= 0 && len > 0)
    {
        /* stmt_location is a byte offset into p_sourcetext */
        int srclen = strlen(source);
        if (loc < srclen)
        {
            int slice_len = Min(len, srclen - loc);
            return pnstrdup(source + loc, slice_len);
        }
    }

    /* Fallback: whole sourcetext */
    return source;
}

/*
 * Lightweight check that statement text begins with SELECT after
 * skipping whitespace and comments.
 */
static bool
is_select_statement_text(const char *stmt)
{
    const char *p = stmt;

    if (p == NULL)
        return false;

    /* Skip whitespace and comments */
    for (;;)
    {
        while (*p && isspace((unsigned char) *p))
            p++;

        /* Line comment */
        if (p[0] == '-' && p[1] == '-')
        {
            p += 2;
            while (*p && *p != '\n')
                p++;
            continue;
        }

        /* Block comment */
        if (p[0] == '/' && p[1] == '*')
        {
            p += 2;
            while (*p && !(p[0] == '*' && p[1] == '/'))
                p++;
            if (*p)
                p += 2;
            continue;
        }

        break;
    }

    /* Case-insensitive "select" */
    if (pg_strncasecmp(p, "select", 6) != 0)
        return false;

    /* Ensure it's a standalone keyword */
    p += 6;
    if (*p && (isalnum((unsigned char) *p) || *p == '_'))
        return false;

    return true;
}

/*
 * Return true only if the query (and any subqueries) references
 * relations in non-system schemas.
 */
static bool
query_references_only_user_tables(Query *query)
{
    ListCell *lc;

    if (query == NULL)
        return false;

    foreach(lc, query->rtable)
    {
        RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);

        if (rte == NULL)
            continue;

        switch (rte->rtekind)
        {
            case RTE_RELATION:
            {
                Oid relid = rte->relid;
                Oid nspid;
                char *nspname;

                if (!OidIsValid(relid))
                    break;

                nspid = get_rel_namespace(relid);
                nspname = get_namespace_name(nspid);

                if (nspname == NULL)
                    return false;

                if (is_system_schema_name(nspname))
                {
                    pfree(nspname);
                    return false;
                }

                pfree(nspname);
                break;
            }
            case RTE_SUBQUERY:
                if (!query_references_only_user_tables(rte->subquery))
                    return false;
                break;
            default:
                /* Other RTE kinds don't directly reference a relation */
                break;
        }
    }

    return true;
}

/*
 * Treat pg_catalog, information_schema, and other pg_* schemas
 * (except pg_temp_*) as system schemas to ignore.
 */
static bool
is_system_schema_name(const char *nspname)
{
    if (nspname == NULL)
        return true;

    if (strcmp(nspname, "pg_catalog") == 0 ||
        strcmp(nspname, "information_schema") == 0)
        return true;

    /* Extension internal bookkeeping schema */
    if (strcmp(nspname, "nrim") == 0)
        return true;

    if (strncmp(nspname, "pg_", 3) == 0 &&
        strncmp(nspname, "pg_temp", 7) != 0)
        return true;

    return false;
}

/*
 * Detect AI engine sessions by application_name.
 * AI engine sets application_name='neurdb_ai' on its connections.
 */
static bool
is_ai_engine_session(void)
{
    const char *appname = GetConfigOption("application_name", true, false);

    if (appname == NULL || appname[0] == '\0')
        return false;

    return (strcmp(appname, "neurdb_ai") == 0 ||
            strcmp(appname, "nrim_reactive_worker") == 0 ||
            strcmp(appname, "neurdb_reactive_worker") == 0);
}

static bool
collect_var_attnum(Query *query, Var *var, Oid *relid_out, AttrNumber *attnum_out)
{
    RangeTblEntry *rte;

    if (query == NULL || var == NULL)
        return false;

    if (var->varno <= 0 || var->varno > list_length(query->rtable))
        return false;

    rte = rt_fetch(var->varno, query->rtable);
    if (rte == NULL || rte->rtekind != RTE_RELATION)
        return false;

    if (!OidIsValid(rte->relid))
        return false;

    if (var->varattno <= 0)
        return false;

    if (relid_out)
        *relid_out = rte->relid;
    if (attnum_out)
        *attnum_out = var->varattno;

    return true;
}

static bool
is_equality_op(Oid opno)
{
    const char *name = get_opname(opno);
    if (name == NULL)
        return false;
    return (strcmp(name, "=") == 0);
}

static bool
is_range_op(Oid opno)
{
    const char *name = get_opname(opno);
    if (name == NULL)
        return false;
    return (strcmp(name, "<") == 0 ||
            strcmp(name, "<=") == 0 ||
            strcmp(name, ">") == 0 ||
            strcmp(name, ">=") == 0);
}

/*
 * Phase 4 (placeholder): per-table feature extraction + candidate generation + merge.
 * This is still synchronous and only logs candidates; later phases will enqueue
 * to a background worker and remove HTTP entirely.
 */
typedef struct NrimTableFeat
{
    Oid relid;
    Bitmapset *eq_cols;     /* members are attnum-1 */
    Bitmapset *range_cols;  /* members are attnum-1 */
    Bitmapset *join_cols;   /* members are attnum-1 */
    List *order_cols;       /* List of int (attnum) in order */
    List *group_cols;       /* List of int (attnum) in order */
} NrimTableFeat;

typedef struct NrimCand
{
    Oid relid;
    int ncols;
    AttrNumber cols[8]; /* max width bounded by GUC (<=8) */
} NrimCand;

typedef struct NrimFeatCtx
{
    Query *query;
    List **tables; /* List of NrimTableFeat* */
} NrimFeatCtx;

static NrimTableFeat *nrim_get_table_feat(List **tables, Oid relid);
static void nrim_add_att_bitmap(Bitmapset **bms, AttrNumber attnum);
static void nrim_add_att_list_unique(List **list, AttrNumber attnum);
static bool nrim_feat_walker(Node *node, NrimFeatCtx *ctx);
static void nrim_collect_order_group(Query *query, List **tables);
static void nrim_generate_candidates_for_table(NrimTableFeat *tf, List **cands);
static void nrim_merge_candidates(List **cands);
static bool nrim_cand_equals(const NrimCand *a, const NrimCand *b);
static bool nrim_cand_is_prefix_of(const NrimCand *shorter, const NrimCand *longer);

/*
 * v2 work-item payload: machine-parseable JSON describing candidates.
 *
 * Format:
 *   [{"relid":12345,"cols":[1,2]}, ...]
 *
 * This avoids relation/attribute name lookups in the hook fast-path.
 */
static char *
nrim_build_candidates_json(Query *query)
{
    List *tables = NIL;
    List *cands = NIL;
    ListCell *lc;
    NrimFeatCtx ctx;
    StringInfoData buf;
    int emitted = 0;

    if (query == NULL)
        return NULL;

    ctx.query = query;
    ctx.tables = &tables;

    if (query->jointree && query->jointree->quals)
        (void) nrim_feat_walker(query->jointree->quals, &ctx);
    nrim_collect_order_group(query, &tables);
    foreach(lc, tables)
        nrim_generate_candidates_for_table((NrimTableFeat *) lfirst(lc), &cands);
    nrim_merge_candidates(&cands);

    initStringInfo(&buf);
    appendStringInfoChar(&buf, '[');

    foreach(lc, cands)
    {
        NrimCand *cand = (NrimCand *) lfirst(lc);
        int i;

        if (emitted >= nr_reactive_max_candidates_per_query)
            break;

        if (emitted > 0)
            appendStringInfoChar(&buf, ',');

        appendStringInfo(&buf, "{\"relid\":%u,\"cols\":[", cand->relid);
        for (i = 0; i < cand->ncols; i++)
        {
            if (i > 0)
                appendStringInfoChar(&buf, ',');
            appendStringInfo(&buf, "%d", cand->cols[i]);
        }
        appendStringInfoString(&buf, "]}");
        emitted++;
    }

    appendStringInfoChar(&buf, ']');

    if (emitted == 0)
    {
        pfree(buf.data);
        return NULL;
    }

    return buf.data;
}

static NrimTableFeat *
nrim_get_table_feat(List **tables, Oid relid)
{
    ListCell *lc;
    foreach(lc, *tables)
    {
        NrimTableFeat *tf = (NrimTableFeat *) lfirst(lc);
        if (tf->relid == relid)
            return tf;
    }

    NrimTableFeat *tf = palloc0(sizeof(*tf));
    tf->relid = relid;
    *tables = lappend(*tables, tf);
    return tf;
}

static void
nrim_add_att_bitmap(Bitmapset **bms, AttrNumber attnum)
{
    if (attnum <= 0)
        return;
    *bms = bms_add_member(*bms, attnum - 1);
}

static void
nrim_add_att_list_unique(List **list, AttrNumber attnum)
{
    ListCell *lc;
    foreach(lc, *list)
    {
        int existing = lfirst_int(lc);
        if (existing == attnum)
            return;
    }
    *list = lappend_int(*list, attnum);
}

static bool
nrim_feat_walker(Node *node, NrimFeatCtx *ctx)
{
    if (node == NULL)
        return false;

    if (IsA(node, OpExpr))
    {
        OpExpr *op = (OpExpr *) node;
        if (list_length(op->args) == 2)
        {
            Node *lhs = strip_implicit_coercions((Node *) linitial(op->args));
            Node *rhs = strip_implicit_coercions((Node *) lsecond(op->args));
            bool is_eq = is_equality_op(op->opno);
            bool is_rng = is_range_op(op->opno);

            if (lhs && rhs && is_eq && IsA(lhs, Var) && IsA(rhs, Var))
            {
                Oid relid_l, relid_r;
                AttrNumber att_l, att_r;
                if (collect_var_attnum(ctx->query, (Var *) lhs, &relid_l, &att_l) &&
                    collect_var_attnum(ctx->query, (Var *) rhs, &relid_r, &att_r))
                {
                    NrimTableFeat *tfl = nrim_get_table_feat(ctx->tables, relid_l);
                    NrimTableFeat *tfr = nrim_get_table_feat(ctx->tables, relid_r);

                    if (relid_l != relid_r)
                    {
                        nrim_add_att_bitmap(&tfl->join_cols, att_l);
                        nrim_add_att_bitmap(&tfr->join_cols, att_r);
                    }
                    else
                    {
                        nrim_add_att_bitmap(&tfl->eq_cols, att_l);
                        nrim_add_att_bitmap(&tfl->eq_cols, att_r);
                    }
                }
            }
            else
            {
                if (lhs && IsA(lhs, Var))
                {
                    Oid relid;
                    AttrNumber attnum;
                    if (collect_var_attnum(ctx->query, (Var *) lhs, &relid, &attnum))
                    {
                        NrimTableFeat *tf = nrim_get_table_feat(ctx->tables, relid);
                        if (is_eq)
                            nrim_add_att_bitmap(&tf->eq_cols, attnum);
                        else if (is_rng)
                            nrim_add_att_bitmap(&tf->range_cols, attnum);
                    }
                }
                if (rhs && IsA(rhs, Var))
                {
                    Oid relid;
                    AttrNumber attnum;
                    if (collect_var_attnum(ctx->query, (Var *) rhs, &relid, &attnum))
                    {
                        NrimTableFeat *tf = nrim_get_table_feat(ctx->tables, relid);
                        if (is_eq)
                            nrim_add_att_bitmap(&tf->eq_cols, attnum);
                        else if (is_rng)
                            nrim_add_att_bitmap(&tf->range_cols, attnum);
                    }
                }
            }
        }
    }

    return expression_tree_walker(node, nrim_feat_walker, (void *) ctx);
}

static void
nrim_collect_order_group(Query *query, List **tables)
{
    ListCell *lc;

    if (query == NULL)
        return;

    if (query->sortClause)
    {
        foreach(lc, query->sortClause)
        {
            SortGroupClause *sgc = (SortGroupClause *) lfirst(lc);
            Node *expr = get_sortgroupclause_expr(sgc, query->targetList);
            expr = strip_implicit_coercions(expr);
            if (expr && IsA(expr, Var))
            {
                Oid relid;
                AttrNumber attnum;
                if (collect_var_attnum(query, (Var *) expr, &relid, &attnum))
                {
                    NrimTableFeat *tf = nrim_get_table_feat(tables, relid);
                    nrim_add_att_list_unique(&tf->order_cols, attnum);
                }
            }
        }
    }

    if (query->groupClause)
    {
        foreach(lc, query->groupClause)
        {
            SortGroupClause *sgc = (SortGroupClause *) lfirst(lc);
            Node *expr = get_sortgroupclause_expr(sgc, query->targetList);
            expr = strip_implicit_coercions(expr);
            if (expr && IsA(expr, Var))
            {
                Oid relid;
                AttrNumber attnum;
                if (collect_var_attnum(query, (Var *) expr, &relid, &attnum))
                {
                    NrimTableFeat *tf = nrim_get_table_feat(tables, relid);
                    nrim_add_att_list_unique(&tf->group_cols, attnum);
                }
            }
        }
    }
}

static void
nrim_generate_candidates_for_table(NrimTableFeat *tf, List **cands)
{
    int member;
    int maxw = nr_reactive_max_columns_per_index;
    List *eq_list = NIL;
    ListCell *lc;

    if (tf == NULL)
        return;

    /* Single-column candidates from eq/join/order/group/range */
    member = -1;
    while ((member = bms_next_member(tf->eq_cols, member)) >= 0)
    {
        NrimCand *c = palloc0(sizeof(*c));
        c->relid = tf->relid;
        c->ncols = 1;
        c->cols[0] = (AttrNumber) (member + 1);
        *cands = lappend(*cands, c);
        eq_list = lappend_int(eq_list, member + 1);
    }
    member = -1;
    while ((member = bms_next_member(tf->join_cols, member)) >= 0)
    {
        NrimCand *c = palloc0(sizeof(*c));
        c->relid = tf->relid;
        c->ncols = 1;
        c->cols[0] = (AttrNumber) (member + 1);
        *cands = lappend(*cands, c);
        if (!list_member_int(eq_list, member + 1))
            eq_list = lappend_int(eq_list, member + 1);
    }
    member = -1;
    while ((member = bms_next_member(tf->range_cols, member)) >= 0)
    {
        NrimCand *c = palloc0(sizeof(*c));
        c->relid = tf->relid;
        c->ncols = 1;
        c->cols[0] = (AttrNumber) (member + 1);
        *cands = lappend(*cands, c);
    }
    foreach(lc, tf->order_cols)
    {
        NrimCand *c = palloc0(sizeof(*c));
        c->relid = tf->relid;
        c->ncols = 1;
        c->cols[0] = (AttrNumber) lfirst_int(lc);
        *cands = lappend(*cands, c);
    }
    foreach(lc, tf->group_cols)
    {
        NrimCand *c = palloc0(sizeof(*c));
        c->relid = tf->relid;
        c->ncols = 1;
        c->cols[0] = (AttrNumber) lfirst_int(lc);
        *cands = lappend(*cands, c);
    }

    /* Simple multi-column candidates (prefix-based) */
    if (list_length(eq_list) >= 2)
    {
        NrimCand *c2 = palloc0(sizeof(*c2));
        c2->relid = tf->relid;
        c2->ncols = 2;
        c2->cols[0] = (AttrNumber) linitial_int(eq_list);
        c2->cols[1] = (AttrNumber) lsecond_int(eq_list);
        *cands = lappend(*cands, c2);
    }

    if (maxw >= 3 && list_length(eq_list) >= 3)
    {
        NrimCand *c3 = palloc0(sizeof(*c3));
        c3->relid = tf->relid;
        c3->ncols = 3;
        c3->cols[0] = (AttrNumber) linitial_int(eq_list);
        c3->cols[1] = (AttrNumber) lsecond_int(eq_list);
        c3->cols[2] = (AttrNumber) lthird_int(eq_list);
        *cands = lappend(*cands, c3);
    }

    /* Filter + ORDER BY heuristic: (eq_prefix..., first_order_col) */
    if (tf->order_cols != NIL && eq_list != NIL && maxw >= 2)
    {
        AttrNumber ord = (AttrNumber) linitial_int(tf->order_cols);
        if (!list_member_int(eq_list, ord))
        {
            NrimCand *co = palloc0(sizeof(*co));
            co->relid = tf->relid;
            co->ncols = 2;
            co->cols[0] = (AttrNumber) linitial_int(eq_list);
            co->cols[1] = ord;
            *cands = lappend(*cands, co);
        }
    }
}

static void
nrim_merge_candidates(List **cands)
{
    List *dedup = NIL;
    ListCell *lc;
    ListCell *lc2;

    /* Dedupe */
    foreach(lc, *cands)
    {
        NrimCand *c = (NrimCand *) lfirst(lc);
        bool exists = false;
        foreach(lc2, dedup)
        {
            NrimCand *d = (NrimCand *) lfirst(lc2);
            if (nrim_cand_equals(c, d))
            {
                exists = true;
                break;
            }
        }
        if (!exists)
            dedup = lappend(dedup, c);
    }

    /* Prefix prune: if (A,B,...) exists, drop (A) */
    List *pruned = NIL;
    foreach(lc, dedup)
    {
        NrimCand *c = (NrimCand *) lfirst(lc);
        bool drop = false;

        if (c->ncols == 1)
        {
            foreach(lc2, dedup)
            {
                NrimCand *d = (NrimCand *) lfirst(lc2);
                if (d->relid == c->relid && d->ncols > 1 &&
                    nrim_cand_is_prefix_of(c, d))
                {
                    drop = true;
                    break;
                }
            }
        }

        if (!drop)
            pruned = lappend(pruned, c);
    }

    *cands = pruned;
}

static bool
nrim_cand_equals(const NrimCand *a, const NrimCand *b)
{
    int i;
    if (a->relid != b->relid || a->ncols != b->ncols)
        return false;
    for (i = 0; i < a->ncols; i++)
        if (a->cols[i] != b->cols[i])
            return false;
    return true;
}

static bool
nrim_cand_is_prefix_of(const NrimCand *shorter, const NrimCand *longer)
{
    int i;
    if (shorter->relid != longer->relid)
        return false;
    if (shorter->ncols >= longer->ncols)
        return false;
    for (i = 0; i < shorter->ncols; i++)
        if (shorter->cols[i] != longer->cols[i])
            return false;
    return true;
}

/*
 * Module initialization
 */
void
_PG_init(void)
{
    /* v2 extension-local GUCs */
    DefineCustomBoolVariable(
        "nr_reactive.enable",
        "Enable reactive index management (v2) in nr_index_management.",
        NULL,
        &nr_reactive_enable,
        true,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomIntVariable(
        "nr_reactive.max_columns_per_index",
        "Maximum number of columns for a generated multi-column index candidate.",
        NULL,
        &nr_reactive_max_columns_per_index,
        3,
        1,
        8,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomIntVariable(
        "nr_reactive.max_candidates_per_query",
        "Maximum number of index candidates generated per query (across tables).",
        NULL,
        &nr_reactive_max_candidates_per_query,
        20,
        1,
        200,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomIntVariable(
        "nr_reactive.worker_interval_ms",
        "Background reactive worker polling interval (milliseconds).",
        NULL,
        &nr_reactive_worker_interval_ms,
        200,
        10,
        60000,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomIntVariable(
        "nr_reactive.max_hypopg_evals_per_minute",
        "Maximum number of HypoPG+EXPLAIN evaluations per minute (soft cap).",
        NULL,
        &nr_reactive_max_hypopg_evals_per_minute,
        30,
        0,
        10000,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomRealVariable(
        "nr_reactive.min_value_improvement",
        "Required relative value improvement before evicting existing reactive indexes.",
        NULL,
        &nr_reactive_min_value_improvement,
        0.20,
        0.0,
        100.0,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomBoolVariable(
        "nr_reactive.use_concurrently",
        "Use CREATE/DROP INDEX CONCURRENTLY in the background worker.",
        NULL,
        &nr_reactive_use_concurrently,
        true,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    DefineCustomBoolVariable(
        "nr_reactive.debug",
        "Emit verbose reactive worker debug logs to help diagnose scoring/creation issues.",
        NULL,
        &nr_reactive_debug,
        false,
        PGC_USERSET,
        0,
        NULL,
        NULL,
        NULL
    );

    /* Install the post_parse_analyze hook */
    prev_post_parse_analyze_hook = post_parse_analyze_hook;
    post_parse_analyze_hook = index_management_post_parse_analyze;

    ereport(LOG,
            (errmsg("REACTIVE INDEX: Index management extension loaded with reactive query interception")));
}

/*
 * Module cleanup
 */
void
_PG_fini(void)
{
    /* Restore the previous hook */
    post_parse_analyze_hook = prev_post_parse_analyze_hook;

    ereport(LOG,
            (errmsg("REACTIVE INDEX: Index management extension unloaded")));
}
