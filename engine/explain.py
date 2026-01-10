from typing import Dict, List, Tuple
from .templates import TemplateRule
from .scoring import clamp_score

def explain_decision(rule: TemplateRule, scores: Dict[str, float]) -> Dict[str, object]:
    """
    Returns:
    - lowest_dimensions (2)
    - highest_dimensions (2)
    - top_positive_contributors (2) by weighted contribution
    - top_negative_contributors (2) by weighted contribution
    """
    items: List[Tuple[str, float, float]] = []  # (dimension, score, weighted_contribution)

    for dim in rule.dimensions:
        s = clamp_score(scores.get(dim, 0.0))
        contribution = s * rule.weights[dim]
        items.append((dim, s, contribution))

    # Lowest/highest by raw score
    sorted_by_score = sorted(items, key=lambda x: x[1])
    lowest = sorted_by_score[:2]
    highest = list(reversed(sorted_by_score))[:2]

    # Positive/negative by contribution
    sorted_by_contrib = sorted(items, key=lambda x: x[2])
    negative = sorted_by_contrib[:2]                     # weakest contributions
    positive = list(reversed(sorted_by_contrib))[:2]     # strongest contributions

    return {
        "lowest_dimensions": [{"dimension": d, "score": s} for d, s, _ in lowest],
        "highest_dimensions": [{"dimension": d, "score": s} for d, s, _ in highest],
        "top_positive_contributors": [{"dimension": d, "weighted": round(c, 2)} for d, _, c in positive],
        "top_negative_contributors": [{"dimension": d, "weighted": round(c, 2)} for d, _, c in negative],
    }