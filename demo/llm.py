"""Optional LLM intent classifier for any OpenAI-compatible chat completions API.

Set OPENAI_API_KEY (and optionally OPENAI_BASE_URL, OPENAI_MODEL) to enable it.
Without a key, or on any error or timeout, the assistant falls back to its rule-based
classifier. The LLM only labels intent; every action still goes through tools.py.
"""
from __future__ import annotations

import json
import os
import urllib.request

INTENTS = ["course_content", "video_issue", "payment_issue", "refund_request", "login_issue",
           "human_handoff", "greeting", "thanks"]

SYSTEM_PROMPT = """You label messages sent to student support at BrightPath, an online exam-prep company.
Reply with JSON only: {"intent": "<label>"} where <label> is one of:
- course_content: class schedule, timetable, syllabus, recordings, notes, mock tests, doubt sessions
- video_issue: video not playing, buffering, black screen, app crashing or other app problems
- payment_issue: payment failed but money deducted/debited, paid but course not unlocked, double charge.
  Money deducted on a failed payment is payment_issue even if the student uses the word "refund".
- refund_request: wants to cancel a course they bought and get their money back
- login_issue: cannot log in, forgot password, OTP not received, account locked or hacked
- human_handoff: asks for a human, agent, call back, or manager
- greeting: only a greeting
- thanks: only thanks
- other: anything else"""


def enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def engine_label() -> str:
    return f"LLM ({os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')}) + rules fallback" if enabled() else "Rule-based (offline)"


def classify(text: str) -> str | None:
    if not enabled():
        return None
    body = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
    }
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.load(response)
        intent = json.loads(data["choices"][0]["message"]["content"]).get("intent")
    except Exception:
        return None
    return intent if intent in INTENTS else None
