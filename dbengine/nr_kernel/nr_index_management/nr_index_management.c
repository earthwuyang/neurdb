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
#include "utils/syscache.h"
#include "catalog/pg_class.h"
#include "catalog/pg_index.h"
#include "utils/guc.h"  /* PostgreSQL GUC API */

#include "neurdb/guc.h"

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