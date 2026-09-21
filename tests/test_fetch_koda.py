import io
import urllib.error
from unittest.mock import patch

import pytest

from kronoberg_transit.fetch_koda import FetchError, fetch_day, fetch_hour, parse_hours

SEVEN_ZIP_MAGIC = b"\x37\x7a\xbc\xaf\x27\x1c"


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("url", code, "reason", {}, io.BytesIO(body))


def test_fetch_hour_skips_existing_file(tmp_path):
    outfile = tmp_path / "08.7z"
    outfile.write_bytes(SEVEN_ZIP_MAGIC + b"already here")

    with patch("kronoberg_transit.fetch_koda.urllib.request.urlopen") as mock_urlopen:
        result = fetch_hour("krono", "TripUpdates", "2026-09-07", 8, "key", tmp_path)

    mock_urlopen.assert_not_called()
    assert result == outfile


def test_fetch_hour_saves_valid_archive(tmp_path):
    body = SEVEN_ZIP_MAGIC + b"archive contents"

    with patch(
        "kronoberg_transit.fetch_koda.urllib.request.urlopen", return_value=FakeResponse(body)
    ):
        result = fetch_hour("krono", "TripUpdates", "2026-09-07", 8, "key", tmp_path)

    assert result == tmp_path / "08.7z"
    assert result.read_bytes() == body
    assert not (tmp_path / "08.7z.part").exists()


def test_fetch_hour_polls_through_202(tmp_path):
    body = SEVEN_ZIP_MAGIC + b"archive contents"
    responses = [http_error(202), FakeResponse(body)]

    def side_effect(*args, **kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with (
        patch("kronoberg_transit.fetch_koda.urllib.request.urlopen", side_effect=side_effect),
        patch("kronoberg_transit.fetch_koda.time.sleep"),
    ):
        result = fetch_hour("krono", "TripUpdates", "2026-09-07", 8, "key", tmp_path)

    assert result.read_bytes() == body


def test_fetch_hour_raises_on_404(tmp_path):
    with (
        patch(
            "kronoberg_transit.fetch_koda.urllib.request.urlopen",
            side_effect=http_error(404),
        ),
        pytest.raises(FetchError, match="404"),
    ):
        fetch_hour("krono", "TripUpdates", "2026-09-07", 8, "key", tmp_path)


def test_fetch_hour_raises_on_non_archive_payload(tmp_path):
    with (
        patch(
            "kronoberg_transit.fetch_koda.urllib.request.urlopen",
            return_value=FakeResponse(b"not a 7z archive"),
        ),
        pytest.raises(FetchError, match="not a 7z archive"),
    ):
        fetch_hour("krono", "TripUpdates", "2026-09-07", 8, "key", tmp_path)


def test_fetch_day_reports_partial_failure(tmp_path):
    body = SEVEN_ZIP_MAGIC + b"archive contents"

    def fake_urlopen(req, timeout=None):
        if "hour=03" in req.full_url:
            raise http_error(404)
        return FakeResponse(body)

    with patch("kronoberg_transit.fetch_koda.urllib.request.urlopen", side_effect=fake_urlopen):
        results = fetch_day("krono", "TripUpdates", "2026-09-07", "key", tmp_path)

    assert len(results) == 24
    assert isinstance(results[3], FetchError)
    assert all(hasattr(v, "read_bytes") or isinstance(v, FetchError) for v in results.values())
    ok_hours = [h for h, v in results.items() if not isinstance(v, Exception)]
    assert len(ok_hours) == 23


def test_fetch_day_fetches_only_requested_hours(tmp_path):
    body = SEVEN_ZIP_MAGIC + b"archive contents"
    requested_urls = []

    def fake_urlopen(req, timeout=None):
        requested_urls.append(req.full_url)
        return FakeResponse(body)

    with patch("kronoberg_transit.fetch_koda.urllib.request.urlopen", side_effect=fake_urlopen):
        results = fetch_day(
            "krono", "TripUpdates", "2026-09-08", "key", tmp_path, hours=[0, 1, 2, 3]
        )

    assert set(results) == {0, 1, 2, 3}
    assert len(requested_urls) == 4


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("3", [3]),
        ("0,4,7", [0, 4, 7]),
        ("0-3", [0, 1, 2, 3]),
        ("0-3,5,7-9", [0, 1, 2, 3, 5, 7, 8, 9]),
        (" 1 , 2 ", [1, 2]),
        ("5,2,5", [2, 5]),
    ],
)
def test_parse_hours(spec, expected):
    assert parse_hours(spec) == expected


def test_parse_hours_rejects_out_of_range():
    with pytest.raises(ValueError, match="out of range"):
        parse_hours("24")


def test_parse_hours_rejects_backwards_range():
    with pytest.raises(ValueError, match="start must not exceed end"):
        parse_hours("5-2")
