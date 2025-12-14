/* nr_index_management extension upgrade: 1.1 -> 1.2 */

-- Work queue table for background reactive worker (Phase 5)
CREATE TABLE IF NOT EXISTS nrim.nrim_work_queue (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'pending',   -- pending|processing|done|error
  query_text text NOT NULL,
  candidates text,
  last_error text
);

CREATE INDEX IF NOT EXISTS nrim_work_queue_status_created_at_idx
ON nrim.nrim_work_queue(status, created_at);

