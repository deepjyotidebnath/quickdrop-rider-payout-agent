"""Payout policy (docs/policy.md) as pure functions. No I/O, no model involvement."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

IST = timezone(timedelta(hours=5, minutes=30))

BASE_FARE = Decimal(25)
PER_KM = Decimal(6)
FREE_KM = Decimal(2)
INCENTIVE_MIN_TRIPS = 12
INCENTIVE_AMOUNT = 150
CANCEL_PENALTY = 10
WINDOW_DAYS = 7
AUTO_PAY_LIMIT = 200  # Finance: per dispute, once per rider per day

# Some trips-system rows store distance in metres (e.g. 5000 instead of 5.0).
# No real trip is anywhere near 200 km, so anything above is treated as metres.
METRES_THRESHOLD = 200


def normalise_km(raw: float) -> float:
    return raw / 1000.0 if raw > METRES_THRESHOLD else raw


def trip_fare(distance_km, surge) -> int:
    """25 + 6/km after the first 2 km, times surge, rounded half up to whole rupees."""
    km = Decimal(str(distance_km))
    fare = BASE_FARE + PER_KM * max(km - FREE_KM, Decimal(0))
    fare *= Decimal(str(surge))
    return int(fare.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def ist_date(value) -> date:
    dt = parse_ts(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date()


def in_window(day: date, today: date) -> bool:
    """Only the last 7 days are looked at (calendar days in IST)."""
    return today - timedelta(days=WINDOW_DAYS) <= day <= today
