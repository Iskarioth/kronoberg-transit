"""Tests for kronoberg_transit.pipeline. Mocks at the network boundary
(HfApi, gspread, transform.run_transform), as the fetch tests do."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from kronoberg_transit.pipeline import (
    ANALYSIS_FLOOR,
    PreflightError,
    RunLog,
    check_hf_preflight,
    check_koda_key_preflight,
    check_sheet_preflight,
    parse_dates_arg,
    process_date,
    select_dates,
    upload_partitions,
)

# --------------------------------------------------------------------------
# Date selection
# --------------------------------------------------------------------------


def test_select_dates_catch_up():
    hf_dates = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    today = date(2026, 9, 10)
    dates = select_dates(hf_dates, today, max_dates=7)
    assert dates == [
        date(2026, 9, 4),
        date(2026, 9, 5),
        date(2026, 9, 6),
        date(2026, 9, 7),
        date(2026, 9, 8),
    ]


def test_select_dates_cap():
    hf_dates = [date(2026, 9, 1)]
    today = date(2026, 9, 30)
    dates = select_dates(hf_dates, today, max_dates=3)
    assert dates == [date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)]


def test_select_dates_today_minus_2_limit():
    hf_dates = [date(2026, 9, 1)]
    today = date(2026, 9, 3)
    dates = select_dates(hf_dates, today, max_dates=7)
    # today - 2 = 2026-09-01, and start = 2026-09-02, so end < start -> no dates.
    assert dates == []

    today2 = date(2026, 9, 4)
    dates2 = select_dates(hf_dates, today2, max_dates=7)
    assert dates2 == [date(2026, 9, 2)]


def test_select_dates_analysis_period_floor_empty_dataset():
    dates = select_dates([], date(2026, 9, 10), max_dates=7)
    assert dates[0] == ANALYSIS_FLOOR


def test_select_dates_analysis_period_floor_stale_dataset():
    # Latest HF date is before the floor - never start earlier than the floor.
    hf_dates = [date(2026, 8, 15)]
    dates = select_dates(hf_dates, date(2026, 9, 10), max_dates=7)
    assert dates[0] == ANALYSIS_FLOOR


def test_parse_dates_arg_range():
    assert parse_dates_arg("2026-09-01..2026-09-03") == [
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]


def test_parse_dates_arg_single():
    assert parse_dates_arg("2026-09-01") == [date(2026, 9, 1)]


def test_parse_dates_arg_rejects_backwards_range():
    with pytest.raises(ValueError, match="start after end"):
        parse_dates_arg("2026-09-05..2026-09-01")


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def test_check_hf_preflight_no_token():
    with pytest.raises(PreflightError, match="HF_TOKEN is not set"):
        check_hf_preflight("owner/repo", None)


def test_check_hf_preflight_no_repo():
    with pytest.raises(PreflightError, match="HF_DATASET_REPO is not set"):
        check_hf_preflight(None, "tok")


def test_check_hf_preflight_read_only_token():
    fake_api = MagicMock()
    fake_api.whoami.return_value = {"auth": {"accessToken": {"role": "read"}}}
    with (
        patch("kronoberg_transit.pipeline.HfApi", return_value=fake_api),
        pytest.raises(PreflightError, match="role 'read'"),
    ):
        check_hf_preflight("owner/repo", "tok")


def test_check_hf_preflight_invalid_token():
    fake_api = MagicMock()
    fake_api.whoami.side_effect = RuntimeError("401")
    with (
        patch("kronoberg_transit.pipeline.HfApi", return_value=fake_api),
        pytest.raises(PreflightError, match="invalid or expired"),
    ):
        check_hf_preflight("owner/repo", "tok")


def test_check_hf_preflight_ok():
    fake_api = MagicMock()
    fake_api.whoami.return_value = {"auth": {"accessToken": {"role": "write"}}}
    fake_api.repo_info.return_value = MagicMock()
    with patch("kronoberg_transit.pipeline.HfApi", return_value=fake_api):
        check_hf_preflight("owner/repo", "tok")  # does not raise


def test_check_sheet_preflight_failure():
    with (
        patch("kronoberg_transit.aggregate.get_sheet", side_effect=RuntimeError("no access")),
        pytest.raises(PreflightError, match="Cannot open the Google Sheet"),
    ):
        check_sheet_preflight()


def test_check_koda_key_preflight_missing(monkeypatch):
    monkeypatch.delenv("TRAFIKLAB_KODA_KEY", raising=False)
    with pytest.raises(PreflightError, match="TRAFIKLAB_KODA_KEY"):
        check_koda_key_preflight()


def test_check_koda_key_preflight_present(monkeypatch):
    monkeypatch.setenv("TRAFIKLAB_KODA_KEY", "key")
    check_koda_key_preflight()  # does not raise


# --------------------------------------------------------------------------
# Upload path construction
# --------------------------------------------------------------------------


def test_upload_partitions_paths(tmp_path):
    svc_date = "2026-09-21"
    for table in ["trips", "stop_events", "routes", "stops", "feed_quality", "feed_gaps"]:
        d = tmp_path / table / f"service_date={svc_date}"
        d.mkdir(parents=True)
        (d / "part-0.parquet").write_bytes(b"fake parquet")

    fake_api = MagicMock()
    with patch("kronoberg_transit.pipeline.HfApi", return_value=fake_api):
        upload_partitions(svc_date, "owner/repo", "tok", tmp_path)

    assert fake_api.create_commit.call_count == 1
    kwargs = fake_api.create_commit.call_args.kwargs
    assert kwargs["repo_id"] == "owner/repo"
    assert kwargs["repo_type"] == "dataset"
    assert kwargs["commit_message"] == "data: add service date 2026-09-21"
    paths_in_repo = {op.path_in_repo for op in kwargs["operations"]}
    assert paths_in_repo == {
        f"data/{t}/service_date=2026-09-21/part-0.parquet"
        for t in ["trips", "stop_events", "routes", "stops", "feed_quality", "feed_gaps"]
    }


def test_upload_partitions_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        upload_partitions("2026-09-21", "owner/repo", "tok", tmp_path)


# --------------------------------------------------------------------------
# Per-date processing: no DST guard (D-021 removes it), warning status on
# schedule mismatches, and stopping on first failure
# --------------------------------------------------------------------------


@pytest.mark.parametrize("svc_date", [date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25)])
def test_process_date_no_longer_blocks_on_dst_adjacent_dates(svc_date, tmp_path):
    """D-021 removes the D-020 date guard: a daylight-saving-adjacent date is
    processed like any other (the window is handled inside the warehouse via
    the dst_ambiguous status, not by refusing the date)."""
    run_log = RunLog()
    with (
        patch(
            "kronoberg_transit.pipeline.transform.run_transform",
            return_value={
                "n_scheduled_trips": 0,
                "feed_quality": {
                    "out_of_scope_trips_in_feed": 0,
                    "schedule_mismatch_stop_events": 0,
                    "unmatched_realtime_trips": 0,
                },
            },
        ) as mock_transform,
        patch("kronoberg_transit.pipeline.upload_partitions"),
        patch("kronoberg_transit.pipeline.delete_interim"),
        patch("kronoberg_transit.pipeline.partition_row_counts", return_value={}),
    ):
        status = process_date(
            svc_date, "owner/repo", "tok", tmp_path, keep_interim=True, run_log=run_log
        )

    mock_transform.assert_called_once()
    assert status == "ok"
    assert run_log.rows[-1]["status"] == "ok"


def test_process_date_warning_status_on_schedule_mismatch(tmp_path):
    """A date with schedule_mismatch_stop_events > 0 is still uploaded, but
    its run_log row gets status='warning' with the count in the message."""
    run_log = RunLog()
    with (
        patch(
            "kronoberg_transit.pipeline.transform.run_transform",
            return_value={
                "n_scheduled_trips": 100,
                "feed_quality": {
                    "out_of_scope_trips_in_feed": 0,
                    "schedule_mismatch_stop_events": 5,
                    "unmatched_realtime_trips": 0,
                },
            },
        ),
        patch("kronoberg_transit.pipeline.upload_partitions") as mock_upload,
        patch("kronoberg_transit.pipeline.delete_interim"),
        patch("kronoberg_transit.pipeline.partition_row_counts", return_value={"trips": 100}),
    ):
        status = process_date(
            date(2025, 10, 25), "owner/repo", "tok", tmp_path, keep_interim=False, run_log=run_log
        )

    assert status == "ok"
    mock_upload.assert_called_once()  # still uploaded, despite the mismatches
    assert run_log.rows[-1]["status"] == "warning"
    assert "schedule_mismatch_stop_events=5" in run_log.rows[-1]["message"]


def test_process_date_warning_status_on_unmatched_realtime_trips(tmp_path):
    """A date with unmatched_realtime_trips > 0 is still uploaded, but its
    run_log row gets status='warning' with the count in the message (D-021:
    the mislabelled-trip case that schedule_mismatch_stop_events cannot
    catch, since an unmatched trip has no scheduled time to compare)."""
    run_log = RunLog()
    with (
        patch(
            "kronoberg_transit.pipeline.transform.run_transform",
            return_value={
                "n_scheduled_trips": 100,
                "feed_quality": {
                    "out_of_scope_trips_in_feed": 0,
                    "schedule_mismatch_stop_events": 0,
                    "unmatched_realtime_trips": 50,
                },
            },
        ),
        patch("kronoberg_transit.pipeline.upload_partitions") as mock_upload,
        patch("kronoberg_transit.pipeline.delete_interim"),
        patch("kronoberg_transit.pipeline.partition_row_counts", return_value={"trips": 100}),
    ):
        status = process_date(
            date(2025, 10, 25), "owner/repo", "tok", tmp_path, keep_interim=False, run_log=run_log
        )

    assert status == "ok"
    mock_upload.assert_called_once()
    assert run_log.rows[-1]["status"] == "warning"
    assert "unmatched_realtime_trips=50" in run_log.rows[-1]["message"]


def test_stops_on_first_failed_date(tmp_path):
    run_log = RunLog()
    dates = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]

    def fake_run_transform(svc_date_str, warehouse_dir=None):
        if svc_date_str == "2026-09-22":
            raise AssertionError("Hard checks failed: [...]")
        return {
            "n_scheduled_trips": 100,
            "feed_quality": {
                "out_of_scope_trips_in_feed": 0,
                "schedule_mismatch_stop_events": 0,
                "unmatched_realtime_trips": 0,
            },
        }

    processed = []
    with (
        patch("kronoberg_transit.pipeline.transform.run_transform", side_effect=fake_run_transform),
        patch("kronoberg_transit.pipeline.upload_partitions"),
        patch("kronoberg_transit.pipeline.delete_interim"),
        patch("kronoberg_transit.pipeline.partition_row_counts", return_value={"trips": 100}),
    ):
        for d in dates:
            status = process_date(
                d, "owner/repo", "tok", tmp_path, keep_interim=False, run_log=run_log
            )
            processed.append((d.isoformat(), status))
            if status == "failed":
                break

    assert processed == [
        ("2026-09-21", "ok"),
        ("2026-09-22", "failed"),
    ]
    statuses = [r["status"] for r in run_log.rows]
    assert statuses == ["ok", "error"]


def test_process_date_stops_before_upload_on_out_of_scope_tripwire(tmp_path):
    run_log = RunLog()
    with (
        patch(
            "kronoberg_transit.pipeline.transform.run_transform",
            return_value={
                "n_scheduled_trips": 50,
                "feed_quality": {"out_of_scope_trips_in_feed": 3},
            },
        ),
        patch("kronoberg_transit.pipeline.upload_partitions") as mock_upload,
    ):
        status = process_date(
            date(2026, 9, 21), "owner/repo", "tok", tmp_path, keep_interim=True, run_log=run_log
        )

    assert status == "failed"
    mock_upload.assert_not_called()
    assert "out_of_scope_trips_in_feed=3" in run_log.rows[-1]["message"]
