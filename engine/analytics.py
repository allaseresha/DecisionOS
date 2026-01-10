from typing import Dict, List, Tuple
from collections import Counter, defaultdict

def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def compute_metrics(rows: List[Dict]) -> Dict:
    total = len(rows)
    if total == 0:
        return {
            "total": 0,
            "outcomes": {},
            "avg_score": None,
            "confidence": {},
            "followup_outcomes": {},
            "weak_dimensions": []
        }

    # outcomes + confidence
    outcome_counts = Counter([r.get("outcome", "Unknown") for r in rows])
    confidence_counts = Counter([r.get("confidence", "Unknown") for r in rows])

    # avg score
    scores = [safe_float(r.get("final_score"), None) for r in rows]
    scores = [s for s in scores if s is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None

    # follow-up outcomes
    follow = [r.get("follow_up") for r in rows if r.get("follow_up")]
    follow_counts = Counter([f.get("outcome", "Unknown") for f in follow]) if follow else Counter()

    # weak dimensions: count which dimensions appear in lowest_dimensions
    weak = Counter()
    for r in rows:
        exp = r.get("explanation") or {}
        lows = exp.get("lowest_dimensions") or []
        for item in lows:
            dim = item.get("dimension")
            if dim:
                weak[dim] += 1

    weak_dimensions = weak.most_common(10)

    return {
        "total": total,
        "outcomes": dict(outcome_counts),
        "avg_score": avg_score,
        "confidence": dict(confidence_counts),
        "followup_outcomes": dict(follow_counts),
        "weak_dimensions": weak_dimensions
    }

def group_by_parent(rows):
    groups = {}
    for r in rows:
        parent = r.get("parent_id") or r.get("decision_id")
        groups.setdefault(parent, []).append(r)
    # sort by version
    for k in groups:
        groups[k] = sorted(groups[k], key=lambda x: x.get("version", 1))
    return groups

def compute_accuracy_metrics(rows: list[dict]) -> dict:
    """
    Uses record["outcome"] (predicted) + record["follow_up"]["outcome"] (actual)
    to compute confusion-like metrics + confidence calibration.
    """
    followups_total = 0
    tp = fp = tn = fn = 0
    review_bucket = 0

    calib = {"Overconfident": 0, "Underconfident": 0, "Calibrated": 0}

    for r in rows:
        follow = r.get("follow_up") or r.get("followup") or r.get("followUp")
        if not follow:
            continue
        followups_total += 1

        predicted = (r.get("outcome") or "").strip().upper()
        # Normalize predicted outcomes across naming styles
        # (your app uses PROCEED instead of GO)
        if predicted in ("PROCEED", "PROCEED ✅", "YES", "APPROVE"):
            predicted = "GO"
        elif predicted in ("NO GO", "NOGO", "NO_GO", "REJECT", "STOP"):
            predicted = "NO-GO"
        elif predicted in ("REVIEW", "REVISE", "REVIEW/REVISE", "HOLD"):
            predicted = "REVIEW/REVISE"
        conf = (r.get("confidence") or "").strip().title()
        actual = (follow.get("outcome") or "").strip().title()

        # map actual -> polarity
        if actual == "Success":
            actual_label = "Positive"
        elif actual == "Failure":
            actual_label = "Negative"
        else:
            actual_label = "Mixed"  # Partial Success or anything else

        # predicted -> polarity
        if predicted == "GO":
            pred_label = "Positive"
        elif predicted == "NO-GO":
            pred_label = "Negative"
        else:
            pred_label = "Mixed"  # REVIEW/REVISE

        # confusion
        if pred_label == "Mixed" or actual_label == "Mixed":
            review_bucket += 1
        else:
            if pred_label == "Positive" and actual_label == "Positive":
                tp += 1
            elif pred_label == "Positive" and actual_label == "Negative":
                fp += 1
            elif pred_label == "Negative" and actual_label == "Negative":
                tn += 1
            elif pred_label == "Negative" and actual_label == "Positive":
                fn += 1

        # confidence calibration (only if actual is Success/Failure)
        if actual_label in ("Positive", "Negative"):
            if conf == "High" and actual_label == "Negative":
                calib["Overconfident"] += 1
            elif conf == "Low" and actual_label == "Positive":
                calib["Underconfident"] += 1
            else:
                calib["Calibrated"] += 1

    total_strict = tp + tn + fp + fn
    accuracy = (tp + tn) / total_strict if total_strict > 0 else None

    return {
        "followups_total": followups_total,
        "tp": tp,
        "fp": fp,  # False GO
        "tn": tn,
        "fn": fn,  # False NO-GO
        "review_bucket": review_bucket,
        "total_strict": total_strict,
        "accuracy": round(accuracy, 3) if accuracy is not None else None,
        "calibration": calib,
    }
def compute_pattern_insights(records: list) -> dict:
    """
    STEP 14 — Decision Pattern Intelligence
    Produces insights by decision_type using:
    - follow_up outcomes (Success/Failure/Partial Success)
    - confidence bands
    - scenario_stress_test spread (Step 11)
    """

    def norm_type(x: str) -> str:
        x = (x or "").strip()
        return x if x else "Unknown"

    def norm_conf(x: str) -> str:
        return (x or "").strip().upper()

    # Convert confidence label -> expected probability (simple heuristic)
    # Tune later if you want.
    def conf_to_p(conf: str) -> float:
        conf = norm_conf(conf)
        if conf == "HIGH":
            return 0.80
        if conf == "MEDIUM":
            return 0.60
        if conf == "LOW":
            return 0.40
        return 0.60  # default

    # Convert follow-up outcome -> numeric success
    def follow_to_success(outcome: str):
        o = (outcome or "").strip().lower()
        if o == "success":
            return 1.0
        if o == "partial success":
            return 0.5
        if o == "failure":
            return 0.0
        return None

    by_type = {}

    for r in records:
        t = norm_type(r.get("decision_type"))
        by_type.setdefault(t, {
            "count": 0,
            "avg_score_sum": 0.0,
            "avg_score_n": 0,
            "spread_sum": 0.0,
            "spread_n": 0,
            "followups_n": 0,
            "success_sum": 0.0,
            "conf_sum": 0.0,
        })

        bucket = by_type[t]
        bucket["count"] += 1

        # avg score
        try:
            s = float(r.get("final_score", 0))
            bucket["avg_score_sum"] += s
            bucket["avg_score_n"] += 1
        except Exception:
            pass

        # avg scenario spread (Step 11)
        sst = r.get("scenario_stress_test") or {}
        spread = sst.get("spread")
        if spread is not None:
            try:
                bucket["spread_sum"] += float(spread)
                bucket["spread_n"] += 1
            except Exception:
                pass

        # follow-up learning + confidence calibration
        fu = r.get("follow_up") or {}
        actual = follow_to_success(fu.get("outcome"))
        if actual is not None:
            bucket["followups_n"] += 1
            bucket["success_sum"] += actual
            bucket["conf_sum"] += conf_to_p(r.get("confidence"))

    # finalize output
    rows = []
    for t, b in by_type.items():
        avg_score = round(b["avg_score_sum"] / b["avg_score_n"], 2) if b["avg_score_n"] else None
        avg_spread = round(b["spread_sum"] / b["spread_n"], 2) if b["spread_n"] else None

        if b["followups_n"]:
            actual_rate = b["success_sum"] / b["followups_n"]
            expected_rate = b["conf_sum"] / b["followups_n"]
            calibration_gap = actual_rate - expected_rate  # + means underconfident, - means overconfident
            rows.append({
                "Type": t,
                "Decisions": b["count"],
                "Follow-ups": b["followups_n"],
                "Avg Score": avg_score,
                "Avg Spread": avg_spread,
                "Actual Success Rate": round(actual_rate, 2),
                "Expected (from Confidence)": round(expected_rate, 2),
                "Calibration Gap": round(calibration_gap, 2),
                "Signal": "UNDERCONFIDENT" if calibration_gap > 0.10 else ("OVERCONFIDENT" if calibration_gap < -0.10 else "CALIBRATED"),
            })
        else:
            rows.append({
                "Type": t,
                "Decisions": b["count"],
                "Follow-ups": 0,
                "Avg Score": avg_score,
                "Avg Spread": avg_spread,
                "Actual Success Rate": None,
                "Expected (from Confidence)": None,
                "Calibration Gap": None,
                "Signal": "NEED FOLLOW-UPS",
            })

    return {"rows": rows}

def compute_template_improvements(records: list) -> dict:
    """
    STEP 15 — Template Improvement Intelligence
    Learns which templates should change based on:
    - False GO / False NO-GO
    - Confidence miscalibration
    - Scenario risk spread
    """

    templates = {}

    def norm(x):
        return (x or "").strip()

    for r in records:
        tpl = norm(r.get("template_name", "Unknown"))
        templates.setdefault(tpl, {
            "count": 0,
            "false_go": 0,
            "false_nogo": 0,
            "spread_sum": 0.0,
            "spread_n": 0,
        })

        t = templates[tpl]
        t["count"] += 1

        # Scenario spread
        sst = r.get("scenario_stress_test") or {}
        spread = sst.get("spread")
        if spread is not None:
            try:
                t["spread_sum"] += float(spread)
                t["spread_n"] += 1
            except Exception:
                pass

        # Follow-up classification
        fu = r.get("follow_up") or {}
        actual = (fu.get("outcome") or "").lower()
        predicted = (r.get("outcome") or "").upper()

        # normalize predicted
        if predicted in ("PROCEED", "YES", "APPROVE"):
            predicted = "GO"
        if predicted in ("NO GO", "NOGO", "STOP"):
            predicted = "NO-GO"

        if actual == "failure" and predicted == "GO":
            t["false_go"] += 1
        if actual == "success" and predicted == "NO-GO":
            t["false_nogo"] += 1

    # Build recommendations
    recs = []
    for tpl, t in templates.items():
        if t["count"] < 3:
            continue  # not enough data

        avg_spread = round(t["spread_sum"] / t["spread_n"], 2) if t["spread_n"] else None

        if t["false_go"] >= 2:
            recs.append({
                "Template": tpl,
                "Issue": "Too many false GO decisions",
                "Recommendation": "Increase Risk weight or raise GO threshold",
                "Avg Spread": avg_spread
            })

        if t["false_nogo"] >= 2:
            recs.append({
                "Template": tpl,
                "Issue": "Too many false NO-GO decisions",
                "Recommendation": "Lower NO-GO threshold or increase Value weight",
                "Avg Spread": avg_spread
            })

        if avg_spread and avg_spread > 3:
            recs.append({
                "Template": tpl,
                "Issue": "High uncertainty (large scenario spread)",
                "Recommendation": "Add assumptions, require validation, or increase confidence penalty",
                "Avg Spread": avg_spread
            })

    return {"recommendations": recs}
