"""PaySwift client. Sandbox behaves like production: random 503s, 429s, and 504s where the
payout WAS processed but the response was lost. So: every payout carries an Idempotency-Key,
failures and timeouts are retried with the same key, and the ledger (GET /v1/payouts) is the
source of truth for what has really been paid."""
import re
import time
from dataclasses import dataclass, field

import requests

from . import config

REF_PREFIX = "QD"
_ITEM_RE = re.compile(r"i=([^|]*)")


def make_reference(dispute_id: str, items: dict) -> str:
    """items: {item_key: rupees}. Parsed back by parse_reference()."""
    packed = ";".join(f"{k}={v}" for k, v in items.items())
    return f"{REF_PREFIX}|d={dispute_id}|i={packed}"


def parse_reference(ref: str) -> dict:
    if not ref or not ref.startswith(REF_PREFIX + "|"):
        return {}
    m = _ITEM_RE.search(ref)
    out = {}
    if m:
        for part in m.group(1).split(";"):
            if "=" in part:
                k, v = part.rsplit("=", 1)
                if v.lstrip("-").isdigit():
                    out[k] = int(v)
    return out


@dataclass
class PayResult:
    status: str                      # sent | unknown | failed
    payout_id: str = ""
    attempts: int = 0
    log: list = field(default_factory=list)   # [(http_status_or_error, seconds)]
    error: str = ""


class PaySwiftClient:
    def __init__(self, base_url=None):
        self.base = (base_url or config.PAYSWIFT_BASE_URL).rstrip("/")
        self.http = requests.Session()

    def health(self) -> bool:
        try:
            return self.http.get(self.base + "/health", timeout=2).status_code == 200
        except requests.RequestException:
            return False

    def list_payouts(self, rider_id: str, tries: int = 3):
        """Returns the rider's ledger rows, or None if PaySwift could not be read."""
        for i in range(tries):
            try:
                r = self.http.get(self.base + "/v1/payouts", params={"rider_id": rider_id},
                                  timeout=config.PAYSWIFT_TRY_TIMEOUT_S)
                if r.status_code == 200:
                    return r.json().get("data", [])
                if r.status_code == 429:
                    time.sleep(min(float(r.headers.get("Retry-After", "0.5") or 0.5), 1.5))
                else:
                    time.sleep(0.2 * (i + 1))
            except (requests.RequestException, ValueError):
                time.sleep(0.2 * (i + 1))
        return None

    def ledger_items(self, rider_id: str):
        """item_key -> rupees already paid to this rider according to PaySwift. None if unreadable."""
        rows = self.list_payouts(rider_id)
        if rows is None:
            return None
        paid = {}
        for row in rows:
            if row.get("status") in ("failed", "reversed"):
                continue
            for k, v in parse_reference(row.get("reference", "")).items():
                paid[k] = paid.get(k, 0) + v
        return paid

    def create_payout(self, rider_id, amount, reference, idem_key, budget_s=None) -> PayResult:
        budget = config.PAYSWIFT_BUDGET_S if budget_s is None else budget_s
        deadline = time.monotonic() + budget
        res = PayResult("unknown")
        body = {"rider_id": rider_id, "amount": int(amount), "reference": reference}
        backoff = 0.25
        while True:
            res.attempts += 1
            t0 = time.monotonic()
            try:
                timeout = max(0.5, min(config.PAYSWIFT_TRY_TIMEOUT_S, deadline - t0))
                r = self.http.post(self.base + "/v1/payouts", json=body, timeout=timeout,
                                   headers={"Idempotency-Key": idem_key})
                res.log.append((r.status_code, round(time.monotonic() - t0, 2)))
                if r.status_code in (200, 201):
                    data = r.json()
                    res.status, res.payout_id = "sent", data.get("payout_id", "")
                    return res
                if r.status_code == 400:
                    res.status, res.error = "failed", r.text[:200]
                    return res
                if r.status_code == 409:
                    # same key already in flight / seen: the ledger decides, otherwise try again
                    ledger = self.ledger_items(rider_id) or {}
                    if any(k in ledger for k in parse_reference(reference)):
                        res.status = "sent"
                        return res
                    res.error = "409 " + r.text[:120]
                elif r.status_code == 429:
                    backoff = min(float(r.headers.get("Retry-After", backoff) or backoff), 1.5)
                # 5xx (incl. 504 where the payout may already exist): retry with the SAME key
            except requests.RequestException as e:
                res.log.append((type(e).__name__, round(time.monotonic() - t0, 2)))
                res.error = type(e).__name__
            if time.monotonic() + backoff >= deadline:
                res.status = "unknown"
                return res
            time.sleep(backoff)
            backoff = min(backoff * 2, 1.5)
