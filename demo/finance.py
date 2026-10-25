"""Human-only refund decisions. The AI assistant (bot.py / tools.py) never imports this module."""
from __future__ import annotations

from store import Store, now
from tools import fmt_money


class DecisionError(ValueError):
    pass


def decide_refund(store: Store, case_id: str, decision: str, reviewer: str,
                  amount: float | str | None = None, note: str = "") -> dict:
    reviewer = (reviewer or "").strip()
    note = (note or "").strip()
    if not reviewer:
        raise DecisionError("A named reviewer is required to decide a refund.")
    if decision not in ("approve", "reject"):
        raise DecisionError("Decision must be 'approve' or 'reject'.")
    with store.lock:
        case = next((c for c in store.refund_cases if c["id"] == case_id), None)
        if case is None:
            raise DecisionError(f"Refund case {case_id} not found.")
        if case["status"] != "pending_approval":
            raise DecisionError(f"{case_id} was already {case['status']} by {case['decided_by']}.")
        student = store.students[case["student_id"]]

        if decision == "approve":
            value = case["policy"]["eligible_amount"] if amount in (None, "") else float(amount)
            if not 0 < value <= case["amount_paid"]:
                raise DecisionError(f"Approved amount must be between \u20b91 and {fmt_money(case['amount_paid'])}.")
            case.update(status="approved", approved_amount=value)
            for e in student["enrollments"]:
                if e["order_id"] == case["order_id"]:
                    e["refunded"] = True
            store.notify(student["id"], "WhatsApp + Email",
                         f"BrightPath: Your refund of {fmt_money(value)} for {case['course']} has been approved "
                         f"(ref {case['id']}). It will reach your original payment method in 5-7 business days.")
            store.audit(reviewer, "Approved refund",
                        f"{case['id']}: {fmt_money(value)} to {case['student_name']}" + (f" - {note}" if note else ""))
        else:
            case.update(status="rejected")
            store.notify(student["id"], "WhatsApp + Email",
                         f"BrightPath: We reviewed your refund request {case['id']} for {case['course']} and can't "
                         f"approve it under our refund policy." + (f" Reviewer's note: {note}" if note else "")
                         + " Reply here if you'd like a specialist to call you.")
            store.audit(reviewer, "Rejected refund",
                        f"{case['id']} for {case['student_name']}" + (f" - {note}" if note else ""))

        case.update(decided_by=reviewer, decided_at=now().isoformat(), decision_note=note or None)
        return case
