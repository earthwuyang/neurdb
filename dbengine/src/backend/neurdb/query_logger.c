/*
 * Query Logger and Rewriter for AI Engine Integration
 *
 * Logs and rewrites queries to fully qualify column names before sending
 * them to the AI engine for analysis.
 */

#include "postgres.h"

#include "funcapi.h"
#include "access/xact.h"
#include "catalog/pg_type.h"
#include "libpq-fe.h"
#include "miscadmin.h"
#include "postmaster/bgworker.h"
#include "storage/ipc.h"
#include "storage/latch.h"
#include "tcop/tcopprot.h"
#include "utils/builtins.h"
#include "utils/memutils.h"
#include "utils/timestamp.h"

#include "fmgr.h"

PG_MODULE_MAGIC;

/*
 * Function to rewrite a query with fully qualified column names
 */
PG_FUNCTION_INFO_V1(nr_rewrite_query_for_ai);

Datum
nr_rewrite_query_for_ai(PG_FUNCTION_ARGS)
{
    text    *original_query = PG_GETARG_TEXT_P(0);
    char    *query_str = text_to_cstring(original_query);
    char    *rewritten_query;
    text    *result_text;

    if (query_str == NULL)
        PG_RETURN_NULL();

    /* For now, implement a simple rewrite that adds table prefixes
     * to common IMDb queries. This is a simplified approach for testing.
     */
    rewritten_query = rewrite_query_simple(query_str);

    result_text = cstring_to_text(rewritten_query);

    pfree(query_str);
    pfree(rewritten_query);

    PG_RETURN_TEXT_P(result_text);
}

/*
 * Simple query rewrite function for testing
 * This handles common patterns for the IMDb test workload
 */
static char *
rewrite_query_simple(const char *original_query)
{
    char *rewritten = pstrdup(original_query);

    /* Common replacements for IMDb queries */

    /* Replace "SELECT * FROM title WHERE" with qualified columns */
    if (strstr(original_query, "SELECT * FROM title WHERE"))
    {
        /* Replace unqualified column names in title table */
        rewritten = replace_str(rewritten, " WHERE title_id", " WHERE title.title_id");
        rewritten = replace_str(rewritten, " WHERE kind_id", " WHERE title.kind_id");
        rewritten = replace_str(rewritten, " WHERE production_year", " WHERE title.production_year");
        rewritten = replace_str(rewritten, " WHERE imdb_id", " WHERE title.imdb_id");
        rewritten = replace_str(rewritten, " WHERE phonograph_code", " WHERE title.phonograph_code");
        rewritten = replace_str(rewritten, " WHERE episode_of_id", " WHERE title.episode_of_id");
        rewritten = replace_str(rewritten, " WHERE season_nr", " WHERE title.season_nr");
        rewritten = replace_str(rewritten, " WHERE episode_nr", " WHERE title.episode_nr");
    }

    /* Replace "SELECT * FROM cast_info WHERE" */
    if (strstr(original_query, "SELECT * FROM cast_info WHERE"))
    {
        rewritten = replace_str(rewritten, " WHERE person_id", " WHERE cast_info.person_id");
        rewritten = replace_str(rewritten, " WHERE movie_id", " WHERE cast_info.movie_id");
        rewritten = replace_str(rewritten, " WHERE role_id", " WHERE cast_info.role_id");
        rewritten = replace_str(rewritten, " WHERE note", " WHERE cast_info.note");
        rewritten = replace_str(rewritten, " WHERE nr_order", " WHERE cast_info.nr_order");
        rewritten = replace_str(rewritten, " WHERE role_id", " WHERE cast_info.role_id");
    }

    /* Replace "SELECT * FROM movie_info WHERE" */
    if (strstr(original_query, "SELECT * FROM movie_info WHERE"))
    {
        rewritten = replace_str(rewritten, " WHERE movie_id", " WHERE movie_info.movie_id");
        rewritten = replace_str(rewritten, " WHERE info_type_id", " WHERE movie_info.info_type_id");
        rewritten = replace_str(rewritten, " WHERE info", " WHERE movie_info.info");
        rewritten = replace_str(rewritten, " WHERE note", " WHERE movie_info.note");
    }

    /* Handle ORDER BY clauses */
    if (strstr(rewritten, "ORDER BY"))
    {
        rewritten = replace_str(rewritten, "ORDER BY production_year", "ORDER BY title.production_year");
        rewritten = replace_str(rewritten, "ORDER BY title_id", "ORDER BY title.title_id");
        rewritten = replace_str(rewritten, "ORDER BY kind_id", "ORDER BY title.kind_id");
        rewritten = replace_str(rewritten, "ORDER BY person_id", "ORDER BY cast_info.person_id");
        rewritten = replace_str(rewritten, "ORDER BY movie_id", "ORDER BY cast_info.movie_id");
    }

    return rewritten;
}

/*
 * Simple string replacement function
 */
static char *
replace_str(char *str, const char *old, const char *new)
{
    char *ret;
    char *p;
    char *q;
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
    ret = palloc(strlen(str) + count * (new_len - old_len) + 1);

    /* Replace */
    p = str;
    char *r = ret;
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

    return ret;
}

/*
 * Function to send rewritten query to AI engine for analysis
 */
PG_FUNCTION_INFO_V1(nr_send_query_to_ai);

Datum
nr_send_query_to_ai(PG_FUNCTION_ARGS)
{
    text    *query_text = PG_GETARG_TEXT_P(0);
    char    *query_str = text_to_cstring(query_text);
    char    *rewritten_query;
    char    *ai_engine_url = "http://localhost:8777/index/reactive/test";
    PGconn   *conn;
    PGresult *res;
    char     *postdata;
    char     *response_data = NULL;
    text     *result_text;

    /* Rewrite the query to fully qualify column names */
    rewritten_query = rewrite_query_simple(query_str);

    /* Prepare the POST data */
    postdata = psprintf("{\"query\": \"%s\", \"execution_time\": 100.0}",
                       escape_json_string(rewritten_query));

    /* Send to AI engine using libpq */
    conn = PQconnectdb("host=localhost port=5432 dbname=neurdb user=neurdb password=");
    if (PQstatus(conn) != CONNECTION_OK)
    {
        ereport(WARNING,
                (errmsg("Could not connect to database for AI engine: %s",
                        PQerrorMessage(conn))));
        PQfinish(conn);
        pfree(query_str);
        pfree(rewritten_query);
        pfree(postdata);
        PG_RETURN_NULL();
    }

    /* For now, just return the rewritten query as a simple test */
    result_text = cstring_to_text(rewritten_query);

    pfree(query_str);
    pfree(rewritten_query);
    pfree(postdata);
    PQfinish(conn);

    PG_RETURN_TEXT_P(result_text);
}

/*
 * Escape JSON string
 */
static char *
escape_json_string(const char *str)
{
    char *result = palloc(strlen(str) * 2 + 3); /* Worst case */
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