-- setup

CREATE TEMP TABLE patterns AS
SELECT service_date,trip_id,
 md5(string_agg(stop_id || ':' || is_timing_stop::VARCHAR, '|' ORDER BY stop_sequence)) pattern,
 md5(string_agg(stop_id || ':' || is_timing_stop::VARCHAR || ':' ||
 date_diff('second',t.scheduled_first_departure_utc,s.scheduled_departure_utc)::VARCHAR,
 '|' ORDER BY stop_sequence)) schedule_pattern
FROM stop_events s JOIN trips t USING(service_date,trip_id)
WHERE t.in_scope GROUP BY 1,2;
CREATE TEMP TABLE journeys AS
SELECT t.*,p.pattern,p.schedule_pattern,r.route_short_name,
 date_diff('second',t.service_date::TIMESTAMP,
 timezone('Europe/Stockholm',timezone('UTC',t.scheduled_first_departure_utc))) start_seconds,
 CASE WHEN extract(isodow FROM t.service_date)<=5 THEN 'weekday'
 WHEN extract(isodow FROM t.service_date)=6 THEN 'saturday' ELSE 'sunday' END day_type
FROM trips t JOIN patterns p USING(service_date,trip_id)
JOIN routes r USING(service_date,route_id) WHERE t.in_scope;
CREATE TEMP TABLE timing AS
SELECT s.*,j.direction_id,j.route_short_name,j.pattern,j.schedule_pattern,j.start_seconds,j.day_type,
 st.stop_name,
 row_number() OVER(PARTITION BY s.service_date,s.trip_id ORDER BY s.stop_sequence) timing_index,
 lead(s.stop_id) OVER w next_stop_id,
 lead(st.stop_name) OVER w next_stop_name,
 lead(s.stop_position) OVER w next_position,
 lead(s.status) OVER w next_status,
 lead(s.delay_s) OVER w next_delay
FROM stop_events s JOIN journeys j USING(service_date,trip_id,route_id)
JOIN stops st USING(service_date,stop_id)
WHERE s.in_scope AND s.is_timing_stop AND s.stop_position<>'final'
WINDOW w AS(PARTITION BY s.service_date,s.trip_id ORDER BY s.stop_sequence);
CREATE TEMP TABLE recurring AS
SELECT route_id,route_short_name,direction_id,pattern,schedule_pattern,start_seconds,day_type,
 count(*) FILTER(WHERE status IN ('observed','unobserved')) eligible,
 count(*) FILTER(WHERE status='observed') observed,
 count(DISTINCT (service_date,trip_id)) FILTER(WHERE status='observed') observed_trips,
 count(DISTINCT service_date) FILTER(WHERE status='observed') observed_dates,
 count(*) FILTER(WHERE status='observed' AND delay_s BETWEEN -60 AND 180) on_time,
 count(*) FILTER(WHERE status='observed' AND delay_s>180) late,
 observed::DOUBLE/nullif(eligible,0) coverage,
 on_time::DOUBLE/nullif(observed,0) punctuality,
 count(DISTINCT service_date) FILTER(WHERE status='observed' AND delay_s>180) late_dates,
 median(delay_s) FILTER(WHERE status='observed') median_delay
FROM timing GROUP BY ALL;
CREATE TEMP TABLE pairs AS
SELECT *,next_delay-delay_s delta,
 status IN ('observed','unobserved') AND next_status IN ('observed','unobserved') pair_eligible,
 status='observed' AND next_status='observed' pair_observed
FROM timing WHERE next_stop_id IS NOT NULL;

CREATE OR REPLACE TEMP VIEW ambiguous AS
SELECT DISTINCT route_id,direction_id,schedule_pattern,start_seconds,day_type
FROM (SELECT service_date,route_id,direction_id,schedule_pattern,start_seconds,day_type,
 count(*) n FROM journeys GROUP BY ALL HAVING n>1);
CREATE OR REPLACE TEMP VIEW safe_recurring AS
SELECT r.* FROM recurring r ANTI JOIN ambiguous a
USING(route_id,direction_id,schedule_pattern,start_seconds,day_type);
CREATE OR REPLACE TEMP VIEW focus AS
SELECT * FROM timing WHERE day_type='weekday' AND
 ((route_id='9011007084000000' AND direction_id=1 AND schedule_pattern='8fcfb02173fc9e4dc1c5f67817a8fcc5' AND start_seconds=58500)
 OR (route_id='9011007000100000' AND direction_id=0 AND schedule_pattern='2eb5a3d596c43db70b9679bfefa12200' AND start_seconds=57180));


-- result: validation
SELECT (SELECT count(*) FROM journeys) scheduled_scope,(SELECT count(*) FROM timing WHERE status IN ('observed','unobserved')) eligible,(SELECT count(*) FROM timing WHERE status='observed') observed,(SELECT count(*) FROM timing WHERE status='observed' AND delay_s BETWEEN -60 AND 180) on_time;

-- result: duplicate_keys
SELECT count(*) duplicate_keys FROM (SELECT service_date,route_id,direction_id,schedule_pattern,start_seconds,count(*) n FROM journeys GROUP BY ALL HAVING n>1);

-- result: recurring_inventory
SELECT day_type,count(*) group_count,count(*) FILTER(WHERE observed_trips>=20 AND coverage>=.9) reportable FROM recurring GROUP BY 1;

-- result: pair_inventory
SELECT count(*) scheduled_pairs,count(*) FILTER(WHERE pair_eligible) eligible_pairs,count(*) FILTER(WHERE pair_observed) observed_pairs,count(DISTINCT (service_date,trip_id)) FILTER(WHERE pair_observed) paired_trips, median(delta) FILTER(WHERE pair_observed) median_change, quantile_cont(delta,.9) FILTER(WHERE pair_observed) p90_change FROM pairs;

-- result: recurring_candidates
SELECT *, round(100*coverage,2) coverage_pct, round(100*punctuality,2) on_time_pct FROM safe_recurring WHERE observed_trips>=20 AND coverage>=.9 AND start_seconds BETWEEN 15*3600 AND 18*3600-1 ORDER BY late DESC,route_id,start_seconds LIMIT 15;

-- result: segment_candidates
SELECT route_id,route_short_name,direction_id,pattern,schedule_pattern,stop_id,stop_name,next_stop_id,next_stop_name,stop_position,count(*) FILTER(WHERE pair_eligible) eligible_pairs,count(*) FILTER(WHERE pair_observed) observed_pairs,count(DISTINCT (service_date,trip_id)) FILTER(WHERE pair_observed) trips,round(100*observed_pairs::DOUBLE/nullif(eligible_pairs,0),2) coverage_pct,median(delta) FILTER(WHERE pair_observed) median_change,quantile_cont(delta,.9) FILTER(WHERE pair_observed) p90_change,count(*) FILTER(WHERE pair_observed AND delay_s<=180 AND next_delay>180) became_late,count(*) FILTER(WHERE pair_observed AND delay_s>180 AND next_delay<=180) recovered_below_late FROM pairs WHERE day_type='weekday' AND start_seconds BETWEEN 15*3600 AND 18*3600-1 GROUP BY ALL HAVING trips>=20 AND coverage_pct>=90 ORDER BY became_late DESC LIMIT 15;

-- result: ambiguity
SELECT count(*) ambiguous_recurring_keys FROM ambiguous;

-- result: safe_inventory
SELECT day_type,count(*) group_count,count(*) FILTER(WHERE observed_trips>=20 AND coverage>=.9) reportable FROM safe_recurring GROUP BY 1 ORDER BY 1;

-- result: focus_keys
SELECT * FROM safe_recurring WHERE day_type='weekday' AND ((route_id='9011007084000000' AND direction_id=1 AND start_seconds=58500) OR (route_id='9011007000100000' AND direction_id=0 AND start_seconds=57180));

-- result: stop_profiles
SELECT route_short_name,start_seconds,timing_index,stop_id,stop_name,stop_position,count(*) scheduled,count(*) FILTER(WHERE status IN ('observed','unobserved')) eligible,count(*) FILTER(WHERE status='observed') observed,count(*) FILTER(WHERE status='observed' AND delay_s BETWEEN -60 AND 180) on_time,count(*) FILTER(WHERE status='observed' AND delay_s>180) late,median(delay_s) FILTER(WHERE status='observed') median_delay,quantile_cont(delay_s,.1) FILTER(WHERE status='observed') p10_delay,quantile_cont(delay_s,.9) FILTER(WHERE status='observed') p90_delay FROM focus GROUP BY ALL ORDER BY route_short_name,timing_index;

-- result: focus_pairs
SELECT route_short_name,start_seconds,stop_name,next_stop_name,count(*) FILTER(WHERE status IN ('observed','unobserved') AND next_status IN ('observed','unobserved')) eligible_pairs,count(*) FILTER(WHERE status='observed' AND next_status='observed') observed_pairs,median(next_delay-delay_s) FILTER(WHERE status='observed' AND next_status='observed') median_change,count(*) FILTER(WHERE status='observed' AND next_status='observed' AND delay_s BETWEEN -60 AND 180 AND next_delay>180) ontime_to_late,count(*) FILTER(WHERE status='observed' AND next_status='observed' AND delay_s>180 AND next_delay BETWEEN -60 AND 180) late_to_ontime FROM focus WHERE next_stop_id IS NOT NULL GROUP BY ALL ORDER BY route_short_name,stop_name;

-- result: day_profiles
SELECT service_date,route_short_name,start_seconds,trip_id,timing_index,stop_name,status,delay_s FROM focus ORDER BY route_short_name,service_date,timing_index;

-- result: 840_same_pattern_times
SELECT start_seconds,observed,observed_trips,observed_dates,late_dates,round(100*coverage,2) coverage_pct,round(100*punctuality,2) on_time_pct,median_delay FROM safe_recurring WHERE route_id='9011007084000000' AND direction_id=1 AND schedule_pattern='8fcfb02173fc9e4dc1c5f67817a8fcc5' AND day_type='weekday' AND observed_trips>=20 AND coverage>=.9 ORDER BY start_seconds;

-- result: route1_segment_hours
SELECT floor(start_seconds/3600)::INT start_hour,count(*) FILTER(WHERE pair_eligible) eligible_pairs,count(*) FILTER(WHERE pair_observed) observed_pairs,median(delta) FILTER(WHERE pair_observed) median_change,count(*) FILTER(WHERE pair_observed AND delay_s BETWEEN -60 AND 180 AND next_delay>180) ontime_to_late FROM pairs WHERE route_id='9011007000100000' AND direction_id=0 AND schedule_pattern='2eb5a3d596c43db70b9679bfefa12200' AND stop_id='9022007080001003' AND day_type='weekday' GROUP BY 1 HAVING observed_pairs>=20 AND observed_pairs::DOUBLE/nullif(eligible_pairs,0)>=.9 ORDER BY 1;
