/*
 * Query Rewriter for NeurDB AI Engine Integration
 * Rewrites queries to fully qualify column names with table prefixes
 */

#include "postgres.h"

#include "access/xact.h"
#include "catalog/pg_type.h"
#include "funcapi.h"
#include "lib/stringinfo.h"
#include "miscadmin.h"
#include "nodes/makefuncs.h"
#include "nodes/nodeFuncs.h"
#include "nodes/parsenodes.h"
#include "parser/analyze.h"
#include "parser/parsetree.h"
#include "parser/parse_relation.h"
#include "parser/parser.h"
#include "tcop/tcopprot.h"
#include "utils/builtins.h"
#include "utils/lsyscache.h"

/*
 * Query rewrite context for tracking table information
 */
typedef struct QueryRewriteContext
{
    List       *table_names;    /* List of table names in the query */
    List       *table_aliases;  /* List of table aliases and their actual names */
    const char *current_query;  /* Original query string */
} QueryRewriteContext;

/* Forward declarations */
static char *simple_replace(char *str, const char *old, const char *new);
static char *rewrite_query_with_table_prefixes(const char *original_query);
static void extract_table_info(SelectStmt *select_stmt, QueryRewriteContext *context);
static char *apply_simple_rewrites(const char *original_query, QueryRewriteContext *context);

/*
 * Rewrite a query to fully qualify column names
 */
PG_FUNCTION_INFO_V1(nr_rewrite_query_columns);

Datum
nr_rewrite_query_columns(PG_FUNCTION_ARGS)
{
    text    *query_text = PG_GETARG_TEXT_P(0);
    char    *query_str = text_to_cstring(query_text);
    char    *rewritten_query;
    text    *result_text;

    if (query_str == NULL)
        PG_RETURN_NULL();

    rewritten_query = rewrite_query_with_table_prefixes(query_str);

    result_text = cstring_to_text(rewritten_query);

    pfree(query_str);
    if (rewritten_query != query_str)
        pfree(rewritten_query);

    PG_RETURN_TEXT_P(result_text);
}

/*
 * Main query rewriting function
 */
char *
rewrite_query_with_table_prefixes(const char *original_query)
{
    List       *raw_parsetree_list;
    ListCell   *lc;
    bool        modified = false;
    char       *rewritten_query = (char *) original_query;

    /* Parse the query */
    raw_parsetree_list = raw_parser(original_query, RAW_PARSE_DEFAULT);

    /* Process each statement */
    foreach(lc, raw_parsetree_list)
    {
        RawStmt    *parsetree = lfirst_node(RawStmt, lc);

        if (parsetree->stmt && IsA(parsetree->stmt, SelectStmt))
        {
            SelectStmt *select_stmt = (SelectStmt *) parsetree->stmt;
            QueryRewriteContext context = {0};

            context.current_query = original_query;

            /* Extract table information */
            extract_table_info(select_stmt, &context);

            /* Rewrite the query if we have table information */
            if (context.table_names != NIL)
            {
                rewritten_query = apply_simple_rewrites(original_query, &context);
                if (rewritten_query != original_query)
                    modified = true;
            }

            break; /* Only handle first SELECT statement for now */
        }
    }

    return rewritten_query;
}

/*
 * Extract table information from a SELECT statement
 */
static void
extract_table_info(SelectStmt *select_stmt, QueryRewriteContext *context)
{
    ListCell   *lc;

    if (select_stmt->fromClause)
    {
        context->table_names = NIL;
        context->table_aliases = NIL;

        foreach(lc, select_stmt->fromClause)
        {
            Node *from_item = lfirst(lc);

            if (IsA(from_item, RangeVar))
            {
                RangeVar   *rv = (RangeVar *) from_item;
                char       *actual_table_name = rv->relname;
                char       *alias_name = NULL;

                /* Check if there's an alias */
                if (rv->alias)
                    alias_name = rv->alias->aliasname;
                else
                    alias_name = actual_table_name;

                /* Store table information */
                context->table_names = lappend(context->table_names, pstrdup(actual_table_name));

                /* Store alias mapping */
                context->table_aliases = lappend(context->table_aliases,
                                                psprintf("%s:%s", alias_name, actual_table_name));
            }
        }
    }
}

/*
 * Apply simple string-based rewrites for common patterns
 * This is a simplified implementation for demonstration
 */
static char *
apply_simple_rewrites(const char *original_query, QueryRewriteContext *context)
{
    char       *rewritten = pstrdup(original_query);
    ListCell   *lc;
    const char *table_name = NULL;

    /* Get the primary table name (first table for simplicity) */
    if (context->table_names != NIL)
        table_name = (const char *) linitial(context->table_names);

    if (table_name == NULL)
        return (char *) original_query;

    /* Common patterns for IMDb queries - simple string replacements */
    if (strcmp(table_name, "title") == 0)
    {
        /* Title table columns */
        rewritten = simple_replace(rewritten, " WHERE title_id", " WHERE title.title_id");
        rewritten = simple_replace(rewritten, " WHERE kind_id", " WHERE title.kind_id");
        rewritten = simple_replace(rewritten, " WHERE production_year", " WHERE title.production_year");
        rewritten = simple_replace(rewritten, " WHERE imdb_id", " WHERE title.imdb_id");
        rewritten = simple_replace(rewritten, " WHERE phonograph_code", " WHERE title.phonograph_code");
        rewritten = simple_replace(rewritten, " WHERE episode_of_id", " WHERE title.episode_of_id");
        rewritten = simple_replace(rewritten, " WHERE season_nr", " WHERE title.season_nr");
        rewritten = simple_replace(rewritten, " WHERE episode_nr", " WHERE title.episode_nr");

        /* ORDER BY clauses */
        rewritten = simple_replace(rewritten, "ORDER BY title_id", "ORDER BY title.title_id");
        rewritten = simple_replace(rewritten, "ORDER BY kind_id", "ORDER BY title.kind_id");
        rewritten = simple_replace(rewritten, "ORDER BY production_year", "ORDER BY title.production_year");
        rewritten = simple_replace(rewritten, "ORDER BY imdb_id", "ORDER BY title.imdb_id");
    }
    else if (strcmp(table_name, "cast_info") == 0)
    {
        /* Cast info table columns */
        rewritten = simple_replace(rewritten, " WHERE person_id", " WHERE cast_info.person_id");
        rewritten = simple_replace(rewritten, " WHERE movie_id", " WHERE cast_info.movie_id");
        rewritten = simple_replace(rewritten, " WHERE role_id", " WHERE cast_info.role_id");
        rewritten = simple_replace(rewritten, " WHERE note", " WHERE cast_info.note");
        rewritten = simple_replace(rewritten, " WHERE nr_order", " WHERE cast_info.nr_order");

        /* ORDER BY clauses */
        rewritten = simple_replace(rewritten, "ORDER BY person_id", "ORDER BY cast_info.person_id");
        rewritten = simple_replace(rewritten, "ORDER BY movie_id", "ORDER BY cast_info.movie_id");
        rewritten = simple_replace(rewritten, "ORDER BY role_id", "ORDER BY cast_info.role_id");
    }
    else if (strcmp(table_name, "movie_info") == 0)
    {
        /* Movie info table columns */
        rewritten = simple_replace(rewritten, " WHERE movie_id", " WHERE movie_info.movie_id");
        rewritten = simple_replace(rewritten, " WHERE info_type_id", " WHERE movie_info.info_type_id");
        rewritten = simple_replace(rewritten, " WHERE info", " WHERE movie_info.info");
        rewritten = simple_replace(rewritten, " WHERE note", " WHERE movie_info.note");

        /* ORDER BY clauses */
        rewritten = simple_replace(rewritten, "ORDER BY movie_id", "ORDER BY movie_info.movie_id");
        rewritten = simple_replace(rewritten, "ORDER BY info_type_id", "ORDER BY movie_info.info_type_id");
    }

    return rewritten;
}

/*
 * Simple string replacement function
 */
static char *
simple_replace(char *str, const char *old, const char *new)
{
    char       *result;
    char       *p;
    char       *q;
    int         old_len = strlen(old);
    int         new_len = strlen(new);
    int         count = 0;

    if (old_len == 0)
        return str;

    /* Count occurrences of old pattern */
    p = str;
    while ((q = strstr(p, old)) != NULL)
    {
        /* Only replace if it looks like a standalone column reference */
        if ((q == str || isspace((unsigned char) q[-1]) || q[-1] == '(') &&
            (q[old_len] == '\0' || isspace((unsigned char) q[old_len]) ||
             q[old_len] == ',' || q[old_len] == ')' || q[old_len] == '>' ||
             q[old_len] == '<' || q[old_len] == '='))
        {
            count++;
        }
        p = q + old_len;
    }

    if (count == 0)
        return str;

    /* Allocate new string */
    result = palloc(strlen(str) + count * (new_len - old_len) + 1);

    /* Replace patterns */
    p = str;
    char *r = result;
    while ((q = strstr(p, old)) != NULL)
    {
        int len = q - p;

        /* Check if this is a valid replacement */
        if ((q == str || isspace((unsigned char) q[-1]) || q[-1] == '(') &&
            (q[old_len] == '\0' || isspace((unsigned char) q[old_len]) ||
             q[old_len] == ',' || q[old_len] == ')' || q[old_len] == '>' ||
             q[old_len] == '<' || q[old_len] == '='))
        {
            memcpy(r, p, len);
            r += len;
            memcpy(r, new, new_len);
            r += new_len;
            p = q + old_len;
        }
        else
        {
            /* Not a valid match, skip it */
            if (len > 0)
            {
                memcpy(r, p, len + 1);
                r += len + 1;
            }
            p = q + 1;
        }
    }
    strcpy(r, p);

    return result;
}

/*
 * Test function to demonstrate query rewriting
 */
PG_FUNCTION_INFO_V1(nr_test_query_rewrite);

Datum
nr_test_query_rewrite(PG_FUNCTION_ARGS)
{
    text    *query_text = PG_GETARG_TEXT_P(0);
    char    *query_str = text_to_cstring(query_text);
    char    *rewritten_query;
    TupleDesc tupdesc;
    Datum    values[2];
    bool     nulls[2];
    HeapTuple tuple;

    if (query_str == NULL)
        PG_RETURN_NULL();

    /* Build tuple descriptor */
    tupdesc = CreateTemplateTupleDesc(2);
    TupleDescInitEntry(tupdesc, (AttrNumber) 1, "original", TEXTOID, -1, 0);
    TupleDescInitEntry(tupdesc, (AttrNumber) 2, "rewritten", TEXTOID, -1, 0);
    BlessTupleDesc(tupdesc);

    /* Rewrite the query */
    rewritten_query = rewrite_query_with_table_prefixes(query_str);

    /* Build tuple */
    values[0] = PointerGetDatum(cstring_to_text(query_str));
    values[1] = PointerGetDatum(cstring_to_text(rewritten_query));
    nulls[0] = false;
    nulls[1] = false;

    tuple = heap_form_tuple(tupdesc, values, nulls);

    pfree(query_str);
    if (rewritten_query != query_str)
        pfree(rewritten_query);

    PG_RETURN_DATUM(HeapTupleGetDatum(tuple));
}