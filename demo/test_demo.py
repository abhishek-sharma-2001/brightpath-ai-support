"""End-to-end checks for every demo scenario.  Run:  python -m unittest test_demo -v"""
from __future__ import annotations

import inspect
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import bot
import capacity_model
import finance
import tools
from store import Store


class Chat:
    def __init__(self, store: Store, student_id: str) -> None:
        self.store, self.student_id, self.session = store, student_id, f"test-{student_id}"
        bot.start_session(store, self.session, student_id)

    def say(self, text: str) -> dict:
        return bot.handle_message(self.store, self.session, self.student_id, text)

    @staticmethod
    def text(reply: dict) -> str:
        return "\n".join(m["text"] for m in reply["messages"])


class ClassifierTests(unittest.TestCase):
    def test_intents(self):
        cases = {
            "When is my next class?": "course_content",
            "class recording not playing": "video_issue",
            "Video is not playing": "video_issue",
            "the app keeps crashing": "video_issue",
            "Payment failed but money deducted": "payment_issue",
            "money got deducted, I want a refund": "payment_issue",
            "I want a refund": "refund_request",
            "I want to cancel and get my money back": "refund_request",
            "I forgot my password": "login_issue",
            "OTP not received": "login_issue",
            "Talk to a human": "human_handoff",
            "hello": "greeting",
            "thanks": "thanks",
            "asdfgh": None,
            "paise kat gaye par course nahi mila": "payment_issue",
            "paise kat gaye, refund do": "payment_issue",
            "mujhe paise wapas chahiye": "refund_request",
            "video nahi chal raha": "video_issue",
            "next class kab hai?": "course_content",
            "password bhool gaya": "login_issue",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(bot.rule_classify(text), expected)

    def test_yes_no(self):
        self.assertTrue(bot.parse_yes_no("Yes, it's working"))
        self.assertFalse(bot.parse_yes_no("No, still not playing"))
        self.assertFalse(bot.parse_yes_no("Still can't log in"))
        self.assertTrue(bot.parse_yes_no("Yes, I'm in"))
        self.assertTrue(bot.parse_yes_no("haan ho gaya"))
        self.assertFalse(bot.parse_yes_no("abhi bhi nahi chal raha"))
        self.assertIsNone(bot.parse_yes_no("hmm"))


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def test_course_schedule_answered_by_bot(self):
        reply = Chat(self.store, "S1001").say("When is my next class?")
        self.assertIn("JEE Advanced Physics 2027", Chat.text(reply))
        self.assertEqual(self.store.metrics()["totals"]["bot_resolved"], 1)
        self.assertEqual(self.store.tickets, [])

    def test_android_known_bug_then_escalation_with_diagnostics(self):
        chat = Chat(self.store, "S1001")
        reply = chat.say("Video is not playing")
        self.assertIn("Compatibility player", Chat.text(reply))
        self.assertEqual(self.store.known_issue_hits["BP-1423"], 1)
        chat.say("No, still not playing")
        ticket = self.store.tickets[0]
        self.assertEqual(ticket["queue"], "Tech support")
        self.assertEqual(ticket["collected"]["known_issue"], "BP-1423")
        self.assertEqual(ticket["collected"]["app_version"], "4.6.2")
        self.assertTrue(any(m["role"] == "student" for m in ticket["transcript"]))

    def test_video_fixed_counts_as_resolved(self):
        chat = Chat(self.store, "S1004")
        self.assertIn("Refresh the page", Chat.text(chat.say("video is buffering")))
        chat.say("Yes, it's working")
        self.assertEqual(self.store.metrics()["by_intent"]["video_issue"]["bot_resolved"], 1)

    def test_failed_payment_under_48h_explains_reversal_and_schedules_check(self):
        reply = Chat(self.store, "S1002").say("Payment failed but money deducted")
        text = Chat.text(reply)
        self.assertIn("reverses this automatically", text)
        self.assertIn("412788341102", text)
        self.assertEqual(self.store.tickets, [])
        self.assertEqual(len(self.store.followups), 1)
        results = tools.run_due_followups(self.store)
        self.assertEqual(results[0]["result"], "reversed")
        self.assertIn("reversed", self.store.notifications[-1]["text"])

    def test_captured_payment_unlocks_course(self):
        reply = Chat(self.store, "S1003").say("I paid for UPSC but course is not unlocked")
        self.assertIn("now unlocked", Chat.text(reply))
        self.assertEqual(self.store.students["S1003"]["enrollments"][0]["course_id"], "UPSC-GS")
        followup = Chat(self.store, "S1003").say("When is my next class?")
        self.assertIn("UPSC GS Foundation 2027", Chat.text(followup))

    def test_payment_stuck_over_48h_raises_priority_ticket(self):
        Chat(self.store, "S1006").say("payment failed, money debited")
        ticket = self.store.tickets[0]
        self.assertEqual((ticket["queue"], ticket["priority"]), ("Payments", "high"))
        self.assertEqual(ticket["collected"]["bank_rrn"], "412561123390")

    def test_login_reset_then_escalate(self):
        chat = Chat(self.store, "S1006")
        reply = chat.say("I forgot my password")
        self.assertIn("reset link", Chat.text(reply))
        self.assertIn("reset", self.store.notifications[-1]["text"].lower())
        chat.say("Still can't log in")
        self.assertEqual(self.store.tickets[0]["queue"], "Account support")

    def test_hacked_account_goes_to_security_without_changes(self):
        Chat(self.store, "S1001").say("I think my account got hacked, can't login")
        self.assertEqual(self.store.tickets[0]["queue"], "Account security")
        self.assertEqual(self.store.notifications, [])

    def test_low_risk_refund_is_prepared_not_issued(self):
        chat = Chat(self.store, "S1004")
        chat.say("I want a refund")
        reply = chat.say("Bought it by mistake")
        self.assertIn("reviewed by a person", Chat.text(reply))
        case = self.store.refund_cases[0]
        self.assertEqual(case["status"], "pending_approval")
        self.assertEqual(case["policy"]["verdict"], "Eligible: full refund")
        self.assertEqual(case["risk"]["level"], "Low")
        self.assertTrue(case["recommendation"].startswith("Approve"))
        self.assertFalse(self.store.students["S1004"]["enrollments"][0]["refunded"])

    def test_high_risk_refund_flagged(self):
        chat = Chat(self.store, "S1005")
        chat.say("refund my SSC course")
        chat.say("Course isn't what I expected")
        case = self.store.refund_cases[0]
        self.assertEqual(case["risk"]["level"], "High")
        self.assertEqual(case["policy"]["eligible_amount"], 0)
        self.assertIn("Do not approve", case["recommendation"])

    def test_duplicate_refund_request_is_not_created(self):
        chat = Chat(self.store, "S1004")
        chat.say("I want a refund")
        chat.say("Bought it by mistake")
        reply = chat.say("I want a refund")
        self.assertIn("already have a refund request", Chat.text(reply))
        self.assertEqual(len(self.store.refund_cases), 1)

    def test_proactive_webhook_prevents_ticket(self):
        payment = tools.payment_gateway_webhook(self.store, "S1001")
        self.assertEqual(self.store.notifications[-1]["student_id"], "S1001")
        self.assertIn(payment["bank_rrn"], self.store.notifications[-1]["text"])
        self.assertEqual(self.store.metrics()["totals"]["prevented"], 1)

    def test_unknown_twice_hands_off(self):
        chat = Chat(self.store, "S1001")
        chat.say("asdfgh")
        chat.say("qwerty")
        self.assertEqual(self.store.tickets[0]["queue"], "General support")

    def test_upset_student_gets_high_priority(self):
        chat = Chat(self.store, "S1001")
        chat.say("this is the worst service, talk to a human")
        self.assertEqual(self.store.tickets[0]["priority"], "high")


class RefundGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        chat = Chat(self.store, "S1004")
        chat.say("I want a refund")
        chat.say("Bought it by mistake")
        self.case_id = self.store.refund_cases[0]["id"]

    def test_assistant_has_no_refund_capability(self):
        self.assertNotIn("import finance", inspect.getsource(bot))
        self.assertNotIn("import finance", inspect.getsource(tools))
        names = [n for n, _ in inspect.getmembers(tools, inspect.isfunction)] + \
                [n for n, _ in inspect.getmembers(bot, inspect.isfunction)]
        self.assertFalse([n for n in names if "approve" in n or "issue_refund" in n])

    def test_reviewer_required(self):
        with self.assertRaises(finance.DecisionError):
            finance.decide_refund(self.store, self.case_id, "approve", "  ")

    def test_amount_capped_at_amount_paid(self):
        with self.assertRaises(finance.DecisionError):
            finance.decide_refund(self.store, self.case_id, "approve", "Priya", amount=999999)

    def test_human_approval_notifies_and_audits_once(self):
        case = finance.decide_refund(self.store, self.case_id, "approve", "Priya", note="Duplicate purchase")
        self.assertEqual((case["status"], case["decided_by"]), ("approved", "Priya"))
        self.assertIn("approved", self.store.notifications[-1]["text"])
        self.assertEqual(self.store.audit_log[-1]["actor"], "Priya")
        self.assertTrue(self.store.students["S1004"]["enrollments"][0]["refunded"])
        with self.assertRaises(finance.DecisionError):
            finance.decide_refund(self.store, self.case_id, "reject", "Someone else")


class CapacityModelTests(unittest.TestCase):
    def test_baseline_matches_exhibit_a(self):
        self.assertAlmostEqual(capacity_model.baseline_hours(), 4663.33, places=1)

    def test_scenarios(self):
        agents = {k: capacity_model.run_model(v)["agents_needed"] for k, v in capacity_model.SCENARIOS.items()}
        self.assertEqual(agents, {"chatbot_only": 27, "conservative": 17, "expected": 11})

    def test_waterfall_adds_up(self):
        r = capacity_model.run_model(capacity_model.SCENARIOS["expected"])
        total = r["current_agents"] + sum(s["fte_change"] for s in r["waterfall"])
        self.assertAlmostEqual(total, r["handling_fte"] + r["ai_ops_fte"], delta=0.2)


class ServerSmokeTest(unittest.TestCase):
    def test_http_flow(self):
        import server
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def call(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw

        try:
            self.assertIn(b"BrightPath Support AI", call("/"))
            self.assertEqual(len(call("/api/config")["students"]), 6)
            session = call("/api/session", {"student_id": "S1002"})["session_id"]
            reply = call("/api/chat", {"session_id": session, "student_id": "S1002", "message": "payment failed"})
            self.assertIn("reverses this automatically", reply["messages"][0]["text"])
            self.assertEqual(call("/api/model", {"params": {}})["agents_needed"], 11)
            self.assertIn("tickets", call("/api/console"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            server.STORE.reset()


if __name__ == "__main__":
    unittest.main()
