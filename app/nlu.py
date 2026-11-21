"""Rule-based understanding of rider messages (Hinglish + English).

This is the deterministic baseline. When an LLM is configured, app/llm.py is only consulted for
messages this parser cannot make sense of, and its output is validated against the message text.
Either way the result is a plain structure of *claims*; nothing in here decides money.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12, "january": 1, "february": 2,
          "march": 3, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9,
          "october": 10, "november": 11, "december": 12}
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

TRIP_RE = re.compile(r"\bT\s?-?(\d{4,7})\b", re.I)
RIDER_RE = re.compile(r"\bR\s?-?(\d{1,3})\b", re.I)

DAY_PATTERNS = [
    # "Sep 19", "September 19th"
    re.compile(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*(?:orders?|trips?|baar|km|rs|rupa))", re.I),
    # "19 sept", "19th september"
    re.compile(rf"(?<![\d.])(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?({MONTH_RE})\b", re.I),
    # "19th"
    re.compile(r"(?<![\d.])(\d{1,2})(?:st|nd|rd|th)\b", re.I),
    # "20 tarikh", "20 ko", "20 wala", "20 ke din"
    re.compile(r"(?<![\d.])(\d{1,2})\s*(?:tarikh|tareekh|taareekh|date|ko|wala|wali|waala|waale|ke din|ka din)\b(?!\.\d)", re.I),
    # "tarikh 20", "date 20"
    re.compile(r"\b(?:tarikh|tareekh|date)\s*(\d{1,2})\b", re.I),
]

REL_DAYS = [
    (re.compile(r"\b(parso|parson|day before yesterday)\b", re.I), 2),
    (re.compile(r"\b(kal|yesterday|kl)\b", re.I), 1),
    (re.compile(r"\b(aaj|today|abhi aaj)\b", re.I), 0),
]

KIND_PATTERNS = [
    ("penalty", re.compile(r"penalt|cancel|kata\b|kate\b|kat gaya|kaat|deduct|fine\b", re.I)),
    ("incentive", re.compile(r"incentive|bonus|inc\b", re.I)),
    ("surge", re.compile(r"surge|multiplier|\b\d(?:\.\d)?\s*x\b", re.I)),
    ("distance", re.compile(r"distance|\bkm\b|kilomet|doori|dooree|\bdur\b", re.I)),
    ("missing", re.compile(r"nahi (?:aaya|aayi|aya|mila|mili)|nahin (?:aaya|mila)|hi nahi|missing|not (?:paid|received|credited|come)|unpaid|didn.?t (?:get|receive)|paisa nahi|payment nahi", re.I)),
    ("generic", re.compile(r"payout|paisa|paise|payment|pay\b|kam\b|galat|wrong|short|less|amount|rupay|rupees|salary|earning", re.I)),
]

INJECTION_RE = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier)|disregard|system\s*:|you are now|"
    r"developer mode|jailbreak|override|new instructions?|act as|pretend|"
    r"(?:saare|sabhi|all)\s+(?:disputes?|claims?)\s+approve|approve (?:all|everything)|"
    r"as an? (?:admin|ops|manager|executive)|i am (?:the )?(?:admin|ops|manager)|mai(?:n)? admin", re.I)
HUMAN_RE = re.compile(r"\b(manager|supervisor|human|insaan|insan|ops team|executive|kisi se baat|talk to (?:a )?(?:person|someone)|call me|call karo)\b", re.I)
THREAT_RE = re.compile(r"consumer court|legal|lawyer|police|media|twitter|report kar|complaint karunga|court", re.I)
SAFETY_RE = re.compile(r"accident|hospital|injur|chot|emergency|bimar|sick|ambulance|police", re.I)
PUSHBACK_RE = re.compile(r"nahi nahi|nahin nahin|dobara|dubara|phir se|fir se|re-?check|check again|maine .* kiye|\bi (?:did|completed|made|was)\b|jhooth|wrong again|\bstill\b|abhi bhi|phir bhi", re.I)
URGE_RE = re.compile(r"abhi|turant|jaldi|immediately|right now|\bnow\b|urgent|fauran|de do|dedo|pay me|bhej do", re.I)
THANKS_RE = re.compile(r"thik hai|theek hai|\bok\b|okay|thanks|thank you|shukriya|dhanyavad|accha|achha|got it|samajh gaya|shukria", re.I)
STATUS_RE = re.compile(r"kab tak|kab aayega|kab milega|when will|how long|kitne (?:der|din|time)|status|aaya kya|mila kya|aa gaya", re.I)
HINGLISH_RE = re.compile(r"\b(bhai|nahi|nahin|hai|hain|kya|ka|ki|ke|ko|aaya|mila|kiye|kiya|maine|mera|meri|tarikh|wala|karo|kal|payout kam|paisa|paise|rupay|aur|isliye|kyun|abhi|jaldi|bhi|thik|theek|accha)\b", re.I)


@dataclass
class Claim:
    kind: str                       # penalty | incentive | surge | distance | missing | generic
    day: date | None = None
    trip_ids: list = field(default_factory=list)
    claimed_count: int | None = None
    text: str = ""

    def as_dict(self):
        return {"kind": self.kind, "day": self.day.isoformat() if self.day else None,
                "trip_ids": self.trip_ids, "claimed_count": self.claimed_count}


@dataclass
class Parsed:
    claims: list = field(default_factory=list)
    other_riders: list = field(default_factory=list)
    trip_ids: list = field(default_factory=list)
    injection: bool = False
    wants_human: bool = False
    threat: bool = False
    safety: bool = False
    pushback: bool = False
    urging: bool = False
    thanks: bool = False
    status_question: bool = False
    claimed_amount: int | None = None
    short_by: int | None = None
    bare_day: date | None = None
    language: str = "hi"
    recheck: bool = False

    def summary(self):
        d = {k: v for k, v in self.__dict__.items() if k != "claims" and v not in (False, None, [], "")}
        d["claims"] = [c.as_dict() for c in self.claims]
        if self.bare_day:
            d["bare_day"] = self.bare_day.isoformat()
        return d


def _to_date(day: int, month: int | None, today: date) -> date | None:
    try:
        if month:
            d = date(today.year, month, day)
            if d > today:
                d = date(today.year - 1, month, day)
            return d
        d = date(today.year, today.month, day) if day <= 31 else None
    except ValueError:
        d = None
    if d is None or d > today:
        # "28" said on the 3rd means last month
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        try:
            d = date(y, m, day)
        except ValueError:
            return None
    return d


def extract_days(text: str, today: date):
    """Returns list of (span_start, date). Deliberately conservative: bare numbers are not days."""
    found, taken = [], []

    def free(s, e):
        return all(e <= a or s >= b for a, b in taken)

    for i, pat in enumerate(DAY_PATTERNS):
        for m in pat.finditer(text):
            s, e = m.span()
            if not free(s, e):
                continue
            g = m.groups()
            if i == 0:
                month, d = MONTHS[g[0].lower()], int(g[1])
            elif i == 1:
                d, month = int(g[0]), MONTHS[g[1].lower()]
            else:
                d, month = int(g[0]), None
            if not 1 <= d <= 31:
                continue
            dt = _to_date(d, month, today)
            if dt:
                found.append((s, dt))
                taken.append((s, e))
    for pat, back in REL_DAYS:
        for m in pat.finditer(text):
            s, e = m.span()
            if free(s, e):
                found.append((s, today - timedelta(days=back)))
                taken.append((s, e))
    return sorted(found)


def _kind_of(clause: str, claimed_count):
    for kind, pat in KIND_PATTERNS:
        if pat.search(clause):
            return kind
    if claimed_count and claimed_count >= 12:
        return "incentive"
    return None


def _count_of(clause: str):
    m = re.search(r"(?<![\d.])(\d{1,3})\s*(?:se zyada\s*|\+\s*)?(?:orders?|trips?|deliver(?:y|ies)|order)\b", clause, re.I)
    return int(m.group(1)) if m else None


def parse(text: str, today: date, own_rider: str, awaiting_day: bool = False) -> Parsed:
    p = Parsed()
    t = text.strip()
    p.language = "hi" if HINGLISH_RE.search(t) else "en"
    p.injection = bool(INJECTION_RE.search(t))
    p.wants_human = bool(HUMAN_RE.search(t))
    p.threat = bool(THREAT_RE.search(t))
    p.safety = bool(SAFETY_RE.search(t))
    p.pushback = bool(PUSHBACK_RE.search(t))
    p.urging = bool(URGE_RE.search(t))
    p.thanks = bool(THANKS_RE.search(t))
    p.status_question = bool(STATUS_RE.search(t))
    p.trip_ids = list(dict.fromkeys("T" + m.group(1) for m in TRIP_RE.finditer(t)))
    for m in RIDER_RE.finditer(t):
        rid = f"R{int(m.group(1)):03d}"
        if rid != own_rider and rid not in p.other_riders:
            p.other_riders.append(rid)
    amt = re.search(r"(?:₹|rs\.?|inr)\s*(\d{1,6})|(\d{1,6})\s*(?:rupay|rupaye|rupees|rs\b|₹)", t, re.I)
    if amt:
        p.claimed_amount = int(amt.group(1) or amt.group(2))
    else:
        m = re.search(r"(?<![\d.])(\d{3,6})\s*(?:kam|less|short|zyada)", t, re.I)
        if m:
            p.claimed_amount = int(m.group(1))

    m = re.search(r"(?<![\d.])(\d{2,6})\s*(?:rupay|rupaye|rupees|rs\.?|₹)?\s*(?:kam|less|short|missing)\b", t, re.I)
    if m:
        p.short_by = int(m.group(1))

    days = extract_days(t, today)
    if awaiting_day and not days:
        m = re.fullmatch(r"\W*(\d{1,2})(?:st|nd|rd|th)?\W*(?:wala|wali|tarikh|ko)?\W*", t, re.I)
        if m:
            dt = _to_date(int(m.group(1)), None, today)
            if dt:
                p.bare_day = dt
                days = [(0, dt)]

    # split into clauses and build claims
    clauses = [c for c in re.split(r"\b(?:aur|and|also|plus|lekin|but|phir)\b|(?<!\d)\.|\.(?!\d)|[;!?\n:]|,", t, flags=re.I) if c.strip()]
    pos = 0
    claims = []
    for c in clauses:
        start = t.find(c, pos)
        pos = start + len(c) if start >= 0 else pos
        cdays = extract_days(c, today)
        ctrips = ["T" + m.group(1) for m in TRIP_RE.finditer(c)]
        count = _count_of(c)
        kind = _kind_of(c, count)
        if not (kind or cdays or ctrips):
            continue
        if kind is None and (cdays or ctrips) :
            kind = "generic" if cdays and not ctrips else "missing"
        if not kind:
            continue
        if kind == "generic" and not cdays and not ctrips and not p.pushback and count is None:
            # keep: vague complaint, caller will ask for details
            pass
        claims.append(Claim(kind, cdays[0][1] if cdays else None, ctrips, count, c.strip()))

    # trip ids / days alone in a clause (e.g. "T795007, T206956") extend the previous claim
    merged = []
    for cl in claims:
        if merged and cl.kind in ("missing", "generic") and not cl.day and cl.trip_ids \
                and not merged[-1].day is None and merged[-1].kind in ("missing", "generic", "surge", "distance"):
            merged[-1].trip_ids += [x for x in cl.trip_ids if x not in merged[-1].trip_ids]
        elif merged and cl.kind == "missing" and not cl.day and not cl.trip_ids and merged[-1].kind == "generic" and not merged[-1].trip_ids:
            merged[-1].kind = "missing"
        else:
            merged.append(cl)
    claims = merged

    msg_days = {d for _, d in days}
    sole_day = next(iter(msg_days)) if len(msg_days) == 1 else None
    for cl in claims:
        if cl.day is None and not cl.trip_ids and sole_day:
            cl.day = sole_day
    # vague clauses ("payout kam aaya", "penalty kyun kaata") add nothing when a more specific claim exists
    final = []
    for cl in claims:
        vague = cl.day is None and not cl.trip_ids
        others = [o for o in claims if o is not cl]
        if cl.kind == "generic" and any(o.kind != "generic" and (o.day is None or cl.day is None or o.day == cl.day)
                                        for o in others):
            continue
        if vague and any(o.kind == cl.kind and (o.trip_ids or o.day) for o in others):
            continue
        final.append(cl)
    # a generic clause + a lone trip id in the message: treat as a claim on that trip
    if not final and p.trip_ids:
        final.append(Claim("missing", None, list(p.trip_ids), None, t))
    p.claims = final
    # drop duplicate claims
    seen, uniq = set(), []
    for cl in p.claims:
        key = (cl.kind if cl.kind in ("penalty", "incentive") else "trip" if cl.trip_ids else cl.kind,
               cl.day, tuple(cl.trip_ids))
        if key not in seen:
            seen.add(key)
            uniq.append(cl)
    p.claims = uniq
    return p
