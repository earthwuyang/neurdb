/*
 * query_logger.c
 *      Query template extraction and workload logging
 */

#include "postgres.h"
#include "fmgr.h"
#include "miscadmin.h"
#include "utils/timestamp.h"
#include "utils/memutils.h"
#include "utils/hsearch.h"
#include "storage/fd.h"
#include "utils/wait_event.h"
#include "nodes/parsenodes.h"
#include "nodes/primnodes.h"
#include "parser/parser.h"
#include <ctype.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#include <openssl/md5.h>

#include "nr_workload_forecast.h"

#define MAX_QUERY_LENGTH 65536
#define TEMPLATE_HASH_LENGTH 33  /* 32 chars + null terminator */

static File workload_file = -1;
static int64 bytes_written = 0;
static bool file_opened = false;  /* Track if we successfully opened a file */

/* Local function declarations */
static void extract_constant_tokens(const char *query, StringInfo template);

/*
 * Extract template from query by normalizing literals
 */
static void
extract_constant_tokens(const char *query, StringInfo template)
{
    int i = 0;
    bool in_string = false;
    bool in_number = false;
    bool in_identifier = false;
    bool escaped = false;
    char string_delim = '\0';

    while (query[i] != '\0' && i < MAX_QUERY_LENGTH)
    {
        char ch = query[i];

        /* Handle escaped characters in strings */
        if (escaped)
        {
            appendStringInfoChar(template, ch);
            escaped = false;
            i++;
            continue;
        }

        /* Check for escape character */
        if (ch == '\\')
        {
            appendStringInfoChar(template, ch);
            escaped = true;
            i++;
            continue;
        }

        /* Handle quoted identifiers */
        if ((ch == '"' || ch == '`') && !in_string)
        {
            /* Toggle identifier quoting */
            appendStringInfoChar(template, ch);
            in_identifier = !in_identifier;
            i++;
            continue;
        }

        /* If in identifier, just copy */
        if (in_identifier)
        {
            appendStringInfoChar(template, ch);
            i++;
            continue;
        }

        /* Handle string literals (single or double quotes) */
        if ((ch == '\'' || ch == '"') && !in_string && !in_number)
        {
            in_string = true;
            string_delim = ch;
            appendStringInfoChar(template, ch);
            i++;

            /* Replace string content with placeholders */
            int placeholder_count = 0;
            while (query[i] != '\0' && i < MAX_QUERY_LENGTH)
            {
                if (query[i] == '\\')
                {
                    /* Skip escaped character */
                    i += 2;
                    continue;
                }
                if (query[i] == string_delim)
                {
                    /* Check if it's an escaped quote */
                    if (query[i + 1] == string_delim)
                    {
                        i += 2;
                        continue;
                    }
                    /* End of string */
                    break;
                }
                i++;
                placeholder_count++;
            }

            /* Add &&& for string content (like QueryBot5000) */
            appendStringInfoString(template, "&&&");
            if (query[i] == string_delim)
            {
                appendStringInfoChar(template, string_delim);
                i++;
            }
            in_string = false;
            continue;
        }

        /* Handle numbers (integers and decimals) */
        if (isdigit(ch) && !in_string)
        {
            in_number = true;
            while (isdigit(query[i]) || query[i] == '.')
            {
                i++;
            }
            appendStringInfoChar(template, '#');
            in_number = false;
            continue;
        }

        /* Handle hex/binary literals (0x..., x'...', b'...') */
        if ((ch == '0' && query[i+1] == 'x') ||
            (ch == 'x' && query[i+1] == '\'') ||
            (ch == 'b' && query[i+1] == '\''))
        {
            /* Skip to end of hex/binary literal */
            if (query[i+1] == 'x' || query[i+1] == 'b')
            {
                appendStringInfoChar(template, ch);
                i++;
                ch = query[i];
                appendStringInfoChar(template, ch);
                i++;

                /* Skip content until space or quote */
                while (query[i] != '\0' && !isspace(query[i]) && query[i] != '\'')
                {
                    i++;
                }
                appendStringInfoString(template, "@@@");
                continue;
            }
        }

        /* Copy character as-is */
        appendStringInfoChar(template, ch);
        i++;
    }
}

/*
 * Normalize query by extracting template and removing extra whitespace
 */
char *
normalize_query(const char *query_text)
{
    StringInfoData template;
    char *normalized;
    int i, j;
    bool in_space = false;

    if (!query_text)
        return NULL;

    initStringInfo(&template);

    /* Extract template with normalized literals */
    extract_constant_tokens(query_text, &template);

    /* Remove extra whitespace and normalize */
    normalized = palloc(template.len + 1);
    j = 0;
    in_space = false;

    for (i = 0; i < template.len; i++)
    {
        char ch = template.data[i];

        if (isspace(ch))
        {
            if (!in_space && j > 0)
            {
                normalized[j++] = ' ';
                in_space = true;
            }
        }
        else
        {
            normalized[j++] = ch;
            in_space = false;
        }
    }

    /* Null-terminate */
    if (j > 0 && normalized[j-1] == ' ')
        j--;
    normalized[j] = '\0';

    pfree(template.data);

    return normalized;
}

/*
 * Compute MD5 hash of template text
 */
char *
compute_template_hash(const char *template_text)
{
    unsigned char digest[MD5_DIGEST_LENGTH];
    char *hash_hex;
    int i;

    if (!template_text)
        return NULL;

    /* Compute MD5 */
    MD5((unsigned char *)template_text, strlen(template_text), digest);

    /* Convert to hex string */
    hash_hex = palloc(TEMPLATE_HASH_LENGTH);
    for (i = 0; i < MD5_DIGEST_LENGTH; i++)
    {
        sprintf(hash_hex + (i * 2), "%02x", digest[i]);
    }
    hash_hex[TEMPLATE_HASH_LENGTH - 1] = '\0';

    return hash_hex;
}

/*
 * Check if query is a SELECT statement
 */
bool
is_select_query(const char *query_text)
{
    const char *p = query_text;

    /* Skip leading whitespace */
    while (*p && isspace(*p))
        p++;

    /* Check for SELECT (case-insensitive) */
    if (pg_strncasecmp(p, "SELECT", 6) == 0)
        return true;

    /* Check for WITH...SELECT (CTE) */
    if (pg_strncasecmp(p, "WITH", 4) == 0)
    {
        /* Look for SELECT after WITH clause */
        p += 4;
        while (*p && isspace(*p))
            p++;

        /* Skip CTE name and AS */
        while (*p && isalnum(*p))
            p++;
        while (*p && isspace(*p))
            p++;

        if (pg_strncasecmp(p, "AS", 2) == 0)
        {
            p += 2;
            while (*p && isspace(*p))
                p++;

            /* Look for SELECT after opening parenthesis */
            if (*p == '(')
            {
                p++;
                while (*p && isspace(*p))
                    p++;

                if (pg_strncasecmp(p, "SELECT", 6) == 0)
                    return true;
            }
        }
    }

    return false;
}

/*
 * Log query to workload file using POSIX file I/O for better compatibility
 */
void
query_logger_log_query(const char *query_text, TimestampTz timestamp)
{
    char *template_text;
    char *template_hash;
    FILE *fp;
    struct tm *tm_info;
    char time_str[128];
    time_t secs;

    if (!query_text || !is_select_query(query_text))
        return;

    /* Extract template */
    template_text = normalize_query(query_text);
    if (!template_text)
        return;

    /* Compute hash */
    template_hash = compute_template_hash(template_text);

    /* Convert timestamp to string */
    secs = (time_t) (timestamp / 1000000 + ((POSTGRES_EPOCH_JDATE - UNIX_EPOCH_JDATE) * SECS_PER_DAY));
    tm_info = localtime(&secs);
    if (tm_info)
    {
        strftime(time_str, sizeof(time_str), "%Y-%m-%d %H:%M:%S", tm_info);
    }
    else
    {
        /* Error fallback */
        strcpy(time_str, "1970-01-01 00:00:00");
    }

    /* Open file in append mode */
    fp = fopen(workload_forecast_config.log_file, "a");
    if (!fp)
    {
        ereport(WARNING,
                (errmsg("Could not open workload log file '%s': %m",
                        workload_forecast_config.log_file)));
        pfree(template_text);
        pfree(template_hash);
        return;
    }

    ereport(DEBUG2,
            (errmsg("Successfully opened workload log file '%s' with fopen", workload_forecast_config.log_file)));

    /* Check if file is empty and write header if needed */
    fseek(fp, 0, SEEK_END);
    long file_size = ftell(fp);
    if (file_size == 0)
    {
        ereport(DEBUG1,
                (errmsg("Writing CSV header to new workload log file")));

        if (fprintf(fp, "timestamp,template_hash,template,original_query\n") < 0)
        {
            ereport(WARNING,
                    (errmsg("Failed to write CSV header to workload log file '%s': %m",
                            workload_forecast_config.log_file)));
            fclose(fp);
            pfree(template_text);
            pfree(template_hash);
            return;
        }

        ereport(DEBUG1,
                (errmsg("Successfully wrote CSV header to workload log file")));
    }

    /* Write CSV line */
    int result = fprintf(fp, "%s,%s,\"", time_str, template_hash);

    /* Escape and write template */
    for (int i = 0; template_text[i] != '\0'; i++)
    {
        if (template_text[i] == '"')
            result += fprintf(fp, "\"");  /* Double the quote */
        result += fprintf(fp, "%c", template_text[i]);
    }

    result += fprintf(fp, "\",\"");

    /* Escape and write original query */
    for (int i = 0; query_text[i] != '\0'; i++)
    {
        if (query_text[i] == '"')
            result += fprintf(fp, "\"");  /* Double the quote */
        result += fprintf(fp, "%c", query_text[i]);
    }

    result += fprintf(fp, "\"\n");

    if (result < 0)
    {
        ereport(WARNING,
                (errmsg("Failed to write to workload log file '%s': %m",
                        workload_forecast_config.log_file)));
        fclose(fp);
        pfree(template_text);
        pfree(template_hash);
        return;
    }

    /* Flush and close file */
    if (fflush(fp) != 0)
    {
        ereport(WARNING,
                (errmsg("Failed to flush workload log file '%s': %m",
                        workload_forecast_config.log_file)));
        fclose(fp);
        pfree(template_text);
        pfree(template_hash);
        return;
    }

    fclose(fp);

    ereport(DEBUG2,
            (errmsg("Successfully wrote query to workload log file (%d bytes)", result)));

    /* Cleanup */
    pfree(template_text);
    pfree(template_hash);
}

/*
 * Ensure log directory exists
 */
bool
ensure_log_directory(const char *log_file_path)
{
    char *dir_path;
    char *last_slash;
    struct stat st;

    if (!log_file_path)
        return false;

    /* Find directory path */
    dir_path = pstrdup(log_file_path);
    last_slash = strrchr(dir_path, '/');

    if (!last_slash)
    {
        pfree(dir_path);
        return true;  /* Current directory */
    }

    *last_slash = '\0';

    /* Check if directory exists */
    if (stat(dir_path, &st) == 0)
    {
        pfree(dir_path);
        return S_ISDIR(st.st_mode);
    }

    /* Create directory */
    if (mkdir(dir_path, S_IRWXU | S_IRGRP | S_IXGRP | S_IROTH | S_IXOTH) != 0)
    {
        ereport(WARNING,
                (errmsg("Failed to create log directory '%s': %m",
                        dir_path)));
        pfree(dir_path);
        return false;
    }

    pfree(dir_path);
    return true;
}

/*
 * Cleanup function to close workload log file
 */
void
workload_log_cleanup(void)
{
    ereport(DEBUG1,
            (errmsg("workload_log_cleanup called, workload_file=%d", workload_file)));

    /* Only try to close if file is actually open */
    if (workload_file >= 0 && workload_file < 1024)  /* Reasonable FD range check */
    {
        ereport(DEBUG1,
                (errmsg("closing workload file")));
        FileClose(workload_file);
        workload_file = -1;
        bytes_written = 0;
    }
    else
    {
        ereport(DEBUG1,
                (errmsg("workload_file not open, skipping close")));
        workload_file = -1;
        bytes_written = 0;
    }
}
