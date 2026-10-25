"""Actions the support assistant is allowed to take.

Everything here either reads data or performs a safe, reversible step.
There is deliberately no refund-issuing function: refunds are approved only
by a named human reviewer through finance.py, which the assistant never imports.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from store import COURSES, DOUBT_SESSION, IST, KNOWN_ISSUES, MOCK_TEST, REFUND_POLICY, Store, now

REVERSAL_WINDOW = timedelta(hours=48)
SLA = {"high": "1 hour", "normal": "4 hours"}


# ---------- formatting helpers ----------

def fmt_money(amount: float) -> str:
    return f"\u20b9{amount:,.0f}"


def fmt_time(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_day(dt: datetime) -> str:
    delta = (dt.date() - now().date()).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    return f"{dt:%a}, {dt.day} {dt:%b}"


def fmt_when(dt: datetime) -> str:
    return f"{fmt_day(dt)} at {fmt_time(dt)}"


def mask_email(email: str) -> str:
    user, domain = email.split("@")
    return f"{user[0]}***{user[-1]}@{domain}"


def mask_phone(phone: str) -> str:
    digits = phone.replace(" ", "")
    return f"{digits[:3]} ******{digits[-4:]}"


# ---------- course content ----------

def _next_weekly(weekdays: list[int], hm: tuple[int, int], after: datetime) -> datetime:
    for add in range(8):
        day = (after + timedelta(days=add)).date()
        if day.weekday() in weekdays:
            dt = datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=IST)
            if dt > after:
                return dt
    raise ValueError("no upcoming slot")


def course_schedule(course_id: str) -> dict:
    c = COURSES[course_id]
    t = now()
    next_class = _next_weekly(c["days"], c["time"], t)
    return {
        "course": c["name"],
        "faculty": c["faculty"],
        "next_class": next_class,
        "topic": c["topics"][next_class.toordinal() % len(c["topics"])],
        "syllabus": c["topics"],
        "next_mock_test": _next_weekly([MOCK_TEST["weekday"]], MOCK_TEST["time"], t),
        "next_doubt_session": _next_weekly([DOUBT_SESSION["weekday"]], DOUBT_SESSION["time"], t),
    }


def match_course(text: str, course_ids: list[str]) -> str | None:
    lowered = text.lower()
    for cid in course_ids:
        c = COURSES[cid]
        if c["name"].lower() in lowered:
            return cid
        if any(re.search(rf"\b{re.escape(a)}\b", lowered) for a in c["aliases"]):
            return cid
    return None


# ---------- video / app ----------

def match_known_issue(device: dict) -> str | None:
    if device["os"] == "Android" and int(device["os_version"]) >= 11:
        return "BP-1423"
    return None


def record_known_issue_hit(store: Store, issue_id: str) -> int:
    store.known_issue_hits[issue_id] += 1
    return store.known_issue_hits[issue_id]


# ---------- payments ----------

PAYMENT_PRIORITY = {"captured_unfulfilled": 0, "failed_debited": 1, "reversed": 2}


def find_payment_issue(store: Store, student_id: str) -> dict | None:
    candidates = [p for p in store.payments if p["student_id"] == student_id and p["status"] in PAYMENT_PRIORITY]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (PAYMENT_PRIORITY[p["status"]], -p["created_at"].timestamp()))[0]


def grant_access_for_captured_payment(store: Store, student: dict, payment: dict) -> dict:
    """Money was captured but enrollment failed: fulfil the order. No money moves."""
    assert payment["status"] == "captured_unfulfilled"
    enrollment = {
        "course_id": payment["course_id"],
        "order_id": store.next_id("ORD"),
        "purchased_at": payment["created_at"],
        "progress": 0.0,
        "amount_paid": payment["amount"],
        "payer_name_matches": True,
        "refunded": False,
    }
    student["enrollments"].append(enrollment)
    payment["status"] = "captured"
    payment["order_id"] = enrollment["order_id"]
    store.audit("AI assistant", "Granted course access",
                f"{student['name']} - {COURSES[payment['course_id']]['name']} (payment {payment['id']} was captured)")
    return enrollment


def schedule_reversal_followup(store: Store, payment: dict) -> dict:
    for f in store.followups:
        if f["payment_id"] == payment["id"] and f["status"] == "scheduled":
            return f
    followup = {"payment_id": payment["id"], "student_id": payment["student_id"],
                "due_at": (payment["created_at"] + REVERSAL_WINDOW).isoformat(), "status": "scheduled"}
    store.followups.append(followup)
    return followup


# ---------- login ----------

def send_password_reset(store: Store, student: dict) -> str:
    text = (f"BrightPath: Reset your password here: https://learn.brightpath.in/reset/"
            f"{student['id'].lower()}x7 (valid for 15 minutes). Didn't ask for this? Ignore this message.")
    store.notify(student["id"], "WhatsApp + Email", text)
    return text


def resend_login_otp(store: Store, student: dict) -> str:
    text = "BrightPath: Your login OTP is 482913. It is valid for 10 minutes. Never share it with anyone."
    store.notify(student["id"], "SMS + Email", text)
    return text


# ---------- tickets ----------

def create_ticket(store: Store, student: dict, intent: str, queue: str, priority: str, summary: str,
                  collected: dict, transcript: list[dict], source: str = "AI assistant") -> dict:
    ticket = {
        "id": store.next_id("T"),
        "student_id": student["id"],
        "student_name": student["name"],
        "intent": intent,
        "queue": queue,
        "priority": priority,
        "sla": SLA[priority],
        "summary": summary,
        "collected": collected,
        "transcript": [dict(m) for m in transcript],
        "created_at": now().isoformat(),
        "status": "open",
        "source": source,
    }
    store.tickets.insert(0, ticket)
    return ticket


# ---------- refunds (prepare only; never issue) ----------

def evaluate_refund_policy(enrollment: dict) -> dict:
    p = REFUND_POLICY
    days = (now() - enrollment["purchased_at"]).days
    progress = enrollment["progress"]
    paid = enrollment["amount_paid"]
    facts = f"Bought {days} days ago, {progress:.0%} of the course watched."
    if days <= p["full_refund_days"] and progress <= p["full_refund_max_progress"]:
        return {"verdict": "Eligible: full refund", "eligible_amount": paid, "days_since_purchase": days,
                "explanation": f"{facts} Within {p['full_refund_days']} days and under "
                               f"{p['full_refund_max_progress']:.0%} watched."}
    if days <= p["partial_refund_days"] and progress <= p["partial_refund_max_progress"]:
        return {"verdict": f"Eligible: {p['partial_refund_share']:.0%} refund",
                "eligible_amount": round(paid * p["partial_refund_share"]), "days_since_purchase": days,
                "explanation": f"{facts} Within {p['partial_refund_days']} days and under "
                               f"{p['partial_refund_max_progress']:.0%} watched."}
    reasons = []
    if days > p["partial_refund_days"]:
        reasons.append(f"more than {p['partial_refund_days']} days since purchase")
    if progress > p["partial_refund_max_progress"]:
        reasons.append(f"more than {p['partial_refund_max_progress']:.0%} of the course watched")
    return {"verdict": "Outside standard policy", "eligible_amount": 0, "days_since_purchase": days,
            "explanation": f"{facts} Not eligible: {' and '.join(reasons)}."}


def score_refund_risk(student: dict, enrollment: dict) -> dict:
    score, signals = 0, []
    if student["prior_refunds"] >= 2:
        score += 40
        signals.append(f"{student['prior_refunds']} refunds in the last 12 months")
    elif student["prior_refunds"] == 1:
        score += 15
        signals.append("1 refund in the last 12 months")
    if student["account_age_days"] < 30:
        score += 20
        signals.append(f"Account is only {student['account_age_days']} days old")
    if enrollment["progress"] > 0.5:
        score += 25
        signals.append(f"{enrollment['progress']:.0%} of the course already watched")
    if not enrollment["payer_name_matches"]:
        score += 20
        signals.append("Name on the payment method differs from the account name")
    if student["chargebacks"]:
        score += 30
        signals.append(f"{student['chargebacks']} previous chargeback(s)")
    level = "Low" if score < 25 else "Medium" if score < 50 else "High"
    return {"score": score, "level": level, "signals": signals or ["No risk signals found"]}


def recommend(policy: dict, risk: dict) -> str:
    if risk["level"] == "High":
        return "Do not approve without verification: high fraud risk"
    if policy["eligible_amount"] and risk["level"] == "Low":
        return f"Approve {fmt_money(policy['eligible_amount'])}"
    if policy["eligible_amount"]:
        return f"Approve {fmt_money(policy['eligible_amount'])} after a quick check"
    return "Reject under policy, or approve as a goodwill exception"


def create_refund_case(store: Store, student: dict, enrollment: dict, reason: str,
                       transcript: list[dict]) -> dict:
    policy = evaluate_refund_policy(enrollment)
    risk = score_refund_risk(student, enrollment)
    course = COURSES[enrollment["course_id"]]["name"]
    notes = []
    issue = match_known_issue(student["device"])
    if issue and "technical" in reason.lower():
        notes.append(f"Student's device matches known bug {issue} ({KNOWN_ISSUES[issue]['title']}). "
                     "Refund may be caused by our product defect.")
    case = {
        "id": store.next_id("RF"),
        "student_id": student["id"],
        "student_name": student["name"],
        "course": course,
        "order_id": enrollment["order_id"],
        "amount_paid": enrollment["amount_paid"],
        "purchased_at": enrollment["purchased_at"].isoformat(),
        "progress": enrollment["progress"],
        "reason": reason,
        "policy": policy,
        "risk": risk,
        "recommendation": recommend(policy, risk),
        "notes": notes,
        "summary": (f"{student['name']} requests a refund for {course} (order {enrollment['order_id']}, "
                    f"{fmt_money(enrollment['amount_paid'])}). Reason: {reason}. {policy['explanation']}"),
        "transcript": [dict(m) for m in transcript],
        "status": "pending_approval",
        "created_at": now().isoformat(),
        "decided_by": None,
        "decided_at": None,
        "approved_amount": None,
        "decision_note": None,
    }
    store.refund_cases.insert(0, case)
    store.audit("AI assistant", "Prepared refund case", f"{case['id']} for {student['name']} - awaiting human approval")
    return case


# ---------- background jobs (triggered by systems, not by the assistant) ----------

def payment_gateway_webhook(store: Store, student_id: str) -> dict:
    """Gateway reports 'failed but debited'. Tell the student before they have to ask."""
    student = store.students[student_id]
    enrolled = {e["course_id"] for e in student["enrollments"]}
    course_id = next(cid for cid in COURSES if cid not in enrolled)
    payment_id = store.next_id("PAY")
    payment = {
        "id": payment_id, "student_id": student_id, "course_id": course_id,
        "amount": COURSES[course_id]["price"], "method": "UPI", "status": "failed_debited",
        "created_at": now(), "bank_rrn": f"4129{payment_id[-5:]}308",
        "gateway_ref": f"pay_Ox{payment_id[-5:]}Wh", "will_auto_reverse": True,
    }
    store.payments.append(payment)
    schedule_reversal_followup(store, payment)
    deadline = payment["created_at"] + REVERSAL_WINDOW
    store.notify(student_id, "SMS + WhatsApp",
                 f"BrightPath: Your payment of {fmt_money(payment['amount'])} for {COURSES[course_id]['name']} "
                 f"did not go through, but your bank debited the amount. It will be reversed automatically by "
                 f"{fmt_when(deadline)} (bank ref {payment['bank_rrn']}). No action needed - we'll update you.")
    store.record_outcome("payment_issue", "prevented", 20)
    return payment


def run_due_followups(store: Store) -> list[dict]:
    """Simulates 48 hours passing: check every scheduled reversal."""
    results = []
    for f in store.followups:
        if f["status"] != "scheduled":
            continue
        payment = next(p for p in store.payments if p["id"] == f["payment_id"])
        student = store.students[payment["student_id"]]
        course = COURSES[payment["course_id"]]["name"]
        f["status"] = "done"
        if payment["will_auto_reverse"]:
            payment["status"] = "reversed"
            payment["reversed_at"] = now().isoformat()
            store.notify(student["id"], "SMS + WhatsApp",
                         f"BrightPath: Good news - your bank has reversed {fmt_money(payment['amount'])} for "
                         f"{course} (ref {payment['bank_rrn']}). It can take 1-3 days to show on your statement.")
            results.append({"payment_id": payment["id"], "student": student["name"], "result": "reversed"})
        else:
            ticket = create_ticket(
                store, student, "payment_issue", "Payments", "high",
                f"Auto-raised: {fmt_money(payment['amount'])} for {course} not reversed 48h after a failed payment.",
                {"payment_id": payment["id"], "gateway_ref": payment["gateway_ref"], "bank_rrn": payment["bank_rrn"],
                 "amount": fmt_money(payment["amount"]), "method": payment["method"]},
                [], source="Automated 48h follow-up")
            store.notify(student["id"], "SMS + WhatsApp",
                         f"BrightPath: Your {fmt_money(payment['amount'])} hasn't been reversed yet. We've raised it with "
                         f"our payments team ({ticket['id']}) - no need to contact us, we'll update you within 1 business day.")
            results.append({"payment_id": payment["id"], "student": student["name"], "result": f"escalated {ticket['id']}"})
    return results
