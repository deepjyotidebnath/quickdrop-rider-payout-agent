"""The dispute agent.

Division of labour
  model (optional, app/llm.py): rephrases replies; parses messages the rule-based parser can't.
  code (this file + disputes.py + policy.py): who the rider is, what is owed, whether it may be paid
        automatically, idempotency, PaySwift calls, ops queue, and every number in every reply.
"""
import hashlib
import threading
import time
import uuid
from datetime import datetime

from . import config, disputes, llm, nlu, policy, replies
from .data import norm_rider
from .db import dumps, loads
from .payswift import make_reference


def now_iso():
    return datetime.now(policy.IST).isoformat(timespec="seconds")


def _h(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:24]


class Agent:
    def __init__(self, db, data, payswift):
        self.db, self.data, self.ps = db, data, payswift
        self._locks, self._locks_guard = {}, threading.Lock()

    # ------------------------------------------------------------------ plumbing
    def _lock(self, rider):
        with self._locks_guard:
            return self._locks.setdefault(rider, threading.Lock())

    def trace(self, rider, type_, name, inp=None, out=None):
        self.db.execute("INSERT INTO trace(n,rider_id,ts,type,name,input,output) VALUES(?,?,?,?,?,?,?)",
                        (self.db.next_n(), rider, now_iso(), type_, name, dumps(inp), dumps(out)))

    def load_state(self, rider):
        row = self.db.one("SELECT state FROM rider_state WHERE rider_id=?", (rider,))
        return loads(row["state"], {}) if row else {}

    def save_state(self, rider, st):
        self.db.execute(
            "INSERT INTO rider_state(rider_id,state,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(rider_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (rider, dumps(st), now_iso()))

    # ------------------------------------------------------------------ ops queue
    def escalate(self, rider, reason, mid=None):
        key = _h("esc", rider, reason)
        row = self.db.one("SELECT id FROM ops_items WHERE dedupe_key=? AND status='pending'", (key,))
        if row:
            return row["id"]
        oid = "esc_" + uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO ops_items(id,rider_id,type,amount,reason,status,items,created_at,message_id,dedupe_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (oid, rider, "escalation", None, reason, "pending", "[]", now_iso(), mid, key))
        self.trace(rider, "decision", "escalate_to_ops", {"reason": reason}, {"id": oid})
        return oid

    def create_approval(self, rider, amount, reason, items, mid=None):
        key = _h("appr", rider, *sorted(i["item_key"] for i in items))
        row = self.db.one("SELECT id,status,amount FROM ops_items WHERE dedupe_key=?", (key,))
        if row:
            return row["id"], row["status"]
        oid = "appr_" + uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO ops_items(id,rider_id,type,amount,reason,status,items,created_at,message_id,dedupe_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (oid, rider, "approval", amount, reason, "pending", dumps(items), now_iso(), mid, key))
        self.trace(rider, "decision", "queue_for_approval", {"amount": amount, "reason": reason,
                   "items": [i["item_key"] for i in items]}, {"id": oid})
        return oid, "pending"

    def pending_items(self):
        rows = self.db.query("SELECT * FROM ops_items WHERE status='pending' ORDER BY created_at, id")
        return [self._ops_view(r) for r in rows]

    def all_items(self):
        rows = self.db.query("SELECT * FROM ops_items ORDER BY created_at DESC, id DESC")
        return [self._ops_view(r) for r in rows]

    @staticmethod
    def _ops_view(r):
        return {"id": r["id"], "rider_id": r["rider_id"], "type": r["type"], "amount": r["amount"],
                "reason": r["reason"], "created_at": r["created_at"], "status": r["status"],
                "items": loads(r["items"], []), "resolution": r["resolution"], "resolved_at": r["resolved_at"]}

    # ------------------------------------------------------------------ money
    def _paid_map(self, rider):
        """item_key -> rupees already paid. PaySwift's ledger is the source of truth; our own
        table only fills in for payouts the ledger doesn't show (e.g. still in flight)."""
        ledger = self.ps.ledger_items(rider)
        paid = dict(ledger or {})
        for r in self.db.query("SELECT item_key, amount FROM paid_items WHERE rider_id=?", (rider,)):
            paid[r["item_key"]] = max(paid.get(r["item_key"], 0), r["amount"])
        for b in self.db.query("SELECT items, status FROM payout_batches WHERE rider_id=? AND status IN ('pending','unknown')", (rider,)):
            for k, v in (loads(b["items"], {}) or {}).items():
                paid[k] = max(paid.get(k, 0), v)
        return paid, ledger is not None

    def _auto_paid_today(self, rider, day):
        row = self.db.one("SELECT COUNT(*) AS c FROM payout_batches WHERE rider_id=? AND mode='auto' AND day=? "
                          "AND status IN ('pending','unknown','sent')", (rider, day))
        return row["c"]

    def pay(self, rider, items: dict, mode, day, source, dispute_id=None):
        """items {item_key: rupees}. Deterministic idempotency key => the same items can never be paid twice."""
        amount = sum(items.values())
        idem = "qd-" + _h(rider, *sorted(f"{k}={v}" for k, v in items.items()))
        row = self.db.one("SELECT status, payout_id FROM payout_batches WHERE idem_key=?", (idem,))
        if row is None:
            self.db.execute(
                "INSERT INTO payout_batches(idem_key,rider_id,amount,items,mode,day,status,payout_id,attempts,source,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idem_key) DO NOTHING",
                (idem, rider, amount, dumps(items), mode, day, "pending", "", 0, source, now_iso(), now_iso()))
        elif row["status"] == "sent":
            return "sent", row["payout_id"]
        ref = make_reference(dispute_id or idem[-8:], items)
        res = self.ps.create_payout(rider, amount, ref, idem)
        self.trace(rider, "tool_call", "payswift.create_payout",
                   {"rider_id": rider, "amount": amount, "idempotency_key": idem, "reference": ref},
                   {"status": res.status, "payout_id": res.payout_id, "attempts": res.attempts, "http": res.log, "error": res.error})
        self._settle_batch(rider, idem, items, res.status, res.payout_id, res.attempts)
        return res.status, res.payout_id

    def _settle_batch(self, rider, idem, items, status, payout_id, attempts):
        self.db.execute("UPDATE payout_batches SET status=?, payout_id=?, attempts=attempts+?, updated_at=? WHERE idem_key=?",
                        (status, payout_id or "", attempts, now_iso(), idem))
        if status == "sent":
            for k, v in items.items():
                self.db.execute("INSERT INTO paid_items(rider_id,item_key,amount,idem_key) VALUES(?,?,?,?) "
                                "ON CONFLICT(rider_id,item_key) DO NOTHING", (rider, k, v, idem))

    def reconcile_unknown(self, limit=10):
        """Background: payouts whose outcome we never saw (504/timeouts). Retry with the SAME key;
        PaySwift replays the original result instead of paying twice."""
        rows = self.db.query("SELECT * FROM payout_batches WHERE status IN ('pending','unknown') AND attempts<40 "
                             "ORDER BY created_at LIMIT ?", (limit,))
        cutoff = datetime.now(policy.IST).timestamp() - 15   # leave in-flight requests alone
        rows = [b for b in rows if datetime.fromisoformat(b["updated_at"]).timestamp() < cutoff]
        for b in rows:
            items = loads(b["items"], {})
            ref = make_reference(b["idem_key"][-8:], items)
            res = self.ps.create_payout(b["rider_id"], b["amount"], ref, b["idem_key"], budget_s=5)
            self.trace(b["rider_id"], "tool_call", "payswift.reconcile_payout",
                       {"idempotency_key": b["idem_key"], "amount": b["amount"]},
                       {"status": res.status, "payout_id": res.payout_id, "http": res.log})
            self._settle_batch(b["rider_id"], b["idem_key"], items, res.status, res.payout_id, res.attempts)
            if res.status == "failed" or (res.status == "unknown" and b["attempts"] + res.attempts >= 40):
                self._settle_batch(b["rider_id"], b["idem_key"], items, "failed", "", 0)
                self.escalate(b["rider_id"], f"PaySwift payout of ₹{b['amount']} could not be completed ({res.error or 'gave up'}); pay manually")
            elif res.status == "sent":
                self._add_notice(b["rider_id"], {"status": "approved" if b["mode"] == "approval" else "sent", "amount": b["amount"]})

    def _add_notice(self, rider, notice):
        st = self.load_state(rider)
        st.setdefault("notices", []).append(notice)
        self.save_state(rider, st)

    # ---- ops decisions --------------------------------------------------
    def decide(self, item_id, approve: bool, who="ops"):
        row = self.db.one("SELECT * FROM ops_items WHERE id=?", (item_id,))
        if not row:
            return {"error": "not_found"}, 404
        if row["status"] != "pending":
            return {"error": "already_resolved", "status": row["status"]}, 409
        rider = row["rider_id"]
        with self._lock(rider):
            res = {"id": item_id, "status": "approved" if approve else "rejected"}
            if row["type"] == "approval" and approve:
                items = loads(row["items"], [])
                paid, ledger_ok = self._paid_map(rider)
                todo = {}
                for it in items:  # never pay more than is still owed, whatever ops clicks
                    rem = it["owed"] - paid.get(it["item_key"], 0)
                    if rem > 0:
                        todo[it["item_key"]] = rem
                if todo:
                    status, pid = self.pay(rider, todo, "approval", None, "ops_approval:" + item_id, dispute_id=item_id[-8:])
                    res.update(paid=sum(todo.values()), payout_status=status, payout_id=pid)
                    if status == "failed":
                        self.db.execute("UPDATE ops_items SET status='pending' WHERE id=?", (item_id,))
                        return {"error": "payout_failed", "detail": "PaySwift rejected the payout"}, 502
                else:
                    res.update(paid=0, note="already settled in PaySwift")
            self.db.execute("UPDATE ops_items SET status=?, resolved_at=?, resolution=? WHERE id=?",
                            (res["status"], now_iso(), dumps({"by": who, **{k: v for k, v in res.items() if k != 'id'}}), item_id))
            self.trace(rider, "decision", "ops_" + res["status"], {"item": item_id, "amount": row["amount"]}, res)
            if row["type"] == "approval":
                st = self.load_state(rider)
                st["approvals_pending"] = [a for a in st.get("approvals_pending", []) if a["id"] != item_id]
                st.setdefault("notices", []).append({"status": res["status"], "amount": row["amount"]})
                self.save_state(rider, st)
        return res, 200

    # ------------------------------------------------------------------ message handling
    def handle(self, message_id, rider_id, text, received_at):
        rider = norm_rider(rider_id)
        with self._lock(rider):
            row = self.db.one("SELECT status, reply FROM messages WHERE message_id=?", (message_id,))
            if row and row["status"] == "done":
                self.trace(rider, "decision", "duplicate_message_ignored", {"message_id": message_id}, {"replayed_reply": True})
                return row["reply"]
            self.db.execute("INSERT INTO messages(message_id,rider_id,text,received_at,status,reply,created_n) "
                            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(message_id) DO NOTHING",
                            (message_id, rider, text, received_at, "processing", "", self.db.next_n()))
            self.trace(rider, "message_in", "rider_message",
                       {"message_id": message_id, "text": text, "received_at": received_at}, None)
            try:
                reply = self._process(rider, message_id, text, received_at)
            except Exception as e:  # never leave the rider without an answer
                self.trace(rider, "error", "agent_exception", {"message_id": message_id}, {"error": repr(e)})
                self.escalate(rider, f"Agent error while handling message {message_id}: {e!r}", message_id)
                reply = replies.escalated(nlu.parse(text, datetime.now(policy.IST).date(), rider).language)
            self.db.execute("UPDATE messages SET status='done', reply=? WHERE message_id=?", (reply, message_id))
            self.trace(rider, "reply", "reply_to_rider", None, reply)
            return reply

    def _today(self, received_at):
        try:
            return policy.ist_date(received_at)
        except Exception:
            return datetime.now(policy.IST).date()

    def _process(self, rider, mid, text, received_at):
        today = self._today(received_at)
        st = self.load_state(rider)
        notices, st["notices"] = st.get("notices", []), []

        if not self.data.rider_exists(rider):
            self.escalate(rider, f"Message from unknown rider id {rider}", mid)
            self.save_state(rider, st)
            return replies.escalated("hi")

        awaiting = st.get("awaiting")
        p = nlu.parse(text, today, rider, awaiting_day=bool(awaiting))
        self.trace(rider, "decision", "parse_message", {"text": text, "today": today.isoformat()}, p.summary())
        lang = p.language

        # model-assisted parsing only for messages the rules could not read
        if not p.claims and not (p.injection or p.thanks or p.status_question or p.wants_human or p.threat) and len(text.split()) > 3:
            got = llm.extract_claims(text, today)
            if got:
                p.claims, wants_human = got
                p.wants_human = p.wants_human or wants_human
                self.trace(rider, "tool_call", "llm.extract_claims", {"text": text}, [c.as_dict() for c in p.claims])

        parts, esc = [], []
        if p.injection:
            esc.append("Message tried to give the agent instructions / change payout rules (possible manipulation)")
            parts.append(replies.injection_refusal(lang))
            p.claims = [c for c in p.claims if c.day or c.trip_ids]
        if p.other_riders:
            esc.append(f"Rider claimed to be / spoke for another rider ({', '.join(p.other_riders)}) from this number")
            parts.append(replies.own_account_only(lang))
        if p.wants_human:
            esc.append("Rider asked to speak to a person")
        if p.threat:
            esc.append("Rider mentions legal/media/police escalation")

        pending_amounts = [a["amount"] for a in st.get("approvals_pending", [])]
        claims = p.claims
        only_vague_or_generic = all(c.kind == "generic" for c in claims)

        # --- rider pushing to get a queued approval paid now
        if pending_amounts and p.urging and only_vague_or_generic and not p.trip_ids and not awaiting and not p.pushback:
            self.trace(rider, "decision", "urging_pending_approval", {"pending": pending_amounts}, {"action": "explain_only"})
            parts.append(replies.urging_line(sum(pending_amounts), lang))
            claims = []
        # --- rider disputes an answer we already gave
        elif p.pushback and st.get("last_claims") and all(not c.day and not c.trip_ids for c in claims):
            self.trace(rider, "decision", "recheck_previous_claims", {"previous": st["last_claims"]}, None)
            claims = [nlu.Claim(c["kind"], datetime.fromisoformat(c["day"]).date() if c.get("day") else None,
                                c.get("trip_ids", [])) for c in st["last_claims"]]
            p.recheck = True
        elif awaiting and claims:
            for c in claims:  # answer to "which day/order?" inherits what they complained about
                if c.kind == "generic" and awaiting.get("kind") not in (None, "generic"):
                    c.kind = awaiting["kind"]
            st["awaiting"] = None

        vague = [c for c in claims if not c.day and not c.trip_ids]
        concrete = [c for c in claims if c.day or c.trip_ids]
        results, used_auto_now = [], False
        approvals_pending = list(st.get("approvals_pending", []))
        recent = list(st.get("recent_payments", []))

        for claim in concrete:
            findings = self._check_claim(rider, claim, today)
            paid_map, ledger_ok = self._paid_map(rider)
            if not ledger_ok:
                self.trace(rider, "error", "payswift_ledger_unavailable", None, {"fallback": "own payout table"})
            for f in findings:
                if f.verdict == "owed":
                    already = min(f.owed, paid_map.get(f.item_key, 0))
                    f.detail["already_paid_amount"] = already
                    f.detail["remaining"] = f.owed - already
            fd = []
            for f in findings:
                d = f.as_dict()
                d["remaining"] = f.detail.get("remaining", f.owed)
                fd.append(d)
            due = [d for d in fd if d["verdict"] == "owed" and d["remaining"] > 0]
            total = sum(d["remaining"] for d in due)
            res = {"claim": claim, "findings": fd, "total": total, "action": None}
            self.trace(rider, "tool_call", "check_claim", claim.as_dict(),
                       {"findings": [{"kind": d["kind"], "verdict": d["verdict"], "owed": d["owed"], "remaining": d["remaining"], "trip": d["trip_id"]} for d in fd]})

            for d in fd:
                if d["verdict"] in ("not_found", "not_riders_trip", "needs_human"):
                    esc.append(f"Order {d['trip_id']}: {d['verdict'].replace('_', ' ')} (rider claimed it)")
            if claim.kind == "distance" and any(d["verdict"] == "not_owed" for d in fd):
                esc.append("Rider disputes the recorded distance; ops should verify the trip distance")
                res["distance"] = True
            if claim.kind == "penalty" and p.safety and any(d["kind"] == "penalty_correct" for d in fd):
                esc.append("Rider says they cancelled because of an accident; penalty waiver needs a human decision")
                res["safety"] = True

            if total > 0:
                items = {d["item_key"]: d["remaining"] for d in due}
                item_list = [{"item_key": d["item_key"], "owed": d["remaining"], "trip_id": d["trip_id"], "kind": d["kind"], "day": d["day"]} for d in due]
                why = None
                if total > config.AUTO_PAY_LIMIT_CFG:
                    why = "limit"
                elif used_auto_now or self._auto_paid_today(rider, today.isoformat()):
                    why = "daily"
                if why:
                    oid, ost = self.create_approval(rider, total, "Over the ₹200 auto-pay limit" if why == "limit" else
                                                    "Rider already received an auto-payment today (limit: one per day)", item_list, mid)
                    res["action"] = ("approval", total, why, ost)
                    if ost == "pending" and not any(a["id"] == oid for a in approvals_pending):
                        approvals_pending.append({"id": oid, "amount": total})
                else:
                    self.trace(rider, "decision", "auto_pay", {"amount": total, "items": items}, {"within_limit": True, "first_today": True})
                    status, pid = self.pay(rider, items, "auto", today.isoformat(), "dispute:" + mid, dispute_id=mid[-6:])
                    used_auto_now = True
                    res["action"] = ("paid", total, status)
                    if status == "failed":
                        esc.append(f"PaySwift refused the ₹{total} payout; pay manually")
                    recent.append({"amount": total, "status": status, "items": list(items)})
            results.append(res)

        # --- compose reply
        if p.status_question or (p.thanks and not claims):
            if not concrete:
                parts.append(replies.status_line([r for r in recent if r], [a["amount"] for a in approvals_pending], lang))
        for r in results:
            done = [d for d in r["findings"] if d["verdict"] == "owed" and d["remaining"] <= 0]
            if len(done) >= 2:   # one line instead of one per order
                lines = [replies.already_sent_summary(sum(d["owed"] for d in done), len(done), lang)]
                lines += [replies.explain(d, lang) for d in r["findings"] if d not in done]
            else:
                lines = [replies.explain(d, lang) for d in r["findings"]]
            lines = [l for l in lines if l]
            if r["findings"] and all(d["verdict"] == "not_owed" for d in r["findings"]) and len(lines) > 3:
                lines = lines[:3] + ([f"(+{len(lines) - 3} more checked, all correct)"] if lang == "en" else [f"(aur {len(lines) - 3} bhi check kiye, sab sahi)"])
            if r.get("distance"):
                lines.append(replies.distance_line(lang))
            if r.get("safety"):
                lines.append(replies.safety_line(lang))
            act = r["action"]
            if act and act[0] == "paid":
                lines.append(replies.paid_line(act[1], act[2], lang))
            elif act and act[0] == "approval":
                if act[3] == "rejected":
                    lines.append("Ye request ops team pehle dekh chuki hai aur approve nahi kar payi." if lang == "hi" else "The ops team already reviewed this and did not approve it.")
                else:
                    lines.append(replies.approval_line(act[1], act[2], lang))
            parts.append(" ".join(lines))
        found_total = sum(r["total"] for r in results) + sum(d["owed"] - d["remaining"] for r in results for d in r["findings"] if d["verdict"] == "owed")
        if p.short_by and results and p.short_by > found_total and not p.recheck:
            parts.append(replies.amount_mismatch(p.short_by, found_total, lang))
        if p.recheck:
            parts.append(replies.pushback_line(lang))
            esc.append("Rider disputes our records after we re-checked and explained")
        if vague and not concrete:
            # nothing checkable yet: ask once, and remember what they complained about
            st["awaiting"] = {"kind": vague[0].kind if vague[0].kind in ("surge", "distance", "incentive", "penalty") else "generic"}
            parts.append(replies.ask_details(lang))
        elif not parts and not claims and not (p.thanks or p.status_question):
            parts.append(replies.ask_details(lang))
        for reason in dict.fromkeys(esc):
            self.escalate(rider, reason, mid)
        if esc and not any("ops team" in x for x in parts):
            parts.append(replies.escalated(lang))

        reply = " ".join(x for x in parts if x).strip()
        if notices:
            prefix = " ".join(replies.notice_line(n, lang) for n in notices if n["status"] in ("approved", "rejected"))
            reply = (prefix + " " + reply).strip()

        # remember outcomes for follow-ups
        st["approvals_pending"] = [a for a in approvals_pending if self._still_pending(a["id"])]
        st["recent_payments"] = recent[-5:]
        if results:
            st["last_claims"] = [r["claim"].as_dict() for r in results]
        if concrete:
            st["awaiting"] = None
        self.save_state(rider, st)

        polished = llm.polish(reply, text) if reply and llm.enabled() else None
        if polished:
            self.trace(rider, "tool_call", "llm.polish_reply", {"draft": reply}, {"final": polished})
            reply = polished
        return reply or replies.ask_details(lang)

    def _still_pending(self, oid):
        r = self.db.one("SELECT status FROM ops_items WHERE id=?", (oid,))
        return bool(r and r["status"] == "pending")

    def _check_claim(self, rider, claim, today):
        out = []
        if claim.trip_ids:
            for tid in claim.trip_ids:
                out.append(disputes.check_trip(self.data, rider, tid, today, claim.kind))
            return out
        day = claim.day
        if claim.kind == "incentive":
            return [disputes.check_incentive(self.data, rider, day, today)]
        if claim.kind == "penalty":
            return disputes.check_penalties_on_day(self.data, rider, day, today)
        if claim.kind in ("surge", "distance", "missing"):
            found = disputes.check_day(self.data, rider, day, today)
            return found or [disputes.Finding("day", f"day:{rider}:{day.isoformat()}", 0,
                             "out_of_window" if not policy.in_window(day, today) else "not_owed", day.isoformat())]
        found = disputes.scan_day(self.data, rider, day, today)
        return found or [disputes.Finding("day", f"day:{rider}:{day.isoformat()}", 0,
                         "out_of_window" if not policy.in_window(day, today) else "not_owed", day.isoformat())]

