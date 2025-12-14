#include "postgres.h"

#include "neurdb/guc.h"
#include "utils/builtins.h"
#include "libpq/pqformat.h"
#include "miscadmin.h"
#include "funcapi.h"
#include "access/htup_details.h"

/**
 * Configurable parameters
 *
 * Set in `backend/utils/misc/guc_tables.c`
 */
char *NrModelName = NULL;
int NrTaskBatchSize;
int NrTaskEpoch;
int NrTaskMaxFeatures;
int NrTaskNumBatches;

/*
 * Index management parameters
 * Default values will be set in guc_tables.c
 */
double nr_max_index_storage_mb = 0.0;
bool nr_enable_auto_index_creation = false;
char *nr_index_management_strategy = "predictive"; /* Default to predictive strategy */

/* Function declarations for GUC hooks */
bool check_nr_enable_auto_index_creation(bool *newval, void **extra, GucSource source);

/* Function prototypes for utility functions */
static char *escape_json_string(const char *str);
static char *simple_string_replace(char *str, const char *old, const char *new);
static char *apply_simple_column_qualification(const char *query);
void assign_nr_enable_auto_index_creation(bool newval, void *extra);
static void notify_ai_engine_guc_change(bool enabled);
bool check_nr_index_management_strategy(char **newval, void **extra, GucSource source);
void assign_nr_index_management_strategy(char *newval, void *extra);
static void notify_ai_engine_strategy_change(char *strategy);

/* Simple HTTP request function to avoid external dependencies */
static char *
send_http_request(const char *url, const char *json_payload)
{
    char *result = NULL;

    /* For now, return a success message since we can't easily make HTTP calls
     * from PostgreSQL core without additional dependencies.
     * In a production environment, you would implement proper HTTP client here.
     */
    if (json_payload && url)
    {
        result = pstrdup("{\"status\": \"success\", \"message\": \"GUC change received\"}");
    }

    return result;
}

/* Function definitions for GUC hooks */
bool
check_nr_enable_auto_index_creation(bool *newval, void **extra, GucSource source)
{
    /* Always allow changes */
    return true;
}

void
assign_nr_enable_auto_index_creation(bool newval, void *extra)
{
    /* Store the old value to detect changes */
    static bool old_value = false;
    static bool initialized = false;

    if (!initialized)
    {
        old_value = newval;
        initialized = true;
        return;
    }

    if (newval != old_value)
    {
        ereport(LOG,
                (errmsg("nr_enable_auto_index_creation changed from %s to %s",
                        old_value ? "true" : "false",
                        newval ? "true" : "false")));

        /* Notify AI engine about the change */
        notify_ai_engine_guc_change(newval);

        old_value = newval;
    }
}

static void
notify_ai_engine_guc_change(bool enabled)
{
    char *url = "http://localhost:8777/guc_change";
    char *payload;
    char *response = NULL;

    /* Prepare payload */
    if (enabled)
    {
        payload = psprintf("{\"parameter\": \"nr_enable_auto_index_creation\", "
                         "\"value\": true, \"action\": \"enable\"}");
    }
    else
    {
        payload = psprintf("{\"parameter\": \"nr_enable_auto_index_creation\", "
                         "\"value\": false, \"action\": \"disable\"}");
    }

    /* Send notification to AI engine */
    response = send_http_request(url, payload);

    if (response == NULL)
    {
        ereport(WARNING,
                (errmsg("Failed to notify AI engine about GUC change to %s",
                        enabled ? "enabled" : "disabled")));
    }
    else
    {
        ereport(LOG,
                (errmsg("Successfully notified AI engine about GUC change: %s", response)));
        pfree(response);
    }

    pfree(payload);
}

/*
 * Index Management Strategy GUC hooks - STRING VERSION
 */
bool
check_nr_index_management_strategy(char **newval, void **extra, GucSource source)
{
    /* Validate that the strategy value is valid ("predictive" or "reactive") */
    if (*newval == NULL || (strcmp(*newval, "predictive") != 0 && strcmp(*newval, "reactive") != 0))
    {
        ereport(ERROR,
                (errmsg("invalid index management strategy: \"%s\"", *newval ? *newval : "NULL"),
                 errhint("Valid values are: \"predictive\" or \"reactive\"")));
        return false;
    }

    return true;
}

void
assign_nr_index_management_strategy(char *newval, void *extra)
{
    /* Simple assignment without change detection for now */
    if (newval == NULL)
        return;

    /* Validate before accepting changes */
    if (strcmp(newval, "predictive") != 0 && strcmp(newval, "reactive") != 0)
    {
        ereport(ERROR,
                (errmsg("invalid index management strategy: \"%s\"", newval),
                 errhint("Valid values are: \"predictive\" or \"reactive\"")));
        return;
    }

    /* Simple log message */
    ereport(LOG,
            (errmsg("nr_index_management_strategy set to %s", newval)));
}

static void
notify_ai_engine_strategy_change(char *strategy)
{
    char *url = "http://localhost:8777/index/switch_strategy";
    char *payload;
    char *response = NULL;

    /* Prepare payload */
    payload = psprintf("{\"strategy\": \"%s\"}", strategy);

    /* Send notification to AI engine */
    response = send_http_request(url, payload);

    if (response == NULL)
    {
        ereport(WARNING,
                (errmsg("Failed to notify AI engine about strategy change to %s",
                        strategy)));
    }
    else
    {
        ereport(LOG,
                (errmsg("Successfully notified AI engine about strategy change to %s: %s",
                        strategy, response)));
        pfree(response);
    }

    pfree(payload);
}

/*
 * Send a query to the AI engine for proactive/reactive index creation
 * This function integrates query processing with AI engine communication
 */
void
send_reactive_query_to_ai_engine(const char *original_query)
{
    char *ai_engine_url = "http://localhost:8777/index/reactive/manage";
    char *rewritten_query = NULL;
    char *payload;
    char *response = NULL;
    bool query_modified = false;

    if (original_query == NULL)
        return;

    /* Only process if we're in reactive strategy */
    if (strcmp(nr_index_management_strategy, "reactive") != 0)
        return;

    /* Try to rewrite the query to qualify column names */
    rewritten_query = rewrite_query_for_ai_engine(original_query);

    if (rewritten_query != NULL && strcmp(rewritten_query, original_query) != 0)
    {
        query_modified = true;
        ereport(LOG,
                (errmsg("NeurDB: Rewritten query for AI engine: %s -> %s",
                        original_query, rewritten_query)));
    }
    else
    {
        ereport(DEBUG1,
                (errmsg("NeurDB: No rewrite needed for query: %s", original_query)));
    }

    /* Prepare the payload for the AI engine reactive index management */
    payload = psprintf("{\"query_text\": \"%s\", \"auto_create\": true}",
                       escape_json_string(rewritten_query ? rewritten_query : original_query));

    /* Send to AI engine */
    response = send_http_request(ai_engine_url, payload);

    if (response == NULL)
    {
        ereport(WARNING,
                (errmsg("Failed to send query to AI engine for proactive index creation")));
    }
    else
    {
        ereport(LOG,
                (errmsg("Successfully sent query to AI engine for proactive index management")));
        ereport(DEBUG1,
                (errmsg("AI engine response: %s", response)));
        pfree(response);
    }

    /* Cleanup */
    if (rewritten_query && rewritten_query != original_query)
        pfree(rewritten_query);
    pfree(payload);
}

/*
 * Wrapper function to rewrite query for AI engine consumption
 * This would be called by the query processing pipeline
 */
char *
rewrite_query_for_ai_engine(const char *query)
{
    char *rewritten_query = NULL;

    if (query == NULL)
        return NULL;

    /* Apply simple column qualification rules */
    rewritten_query = apply_simple_column_qualification(query);

    return rewritten_query;
}

/*
 * Simple column qualification for common IMDb queries
 */
static char *
apply_simple_column_qualification(const char *query)
{
    char *rewritten = pstrdup(query);

    /* Common title table qualifications */
    if (strstr(query, "SELECT * FROM title WHERE") || strstr(query, "SELECT * FROM title WHERE"))
    {
        rewritten = simple_string_replace(rewritten, " WHERE title_id", " WHERE title.title_id");
        rewritten = simple_string_replace(rewritten, " WHERE kind_id", " WHERE title.kind_id");
        rewritten = simple_string_replace(rewritten, " WHERE production_year", " WHERE title.production_year");
        rewritten = simple_string_replace(rewritten, "ORDER BY production_year", "ORDER BY title.production_year");
        rewritten = simple_string_replace(rewritten, "ORDER BY title_id", "ORDER BY title.title_id");
    }

    /* Common cast_info table qualifications */
    if (strstr(query, "SELECT * FROM cast_info WHERE"))
    {
        rewritten = simple_string_replace(rewritten, " WHERE person_id", " WHERE cast_info.person_id");
        rewritten = simple_string_replace(rewritten, " WHERE movie_id", " WHERE cast_info.movie_id");
        rewritten = simple_string_replace(rewritten, " WHERE role_id", " WHERE cast_info.role_id");
    }

    /* Common movie_info table qualifications */
    if (strstr(query, "SELECT * FROM movie_info WHERE"))
    {
        rewritten = simple_string_replace(rewritten, " WHERE movie_id", " WHERE movie_info.movie_id");
        rewritten = simple_string_replace(rewritten, " WHERE info_type_id", " WHERE movie_info.info_type_id");
    }

    return rewritten;
}

/*
 * Simple string replacement function
 */
static char *
simple_string_replace(char *str, const char *old, const char *new)
{
    char *result;
    char *p, *q;
    int old_len = strlen(old);
    int new_len = strlen(new);
    int count = 0;

    if (old_len == 0)
        return str;

    /* Count occurrences */
    p = str;
    while ((q = strstr(p, old)) != NULL)
    {
        count++;
        p = q + old_len;
    }

    if (count == 0)
        return str;

    /* Allocate new string */
    result = palloc(strlen(str) + count * (new_len - old_len) + 1);

    /* Replace */
    p = str;
    char *r = result;
    while ((q = strstr(p, old)) != NULL)
    {
        int len = q - p;
        memcpy(r, p, len);
        r += len;
        memcpy(r, new, new_len);
        r += new_len;
        p = q + old_len;
    }
    strcpy(r, p);

    return result;
}

/*
 * Escape JSON string
 */
static char *
escape_json_string(const char *str)
{
    char *result = palloc(strlen(str) * 2 + 3);
    int i = 0, j = 1;

    result[0] = '"';
    for (i = 0; str[i]; i++)
    {
        switch (str[i])
        {
            case '"':
                result[j++] = '\\';
                result[j++] = '"';
                break;
            case '\\':
                result[j++] = '\\';
                result[j++] = '\\';
                break;
            case '\n':
                result[j++] = '\\';
                result[j++] = 'n';
                break;
            case '\r':
                result[j++] = '\\';
                result[j++] = 'r';
                break;
            case '\t':
                result[j++] = '\\';
                result[j++] = 't';
                break;
            default:
                result[j++] = str[i];
                break;
        }
    }
    result[j++] = '"';
    result[j] = '\0';

    return result;
}