from dataclasses import dataclass
from typing import Dict, List, Tuple

@dataclass(frozen=True)
class TemplateRule:
    template_id: str
    template_name: str
    dimensions: List[str]
    weights: Dict[str, float]  # must sum to 1.0
    thresholds: List[Tuple[float, str]]  # (min_score_inclusive, label)

TEMPLATES: Dict[str, TemplateRule] = {
    "go_no_go": TemplateRule(
        template_id="go_no_go",
        template_name="Go / No-Go Decision",
        dimensions=["Value", "Feasibility", "Risk", "Alignment", "Urgency"],
        weights={
            "Value": 0.25,
            "Feasibility": 0.25,
            "Risk": 0.20,
            "Alignment": 0.20,
            "Urgency": 0.10,
        },
        thresholds=[
            (7.5, "PROCEED"),
            (6.0, "REVIEW / REVISE"),
            (0.0, "DO NOT PROCEED"),
        ],
    ),
    "risk_exposure": TemplateRule(
        template_id="risk_exposure",
        template_name="Risk Exposure Assessment",
        dimensions=["Financial Risk", "Operational Risk", "Legal/Compliance Risk", "Reputational Risk", "Control Readiness"],
        weights={
            "Financial Risk": 0.30,
            "Operational Risk": 0.25,
            "Legal/Compliance Risk": 0.20,
            "Reputational Risk": 0.15,
            "Control Readiness": 0.10,
        },
        thresholds=[
            (7.5, "LOW RISK"),
            (6.0, "MODERATE RISK"),
            (0.0, "HIGH RISK"),
        ],
    ),
    "change_impact": TemplateRule(
        template_id="change_impact",
        template_name="Change Impact Decision",
        dimensions=["Impact Value", "Change Complexity", "Team Readiness", "Risk of Resistance", "Reversibility"],
        weights={
            "Impact Value": 0.30,
            "Change Complexity": 0.25,
            "Team Readiness": 0.20,
            "Risk of Resistance": 0.15,
            "Reversibility": 0.10,
        },
        thresholds=[
            (7.5, "SAFE TO IMPLEMENT"),
            (6.0, "IMPLEMENT WITH CAUTION"),
            (0.0, "HIGH IMPACT RISK"),
        ],
    ),
}