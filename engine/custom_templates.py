import json
import os
from dataclasses import dataclass
from typing import Dict, List

CUSTOM_TEMPLATES_PATH = "data/custom_templates.json"

@dataclass
class TemplateRule:
    template_id: str
    template_name: str
    dimensions: List[str]
    weights: Dict[str, float]
    thresholds: Dict[str, float]  # {"go": 8.0, "review": 6.0}

def load_custom_templates() -> Dict[str, TemplateRule]:
    if not os.path.exists(CUSTOM_TEMPLATES_PATH):
        return {}

    try:
        with open(CUSTOM_TEMPLATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    out: Dict[str, TemplateRule] = {}
    for k, v in data.items():
        out[k] = TemplateRule(
            template_id=v["template_id"],
            template_name=v["template_name"],
            dimensions=v["dimensions"],
            weights=v["weights"],
            thresholds=v["thresholds"],
        )
    return out

def save_custom_templates(templates: Dict[str, TemplateRule]) -> None:
    os.makedirs("data", exist_ok=True)

    payload = {}
    for k, t in templates.items():
        payload[k] = {
            "template_id": t.template_id,
            "template_name": t.template_name,
            "dimensions": t.dimensions,
            "weights": t.weights,
            "thresholds": t.thresholds,
        }

    with open(CUSTOM_TEMPLATES_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
