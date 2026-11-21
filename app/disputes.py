"""Deterministic dispute checks. This module decides what the rider is owed; the model never does.

Each check returns a Finding. owed > 0 means money is due; owed == 0 means we checked and
the rider is not owed anything (the reason says why, so the reply can explain it).
"""
from dataclasses import dataclass, field
from datetime import date

from . import policy


@dataclass
class Finding:
    kind: str            # trip_underpaid | trip_unpaid | incentive | penalty_duplicate | penalty_wrongful | ...
    item_key: str        # stable id of the money item, used for idempotency and ledger reconciliation
    owed: int            # rupees due to the rider (before PaySwift reconciliation)
    verdict: str         # owed | not_owed | out_of_window | not_found | not_riders_trip | needs_human
    day: str = ""
    trip_id: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {"kind": self.kind, "item_key": self.item_key, "owed": self.owed, "verdict": self.verdict,
                "day": self.day, "trip_id": self.trip_id, "detail": self.detail}


def check_trip(data, rider_id: str, trip_id: str, today: date, claim: str = "any") -> Finding:
    """claim: 'surge' | 'distance' | 'missing' | 'penalty' | 'any' (what the rider says is wrong)."""
    trip = data.trip(trip_id)
    key = f"trip:{trip_id}"
    if trip is None:
        return Finding("trip", key, 0, "not_found", trip_id=trip_id)
    if trip.rider_id != rider_id:
        # never reveal anything about another rider's trip
        return Finding("trip", key, 0, "not_riders_trip", trip_id=trip_id)
    day = trip.day.isoformat()
    if not policy.in_window(trip.day, today):
        return Finding("trip", key, 0, "out_of_window", day=day, trip_id=trip_id)
    if trip.trip_id in data.conflicting_trip_ids:
        return Finding("trip", key, 0, "needs_human", day=day, trip_id=trip_id,
                       detail={"why": "conflicting duplicate rows in trips export"})

    if trip.status == "cancelled_by_customer":
        pens = data.lines_for_trip(trip_id, "cancellation_penalty")
        if pens:
            owed = -sum(l["amount"] for l in pens)
            return Finding("penalty_wrongful", f"penalty:{trip_id}", owed, "owed", day, trip_id,
                           {"status": trip.status, "lines": len(pens)})
        return Finding("trip_cancelled_customer", key, 0, "not_owed", day, trip_id, {"status": trip.status})

    if trip.status == "cancelled_by_rider":
        pens = data.lines_for_trip(trip_id, "cancellation_penalty")
        if len(pens) > 1:
            extra = -sum(l["amount"] for l in pens[1:])
            return Finding("penalty_duplicate", f"penalty_dup:{trip_id}", extra, "owed", day, trip_id,
                           {"lines": len(pens)})
        return Finding("penalty_correct", f"penalty:{trip_id}", 0, "not_owed", day, trip_id,
                       {"status": trip.status, "penalty": policy.CANCEL_PENALTY})

    # completed trip
    expected = trip.fare
    tl = data.lines_for_trip(trip_id, "trip")
    paid = sum(l["amount"] for l in tl)
    detail = {"km": trip.km, "surge": trip.surge, "expected": expected, "paid": paid,
              "km_was_metres": trip.km_was_metres}
    if not tl:
        return Finding("trip_unpaid", f"trip_missing:{trip_id}", expected, "owed", day, trip_id, detail)
    if paid < expected:
        return Finding("trip_underpaid", f"trip_diff:{trip_id}", expected - paid, "owed", day, trip_id, detail)
    return Finding("trip_ok", key, 0, "not_owed", day, trip_id, detail)


def check_incentive(data, rider_id: str, day: date, today: date) -> Finding:
    key = f"incentive:{rider_id}:{day.isoformat()}"
    if not policy.in_window(day, today):
        return Finding("incentive", key, 0, "out_of_window", day.isoformat())
    n = data.completed_count(rider_id, day)
    paid = data.incentive_lines(rider_id, day)
    detail = {"completed_trips": n, "required": policy.INCENTIVE_MIN_TRIPS}
    if n < policy.INCENTIVE_MIN_TRIPS:
        return Finding("incentive", key, 0, "not_owed", day.isoformat(), detail=detail)
    if paid:
        detail["already_paid"] = True
        return Finding("incentive", key, 0, "not_owed", day.isoformat(), detail=detail)
    return Finding("incentive", key, policy.INCENTIVE_AMOUNT, "owed", day.isoformat(), detail=detail)


def check_penalties_on_day(data, rider_id: str, day: date, today: date):
    """Wrongful or duplicated cancellation penalties on one IST day."""
    if not policy.in_window(day, today):
        return [Finding("penalty", f"penalty_day:{rider_id}:{day.isoformat()}", 0, "out_of_window", day.isoformat())]
    out, rider_cancels = [], 0
    for t in data.trips_on(rider_id, day):
        if t.status == "cancelled_by_rider":
            rider_cancels += 1
        if t.status in ("cancelled_by_rider", "cancelled_by_customer"):
            f = check_trip(data, rider_id, t.trip_id, today, "penalty")
            if f.verdict == "owed":
                out.append(f)
    if not out:
        out.append(Finding("penalty", f"penalty_day:{rider_id}:{day.isoformat()}", 0, "not_owed", day.isoformat(),
                           detail={"rider_cancellations": rider_cancels}))
    return out


def check_day(data, rider_id: str, day: date, today: date, kinds=("missing", "surge", "distance")):
    """All trip-pay problems on a day: unpaid trips and underpaid trips."""
    if not policy.in_window(day, today):
        return [Finding("day", f"day:{rider_id}:{day.isoformat()}", 0, "out_of_window", day.isoformat())]
    found = []
    for t in data.trips_on(rider_id, day):
        if t.status != "completed":
            continue
        f = check_trip(data, rider_id, t.trip_id, today)
        if f.verdict == "owed":
            found.append(f)
    return found


def scan_day(data, rider_id: str, day: date, today: date):
    """Everything wrong on one day (used when the rider just says 'the 20th was wrong')."""
    out = check_day(data, rider_id, day, today)
    inc = check_incentive(data, rider_id, day, today)
    if inc.verdict == "owed":
        out.append(inc)
    out += [f for f in check_penalties_on_day(data, rider_id, day, today) if f.verdict == "owed"]
    return out
