from app.payswift import PayResult, parse_reference


class FakePaySwift:
    """In-memory PaySwift with the sandbox's failure modes:
    lose_response=N  -> first N payout calls are recorded but the caller sees an unknown outcome (like a 504)
    fail_first=N     -> first N calls fail before recording anything (like a 503)"""

    def __init__(self, lose_response=0, fail_first=0):
        self.ledger, self.keys = [], {}
        self.lose_response, self.fail_first = lose_response, fail_first
        self.calls = 0

    def health(self):
        return True

    def list_payouts(self, rider_id, tries=3):
        return [p for p in self.ledger if p["rider_id"] == rider_id]

    def ledger_items(self, rider_id):
        out = {}
        for p in self.list_payouts(rider_id):
            for k, v in parse_reference(p["reference"]).items():
                out[k] = out.get(k, 0) + v
        return out

    def total(self, rider_id):
        return sum(p["amount"] for p in self.list_payouts(rider_id))

    def create_payout(self, rider_id, amount, reference, idem_key, budget_s=None):
        self.calls += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            return PayResult("unknown", attempts=1, error="503")
        if idem_key in self.keys:
            return PayResult("sent", self.keys[idem_key], 1)
        pid = f"pout_{len(self.ledger) + 1}"
        self.keys[idem_key] = pid
        self.ledger.append({"payout_id": pid, "rider_id": rider_id, "amount": amount, "reference": reference, "status": "processed"})
        if self.lose_response > 0:
            self.lose_response -= 1
            return PayResult("unknown", attempts=1, error="504")
        return PayResult("sent", pid, 1)
