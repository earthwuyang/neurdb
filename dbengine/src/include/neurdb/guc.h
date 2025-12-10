#ifndef NR_GUC_H
#define NR_GUC_H

#include "postgres.h"
#include "utils/guc.h"

/*
 * GUC variable for current configuration
 */
extern PGDLLIMPORT char *NrModelName;
extern PGDLLIMPORT int NrTaskBatchSize;
extern PGDLLIMPORT int NrTaskEpoch;
extern PGDLLIMPORT int NrTaskMaxFeatures;
extern PGDLLIMPORT int NrTaskNumBatches;

/*
 * Index management configuration
 */
extern PGDLLIMPORT double nr_max_index_storage_mb;
extern PGDLLIMPORT bool nr_enable_auto_index_creation;
extern PGDLLIMPORT char *nr_index_management_strategy; /* "predictive" or "reactive" */

/* GUC hook function declarations */
extern bool check_nr_enable_auto_index_creation(bool *newval, void **extra, GucSource source);
extern void assign_nr_enable_auto_index_creation(bool newval, void *extra);
extern bool check_nr_index_management_strategy(char **newval, void **extra, GucSource source);
extern void assign_nr_index_management_strategy(char *newval, void *extra);

/* Query rewriting for AI engine integration */
extern void send_reactive_query_to_ai_engine(const char *original_query);
extern char *rewrite_query_for_ai_engine(const char *query);

#endif
