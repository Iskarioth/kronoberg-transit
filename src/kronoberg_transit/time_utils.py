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
