/*
 * http_client.c
 *      HTTP client for communicating with AI engine
 */

#include "postgres.h"
#include "fmgr.h"
#include "miscadmin.h"
#include "utils/memutils.h"
#include "utils/ps_status.h"
#include "utils/wait_event.h"
#include "storage/proc.h"
#include "pgstat.h"
#include "nr_workload_forecast.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAX_RESPONSE_SIZE (1024 * 1024)  /* 1MB max response */

/* Local function declarations */
static char *escape_json_string(const char *str);
static size_t strip_html_tags(char *buffer, size_t len);

/*
 * Send HTTP POST request using curl
 */
char *
send_http_request(const char *url, const char *json_payload)
{
    StringInfoData command;
    StringInfoData response;
    FILE *curl_output = NULL;
    char *escaped_payload = NULL;
    char line[4096];
    int ret;

    if (!url || !json_payload)
        return NULL;

    /* Escape JSON payload for shell */
    escaped_payload = escape_json_string(json_payload);
    if (!escaped_payload)
        return NULL;

    /* Build curl command */
    initStringInfo(&command);
    appendStringInfo(&command,
        "curl -s -X POST "
        "-H 'Content-Type: application/json' "
        "-H 'Connection: keep-alive' "
        "-H 'User-Agent: nr_workload_forecast/1.0' "
        "-d '%s' "
        "--connect-timeout 5 "
        "--max-time 30 "
        "'%s' ",
        escaped_payload, url);

    ereport(DEBUG2,
            (errmsg("Sending HTTP request to %s", url)));

    /* Set process title for monitoring */
    set_ps_display("workload forecast: sending request");

    /* Execute curl command */
    curl_output = popen(command.data, "r");
    if (!curl_output)
    {
        ereport(WARNING,
                (errmsg("Failed to execute curl command: %m")));
        goto cleanup;
    }

    /* Read response */
    initStringInfo(&response);
    while (fgets(line, sizeof(line), curl_output) != NULL)
    {
        appendStringInfoString(&response, line);

        /* Prevent oversized responses */
        if (response.len > MAX_RESPONSE_SIZE)
        {
            ereport(WARNING,
                    (errmsg("HTTP response too large (max %d bytes)",
                            MAX_RESPONSE_SIZE)));
            goto cleanup;
        }
    }

    /* Close pipe and get return code */
    ret = pclose(curl_output);
    curl_output = NULL;

    if (ret == -1)
    {
        ereport(WARNING,
                (errmsg("Failed to close curl pipe: %m")));
        goto cleanup;
    }

    /* Check curl exit status */
    if (WIFEXITED(ret))
    {
        int exit_code = WEXITSTATUS(ret);
        if (exit_code != 0)
        {
            ereport(WARNING,
                    (errmsg("curl failed with exit code %d", exit_code)));
            goto cleanup;
        }
    }
    else if (WIFSIGNALED(ret))
    {
        ereport(WARNING,
                (errmsg("curl terminated by signal %d", WTERMSIG(ret))));
        goto cleanup;
    }

    /* Check if response is empty */
    if (response.len == 0)
    {
        ereport(WARNING,
                (errmsg("Empty response from AI engine")));
        goto cleanup;
    }

    /* Remove HTML tags if present */
    strip_html_tags(response.data, response.len);

    ereport(DEBUG1,
            (errmsg("Received HTTP response (%d bytes)", (int)response.len)));

    pfree(command.data);
    pfree(escaped_payload);

    return response.data;

cleanup:
    if (curl_output)
        pclose(curl_output);
    if (command.data)
        pfree(command.data);
    if (escaped_payload)
        pfree(escaped_payload);
    if (response.data)
        pfree(response.data);

    return NULL;
}

/*
 * Escape string for shell usage
 */
static char *
escape_json_string(const char *str)
{
    StringInfoData escaped;
    int i;

    if (!str)
        return NULL;

    initStringInfo(&escaped);

    for (i = 0; str[i] != '\0'; i++)
    {
        char ch = str[i];

        switch (ch)
        {
            case '"':
            case '\\':
            case '$':
            case '`':
                appendStringInfoChar(&escaped, '\\');
                appendStringInfoChar(&escaped, ch);
                break;
            case '\n':
                appendStringInfoString(&escaped, "\\n");
                break;
            case '\r':
                appendStringInfoString(&escaped, "\\r");
                break;
            case '\t':
                appendStringInfoString(&escaped, "\\t");
                break;
            default:
                appendStringInfoChar(&escaped, ch);
                break;
        }
    }

    return escaped.data;
}

/*
 * Strip HTML tags if response is HTML instead of JSON
 */
static size_t
strip_html_tags(char *buffer, size_t len)
{
    bool in_tag = false;
    size_t src, dst;

    if (!buffer || len == 0)
        return 0;

    for (src = 0, dst = 0; src < len; src++)
    {
        if (buffer[src] == '<')
        {
            in_tag = true;
            continue;
        }

        if (buffer[src] == '>')
        {
            in_tag = false;
            continue;
        }

        if (!in_tag)
        {
            buffer[dst++] = buffer[src];
        }
    }

    buffer[dst] = '\0';
    return dst;
}
