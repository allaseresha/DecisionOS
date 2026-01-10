from typing import Dict, Any, List

DEFAULT_ACTIONS = {
    "Value": [
        "Clarify measurable business impact (revenue, cost, time saved).",
        "Validate customer pain with 5–10 interviews.",
        "Define success metrics and a 30-day experiment."
    ],
    "Feasibility": [
        "Break into milestones and estimate effort for each.",
        "Identify required skills/tools and fill gaps.",
        "Create a small prototype to reduce uncertainty."
    ],
    "Risk": [
        "List top 5 risks (technical, legal, market, delivery).",
        "Add mitigations and owners for each risk.",
        "Run a pre-mortem: why might this fail?"
    ],
    "Urgency": [
        "Define deadline and what happens if delayed.",
        "Confirm stakeholder priority against other work.",
        "Set a decision date + fast validation plan."
    ],
}

def build_playbook(rule, scores: Dict[str, float], final_score: float, outcome: str, explanation: Dict[str, Any]) -> Dict[str, Any]:
    # lowest dimensions from explainability, fallback to raw scores if missing
    lows = explanation.get("lowest_dimensions") or []
    if lows:
        low_dims = [x["dimension"] for x in lows][:3]
    else:
        low_dims = sorted(scores.keys(), key=lambda d: scores[d])[:3]

    actions: List[Dict[str, Any]] = []
    for d in low_dims:
        actions.append({
            "dimension": d,
            "score": scores.get(d),
            "recommended_actions": DEFAULT_ACTIONS.get(d, [
                "Define what 'good' looks like for this dimension.",
                "Collect evidence to increase confidence.",
                "Create a small test to improve this score."
            ])
        })

    flags = []
    # Simple flags: low score dimensions + overall outcome
    for d, s in scores.items():
        if s <= 3:
            flags.append(f"Very low score detected in '{d}' ({s}).")

    if outcome in ["NO-GO", "REVIEW / REVISE"]:
        flags.append("Outcome indicates risk — treat as not approved until fixes are done.")

    checklist = [
        "Confirm decision owner + stakeholders",
        "Write assumptions explicitly",
        "Define success metrics",
        "Run quick validation test",
        "Re-score after fixes"
    ]

    return {
        "summary": f"Top focus areas: {', '.join(low_dims)}",
        "focus_dimensions": low_dims,
        "actions": actions,
        "flags": flags,
        "checklist": checklist,
    }