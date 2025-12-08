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

/* GUC hook function declarations */
extern bool check_nr_enable_auto_index_creation(bool *newval, void **extra, GucSource source);
extern void assign_nr_enable_auto_index_creation(bool newval, void *extra);

#endif
