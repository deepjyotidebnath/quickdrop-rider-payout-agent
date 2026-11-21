"""Read-only view of the ops exports (trips, payout lines, riders), cleaned on load.

Known export quirks handled here:
  * rider ids like 'r19' / 'R7' -> 'R019' / 'R007'
  * exact duplicate trip rows (same trip_id) -> kept once
  * distance stored in metres on some rows -> converted to km
  * trip timestamps are UTC; policy days are IST calendar days
"""
import csv
import os
import re
from collections import defaultdict

from . import policy


def norm_rider(raw: str) -> str:
    m = re.match(r"^\s*[rR]0*(\d+)\s*$", raw or "")
    return f"R{int(m.group(1)):03d}" if m else (raw or "").strip().upper()


def norm_trip(raw: str) -> str:
    return (raw or "").strip().upper()


class Trip:
    __slots__ = ("trip_id", "rider_id", "started_at", "day", "status", "km", "surge", "km_was_metres")

    def __init__(self, row):
        self.trip_id = norm_trip(row["trip_id"])
        self.rider_id = norm_rider(row["rider_id"])
        self.started_at = policy.parse_ts(row["started_at"])
        self.day = policy.ist_date(self.started_at)
        self.status = row["status"].strip()
        raw_km = float(row["distance_km"])
        self.km = policy.normalise_km(raw_km)
        self.km_was_metres = raw_km > policy.METRES_THRESHOLD
        self.surge = float(row["surge_multiplier"])

    @property
    def fare(self) -> int:
        return policy.trip_fare(self.km, self.surge)


class DataStore:
    def __init__(self, data_dir: str):
        self.trips = {}
        self.conflicting_trip_ids = set()
        self.riders = {}
        self.lines = []
        self._load(data_dir)

    def _read(self, data_dir, name):
        with open(os.path.join(data_dir, name), newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _load(self, d):
        for row in self._read(d, "riders.csv"):
            self.riders[norm_rider(row["rider_id"])] = row
        for row in self._read(d, "trips.csv"):
            t = Trip(row)
            old = self.trips.get(t.trip_id)
            if old is None:
                self.trips[t.trip_id] = t
            elif (old.rider_id, old.started_at, old.status, old.km, old.surge) != (
                t.rider_id, t.started_at, t.status, t.km, t.surge
            ):
                self.conflicting_trip_ids.add(t.trip_id)
        for row in self._read(d, "payout_lines.csv"):
            self.lines.append({
                "line_id": row["line_id"],
                "date": row["payout_date"],
                "rider_id": norm_rider(row["rider_id"]),
                "type": row["line_type"],
                "trip_id": norm_trip(row["trip_id"]),
                "amount": int(row["amount"]),
            })
        self.by_trip = defaultdict(list)
        self.by_rider_day = defaultdict(list)
        for ln in self.lines:
            if ln["trip_id"]:
                self.by_trip[ln["trip_id"]].append(ln)
            self.by_rider_day[(ln["rider_id"], ln["date"])].append(ln)
        self.trips_by_rider_day = defaultdict(list)
        for t in self.trips.values():
            self.trips_by_rider_day[(t.rider_id, t.day.isoformat())].append(t)

    # -- lookups ---------------------------------------------------------
    def rider_exists(self, rider_id: str) -> bool:
        return rider_id in self.riders

    def trip(self, trip_id: str):
        return self.trips.get(norm_trip(trip_id))

    def trips_on(self, rider_id: str, day):
        return sorted(self.trips_by_rider_day.get((rider_id, day.isoformat()), []),
                      key=lambda t: t.started_at)

    def completed_count(self, rider_id: str, day) -> int:
        return sum(1 for t in self.trips_on(rider_id, day) if t.status == "completed")

    def lines_for_trip(self, trip_id: str, line_type: str):
        return [l for l in self.by_trip.get(norm_trip(trip_id), []) if l["type"] == line_type]

    def incentive_lines(self, rider_id: str, day):
        return [l for l in self.by_rider_day.get((rider_id, day.isoformat()), [])
                if l["type"] == "daily_incentive"]
