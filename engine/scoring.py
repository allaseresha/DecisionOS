# engine/scoring.py

ENGINE_VERSION = "0.1.0"
RULESET_VERSION = "0.1.0"

from typing import Dict
from .templates import TemplateRule

def clamp_score(score: float) -> float:
    return max(0.0, min(10.0, float(score)))

def compute_weighted_score(rule: TemplateRule, scores: Dict[str, float]) -> float:
    total = 0.0
    for dim in rule.dimensions:
        total += clamp_score(scores.get(dim, 0.0)) * rule.weights[dim]
    return round(total, 2)

def determine_outcome(rule: TemplateRule, final_score: float) -> str:
    for min_score, label in rule.thresholds:
        if final_score >= min_score:
            return label
    return rule.thresholds[-1][1]

def confidence_band(score: float) -> str:
    if score >= 8.0:
        return "HIGH"
    if score >= 6.0:
        return "MEDIUM"
    return "LOW"