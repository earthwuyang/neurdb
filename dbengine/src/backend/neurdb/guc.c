#include "postgres.h"

#include "neurdb/guc.h"
#include "utils/builtins.h"
#include "libpq/pqformat.h"
#include "miscadmin.h"

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

/* Function declarations for GUC hooks */
static bool check_nr_enable_auto_index_creation(bool *newval, void **extra, GucSource source);
static void assign_nr_enable_auto_index_creation(bool newval, void *extra);
static void notify_ai_engine_guc_change(bool enabled);

/* Function definitions for GUC hooks */
static bool
check_nr_enable_auto_index_creation(bool *newval, void **extra, GucSource source)
{
    /* Always allow changes */
    return true;
}

static void
assign_nr_enable_auto_index_creation(bool newval, void *extra)
{
    /* Store the old value to detect changes */
    static bool old_value = false;

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
