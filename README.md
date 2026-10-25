# BrightPath Learning: AI support assignment

ClearTax · Forward Deployed Engineer · technical assignment · Abhishek Sharma

## What's in this folder

| File | What it is |
|---|---|
| `01_Solution_Memo.md` | **Start here.** The analysis and recommendation: why a chatbot alone gets 30 → ~27, and how 12 becomes reachable. |
| `02_AI_Chat_Summary.md` | Summary of the AI-assisted working session: approach, key insights, decisions and open questions. |
| `demo/` | Working prototype: student chat assistant, support queue, finance refund approvals, insights and a live impact model. |
| `demo/capacity_model.py` | The numbers behind the memo. `python capacity_model.py` prints every table. |
| `screenshots/` | Screenshots of the demo flows. |
| `run_demo.bat` | Double-click to start the demo and open it in your browser. |

## Run the demo

Requires Python 3.10+ and nothing else (standard library only, no `pip install`).

```powershell
cd demo
python server.py          # then open http://localhost:8000
```

Or double-click `run_demo.bat`.

Optional: set `OPENAI_API_KEY` (and optionally `OPENAI_MODEL` / `OPENAI_BASE_URL` for any OpenAI-compatible API) before starting, and an LLM classifies intent. Without a key, a rule-based classifier is used, so the demo works offline and behaves the same every time.

Run the tests (24 end-to-end checks covering every scenario, the refund guardrails and the model):

```powershell
cd demo
python -m unittest test_demo -v
```

## Five-minute demo script

Student on the left, staff consoles on the right.

1. **Riya: Android video bug.** Click *Video is not playing*. The assistant recognises her device, matches known bug BP-1423 and gives the workaround. Click *No, still not playing*. A ticket appears in **Support queue** with device diagnostics and the full conversation, so the agent never re-asks. **Insights** shows the bug's monthly cost (7.3 agents).
2. **Arjun: payment failed 6 hours ago.** Click *Payment failed but money deducted*. He gets the reversal deadline and bank reference, and a 48h check is scheduled. No ticket is created.
3. **Sneha: paid, course never unlocked.** Type *I paid but the course is not unlocked*. The gateway shows the money was captured, so the assistant unlocks the course. No money moves and no human is needed.
4. **Meera: payment stuck for 60 hours.** Say *payment failed*. A priority ticket goes to Payments with gateway and bank references. Then *I forgot my password* sends a reset link to her inbox (bottom left).
5. **Karan and Vikram: refunds.** Each says *I want a refund* and picks a reason. In **Finance approvals**, Karan's case is "Eligible, Low risk, Approve" and Vikram's is "Outside policy, High risk (4 signals), Do not approve". Approve Karan's as the named reviewer. The student is notified and the audit log records who decided.
6. **Proactive payments.** In **Support queue**, click *Send gateway webhook* for any student. They get an SMS/WhatsApp before they think to contact support. Click *Fast-forward 48 hours* to see reversals confirmed, or escalated automatically when the money isn't back.
7. **Impact model.** Switch between *FAQ chatbot only* (27 agents), *conservative* (17) and *expected* (11), then drag any assumption.

## How the demo is built

```
student message
   -> intent (LLM if configured, else rules)        bot.py / llm.py
   -> playbook for that intent                       bot.py
   -> tools: read data or take safe, reversible steps tools.py
        schedule lookup, payment lookup, unlock captured payment,
        schedule 48h check, reset link / OTP, create ticket,
        prepare refund case (policy check + risk score)
   -> reply + events shown to staff

refund approval  -> finance.py  (named human only; never imported by the assistant)
background jobs  -> gateway webhook, 48h follow-ups   tools.py
capacity model   -> capacity_model.py (memo tables + Impact tab)
```

- **The tool list is the guardrail.** The assistant has no way to issue a refund or move money. Approvals go through `finance.py`, which requires a reviewer's name, caps the amount at what was paid, rejects double decisions and writes an audit entry. A test checks that the assistant modules never import it.
- **Answers come from data, not from the model.** Dates, amounts and statuses come from tool results, and the LLM (when enabled) only picks the intent. This avoids made-up class times or refund promises.
- **Mocked for the demo:** students, courses, payments, gateway, messaging (SMS/WhatsApp/email) and the helpdesk. In production these become the real schedule, payment gateway, auth and helpdesk APIs; the playbooks and tools stay the same.
