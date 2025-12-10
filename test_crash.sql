-- SQL script to trigger HypoPG crash on large table
\c imdb_test neurdb

-- First, enable HypoPG extension
CREATE EXTENSION IF NOT EXISTS hypopg;

-- Step 1: Create hypothetical index (this should work)
SELECT hypopg_create_index('CREATE INDEX idx_crash_test ON cast_info(person_id)');

-- Step 2: Run EXPLAIN (this should crash the server)
EXPLAIN (FORMAT JSON) SELECT * FROM cast_info WHERE person_id = 12345;