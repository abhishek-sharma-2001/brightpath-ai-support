"""Capacity model: how many support agents BrightPath needs under different plans.

Run `python capacity_model.py` to print the tables quoted in the solution memo.
The demo server imports `run_model` to power the Impact Model tab.
"""
from __future__ import annotations

import math

CATEGORIES = [
    {"key": "content", "label": "Course content questions", "volume": 9500, "aht": 3},
    {"key": "video", "label": "Video not playing / app issues", "volume": 6200, "aht": 14},
    {"key": "payment", "label": "Payment failed but money deducted", "volume": 4100, "aht": 20},
    {"key": "refund", "label": "Refund requests", "volume": 2700, "aht": 25},
    {"key": "login", "label": "Login / password problems", "volume": 2500, "aht": 6},
]
CURRENT_AGENTS = 30
TARGET_AGENTS = 12
ANDROID_SHARE_OF_VIDEO = 0.78
SELF_REVERSING_SHARE_OF_PAYMENT = 0.60

PARAM_INFO = {
    "content_bot": "Share of content questions fully answered by the bot",
    "content_residual_aht": "Minutes per content question that still reaches a human",
    "android_fix": "Share of Android video tickets eliminated by fixing the player",
    "video_bot": "Share of remaining video tickets solved by guided troubleshooting",
    "video_residual_aht": "Minutes per escalated video ticket (diagnostics pre-collected)",
    "reversal_deflect": "Share of self-reversing payment tickets that never reach a human",
    "reversal_residual_aht": "Minutes per self-reversing payment ticket that still reaches a human",
    "payment_action_bot": "Share of genuine payment problems auto-fixed (e.g. captured but not enrolled)",
    "payment_action_residual_aht": "Minutes per genuine payment problem handled by a human",
    "refund_aht": "Minutes of human review per refund (case prepared by AI)",
    "login_bot": "Share of login problems solved by self-serve reset/OTP",
    "login_residual_aht": "Minutes per login problem that still reaches a human",
    "ai_ops_fte": "Agents reassigned to run the AI: QA, knowledge base, bot tuning",
}

SCENARIOS = {
    "chatbot_only": {
        "label": "FAQ chatbot only (the literal ask)",
        "content_bot": 0.75, "content_residual_aht": 4,
        "android_fix": 0.0, "video_bot": 0.10, "video_residual_aht": 14,
        "reversal_deflect": 0.15, "reversal_residual_aht": 20,
        "payment_action_bot": 0.0, "payment_action_residual_aht": 20,
        "refund_aht": 25,
        "login_bot": 0.40, "login_residual_aht": 6,
        "ai_ops_fte": 1.0,
    },
    "conservative": {
        "label": "Full plan, conservative",
        "content_bot": 0.60, "content_residual_aht": 4,
        "android_fix": 0.40, "video_bot": 0.20, "video_residual_aht": 13,
        "reversal_deflect": 0.70, "reversal_residual_aht": 10,
        "payment_action_bot": 0.10, "payment_action_residual_aht": 17,
        "refund_aht": 14,
        "login_bot": 0.50, "login_residual_aht": 7,
        "ai_ops_fte": 2.0,
    },
    "expected": {
        "label": "Full plan, expected",
        "content_bot": 0.75, "content_residual_aht": 4,
        "android_fix": 0.70, "video_bot": 0.30, "video_residual_aht": 12,
        "reversal_deflect": 0.85, "reversal_residual_aht": 8,
        "payment_action_bot": 0.25, "payment_action_residual_aht": 15,
        "refund_aht": 10,
        "login_bot": 0.65, "login_residual_aht": 7,
        "ai_ops_fte": 1.5,
    },
}


DO_NOTHING = {
    "content_bot": 0.0, "content_residual_aht": 3,
    "android_fix": 0.0, "video_bot": 0.0, "video_residual_aht": 14,
    "reversal_deflect": 0.0, "reversal_residual_aht": 20,
    "payment_action_bot": 0.0, "payment_action_residual_aht": 20,
    "refund_aht": 25,
    "login_bot": 0.0, "login_residual_aht": 6,
    "ai_ops_fte": 0.0,
}

LEVERS = [
    ("Fix the Android video player (engineering, not AI)", ["android_fix"]),
    ("Proactive 'your bank will reverse this' alerts + bot payment lookup", ["reversal_deflect", "reversal_residual_aht"]),
    ("AI-prepared refund cases (a human still approves)", ["refund_aht"]),
    ("Bot answers course / schedule questions", ["content_bot", "content_residual_aht"]),
    ("Guided video troubleshooting + auto-collected diagnostics", ["video_bot", "video_residual_aht"]),
    ("Payment auto-reconcile + pre-pulled gateway data", ["payment_action_bot", "payment_action_residual_aht"]),
    ("Self-serve password reset / OTP", ["login_bot", "login_residual_aht"]),
    ("New work: AI operations (QA, knowledge base, tuning)", ["ai_ops_fte"]),
]


SENSITIVITY = [
    ("Expected plan", {}),
    ("Android fix never ships", {"android_fix": 0.0}),
    ("Android fix only 40% effective", {"android_fix": 0.40}),
    ("Refund review takes 15 min, not 10", {"refund_aht": 15}),
    ("Proactive payment alerts deflect 60%, not 85%", {"reversal_deflect": 0.60}),
    ("Bot answers 50% of content questions, not 75%", {"content_bot": 0.50}),
    ("Every bot resolution rate 20 points lower", {"content_bot": 0.55, "video_bot": 0.10, "reversal_deflect": 0.65,
                                                   "payment_action_bot": 0.05, "login_bot": 0.45}),
    ("Month 3 snapshot: quick wins live, no Android fix or refund console yet",
     {"content_bot": 0.60, "login_bot": 0.50, "reversal_deflect": 0.80, "android_fix": 0.0, "video_bot": 0.15,
      "video_residual_aht": 13, "payment_action_bot": 0.0, "payment_action_residual_aht": 18, "refund_aht": 25,
      "ai_ops_fte": 1.0}),
]


def baseline_hours() -> float:
    return sum(c["volume"] * c["aht"] for c in CATEGORIES) / 60


def hours_per_agent() -> float:
    """Handling hours one agent delivers per month, calibrated to today's team."""
    return baseline_hours() / CURRENT_AGENTS


def _human_load(key: str, volume: float, p: dict) -> tuple[float, float]:
    """Return (tickets reaching a human, human minutes) for one category."""
    if key == "content":
        tickets = volume * (1 - p["content_bot"])
        return tickets, tickets * p["content_residual_aht"]
    if key == "video":
        android = volume * ANDROID_SHARE_OF_VIDEO
        remaining = android * (1 - p["android_fix"]) + (volume - android)
        tickets = remaining * (1 - p["video_bot"])
        return tickets, tickets * p["video_residual_aht"]
    if key == "payment":
        reversing = volume * SELF_REVERSING_SHARE_OF_PAYMENT
        genuine = volume - reversing
        t_rev = reversing * (1 - p["reversal_deflect"])
        t_gen = genuine * (1 - p["payment_action_bot"])
        return t_rev + t_gen, t_rev * p["reversal_residual_aht"] + t_gen * p["payment_action_residual_aht"]
    if key == "refund":
        return volume, volume * p["refund_aht"]
    if key == "login":
        tickets = volume * (1 - p["login_bot"])
        return tickets, tickets * p["login_residual_aht"]
    raise ValueError(key)


def run_model(params: dict) -> dict:
    p = {**SCENARIOS["expected"], **{k: float(v) for k, v in params.items() if k in PARAM_INFO}}
    hpa = hours_per_agent()
    rows = []
    for c in CATEGORIES:
        tickets, minutes = _human_load(c["key"], c["volume"], p)
        rows.append({
            "key": c["key"],
            "label": c["label"],
            "volume": c["volume"],
            "aht": c["aht"],
            "baseline_hours": round(c["volume"] * c["aht"] / 60, 1),
            "human_tickets": round(tickets),
            "new_hours": round(minutes / 60, 1),
        })
    new_hours = sum(r["new_hours"] for r in rows)
    handling_fte = new_hours / hpa
    total_fte = handling_fte + p["ai_ops_fte"]
    return {
        "params": p,
        "rows": rows,
        "baseline_hours": round(baseline_hours(), 1),
        "new_hours": round(new_hours, 1),
        "hours_per_agent": round(hpa, 1),
        "handling_fte": round(handling_fte, 1),
        "ai_ops_fte": p["ai_ops_fte"],
        "agents_needed": math.ceil(round(total_fte, 2)),
        "current_agents": CURRENT_AGENTS,
        "target_agents": TARGET_AGENTS,
        "human_tickets": sum(r["human_tickets"] for r in rows),
        "waterfall": lever_waterfall(p),
    }


def _total_fte(p: dict) -> float:
    minutes = sum(_human_load(c["key"], c["volume"], p)[1] for c in CATEGORIES)
    return minutes / 60 / hours_per_agent() + p["ai_ops_fte"]


def lever_waterfall(params: dict) -> list[dict]:
    """Apply each lever in turn, starting from today's team, and record the FTE change."""
    current = dict(DO_NOTHING)
    before = _total_fte(current)
    steps = []
    for label, keys in LEVERS:
        for k in keys:
            current[k] = params[k]
        after = _total_fte(current)
        steps.append({"lever": label, "fte_change": round(after - before, 1)})
        before = after
    return steps


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def print_report() -> None:
    total_tickets = sum(c["volume"] for c in CATEGORIES)
    base_h = baseline_hours()
    hpa = hours_per_agent()

    print("## Baseline: ticket share vs. time share\n")
    print("| Ticket type | Tickets/mo | % of tickets | AHT (min) | Agent hours/mo | % of agent time | Agents (FTE) |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for c in CATEGORIES:
        h = c["volume"] * c["aht"] / 60
        print(f"| {c['label']} | {_fmt(c['volume'])} | {c['volume'] / total_tickets:.0%} | {c['aht']} "
              f"| {_fmt(h)} | {h / base_h:.0%} | {h / hpa:.1f} |")
    print(f"| **Total** | **{_fmt(total_tickets)}** | 100% | {base_h * 60 / total_tickets:.1f} avg "
          f"| **{_fmt(base_h)}** | 100% | **{CURRENT_AGENTS}** |")
    print(f"\nOne agent = {hpa:.1f} handling hours/month "
          f"({hpa / 176:.0%} occupancy on a 176-hour month).")

    android_h = 6200 * ANDROID_SHARE_OF_VIDEO * 14 / 60
    wait_h = 4100 * SELF_REVERSING_SHARE_OF_PAYMENT * 20 / 60
    content_h = 9500 * 3 / 60
    login_h = 2500 * 6 / 60
    refund_h = 2700 * 25 / 60
    print("\n## What single problems cost (agent-equivalents per month)\n")
    print("| Problem | Tickets/mo | Agent hours/mo | Agents (FTE) |")
    print("|---|---:|---:|---:|")
    print(f"| Android video bug (78% of video tickets) | {_fmt(6200 * ANDROID_SHARE_OF_VIDEO)} | {_fmt(android_h)} | {android_h / hpa:.1f} |")
    print(f"| \"Wait 48h, your bank will reverse it\" tickets (60% of payment) | {_fmt(4100 * SELF_REVERSING_SHARE_OF_PAYMENT)} | {_fmt(wait_h)} | {wait_h / hpa:.1f} |")
    print(f"| Refund handling (human approval stays) | 2,700 | {_fmt(refund_h)} | {refund_h / hpa:.1f} |")
    print(f"| All course content questions (the chatbot's sweet spot) | 9,500 | {_fmt(content_h)} | {content_h / hpa:.1f} |")
    print(f"| All login problems | 2,500 | {_fmt(login_h)} | {login_h / hpa:.1f} |")

    results = {k: run_model(v) for k, v in SCENARIOS.items()}
    print("\n## Scenarios: agent hours per month after six months\n")
    header = "| Ticket type | Today | " + " | ".join(SCENARIOS[k]["label"] for k in SCENARIOS) + " |"
    print(header)
    print("|---|---:|" + "---:|" * len(SCENARIOS))
    for i, c in enumerate(CATEGORIES):
        cells = " | ".join(_fmt(results[k]["rows"][i]["new_hours"]) for k in SCENARIOS)
        print(f"| {c['label']} | {_fmt(c['volume'] * c['aht'] / 60)} | {cells} |")
    print(f"| **Handling hours** | **{_fmt(base_h)}** | "
          + " | ".join(f"**{_fmt(results[k]['new_hours'])}**" for k in SCENARIOS) + " |")
    print(f"| Tickets reaching a human | {_fmt(total_tickets)} | "
          + " | ".join(_fmt(results[k]["human_tickets"]) for k in SCENARIOS) + " |")
    print(f"| Handling agents (FTE) | {CURRENT_AGENTS} | "
          + " | ".join(f"{results[k]['handling_fte']:.1f}" for k in SCENARIOS) + " |")
    print(f"| + AI operations (QA, knowledge base) | 0 | "
          + " | ".join(f"{results[k]['ai_ops_fte']:.1f}" for k in SCENARIOS) + " |")
    print(f"| **Team size needed** | **{CURRENT_AGENTS}** | "
          + " | ".join(f"**{results[k]['agents_needed']}**" for k in SCENARIOS) + " |")

    exp = results["expected"]
    print("\n## Where the savings come from (expected scenario)\n")
    print("| Step | Change in agents | Running total |")
    print("|---|---:|---:|")
    running = float(CURRENT_AGENTS)
    print(f"| Today | | {running:.1f} |")
    for step in exp["waterfall"]:
        running += step["fte_change"]
        print(f"| {step['lever']} | {step['fte_change']:+.1f} | {running:.1f} |")
    print(f"| **Team size (rounded up)** | | **{exp['agents_needed']}** |")

    print("\n## Sensitivity: what the target depends on\n")
    print("| Change to the expected plan | Agents (exact) | Team size |")
    print("|---|---:|---:|")
    for label, overrides in SENSITIVITY:
        r = run_model({**SCENARIOS["expected"], **overrides})
        print(f"| {label} | {r['handling_fte'] + r['ai_ops_fte']:.1f} | {r['agents_needed']} |")

    print("\n## Scenario assumptions\n")
    print("| Assumption | " + " | ".join(SCENARIOS[k]["label"] for k in SCENARIOS) + " |")
    print("|---|" + "---:|" * len(SCENARIOS))
    for key, desc in PARAM_INFO.items():
        vals = []
        for k in SCENARIOS:
            v = SCENARIOS[k][key]
            vals.append(f"{v:.0%}" if 0 < v < 1 or (v == 0 and "aht" not in key and "fte" not in key) else f"{v:g}")
        print(f"| {desc} | " + " | ".join(vals) + " |")


if __name__ == "__main__":
    print_report()
