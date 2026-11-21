"""Reply wording. Facts come from code; this module only renders them (Hinglish or English).
An LLM may rephrase the result (app/llm.py) but can never change the numbers in it."""
from datetime import date


def d_txt(day: str, lang: str) -> str:
    dt = date.fromisoformat(day)
    return dt.strftime("%d %b").lstrip("0")


def _surge(s, lang):
    return (f", {s:g}x surge" if s and s > 1 else "")


def explain(f: dict, lang: str) -> str:
    """One finding (a Finding.as_dict()) -> one sentence."""
    hi = lang == "hi"
    k, v = f["kind"], f["verdict"]
    tid, det = f.get("trip_id", ""), f.get("detail", {})
    day = d_txt(f["day"], lang) if f.get("day") else ""
    if v == "out_of_window":
        return (f"{day} ka dispute 7 din se purana hai; hum sirf pichhle 7 din ke disputes dekh sakte hain." if hi
                else f"{day} is older than 7 days; we can only review disputes from the last 7 days.")
    if v == "not_found":
        return (f"Order {tid} humare record mein nahi mila." if hi else f"We couldn't find order {tid} in our records.")
    if v == "not_riders_trip":
        return (f"Order {tid} aapke account se juda hua nahi dikh raha, isliye main ispe kuch nahi kar sakta." if hi
                else f"Order {tid} isn't linked to your account, so I can't act on it.")
    if v == "needs_human":
        return (f"Order {tid} ke record mein gadbad hai, ops team ise dekhegi." if hi
                else f"Order {tid} has a data issue that the ops team needs to check.")
    owed_paid = det.get("already_paid_amount", 0)
    if v == "owed" and f.get("remaining", f["owed"]) <= 0 and owed_paid:
        return (f"{('Order ' + tid) if tid else day} ka ₹{owed_paid} aapko pehle hi bheja ja chuka hai." if hi
                else f"₹{owed_paid} for {('order ' + tid) if tid else day} was already sent to you earlier.")
    if k == "trip_underpaid":
        s = _surge(det.get("surge"), lang)
        return (f"Order {tid} ({day}, {det['km']:g} km{s}): ₹{det['expected']} banta tha, ₹{det['paid']} mila. Farak ₹{f['owed']}." if hi
                else f"Order {tid} ({day}, {det['km']:g} km{s}): ₹{det['expected']} was due, ₹{det['paid']} was paid. Short by ₹{f['owed']}.")
    if k == "trip_unpaid":
        return (f"Order {tid} ({day}, {det['km']:g} km) ka ₹{det['expected']} payout mein aaya hi nahi." if hi
                else f"Order {tid} ({day}, {det['km']:g} km): ₹{det['expected']} was never paid out.")
    if k == "incentive":
        n = det.get("completed_trips", 0)
        if v == "owed":
            return (f"{day} ko aapke {n} trips complete hue the, isliye ₹150 incentive banta tha jo nahi mila." if hi
                    else f"You completed {n} trips on {day}, so the ₹150 incentive was due but not paid.")
        if det.get("already_paid"):
            return (f"{day} ka ₹150 incentive aapko pehle hi mil chuka hai." if hi
                    else f"The ₹150 incentive for {day} was already paid to you.")
        return (f"{day} ko hamare record mein {n} completed trips hain; incentive 12 trips pe milta hai, isliye nahi bana." if hi
                else f"Our records show {n} completed trips on {day}; the incentive needs 12, so it wasn't due.")
    if k == "penalty_duplicate":
        return (f"Order {tid} ({day}) pe cancel penalty do baar kata tha; ₹{f['owed']} extra kata hai." if hi
                else f"The cancellation penalty for order {tid} ({day}) was charged twice; ₹{f['owed']} too much was deducted.")
    if k == "penalty_wrongful":
        return (f"Order {tid} customer ne cancel kiya tha, phir bhi penalty kata (₹{f['owed']})." if hi
                else f"Order {tid} was cancelled by the customer, yet a penalty (₹{f['owed']}) was deducted.")
    if k == "penalty_correct":
        return (f"Order {tid} aapne cancel kiya tha; policy ke hisaab se ₹10 penalty lagti hai, isliye woh sahi kata hai." if hi
                else f"You cancelled order {tid}; policy charges a ₹10 penalty, so that deduction is correct.")
    if k == "trip_cancelled_customer":
        return (f"Order {tid} customer ne cancel kiya tha aur koi penalty nahi kata." if hi
                else f"Order {tid} was cancelled by the customer and no penalty was charged.")
    if k == "trip_ok":
        sg = det.get("surge", 1)
        sx = (f"{sg:g}x surge ke saath" if sg > 1 else "1.0x yaani bina surge ke") if hi else (f"with {sg:g}x surge" if sg > 1 else "with no surge (1.0x)")
        return (f"Order {tid} ({day}, {det['km']:g} km, {sx}): ₹{det['expected']} banta hai aur ₹{det['paid']} mila, payout sahi hai." if hi
                else f"Order {tid} ({day}, {det['km']:g} km, {sx}): ₹{det['expected']} is due and ₹{det['paid']} was paid, so the payout is correct.")
    if k == "penalty":
        n = det.get("rider_cancellations", 0)
        return (f"{day} ko cancel penalty mein koi galti nahi mili." if hi else f"No cancellation-penalty error found on {day}.")
    if k == "day":
        return (f"{day} ke saare completed trips ka payout sahi mila, koi kami nahi mili." if hi
                else f"All completed trips on {day} were paid correctly; I found no shortfall.")
    return ""


def paid_line(amount: int, status: str, lang: str) -> str:
    hi = lang == "hi"
    if status == "sent":
        return (f"₹{amount} PaySwift pe bhej diya gaya hai." if hi else f"₹{amount} has been sent via PaySwift.")
    if status == "unknown":
        return (f"₹{amount} ka payment process ho raha hai; PaySwift se confirm hote hi aapko mil jayega." if hi
                else f"The ₹{amount} payment is being processed; it will reach you as soon as PaySwift confirms.")
    return (f"₹{amount} abhi bhej nahi paaye; ops team isse manually dekhegi." if hi
            else f"I couldn't send the ₹{amount} just now; the ops team will handle it manually.")


def approval_line(amount: int, why: str, lang: str) -> str:
    hi = lang == "hi"
    if why == "limit":
        return (f"₹{amount} ₹200 ki auto-pay limit se bada hai, isliye ops team ki approval ke liye bhej diya hai; approve hote hi bhej diya jayega." if hi
                else f"₹{amount} is above the ₹200 auto-pay limit, so it has gone to the ops team for approval; it will be sent once approved.")
    return (f"Ek din mein ek hi auto-payment ho sakta hai, isliye ₹{amount} ops team ki approval ke liye bhej diya hai; approve hote hi aa jayega." if hi
            else f"Only one auto-payment is allowed per day, so ₹{amount} has gone to the ops team for approval; it will be sent once approved.")


def ask_details(lang: str, kind: str = "generic") -> str:
    if lang == "hi":
        return "Kaunse din ya kaunse order ka payout galat laga? Date ya order ID (jaise T123456) bata dijiye."
    return "Which day or which order is the problem? Please send the date or the order ID (for example T123456)."


def escalated(lang: str) -> str:
    return ("Aapki baat ops team ko bhej di hai, wo dekhkar aapse sampark karenge." if lang == "hi"
            else "I've passed this to the ops team; they will review it and get back to you.")


def own_account_only(lang: str) -> str:
    return ("Main sirf is number se jude account ke baare mein madad kar sakta hoon, aur kisi ke kehne par payout approve nahi hota." if lang == "hi"
            else "I can only help with the account linked to this number, and I can't approve payouts on instruction.")


def injection_refusal(lang: str) -> str:
    return ("Main apne payout rules nahi badal sakta; har dispute sirf trips aur payout record ke hisaab se check hota hai. Apni specific problem (date ya order ID) bataiye." if lang == "hi"
            else "I can't change the payout rules; every dispute is checked against trip and payout records only. Please tell me the specific problem (date or order ID).")


def amount_mismatch(claimed: int, found: int, lang: str) -> str:
    return (f"Aapne ₹{claimed} kam bataya, lekin record mein mujhe sirf ₹{found} ka farak mila. Baaki ke liye aur order ID ya date bata dijiye." if lang == "hi"
            else f"You mentioned being ₹{claimed} short, but the records only show ₹{found}. If other orders are affected, please send their IDs or dates.")


def safety_line(lang: str) -> str:
    return ("Accident ki baat sunkar bura laga; penalty maaf karne ka faisla ops team karti hai, maine unhe aapka case bhej diya hai." if lang == "hi"
            else "I'm sorry to hear about the accident. Waiving a penalty is the ops team's call, and I've sent them your case.")


def distance_line(lang: str) -> str:
    return ("Recorded distance ke hisaab se payout sahi bana hai; aapko distance galat lagta hai to ops team use verify karegi, maine bhej diya hai." if lang == "hi"
            else "The payout matches the recorded distance. Since you believe the distance is wrong, the ops team will verify it; I've sent it to them.")


def pushback_line(lang: str) -> str:
    return ("Maine dobara check kiya, record wahi hai. Aapko lagta hai record galat hai, isliye ise ops team ko bhej diya hai." if lang == "hi"
            else "I re-checked and the record is the same. As you believe it's wrong, I've sent it to the ops team to review.")


def urging_line(amount: int, lang: str) -> str:
    amt = f"₹{amount} " if amount else ""
    return (f"Samajh sakta hoon. {amt}ops team ki approval ke liye pending hai, main ise khud approve nahi kar sakta; approve hote hi aa jayega." if lang == "hi"
            else f"I understand. The {amt}is waiting for ops approval and I can't approve it myself; it will be sent as soon as it's approved.")


def status_line(recent: list, pending: list, lang: str) -> str:
    hi = lang == "hi"
    parts = []
    for r in recent[-3:]:
        parts.append((f"₹{r['amount']} PaySwift pe process ho chuka hai" if r["status"] == "sent" else f"₹{r['amount']} abhi process ho raha hai") if hi
                     else (f"₹{r['amount']} has been processed on PaySwift" if r["status"] == "sent" else f"₹{r['amount']} is still being processed"))
    for p in pending:
        parts.append(f"₹{p} ops approval ke liye pending hai" if hi else f"₹{p} is waiting for ops approval")
    if not parts:
        return ("Abhi koi pending request nahi hai. Koi aur dikkat ho to date ya order ID ke saath bata dijiye." if hi
                else "There's nothing pending right now. If something else is wrong, send me the date or order ID.")
    return ("; ".join(parts) + "." if hi else "; ".join(parts) + ".")


def notice_line(n: dict, lang: str) -> str:
    hi = lang == "hi"
    if n["status"] == "approved":
        return (f"Update: ops team ne ₹{n['amount']} approve kar diya hai." if n["amount"] else "Update: ops team ne aapka request approve kar diya hai.") if hi \
            else (f"Update: the ops team approved ₹{n['amount']}." if n["amount"] else "Update: the ops team approved your request.")
    return ("Update: ops team ne aapka request dekha, lekin approve nahi kar paayi." if hi
            else "Update: the ops team reviewed your request but could not approve it.")


def already_sent_summary(amount: int, n: int, lang: str) -> str:
    return (f"{n} items ka ₹{amount} aapko pehle hi bheja ja chuka hai." if lang == "hi"
            else f"₹{amount} for {n} items was already sent to you earlier.")
