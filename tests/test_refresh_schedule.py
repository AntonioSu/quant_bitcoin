"""Clock-aligned AI / news refresh timers."""



from datetime import datetime, timezone

from utils.common_utils import seconds_until_next_boundary


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


def test_hourly_boundary_is_on_the_hour():
    now = _ts("2026-08-31T15:23:40")
    delay = seconds_until_next_boundary(3600, now)
    next_ts = now + delay
    nxt = datetime.fromtimestamp(next_ts, tz=timezone.utc)
    assert nxt.minute == 0
    assert nxt.second == 0
    assert nxt.hour == 16
    assert 0 < delay < 3600


def test_hourly_gap_is_one_hour_from_the_hour():
    on_hour = _ts("2026-08-31T16:00:00.1")
    delay = seconds_until_next_boundary(3600, on_hour)
    nxt = datetime.fromtimestamp(on_hour + delay, tz=timezone.utc)
    assert nxt.hour == 17
    assert nxt.minute == 0
    assert abs(delay - 3599.9) < 0.01


def test_news_boundary_is_two_hours():
    now = _ts("2026-08-31T15:23:40")
    delay = seconds_until_next_boundary(7200, now)
    nxt = datetime.fromtimestamp(now + delay, tz=timezone.utc)
    assert nxt.hour == 16
    assert nxt.minute == 0
    assert nxt.second == 0
    assert 0 < delay <= 7200


def test_news_gap_from_even_hour_is_two_hours():
    even_hour = _ts("2026-08-31T16:00:00.1")
    delay = seconds_until_next_boundary(7200, even_hour)
    nxt = datetime.fromtimestamp(even_hour + delay, tz=timezone.utc)
    assert nxt.hour == 18
    assert nxt.minute == 0
    assert abs(delay - 7199.9) < 0.01
