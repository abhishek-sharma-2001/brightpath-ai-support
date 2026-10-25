"""JSON API for the demo, independent of any web server.

server.py serves it over HTTP locally. The hosted version (web/) runs this same module in the
browser with Pyodide, so every visitor gets their own in-memory store.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import bot
import capacity_model
import finance
import llm
import tools
from store import COURSES, KNOWN_ISSUES, Store

STORE = Store()


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def _student_summary(s: dict) -> dict:
    d = s["device"]
    return {
        "id": s["id"],
        "name": s["name"],
        "scenario": s["scenario"],
        "device": f"{d['model']} ({d['os']} {d['os_version']}, app {d['app_version']})",
        "courses": [COURSES[e["course_id"]]["name"] for e in s["enrollments"] if not e["refunded"]],
    }


def _console_snapshot() -> dict:
    with STORE.lock:
        followups = []
        for f in STORE.followups:
            p = next(p for p in STORE.payments if p["id"] == f["payment_id"])
            followups.append({**f, "student": STORE.students[f["student_id"]]["name"],
                              "amount": tools.fmt_money(p["amount"]), "payment_status": p["status"]})
        return {
            "tickets": STORE.tickets,
            "refund_cases": STORE.refund_cases,
            "audit_log": list(reversed(STORE.audit_log)),
            "metrics": STORE.metrics(),
            "known_issues": [{"id": k, **v, "demo_hits": STORE.known_issue_hits[k]} for k, v in KNOWN_ISSUES.items()],
            "followups": followups,
            "students": [_student_summary(s) for s in STORE.students.values()],
        }


def _student_id(data: dict) -> str | None:
    sid = data.get("student_id")
    return sid if sid in STORE.students else None


def _get(path: str, query: dict) -> tuple[int, dict]:
    if path == "/api/config":
        with STORE.lock:
            students = [_student_summary(s) for s in STORE.students.values()]
        return 200, {"engine": llm.engine_label(), "students": students}
    if path == "/api/notifications":
        sid = query.get("student_id", [""])[0]
        with STORE.lock:
            items = [n for n in STORE.notifications if n["student_id"] == sid]
        return 200, {"notifications": list(reversed(items))}
    if path == "/api/console":
        return 200, _console_snapshot()
    if path == "/api/scenarios":
        return 200, {"scenarios": capacity_model.SCENARIOS, "param_info": capacity_model.PARAM_INFO,
                     "categories": capacity_model.CATEGORIES}
    if path == "/api/model":
        scenario = query.get("scenario", ["expected"])[0]
        if scenario not in capacity_model.SCENARIOS:
            return 400, {"error": "Unknown scenario"}
        return 200, capacity_model.run_model(capacity_model.SCENARIOS[scenario])
    return 404, {"error": "Not found"}


def _post(path: str, data: dict) -> tuple[int, dict]:
    if path == "/api/session":
        sid = _student_id(data)
        if not sid:
            return 400, {"error": "Unknown student"}
        session_id = uuid.uuid4().hex
        return 200, {"session_id": session_id, **bot.start_session(STORE, session_id, sid)}

    if path == "/api/chat":
        sid = _student_id(data)
        message = str(data.get("message", "")).strip()
        if not sid or not message or not data.get("session_id"):
            return 400, {"error": "student_id, session_id and message are required"}
        return 200, bot.handle_message(STORE, str(data["session_id"]), sid, message)

    match = re.fullmatch(r"/api/refunds/(RF-\d+)/decision", path)
    if match:
        try:
            case = finance.decide_refund(STORE, match.group(1), str(data.get("decision", "")),
                                         str(data.get("reviewer", "")), data.get("amount"),
                                         str(data.get("note", "")))
        except (finance.DecisionError, ValueError) as exc:
            return 400, {"error": str(exc)}
        return 200, case

    match = re.fullmatch(r"/api/tickets/(T-\d+)/resolve", path)
    if match:
        with STORE.lock:
            ticket = next((t for t in STORE.tickets if t["id"] == match.group(1)), None)
            if ticket is None:
                return 404, {"error": "Ticket not found"}
            agent = str(data.get("agent") or "Support agent")
            ticket.update(status="resolved", resolved_by=agent)
            STORE.audit(agent, "Resolved ticket", f"{ticket['id']} ({ticket['queue']}) for {ticket['student_name']}")
        return 200, ticket

    if path == "/api/simulate/payment-webhook":
        sid = _student_id(data)
        if not sid:
            return 400, {"error": "Unknown student"}
        with STORE.lock:
            payment = tools.payment_gateway_webhook(STORE, sid)
        return 200, {"payment": payment, "student": STORE.students[sid]["name"]}

    if path == "/api/simulate/fast-forward":
        with STORE.lock:
            results = tools.run_due_followups(STORE)
        return 200, {"results": results}

    if path == "/api/model":
        return 200, capacity_model.run_model(data.get("params", {}))

    if path == "/api/reset":
        STORE.reset()
        return 200, {"ok": True}

    return 404, {"error": "Not found"}


def handle(method: str, url: str, data: dict | None = None) -> tuple[int, str]:
    """Route one API call. Returns (HTTP status, JSON body)."""
    parsed = urlparse(url)
    if method == "GET":
        status, payload = _get(parsed.path, parse_qs(parsed.query))
    else:
        status, payload = _post(parsed.path, data or {})
    return status, json.dumps(payload, default=_json_default)


def handle_browser(method: str, url: str, body: str) -> str:
    """Entry point for the in-browser (Pyodide) build: JSON in, JSON envelope out."""
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return json.dumps({"status": 400, "body": {"error": "Body must be JSON"}})
    status, payload = handle(method, url, data)
    return f'{{"status": {status}, "body": {payload}}}'
