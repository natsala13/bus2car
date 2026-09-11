from datetime import datetime, timezone

import pytest

from routes_api.utils import normalize_departure_time, parse_google_duration


def test_parse_google_duration() -> None:
    assert parse_google_duration("596s") == 596
    assert parse_google_duration("3.5s") == 3.5


def test_normalize_departure_time_to_utc() -> None:
    assert normalize_departure_time("2026-09-14T08:00:00+03:00") == "2026-09-14T05:00:00Z"
    assert normalize_departure_time(datetime(2026, 9, 14, 5, tzinfo=timezone.utc)) == "2026-09-14T05:00:00Z"


def test_reject_naive_departure_time() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        normalize_departure_time("2026-09-14T08:00:00")
