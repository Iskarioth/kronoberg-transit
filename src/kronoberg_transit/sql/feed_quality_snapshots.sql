-- Snapshot-level statistics over D's own local-hour archives only (not the
-- D+1 hours read to catch late-running trips per D-011). Scalar aggregates
-- (first/last snapshot, max gap, hours without snapshots) are computed in
-- Python from these tables.
-- Params: $own_glob (a DuckDB list literal of parquet glob paths for D's own hours)

CREATE OR REPLACE TABLE raw_rows_own AS SELECT * FROM read_parquet($own_glob);

CREATE OR REPLACE TABLE all_snapshot_files_own AS
SELECT DISTINCT snapshot_file, header_timestamp, hour FROM raw_rows_own;

CREATE OR REPLACE TABLE snapshots_own AS
SELECT header_timestamp, MIN(hour) AS hour
FROM all_snapshot_files_own
GROUP BY header_timestamp
ORDER BY header_timestamp;

CREATE OR REPLACE TABLE snapshot_gaps_own AS
SELECT header_timestamp,
       header_timestamp - LAG(header_timestamp) OVER (ORDER BY header_timestamp) AS gap_s
FROM snapshots_own;
