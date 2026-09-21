-- Load staged TripUpdates snapshots (D's own hours plus any D+1 hours read)
-- and deduplicate by feed header timestamp: ~15-17% of archive files are
-- duplicate snapshots (same header_timestamp, different file). Keep one
-- canonical file per timestamp.
--
-- Params: $glob_list (a DuckDB list literal of parquet glob paths)

CREATE OR REPLACE TABLE raw_rows AS SELECT * FROM read_parquet($glob_list);

CREATE OR REPLACE TABLE all_snapshot_files AS
SELECT DISTINCT snapshot_file, header_timestamp FROM raw_rows;

CREATE OR REPLACE TABLE canonical_file AS
SELECT header_timestamp, MIN(snapshot_file) AS snapshot_file
FROM all_snapshot_files
GROUP BY header_timestamp;

CREATE OR REPLACE TABLE rows_dedup AS
SELECT r.*
FROM raw_rows r
JOIN canonical_file c
  ON r.header_timestamp = c.header_timestamp AND r.snapshot_file = c.snapshot_file;
