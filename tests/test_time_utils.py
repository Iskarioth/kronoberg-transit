from datetime import date

import pytest

from kronoberg_transit.time_utils import local_hour_labels


@pytest.mark.parametrize(
    ("svc_date", "expected"),
    [
        # Spring change: 2026-03-29 is a 23-hour local day (clocks jump
        # 02:00 -> 03:00 CET/CEST). KoDa has no 02 archive at all.
        (date(2026, 3, 29), [h for h in range(24) if h != 2]),
        # Autumn change: 2025-10-26 is a 25-hour local day (clocks fall back
        # 03:00 -> 02:00 CEST/CET). All 24 labels exist; the 02 archive
        # holds both real occurrences of local hour 02.
        (date(2025, 10, 26), list(range(24))),
        # Ordinary day: all 24 labels.
        (date(2026, 9, 7), list(range(24))),
    ],
)
def test_local_hour_labels(svc_date, expected):
    assert local_hour_labels(svc_date) == expected


def test_local_hour_labels_spring_change_excludes_02():
    assert 2 not in local_hour_labels(date(2026, 3, 29))


def test_local_hour_labels_autumn_change_keeps_all_24():
    assert local_hour_labels(date(2025, 10, 26)) == list(range(24))
    assert len(local_hour_labels(date(2025, 10, 26))) == 24
