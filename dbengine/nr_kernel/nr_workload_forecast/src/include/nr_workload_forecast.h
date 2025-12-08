#ifndef NR_WORKLOAD_FORECAST_H
#define NR_WORKLOAD_FORECAST_H

#include "postgres.h"
#include "fmgr.h"
#include "utils/timestamp.h"
#include "tcop/utility.h"

/* Forward declarations for parser structures */
typedef struct ParseState ParseState;
typedef struct Query Query;
typedef struct JumbleState JumbleState;

/* GUC Variables */
typedef struct WorkloadForecastConfig
{
    bool        enable;
    char       *server_url;
    char       *log_file;
    int         analysis_interval;
    bool        auto_apply_indexes;
    int         log_rotation_size;
} WorkloadForecastConfig;

extern WorkloadForecastConfig workload_forecast_config;

/* Function prototypes */
extern void _PG_init(void);
extern void _PG_fini(void);

/* Hook functions */
extern void workload_forecast_post_parse_analyze(ParseState *pstate, Query *query, JumbleState *jstate);

/* Query logging functions */
extern void query_logger_log_query(const char *query_text, TimestampTz timestamp);
extern char *extract_query_template(const char *query_text);

/* Workload storage functions */
extern void workload_store_append(const char *template_hash, const char *template_text,
                                 TimestampTz timestamp, const char *original_query);
extern bool ensure_log_directory(const char *log_file_path);
extern void workload_log_cleanup(void);

/* HTTP client functions */
extern char *send_http_request(const char *url, const char *json_payload);

/* Template extraction functions */
extern char *normalize_query(const char *query_text);
extern char *compute_template_hash(const char *template_text);

/* Utility functions */
extern bool is_select_query(const char *query_text);

#endif /* NR_WORKLOAD_FORECAST_H */
