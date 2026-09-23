"""Scheduled-time conversion helpers shared by the pipeline and the validation scripts."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

STOCKHOLM = ZoneInfo("Europe/Stockholm")


def gtfs_hms_to_unix(svc_date: date, hms: str) -> int | None:
    """Convert a GTFS HH:MM:SS local time (hours may exceed 23) on a service
    date, in Europe/Stockholm, to a unix timestamp."""
    if not hms:
        return None
    parts = hms.split(":")
    if len(parts) != 3:
        return None
    h, m, s = (int(p) for p in parts)
    extra_days, h = divmod(h, 24)
    local_dt = datetime(svc_date.year, svc_date.month, svc_date.day, h, m, s, tzinfo=STOCKHOLM)
    local_dt += timedelta(days=extra_days)
    return int(local_dt.timestamp())


def local_hour_labels(svc_date: date) -> list[int]:
    """The KoDa hour labels (00-23) that exist for a local Stockholm service
    date.

    A normal (24h) or autumn-change, fall-back (25h) day has all 24 labels:
    on a fall-back day the repeated local hour's archive holds both real
    occurrences of that hour, under one label. A spring-change,
    spring-forward (23h) day omits the label for its skipped local hour
    entirely - KoDa has no archive for it.
    """
    start_utc = datetime(
        svc_date.year, svc_date.month, svc_date.day, 0, 0, 0, tzinfo=STOCKHOLM
    ).astimezone(UTC)
    next_day = svc_date + timedelta(days=1)
    end_utc = datetime(
        next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=STOCKHOLM
    ).astimezone(UTC)
    day_length_h = round((end_utc - start_utc).total_seconds() / 3600)

    if day_length_h in (24, 25):
        return list(range(24))
    if day_length_h != 23:
        raise RuntimeError(f"Unexpected Stockholm day length {day_length_h}h for {svc_date}")

    for h in range(24):
        probe = datetime(svc_date.year, svc_date.month, svc_date.day, h, 30, 0, tzinfo=STOCKHOLM)
        if probe.astimezone(UTC).astimezone(STOCKHOLM).hour != h:
            return [x for x in range(24) if x != h]
    raise RuntimeError(f"23-hour day {svc_date} but no skipped local hour found")


def _midnight_utcoffset(d: date) -> timedelta:
    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=STOCKHOLM).utcoffset()


def is_offset_change_date(d: date) -> bool:
    """True when Europe/Stockholm's UTC offset changes during this specific
    calendar date (a 23h spring-forward or 25h fall-back day). Used to mark
    stop events in the daylight-saving window (D-021)."""
    return _midnight_utcoffset(d) != _midnight_utcoffset(d + timedelta(days=1))


def scheduled_time_utc(svc_date: date, hms: str) -> int | None:
    """GTFS spec rule for a HH:MM:SS service-day time -> UTC unix timestamp:
    noon Europe/Stockholm on the service date, minus 12h, plus HH:MM:SS. This
    anchors on an always-unambiguous local time (noon) before doing the rest
    of the arithmetic in UTC, so it handles times past 24:00 and DST changes
    correctly even when HH:MM:SS itself would name an ambiguous or
    non-existent local wall-clock time.
    """
    if not hms:
        return None
    parts = hms.split(":")
    if len(parts) != 3:
        return None
    h, m, s = (int(p) for p in parts)
    noon_local = datetime(svc_date.year, svc_date.month, svc_date.day, 12, 0, 0, tzinfo=STOCKHOLM)
    anchor_utc = noon_local.astimezone(UTC) - timedelta(hours=12)
    result = anchor_utc + timedelta(hours=h, minutes=m, seconds=s)
    return int(result.timestamp())
