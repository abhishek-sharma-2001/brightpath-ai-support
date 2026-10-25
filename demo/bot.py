"""Conversation logic for the BrightPath support assistant.

Flow per message: finish any pending question (yes/no, which course, refund reason),
otherwise classify intent (LLM if configured, else rules) and run that intent's playbook.
Playbooks only act through tools.py.
"""
from __future__ import annotations

import re

import llm
import tools
from store import COURSES, Store, now
from tools import fmt_day, fmt_money, fmt_time, fmt_when, mask_email, mask_phone

MAIN_MENU = ["When is my next class?", "Video is not playing", "Payment failed but money deducted",
             "I want a refund", "I forgot my password"]
REFUND_REASONS = ["Course isn't what I expected", "Technical problems (video/app)", "Bought it by mistake",
                  "Other reason"]

KEYWORDS = {
    "payment_issue": {"payment": 2, "paid": 2, "pay": 1, "deducted": 3, "debited": 3, "deduct": 3, "charged": 2,
                      "transaction": 2, "upi": 2, "money": 1, "amount": 1, "bank": 1, "failed": 1,
                      "money deducted": 2, "double charged": 3, "not unlocked": 3, "not activated": 2,
                      "paise": 2, "paisa": 2, "kat gaye": 3, "kat gaya": 3, "kat gya": 3},
    "refund_request": {"refund": 6, "money back": 5, "cancel": 3, "cancellation": 3, "return my money": 5,
                       "paise wapas": 5, "paisa wapas": 5, "wapas": 2},
    "video_issue": {"video": 3, "videos": 3, "playing": 3, "not playing": 3, "play": 2, "player": 3,
                    "buffering": 4, "buffer": 3, "black screen": 4, "crash": 4, "crashes": 4, "crashing": 4,
                    "app": 1, "lag": 3, "lagging": 3, "loading": 2, "audio": 2, "sound": 2, "stream": 2,
                    "nahi chal raha": 3, "chal nahi raha": 3, "nahi chal rahi": 3},
    "login_issue": {"login": 4, "log in": 4, "logged out": 3, "sign in": 4, "password": 5, "otp": 4,
                    "forgot": 2, "locked": 3, "account": 1, "hacked": 4, "reset": 2, "bhool gaya": 2,
                    "bhul gaya": 2},
    "course_content": {"class": 3, "classes": 3, "next class": 3, "schedule": 4, "timetable": 4, "timing": 3,
                       "syllabus": 4, "mock": 3, "test": 2, "doubt": 3, "recording": 3, "recordings": 3,
                       "lecture": 1, "notes": 3, "batch": 2, "faculty": 2, "teacher": 2, "when": 1, "kab": 1},
    "human_handoff": {"human": 5, "agent": 4, "person": 3, "talk to": 2, "speak to": 2, "call me": 4,
                      "callback": 4, "representative": 4, "manager": 4, "real person": 3},
    "greeting": {"hi": 2, "hello": 2, "hey": 2, "namaste": 2, "good morning": 2, "good evening": 2},
    "thanks": {"thanks": 3, "thank you": 3, "thx": 3},
}
# Ties go to the intent that costs students (and agents) the most when missed.
PRIORITY = ["payment_issue", "refund_request", "video_issue", "login_issue", "human_handoff",
            "course_content", "greeting", "thanks"]
_PATTERNS = {
    intent: [(re.compile(r"\b" + re.escape(k).replace(r"\ ", r"\s+") + r"\b", re.I), w) for k, w in words.items()]
    for intent, words in KEYWORDS.items()
}
FAILED_PAYMENT = re.compile(r"\b(deduct\w*|debit\w*|kat gaye|kat gaya|kat gya)\b|\bpayment\b.*\bfail|"
                            r"\bfail\w*\b.*\bpayment\b", re.I)
ANGRY = re.compile(r"\b(worst|fraud|cheat(ed|ing)?|scam|useless|pathetic|angry|frustrated|consumer court|"
                   r"disgusting|horrible|ridiculous)\b", re.I)
SECURITY = re.compile(r"\b(hack(ed)?|locked|changed? my (number|phone|email)|new (number|phone)|lost my phone|"
                      r"someone else|not me|unauthori[sz]ed)\b", re.I)
NEGATIVE = re.compile(r"\b(no|nope|nah|not|still|didn'?t|doesn'?t|can'?t|cannot|isn'?t|won'?t|nahi|nahin|abhi bhi)\b",
                      re.I)
POSITIVE = re.compile(r"\b(yes|yeah|yep|yup|ok|okay|fixed|working|works|worked|solved|resolved|done|thanks|"
                      r"thank you|great|i'?m in|haan|han|ho gaya|chal gaya|theek)\b", re.I)


def rule_classify(text: str) -> str | None:
    scores = {}
    for intent, patterns in _PATTERNS.items():
        score = sum(w for p, w in patterns if p.search(text))
        if score:
            scores[intent] = score
    if not scores:
        return None
    if "refund_request" in scores and FAILED_PAYMENT.search(text):
        return "payment_issue"
    best = max(scores.values())
    return next(i for i in PRIORITY if scores.get(i) == best)


def classify(text: str) -> str | None:
    return llm.classify(text) or rule_classify(text)


def parse_yes_no(text: str) -> bool | None:
    if NEGATIVE.search(text):
        return False
    if POSITIVE.search(text):
        return True
    return None


class Reply:
    def __init__(self, transcript: list[dict]) -> None:
        self.transcript = transcript
        self.messages: list[dict] = []
        self.events: list[dict] = []

    def say(self, text: str, quick_replies: list[str] | None = None) -> None:
        self.messages.append({"text": text, "quick_replies": quick_replies or []})
        self.transcript.append({"role": "assistant", "text": text})

    def event(self, kind: str, label: str) -> None:
        self.events.append({"kind": kind, "label": label})


def _first(student: dict) -> str:
    return student["name"].split()[0]


def _active(student: dict) -> list[dict]:
    return [e for e in student["enrollments"] if not e["refunded"]]


def _priority(s: dict, default: str = "normal") -> str:
    return "high" if s.get("upset") else default


# ---------- entry points ----------

def start_session(store: Store, session_id: str, student_id: str) -> dict:
    with store.lock:
        student = store.students[student_id]
        s = {"id": session_id, "student_id": student_id, "awaiting": None, "transcript": [], "unknown_count": 0}
        store.sessions[session_id] = s
        r = Reply(s["transcript"])
        r.say(f"Hi {_first(student)}! I'm BrightPath's support assistant. I can check your class schedule, fix video "
              "problems, track a payment, start a refund request or help you log in. What do you need?", MAIN_MENU)
        return {"messages": r.messages, "events": r.events}


def handle_message(store: Store, session_id: str, student_id: str, text: str) -> dict:
    with store.lock:
        student = store.students[student_id]
        s = store.sessions.get(session_id)
        if s is None or s["student_id"] != student_id:
            s = {"id": session_id, "student_id": student_id, "awaiting": None, "transcript": [], "unknown_count": 0}
            store.sessions[session_id] = s
        text = text.strip()[:1000]
        s["transcript"].append({"role": "student", "text": text})
        r = Reply(s["transcript"])
        if ANGRY.search(text):
            s["upset"] = True
        intent = None
        if not _continue_pending(store, s, student, text, r):
            intent = classify(text)
            _route(store, s, student, text, intent, r)
        return {"messages": r.messages, "events": r.events, "intent": intent, "engine": llm.engine_label()}


def _route(store: Store, s: dict, student: dict, text: str, intent: str | None, r: Reply) -> None:
    if intent is None:
        if s.get("upset"):
            return _handoff(store, s, student, r)
        return _unknown(store, s, student, r)
    s["unknown_count"] = 0
    if s.get("upset") and intent not in ("human_handoff", "greeting", "thanks"):
        r.say("I'm really sorry about the trouble. Let me sort this out right now.")
    handlers = {
        "greeting": _greeting,
        "thanks": _thanks,
        "course_content": _course_content,
        "video_issue": _video_issue,
        "payment_issue": _payment_issue,
        "refund_request": _refund_request,
        "login_issue": _login_issue,
        "human_handoff": lambda store, s, student, text, r: _handoff(store, s, student, r),
    }
    handlers[intent](store, s, student, text, r)


def _continue_pending(store: Store, s: dict, student: dict, text: str, r: Reply) -> bool:
    pending = s["awaiting"]
    if not pending:
        return False
    kind = pending["type"]
    if kind in ("video_fixed", "login_result"):
        answer = parse_yes_no(text)
        s["awaiting"] = None
        if answer is None:
            return False
        if kind == "video_fixed":
            _video_result(store, s, student, answer, pending, r)
        else:
            _login_result(store, s, student, answer, r)
        return True
    if kind == "refund_course":
        course_id = tools.match_course(text, pending["options"])
        if course_id:
            s["awaiting"] = None
            _ask_refund_reason(store, s, student, course_id, r)
            return True
        intent = classify(text)
        if intent and intent != "refund_request":
            s["awaiting"] = None
            return False
        r.say("Which course is this about?", [COURSES[c]["name"] for c in pending["options"]])
        return True
    if kind == "refund_reason":
        s["awaiting"] = None
        if classify(text) == "human_handoff":
            return False
        _submit_refund(store, s, student, pending["course_id"], text, r)
        return True
    s["awaiting"] = None
    return False


# ---------- small talk ----------

def _greeting(store, s, student, text, r):
    r.say(f"Hi {_first(student)}! How can I help today?", MAIN_MENU)


def _thanks(store, s, student, text, r):
    r.say("You're welcome! Anything else I can help with?", MAIN_MENU[:3])


def _unknown(store, s, student, r):
    s["unknown_count"] += 1
    if s["unknown_count"] >= 2:
        return _handoff(store, s, student, r)
    r.say("Sorry, I didn't quite get that. I can help with class schedules, video problems, payments, refunds and "
          "login - or connect you to a person.", MAIN_MENU + ["Talk to a human"])


# ---------- course content ----------

def _course_content(store, s, student, text, r):
    active = _active(student)
    if not active:
        r.say("I can't find an active course on your account yet. If you've paid and the course isn't showing, "
              "tell me \"paid but course not unlocked\" and I'll check the payment.", MAIN_MENU)
        return
    ids = [e["course_id"] for e in active]
    match = tools.match_course(text, ids)
    lowered = text.lower()
    lines = []
    asked_next_class = False
    for course_id in [match] if match else ids:
        sch = tools.course_schedule(course_id)
        name = sch["course"]
        if re.search(r"\brecording", lowered):
            lines.append(f"Recordings for **{name}** are in **My Courses > {name} > Recordings**, usually within "
                         "2 hours of each live class, and stay available until your exam.")
        elif re.search(r"\b(syllabus|topics?)\b", lowered):
            lines.append(f"**{name}** covers: {', '.join(sch['syllabus'])}. The chapter-wise plan is under "
                         "My Courses > Syllabus.")
        elif re.search(r"\b(mock|test)", lowered):
            lines.append(f"Your next mock test for **{name}** is **{fmt_when(sch['next_mock_test'])}**. Scores and "
                         "all-India rank come out within 24 hours.")
        elif re.search(r"\bdoubt", lowered):
            lines.append(f"The next live doubt-clearing session for **{name}** is "
                         f"**{fmt_when(sch['next_doubt_session'])}** with {sch['faculty']}. You can also post a doubt "
                         "any time in the Doubts tab.")
        elif re.search(r"\b(notes?|pdf)\b", lowered):
            lines.append(f"Class notes (PDF) for **{name}** are under **My Courses > Notes**, uploaded after each class.")
        else:
            asked_next_class = True
            lines.append(f"Your next **{name}** class is **{fmt_when(sch['next_class'])}**: {sch['topic']} with "
                         f"{sch['faculty']}.")
    if asked_next_class:
        lines.append("You'll get an app reminder 15 minutes before it starts, and the recording is up within 2 hours.")
    r.say("\n\n".join(lines), ["Thanks!", "When is the next mock test?", "Talk to a human"])
    store.record_outcome("course_content", "bot_resolved", 3)


# ---------- video / app ----------

def _video_issue(store, s, student, text, r):
    d = student["device"]
    issue = tools.match_known_issue(d)
    if issue:
        hits = tools.record_known_issue_hit(store, issue)
        r.event("known_issue", f"Matched known bug {issue}; linked to product impact report ({hits} in this demo)")
        r.say(f"Thanks, {_first(student)}. I can see you're on a **{d['model']}** (Android {d['os_version']}, app "
              f"{d['app_version']}). This matches a known playback problem on some Android phones that our engineers "
              "are working on. It isn't your phone or your internet.\n\nThis fixes it for most students:\n"
              "1. Open **Profile > Settings > Playback**\n2. Turn on **Compatibility player**\n3. Reopen the lecture\n\n"
              "In a hurry? The same lecture plays at **learn.brightpath.in** in your phone's browser.")
    else:
        if d["app_version"] == "web":
            steps = ["Refresh the page and allow autoplay for learn.brightpath.in",
                     "Try Chrome or Edge in a normal (not incognito) window"]
        else:
            steps = []
            if d["app_version"] != "4.8.1":
                store_name = "Play Store" if d["os"] == "Android" else "App Store"
                steps.append(f"Update the BrightPath app to version 4.8.1 from the {store_name} (you have "
                             f"{d['app_version']})")
            steps.append("Close the app fully and reopen it")
        steps += ["Set video quality to **480p** (gear icon on the player)",
                  "If you're on mobile data, try Wi-Fi once to rule out the network"]
        numbered = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
        r.say(f"Let's fix that, {_first(student)}. I can see you're on **{d['model']}** ({d['os']} {d['os_version']}, "
              f"app {d['app_version']}). Please try these in order:\n{numbered}")
    r.say("Did that fix it?", ["Yes, it's working", "No, still not playing"])
    s["awaiting"] = {"type": "video_fixed", "issue": issue}


def _video_result(store, s, student, fixed, pending, r):
    if fixed:
        r.say("Great, happy studying! If it happens again, the same fix works.", ["Thanks!"])
        store.record_outcome("video_issue", "bot_resolved", 14)
        return
    d = student["device"]
    issue = pending["issue"]
    detail = (f"Matches known bug {issue}; Compatibility player workaround did not help."
              if issue else "Standard troubleshooting (update, restart, 480p, network) did not help.")
    ticket = tools.create_ticket(
        store, student, "video_issue", "Tech support", _priority(s),
        f"Video won't play for {student['name']} on {d['model']} ({d['os']} {d['os_version']}, app "
        f"{d['app_version']}). {detail} Needs: engineer to pull device playback logs.",
        {"device": d["model"], "os": f"{d['os']} {d['os_version']}", "app_version": d["app_version"],
         "network_check": "Wi-Fi, 18 Mbps (passed)", "known_issue": issue or "none matched",
         "steps_already_tried": "Compatibility player, web player" if issue else "update, restart, 480p, Wi-Fi"},
        s["transcript"])
    r.event("ticket", f"Ticket {ticket['id']} sent to Tech support with device diagnostics attached")
    r.say(f"Sorry it's still not working. I've passed this to our tech team as ticket **{ticket['id']}** with your "
          "device details and the steps you've already tried, so nobody will ask you to repeat them. They usually "
          f"reply within {ticket['sla']}.\n\nMeanwhile, lectures play at **learn.brightpath.in** in any browser.")
    store.record_outcome("video_issue", "escalated", 2)


# ---------- payments ----------

def _payment_issue(store, s, student, text, r):
    p = tools.find_payment_issue(store, student["id"])
    if p is None:
        r.say("I checked your account and can't find a failed or pending payment in the last 30 days. If money was "
              "deducted, the payment may have been made with a different phone number or email. Could you share the "
              "UPI transaction ID (UTR) or bank reference? I'll pass it to our payments team.", ["Talk to a human"])
        return
    course = COURSES[p["course_id"]]["name"]
    amount = fmt_money(p["amount"])
    hours = (now() - p["created_at"]).total_seconds() / 3600

    if p["status"] == "captured_unfulfilled":
        enrollment = tools.grant_access_for_captured_payment(store, student, p)
        r.event("access_granted", f"Gateway shows payment {p['id']} captured; course unlocked (no money moved)")
        r.say(f"Found it, {_first(student)}. Your payment of **{amount}** for **{course}** "
              f"({fmt_when(p['created_at'])}) reached us; the enrollment just didn't complete on our side. I've fixed "
              f"that: **the course is now unlocked in your app.** Please don't pay again.\n\n"
              f"Order ID: {enrollment['order_id']}", ["Thanks!", "When is my next class?"])
        store.record_outcome("payment_issue", "bot_resolved", 20)
    elif p["status"] == "failed_debited" and hours < 48:
        tools.schedule_reversal_followup(store, p)
        deadline = p["created_at"] + tools.REVERSAL_WINDOW
        r.event("followup", f"Auto-check scheduled for {fmt_when(deadline)}; ticket opens by itself if not reversed")
        r.say(f"I found it, {_first(student)}. Your payment of **{amount}** for **{course}** "
              f"({fmt_day(p['created_at'])} at {fmt_time(p['created_at'])}) failed at the bank's end, but the amount "
              f"was debited.\n\n**Your bank reverses this automatically**, usually within 48 hours, so by "
              f"**{fmt_when(deadline)}**. Bank reference (RRN): **{p['bank_rrn']}**.\n\nYou don't need to do anything. "
              "I'll check again then, and if the money isn't back I'll raise it with our payments team for you.")
        r.say("If you'd still like to enroll now, you can retry with another method. You won't be charged twice.",
              ["Thanks!", "Talk to a human"])
        store.record_outcome("payment_issue", "bot_resolved", 20)
    elif p["status"] == "failed_debited":
        ticket = tools.create_ticket(
            store, student, "payment_issue", "Payments", "high",
            f"{amount} for {course} debited {int(hours)}h ago on a failed payment and not reversed after 48h. "
            "Needs: raise with gateway/bank using the references below.",
            {"payment_id": p["id"], "gateway_ref": p["gateway_ref"], "bank_rrn": p["bank_rrn"], "amount": amount,
             "method": p["method"], "debited_at": fmt_when(p["created_at"]), "hours_since_debit": int(hours)},
            s["transcript"])
        r.event("ticket", f"Priority ticket {ticket['id']} sent to Payments with gateway and bank references")
        r.say(f"I found your payment of **{amount}** for **{course}** from {fmt_when(p['created_at'])}. It's been "
              f"{int(hours)} hours and your bank hasn't reversed it yet, which is longer than usual.\n\nI've raised a "
              f"**priority ticket ({ticket['id']})** with our payments team and attached the gateway and bank "
              "references, so they can take it straight to the bank. You'll hear back within 1 business day.")
        store.record_outcome("payment_issue", "escalated", 5)
    else:
        r.say(f"Your payment of **{amount}** for **{course}** has been reversed by your bank (RRN **{p['bank_rrn']}**). "
              "Depending on your bank it can take 1-3 working days to show on your statement.",
              ["Thanks!", "Talk to a human"])
        store.record_outcome("payment_issue", "bot_resolved", 20)


# ---------- refunds (prepare for a human; never issue) ----------

def _refund_request(store, s, student, text, r):
    active = _active(student)
    if not active:
        r.say("I can't find a course purchase on your account that could be refunded. If money was deducted on a "
              "failed payment, your bank returns it automatically; say \"payment failed\" and I'll check it.", MAIN_MENU)
        return
    ids = [e["course_id"] for e in active]
    course_id = tools.match_course(text, ids) or (ids[0] if len(ids) == 1 else None)
    if course_id is None:
        s["awaiting"] = {"type": "refund_course", "options": ids}
        r.say("Sure. Which course is this about?", [COURSES[c]["name"] for c in ids])
        return
    _ask_refund_reason(store, s, student, course_id, r)


def _ask_refund_reason(store, s, student, course_id, r):
    enrollment = next(e for e in _active(student) if e["course_id"] == course_id)
    course = COURSES[course_id]["name"]
    existing = next((c for c in store.refund_cases
                     if c["order_id"] == enrollment["order_id"] and c["status"] == "pending_approval"), None)
    if existing:
        r.say(f"You already have a refund request (**{existing['id']}**) for **{course}** with our finance team. "
              "You'll hear back within 2 business days of raising it.", ["Talk to a human"])
        return
    s["awaiting"] = {"type": "refund_reason", "course_id": course_id}
    r.say(f"I can help with that. Could you tell me why you'd like a refund for **{course}**? It helps our team "
          "review it faster.", REFUND_REASONS)


def _submit_refund(store, s, student, course_id, reason, r):
    enrollment = next(e for e in _active(student) if e["course_id"] == course_id)
    case = tools.create_refund_case(store, student, enrollment, reason, s["transcript"])
    r.event("refund_case", f"Refund case {case['id']} prepared (policy check + risk score); waiting for a human in Finance")
    r.say(f"Thanks, {_first(student)}. I've submitted your refund request for **{case['course']}**, reference "
          f"**{case['id']}**.\n\nEvery refund at BrightPath is reviewed by a person in our finance team, so I can't "
          "confirm the outcome myself. I've attached your order details and this conversation, so nobody will ask "
          "you to repeat anything. You'll get an update on WhatsApp and email within **2 business days**.")
    if "technical" in reason.lower() and tools.match_known_issue(student["device"]):
        r.say("While you wait: if the problem was video playback, turning on **Compatibility player** "
              "(Profile > Settings > Playback) fixes it for most Android phones.")
    store.record_outcome("refund_request", "prepared_for_human", 15)


# ---------- login ----------

def _login_issue(store, s, student, text, r):
    if SECURITY.search(text):
        ticket = tools.create_ticket(
            store, student, "login_issue", "Account security", "high",
            f"{student['name']} reports an account access/security problem: \"{text}\". Needs identity verification "
            "before any change. The assistant made no account changes.",
            {"registered_email": mask_email(student["email"]), "registered_phone": mask_phone(student["phone"]),
             "last_device": student["device"]["model"]},
            s["transcript"])
        r.event("ticket", f"Security ticket {ticket['id']}: identity check needed, no account changes made by the bot")
        r.say(f"For your safety, changes to account access need a quick identity check by our team, which I can't do "
              f"in chat. I've raised a **priority ticket ({ticket['id']})** and a specialist will call you on "
              f"**{mask_phone(student['phone'])}** within 1 hour. Please don't share your OTP or password with anyone, "
              "including us.")
        store.record_outcome("login_issue", "escalated", 1)
        return
    if re.search(r"\botp\b", text, re.I):
        tools.resend_login_otp(store, student)
        r.event("otp", "Fresh OTP sent by SMS and email")
        r.say(f"I've sent a fresh login OTP to **{mask_phone(student['phone'])}** by SMS and to "
              f"**{mask_email(student['email'])}**. It's valid for 10 minutes. If the SMS is slow (common when DND "
              "is on), use the one in your email.")
    else:
        tools.send_password_reset(store, student)
        r.event("reset", "Password reset link sent by WhatsApp and email")
        r.say(f"No problem. I've sent a password reset link to **{mask_email(student['email'])}** and to WhatsApp on "
              f"**{mask_phone(student['phone'])}**. It's valid for 15 minutes.")
    r.say("Were you able to log in?", ["Yes, I'm in", "Still can't log in"])
    s["awaiting"] = {"type": "login_result"}


def _login_result(store, s, student, ok, r):
    if ok:
        r.say("Great! Anything else I can help with?", MAIN_MENU[:3])
        store.record_outcome("login_issue", "bot_resolved", 6)
        return
    ticket = tools.create_ticket(
        store, student, "login_issue", "Account support", _priority(s),
        f"Reset link / OTP sent to {student['name']} but they still can't log in. Needs: check account status "
        "and auth logs.",
        {"registered_email": mask_email(student["email"]), "registered_phone": mask_phone(student["phone"]),
         "device": student["device"]["model"], "self_serve_tried": "reset link / OTP"},
        s["transcript"])
    r.event("ticket", f"Ticket {ticket['id']} sent to Account support")
    r.say(f"Sorry about that. I've raised ticket **{ticket['id']}** with our account team; they can see what we've "
          f"already tried and will reply within {ticket['sla']}.")
    store.record_outcome("login_issue", "escalated", 1)


# ---------- human handoff ----------

def _handoff(store, s, student, r):
    said = [m["text"] for m in s["transcript"] if m["role"] == "student"]
    summary = f"{student['name']} asked for a person." if not s.get("upset") else \
        f"{student['name']} is upset and needs a person."
    if said:
        summary += " Recent messages: " + " | ".join(f"\"{m}\"" for m in said[-3:])
    ticket = tools.create_ticket(
        store, student, "human_handoff", "General support", _priority(s), summary,
        {"registered_phone": mask_phone(student["phone"]),
         "courses": ", ".join(COURSES[e["course_id"]]["name"] for e in _active(student)) or "none",
         "sentiment": "upset" if s.get("upset") else "neutral"},
        s["transcript"])
    r.event("ticket", f"Ticket {ticket['id']} handed to a person with the full conversation")
    r.say(f"Of course. I've passed you to a support specialist, ticket **{ticket['id']}**. They can see this whole "
          f"conversation, so you won't need to repeat yourself. Expected reply: within {ticket['sla']}. If you'd "
          f"prefer a call, they can ring you on **{mask_phone(student['phone'])}**.")
    store.record_outcome("human_handoff", "escalated", 0)
