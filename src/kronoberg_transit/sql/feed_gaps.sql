-- Feed outages (D-016) over D's full snapshot timeline: D's own hours plus
-- any D+1 hours read (D-011), deduplicated by header timestamp. Reuses
-- canonical_file from realtime_dedup.sql, so that must have already run. A
-- gap is a stretch of more than 300 s with no snapshot: 'between' two
-- consecutive snapshots, 'before_first' (from the window start, D's local
-- midnight, to the first snapshot) or 'after_last' (from the last snapshot
-- to the window end, the end of the last hour read).
--
-- Params: $svc_date, $feed, $window_start_utc, $window_end_utc (BIGINT unix seconds)

CREATE OR REPLACE TABLE feed_gap_bounds AS
SELECT MIN(header_timestamp) AS first_ts, MAX(header_timestamp) AS last_ts
FROM canonical_file;

CREATE OR REPLACE TABLE feed_gaps AS
WITH between_gaps AS (
    SELECT
        LAG(header_timestamp) OVER (ORDER BY header_timestamp) AS gap_start_utc,
        header_timestamp AS gap_end_utc,
        header_timestamp - LAG(header_timestamp) OVER (ORDER BY header_timestamp) AS gap_s,
        'between' AS kind
    FROM canonical_file
),
before_first AS (
    SELECT $window_start_utc AS gap_start_utc, first_ts AS gap_end_utc,
           first_ts - $window_start_utc AS gap_s, 'before_first' AS kind
    FROM feed_gap_bounds
    WHERE first_ts IS NOT NULL
),
after_last AS (
    SELECT last_ts AS gap_start_utc, $window_end_utc AS gap_end_utc,
           $window_end_utc - last_ts AS gap_s, 'after_last' AS kind
    FROM feed_gap_bounds
    WHERE last_ts IS NOT NULL
),
unioned AS (
    SELECT * FROM between_gaps
    UNION ALL SELECT * FROM before_first
    UNION ALL SELECT * FROM after_last
)
SELECT
    DATE '$svc_date' AS service_date,
    '$feed' AS feed,
    to_timestamp(gap_start_utc)::TIMESTAMP AS gap_start_utc,
    to_timestamp(gap_end_utc)::TIMESTAMP AS gap_end_utc,
    gap_s,
    kind
FROM unioned
WHERE gap_start_utc IS NOT NULL AND gap_s > 300
ORDER BY gap_start_utc;
