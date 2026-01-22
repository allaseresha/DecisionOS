from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class ReadinessResult:
    score: int
    status: str          # "BLOCK" | "REVIEW" | "APPROVE"
    min_required: int
    issues: List[str]
    blockers: List[str]

def _empty(x) -> bool:
    if x is None:
        return True
    if isinstance(x, str):
        return len(x.strip()) == 0
    if isinstance(x, list):
        return len([a for a in x if str(a).strip()]) == 0
    return False

def _confidence_to_number(conf) -> int:
    """
    Your app produces confidence_band(final_score) like: "High", "Medium", "Low"
    Convert to a numeric value so readiness can detect overconfidence patterns.
    """
    if isinstance(conf, (int, float)):
        return int(conf)
    conf = str(conf).strip().lower()
    if conf in ["high", "h"]:
        return 85
    if conf in ["medium", "med", "m"]:
        return 70
    if conf in ["low", "l"]:
        return 55
    return 70

def calculate_decision_readiness(decision: Dict[str, Any]) -> ReadinessResult:
    score = 100
    issues: List[str] = []
    blockers: List[str] = []

    owner = decision.get("owner", "")
    decision_type = decision.get("decision_type", "Strategic")
    decision_class = decision.get("decision_class", "Two-way")
    stakeholders = decision.get("stakeholders", [])
    assumptions = decision.get("assumptions", [])
    risks = decision.get("risks", [])
    confidence = _confidence_to_number(decision.get("confidence", "Medium"))
    weights = decision.get("weights", {})
    responsibility_confirmed = bool(decision.get("responsibility_confirmed", False))

    # -------------------------
    # Pillar 1: Completeness
    # -------------------------
    if _empty(owner):
        blockers.append("Decision owner is missing (accountability not defined).")

    # Weights validity (NOT sum=100)
    if not isinstance(weights, dict) or len(weights) == 0:
        blockers.append("Template weights are missing/invalid (cannot evaluate reliably).")
    else:
        bad = [k for k, v in weights.items() if float(v) <= 0]
        if bad:
            blockers.append(f"Invalid weights (<=0) found for: {', '.join(bad)}")

    if _empty(assumptions):
        score -= 10
        issues.append("Key assumptions are not documented.")

    if _empty(stakeholders):
        score -= 5
        issues.append("Stakeholders not listed (visibility may be incomplete).")

    # If core blockers exist, cap score
    if blockers:
        score = min(score, 50)

    # -------------------------
    # Pillar 2: Risk awareness
    # -------------------------
    if decision_class == "One-way" and _empty(risks):
        blockers.append("One-way decisions require explicit risks/unknowns.")
    elif decision_class == "Two-way" and _empty(risks):
        score -= 10
        issues.append("Consider documenting at least one risk/unknown.")

    # -------------------------
    # Pillar 3: Confidence integrity (anti-overconfidence)
    # -------------------------
    if confidence >= 80 and _empty(risks):
        score -= 20
        issues.append("High confidence without documented risks suggests overconfidence.")
    if confidence >= 70 and _empty(assumptions):
        score -= 10
        issues.append("Confidence should be backed by explicit assumptions.")

    # -------------------------
    # Pillar 4: Severity thresholds
    # -------------------------
    if decision_type == "Strategic" and decision_class == "One-way":
        min_required = 75
    else:
        min_required = 60

    # -------------------------
    # Pillar 5: Accountability (hard gate)
    # -------------------------
    if not responsibility_confirmed:
        blockers.append("Accountability confirmation is required to approve.")

    # Clamp
    score = max(0, min(score, 100))

    # Status
    if blockers or score < min_required:
        status = "BLOCK"
    elif score < 75:
        status = "REVIEW"
    else:
        status = "APPROVE"

    return ReadinessResult(
        score=score,
        status=status,
        min_required=min_required,
        issues=issues,
        blockers=blockers,
    )