-- setup
CREATE TEMP VIEW study_timing AS
SELECT * FROM timing
WHERE route_id='9011007000100000' AND category='Växjö city lines'
  AND day_type='weekday' AND direction_id=0
  AND schedule_pattern='2eb5a3d596c43db70b9679bfefa12200';

CREATE TEMP VIEW study_pairs AS
SELECT * FROM pairs
WHERE route_id='9011007000100000' AND category='Växjö city lines'
  AND day_type='weekday' AND direction_id=0
  AND schedule_pattern='2eb5a3d596c43db70b9679bfefa12200';

-- result: scope
SELECT count(*) scheduled_trips, count(DISTINCT service_date) scheduled_dates,
 min(start_seconds) earliest_start, max(start_seconds) latest_start
FROM journeys WHERE day_type='weekday' AND direction_id=0
 AND schedule_pattern='2eb5a3d596c43db70b9679bfefa12200';

-- result: route_population
SELECT count(*) FILTER(WHERE status IN ('observed','unobserved')) eligible,
 count(*) FILTER(WHERE status='observed') observed,
 count(*) FILTER(WHERE status='observed' AND delay_s BETWEEN -60 AND 180) on_time
FROM timing;

-- result: pattern_inventory
SELECT direction_id,schedule_pattern,count(*) scheduled_trips,
 count(DISTINCT service_date) service_dates
FROM journeys WHERE day_type='weekday'
GROUP BY ALL ORDER BY direction_id,scheduled_trips DESC;

-- result: section_summary
SELECT timing_index,stop_id,stop_name,next_stop_id,next_stop_name,stop_position,
 count(*) scheduled_pairs,
 count(*) FILTER(WHERE pair_eligible) eligible_pairs,
 count(*) FILTER(WHERE pair_observed) observed_pairs,
 count(DISTINCT (service_date,trip_id)) FILTER(WHERE pair_observed) observed_trips,
 round(100*observed_pairs::DOUBLE/nullif(eligible_pairs,0),2) coverage_pct,
 median(delta) FILTER(WHERE pair_observed) median_change,
 quantile_cont(delta,.1) FILTER(WHERE pair_observed) p10_change,
 quantile_cont(delta,.9) FILTER(WHERE pair_observed) p90_change,
 count(*) FILTER(WHERE pair_observed AND delta>0) gained_delay,
 count(*) FILTER(WHERE pair_observed AND delta<0) lost_delay,
 count(*) FILTER(WHERE pair_observed AND delay_s BETWEEN -60 AND 180 AND next_delay>180) ontime_to_late,
 count(*) FILTER(WHERE pair_observed AND delay_s>180 AND next_delay BETWEEN -60 AND 180) late_to_ontime
FROM study_pairs GROUP BY ALL ORDER BY timing_index;

-- result: recurring_section
SELECT start_seconds, count(*) scheduled_pairs,
 count(*) FILTER(WHERE pair_eligible) eligible_pairs,
 count(*) FILTER(WHERE pair_observed) observed_pairs,
 count(DISTINCT (service_date,trip_id)) FILTER(WHERE pair_observed) observed_trips,
 count(DISTINCT service_date) FILTER(WHERE pair_observed) observed_dates,
 round(100*observed_pairs::DOUBLE/nullif(eligible_pairs,0),2) coverage_pct,
 median(delta) FILTER(WHERE pair_observed) median_change,
 quantile_cont(delta,.1) FILTER(WHERE pair_observed) p10_change,
 quantile_cont(delta,.9) FILTER(WHERE pair_observed) p90_change,
 count(*) FILTER(WHERE pair_observed AND delta>0) gained_delay,
 count(*) FILTER(WHERE pair_observed AND delta<0) lost_delay,
 count(*) FILTER(WHERE pair_observed AND delta=0) unchanged,
 count(*) FILTER(WHERE pair_observed AND delay_s BETWEEN -60 AND 180) upstream_ontime,
 count(*) FILTER(WHERE pair_observed AND delay_s>180) upstream_late,
 count(*) FILTER(WHERE pair_observed AND next_delay BETWEEN -60 AND 180) downstream_ontime,
 count(*) FILTER(WHERE pair_observed AND next_delay>180) downstream_late,
 count(*) FILTER(WHERE pair_observed AND delay_s BETWEEN -60 AND 180 AND next_delay>180) ontime_to_late,
 count(*) FILTER(WHERE pair_observed AND delay_s>180 AND next_delay BETWEEN -60 AND 180) late_to_ontime,
 observed_trips>=20 AND coverage_pct>=90 reportable
FROM study_pairs WHERE stop_id='9022007080001003'
GROUP BY start_seconds ORDER BY start_seconds;

-- result: section_hour
SELECT floor(start_seconds/3600)::INT journey_start_hour,
 count(*) FILTER(WHERE pair_eligible) eligible_pairs,
 count(*) FILTER(WHERE pair_observed) observed_pairs,
 round(100*observed_pairs::DOUBLE/nullif(eligible_pairs,0),2) coverage_pct,
 median(delta) FILTER(WHERE pair_observed) median_change,
 count(*) FILTER(WHERE pair_observed AND delta>0) gained_delay,
 count(*) FILTER(WHERE pair_observed AND delta<0) lost_delay,
 count(*) FILTER(WHERE pair_observed AND delay_s BETWEEN -60 AND 180 AND next_delay>180) ontime_to_late
FROM study_pairs WHERE stop_id='9022007080001003'
GROUP BY 1 ORDER BY 1;

-- result: section_dates
SELECT service_date,count(*) FILTER(WHERE pair_eligible) eligible_pairs,
 count(*) FILTER(WHERE pair_observed) observed_pairs,
 round(100*observed_pairs::DOUBLE/nullif(eligible_pairs,0),2) coverage_pct,
 median(delta) FILTER(WHERE pair_observed) median_change,
 count(*) FILTER(WHERE pair_observed AND delta>0) gained_delay
FROM study_pairs WHERE stop_id='9022007080001003'
GROUP BY 1 ORDER BY 1;

-- result: individual_pairs
SELECT service_date,trip_id,start_seconds,stop_name,next_stop_name,status,next_status,
 pair_eligible,pair_observed,delay_s,next_delay,delta
FROM study_pairs WHERE stop_id='9022007080001003'
ORDER BY start_seconds,service_date;

-- result: individual_journeys
SELECT service_date,trip_id,start_seconds,timing_index,stop_id,stop_name,status,delay_s,
 date_diff('second',scheduled_departure_utc,held_departure_utc) timestamp_delay_s
FROM study_timing ORDER BY start_seconds,service_date,timing_index;

-- result: afternoon_sections
SELECT timing_index,stop_name,next_stop_name,
 count(*) FILTER(WHERE pair_eligible) eligible_pairs,
 count(*) FILTER(WHERE pair_observed) observed_pairs,
 round(100*observed_pairs::DOUBLE/nullif(eligible_pairs,0),2) coverage_pct,
 median(delta) FILTER(WHERE pair_observed) median_change,
 count(*) FILTER(WHERE pair_observed AND delta>0) gained_delay,
 count(*) FILTER(WHERE pair_observed AND delta<0) lost_delay,
 count(*) FILTER(WHERE pair_observed AND delay_s BETWEEN -60 AND 180 AND next_delay>180) ontime_to_late,
 count(*) FILTER(WHERE pair_observed AND delay_s>180 AND next_delay BETWEEN -60 AND 180) late_to_ontime
FROM study_pairs WHERE start_seconds BETWEEN 15*3600 AND 18*3600-1
GROUP BY ALL ORDER BY timing_index;

-- result: linked_recovery
SELECT CASE WHEN a.start_seconds BETWEEN 15*3600 AND 18*3600-1
 THEN '15:00–17:59' ELSE 'Other start times' END start_period,
 count(*) observed_three_stop_trips,
 count(*) FILTER(WHERE a.delta<0 AND b.delta>0) recovered_then_gained,
 count(*) FILTER(WHERE a.next_delay BETWEEN -60 AND 180 AND b.next_delay>180) ontime_at_stortorget_late_at_dalbo
FROM study_pairs a JOIN study_pairs b USING(service_date,trip_id)
WHERE a.timing_index=2 AND b.timing_index=3 AND a.pair_observed AND b.pair_observed
GROUP BY 1 ORDER BY 1;

-- result: selected_departure_stops
SELECT start_seconds,timing_index,stop_name,
 count(*) FILTER(WHERE status IN ('observed','unobserved')) eligible,
 count(*) FILTER(WHERE status='observed') observed,
 count(DISTINCT (service_date,trip_id)) FILTER(WHERE status='observed') observed_trips,
 round(100*observed::DOUBLE/nullif(eligible,0),2) coverage_pct,
 count(*) FILTER(WHERE status='observed' AND delay_s BETWEEN -60 AND 180) on_time,
 median(delay_s) FILTER(WHERE status='observed') median_delay,
 observed_trips>=20 AND coverage_pct>=90 reportable
FROM study_timing WHERE start_seconds IN (55380,84780)
GROUP BY ALL ORDER BY start_seconds,timing_index;

-- result: leave_one_date_out
SELECT dates.service_date omitted_date,median(p.delta) median_change,
 count(*) observed_pairs
FROM (SELECT DISTINCT service_date FROM study_pairs) dates
CROSS JOIN study_pairs p
WHERE p.stop_id='9022007080001003' AND p.pair_observed
 AND p.service_date<>dates.service_date
GROUP BY 1 ORDER BY 1;
