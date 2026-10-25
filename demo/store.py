"""In-memory data and state for the demo: courses, students, payments, tickets, refund cases."""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def now() -> datetime:
    return datetime.now(IST)


MOCK_TEST = {"weekday": 6, "time": (10, 0)}
DOUBT_SESSION = {"weekday": 5, "time": (17, 0)}

COURSES = {
    "JEE-PHY": {"name": "JEE Advanced Physics 2027", "aliases": ["jee", "physics"], "price": 8999,
                "faculty": "Dr. Anil Verma", "days": [0, 2, 4], "time": (19, 0),
                "topics": ["Rotational Motion", "Electrostatics", "Current Electricity", "Modern Physics"]},
    "NEET-CHEM": {"name": "NEET Chemistry Full Course", "aliases": ["neet", "chemistry", "chem"], "price": 5999,
                  "faculty": "Dr. Kavita Rao", "days": [1, 3, 5], "time": (18, 0),
                  "topics": ["Chemical Bonding", "Organic Chemistry Basics", "Equilibrium", "Coordination Compounds"]},
    "NEET-BIO": {"name": "NEET Biology Crash Course", "aliases": ["biology", "bio"], "price": 4999,
                 "faculty": "Dr. Sameer Khan", "days": [0, 3], "time": (17, 0),
                 "topics": ["Human Physiology", "Genetics", "Plant Physiology", "Ecology"]},
    "UPSC-GS": {"name": "UPSC GS Foundation 2027", "aliases": ["upsc", "gs", "general studies"], "price": 24999,
                "faculty": "Ms. Shalini Gupta", "days": [0, 1, 2, 3, 4], "time": (7, 0),
                "topics": ["Indian Polity", "Modern History", "Indian Economy", "Geography"]},
    "CAT-QA": {"name": "CAT Quant Mastery", "aliases": ["cat", "quant", "quants"], "price": 6999,
               "faculty": "Mr. Rohit Bansal", "days": [5, 6], "time": (10, 0),
               "topics": ["Arithmetic", "Algebra", "Geometry", "Number Systems"]},
    "SSC-CGL": {"name": "SSC CGL Complete Pack", "aliases": ["ssc", "cgl"], "price": 3499,
                "faculty": "Mr. Deepak Joshi", "days": [1, 3, 5], "time": (20, 0),
                "topics": ["Quantitative Aptitude", "Reasoning", "English Comprehension", "General Awareness"]},
    "GATE-CS": {"name": "GATE CS Live Batch", "aliases": ["gate", "cs", "computer science"], "price": 11999,
                "faculty": "Prof. Neha Kulkarni", "days": [2, 4, 6], "time": (20, 30),
                "topics": ["Operating Systems", "DBMS", "Computer Networks", "Algorithms"]},
    "GATE-TS": {"name": "GATE CS Test Series", "aliases": ["test series"], "price": 1999,
                "faculty": "BrightPath Test Team", "days": [6], "time": (10, 0),
                "topics": ["Full-length Mock Test"]},
}

KNOWN_ISSUES = {
    "BP-1423": {
        "title": "Lecture videos fail to play on some Android phones (hardware video decoder)",
        "status": "In product backlog for months, not scheduled",
        "workaround": "Settings > Playback > turn on Compatibility player",
        "monthly_tickets": 4836,
        "monthly_agent_hours": 1128,
        "agent_equivalent": 7.3,
    }
}

REFUND_POLICY = {
    "full_refund_days": 7,
    "full_refund_max_progress": 0.25,
    "partial_refund_days": 30,
    "partial_refund_max_progress": 0.50,
    "partial_refund_share": 0.50,
}

# Baseline handling minutes per ticket type (Exhibit A), used to estimate agent time saved.
BASELINE_AHT = {"course_content": 3, "video_issue": 14, "payment_issue": 20, "refund_request": 25, "login_issue": 6}


def _enrollment(course_id: str, order_id: str, days_ago: float, progress: float, t: datetime,
                payer_name_matches: bool = True) -> dict:
    return {
        "course_id": course_id,
        "order_id": order_id,
        "purchased_at": t - timedelta(days=days_ago),
        "progress": progress,
        "amount_paid": COURSES[course_id]["price"],
        "payer_name_matches": payer_name_matches,
        "refunded": False,
    }


def _seed_students(t: datetime) -> dict:
    students = [
        {"id": "S1001", "name": "Riya Sharma", "email": "riya.sharma@example.com", "phone": "+91 98100 41234",
         "device": {"os": "Android", "os_version": "13", "model": "Redmi Note 11", "app_version": "4.6.2"},
         "account_age_days": 310, "prior_refunds": 0, "chargebacks": 0,
         "enrollments": [_enrollment("JEE-PHY", "ORD-50121", 41, 0.46, t)],
         "scenario": "Video won't play on her Android phone; asks about next class"},
        {"id": "S1002", "name": "Arjun Mehta", "email": "arjun.mehta@example.com", "phone": "+91 99870 22871",
         "device": {"os": "Android", "os_version": "14", "model": "Samsung Galaxy M34", "app_version": "4.8.1"},
         "account_age_days": 190, "prior_refunds": 0, "chargebacks": 0,
         "enrollments": [_enrollment("NEET-CHEM", "ORD-49870", 75, 0.62, t)],
         "scenario": "Payment failed 6 hours ago but money was deducted"},
        {"id": "S1003", "name": "Sneha Iyer", "email": "sneha.iyer@example.com", "phone": "+91 90045 77310",
         "device": {"os": "iOS", "os_version": "17", "model": "iPhone 13", "app_version": "4.8.1"},
         "account_age_days": 95, "prior_refunds": 0, "chargebacks": 0,
         "enrollments": [],
         "scenario": "Paid for UPSC course, but it never unlocked"},
        {"id": "S1004", "name": "Karan Singh", "email": "karan.singh@example.com", "phone": "+91 98330 61102",
         "device": {"os": "Windows", "os_version": "11", "model": "Chrome browser", "app_version": "web"},
         "account_age_days": 420, "prior_refunds": 0, "chargebacks": 0,
         "enrollments": [_enrollment("CAT-QA", "ORD-51230", 3, 0.08, t)],
         "scenario": "Wants a refund 3 days after buying (low risk)"},
        {"id": "S1005", "name": "Vikram Rao", "email": "vikram.rao@example.com", "phone": "+91 97411 09876",
         "device": {"os": "Android", "os_version": "10", "model": "Vivo Y20", "app_version": "4.7.0"},
         "account_age_days": 25, "prior_refunds": 2, "chargebacks": 0,
         "enrollments": [_enrollment("SSC-CGL", "ORD-50877", 20, 0.72, t, payer_name_matches=False)],
         "scenario": "Wants a refund after using 72% of the course (high risk)"},
        {"id": "S1006", "name": "Meera Nair", "email": "meera.nair@example.com", "phone": "+91 99200 35567",
         "device": {"os": "iOS", "os_version": "17", "model": "iPhone 12", "app_version": "4.8.1"},
         "account_age_days": 520, "prior_refunds": 0, "chargebacks": 0,
         "enrollments": [_enrollment("GATE-CS", "ORD-47711", 120, 0.81, t)],
         "scenario": "Forgot password; payment stuck for 60 hours (not reversed)"},
    ]
    return {s["id"]: s for s in students}


def _seed_payments(t: datetime) -> list[dict]:
    return [
        {"id": "PAY-88213", "student_id": "S1002", "course_id": "NEET-BIO", "amount": 4999, "method": "UPI",
         "status": "failed_debited", "created_at": t - timedelta(hours=6), "bank_rrn": "412788341102",
         "gateway_ref": "pay_Nx81Kd02Lm", "will_auto_reverse": True},
        {"id": "PAY-88240", "student_id": "S1003", "course_id": "UPSC-GS", "amount": 24999, "method": "Debit card",
         "status": "captured_unfulfilled", "created_at": t - timedelta(hours=2), "bank_rrn": "412790017745",
         "gateway_ref": "pay_Nx93Qa17Ty", "will_auto_reverse": False},
        {"id": "PAY-87990", "student_id": "S1006", "course_id": "GATE-TS", "amount": 1999, "method": "UPI",
         "status": "failed_debited", "created_at": t - timedelta(hours=60), "bank_rrn": "412561123390",
         "gateway_ref": "pay_Nw40Bb88Ze", "will_auto_reverse": False},
    ]


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            t = now()
            self.students = _seed_students(t)
            self.payments = _seed_payments(t)
            self.tickets: list[dict] = []
            self.refund_cases: list[dict] = []
            self.notifications: list[dict] = []
            self.followups: list[dict] = []
            self.outcomes: list[dict] = []
            self.audit_log: list[dict] = []
            self.known_issue_hits = {key: 0 for key in KNOWN_ISSUES}
            self.sessions: dict[str, dict] = {}
            self._counters = {
                "T": itertools.count(1001),
                "RF": itertools.count(2001),
                "N": itertools.count(1),
                "ORD": itertools.count(52001),
                "PAY": itertools.count(89001),
            }

    def next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._counters[prefix])}"

    def notify(self, student_id: str, channel: str, text: str) -> dict:
        n = {"id": self.next_id("N"), "student_id": student_id, "channel": channel, "text": text,
             "created_at": now().isoformat()}
        self.notifications.append(n)
        return n

    def record_outcome(self, intent: str, outcome: str, minutes_saved: float) -> None:
        self.outcomes.append({"intent": intent, "outcome": outcome, "minutes_saved": minutes_saved,
                              "at": now().isoformat()})

    def audit(self, actor: str, action: str, detail: str) -> None:
        self.audit_log.append({"at": now().isoformat(), "actor": actor, "action": action, "detail": detail})

    def metrics(self) -> dict:
        intents = list(BASELINE_AHT) + ["human_handoff"]
        by_intent = {i: {"bot_resolved": 0, "escalated": 0, "prepared_for_human": 0, "prevented": 0} for i in intents}
        for o in self.outcomes:
            by_intent.setdefault(o["intent"], {"bot_resolved": 0, "escalated": 0, "prepared_for_human": 0,
                                               "prevented": 0})
            by_intent[o["intent"]][o["outcome"]] += 1
        totals = {k: sum(v[k] for v in by_intent.values())
                  for k in ("bot_resolved", "escalated", "prepared_for_human", "prevented")}
        return {
            "by_intent": by_intent,
            "totals": totals,
            "minutes_saved": round(sum(o["minutes_saved"] for o in self.outcomes)),
            "known_issue_hits": dict(self.known_issue_hits),
            "open_tickets": sum(1 for t in self.tickets if t["status"] == "open"),
            "pending_refunds": sum(1 for c in self.refund_cases if c["status"] == "pending_approval"),
        }
