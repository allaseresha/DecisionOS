import os
import datetime as dt
import streamlit as st

# Must be first Streamlit call
st.set_page_config(page_title="DecisionOS (MVP)", layout="wide")

# ----------------------------
# Imports (engine)
# ----------------------------
from engine.templates import TEMPLATES
from engine.scoring import (
    compute_weighted_score,
    determine_outcome,
    confidence_band,
    ENGINE_VERSION,
    RULESET_VERSION,
)
from engine.explain import explain_decision
from engine.readiness import calculate_decision_readiness
from engine.storage import (
    append_jsonl,
    read_jsonl,
    now_iso,
    new_id,
    update_decision_outcome,
    delete_decisions_by_title_contains,
    delete_legacy_no_id,
    migrate_history_schema,
)
from engine.analytics import (
    compute_metrics,
    group_by_parent,
    compute_accuracy_metrics,
    compute_pattern_insights,
    compute_template_improvements,
)
from engine.custom_templates import load_custom_templates, save_custom_templates, TemplateRule
from engine.pdf_report import write_pdf_report
from engine.playbook import build_playbook

HISTORY_PATH = "data/decision_history.jsonl"
REPORTS_DIR = "reports"

os.makedirs("data", exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# ----------------------------
# Session state
# ----------------------------
if "last_record" not in st.session_state:
    st.session_state.last_record = None
if "migration_summary" not in st.session_state:
    st.session_state.migration_summary = None
if "last_playbook" not in st.session_state:
    st.session_state.last_playbook = None

# Demo mode (read-only demo decision)
if "demo_mode" not in st.session_state:
    st.session_state.demo_mode = False

# Ensure these exist so we can safely update them BEFORE widgets render
if "stakeholders_text" not in st.session_state:
    st.session_state["stakeholders_text"] = ""
if "assumptions_text" not in st.session_state:
    st.session_state["assumptions_text"] = ""
if "unknowns_text" not in st.session_state:
    st.session_state["unknowns_text"] = ""
if "assumptions_notes" not in st.session_state:
    st.session_state["assumptions_notes"] = ""
if "unknowns_notes" not in st.session_state:
    st.session_state["unknowns_notes"] = ""

# ----------------------------
# Enterprise UI styling
# ----------------------------
st.markdown(
    """
<style>
/* tighten + enterprise feel */
.block-container { padding-top: 1.1rem; padding-bottom: 2rem; }
div[data-testid="stSidebar"] { padding-top: 1rem; }
h1, h2, h3 { letter-spacing: -0.02em; }
small, .stCaption { color: rgba(0,0,0,0.62) !important; }

/* preset buttons row */
.preset-wrap { margin-top: 0.25rem; margin-bottom: 0.75rem; }

/* executive card feel */
.exec-card {
  padding: 1rem 1rem;
  border-radius: 16px;
  border: 1px solid rgba(0,0,0,0.08);
  background: rgba(255,255,255,0.75);
}
.exec-title { font-weight: 800; font-size: 1.1rem; margin-bottom: 0.25rem; }
.exec-sub { color: rgba(0,0,0,0.70); margin-bottom: 0.65rem; }
.exec-kv { margin: 0.2rem 0; }
.exec-list { margin: 0.25rem 0 0 1.1rem; }
</style>
""",
    unsafe_allow_html=True,
)

# ----------------------------
# Helpers
# ----------------------------
def badge(text: str, tone: str = "neutral"):
    tones = {
        "neutral": ("#111827", "#E5E7EB"),
        "good": ("#065F46", "#D1FAE5"),
        "warn": ("#92400E", "#FEF3C7"),
        "bad": ("#7F1D1D", "#FEE2E2"),
        "info": ("#1E3A8A", "#DBEAFE"),
    }
    fg, bg = tones.get(tone, tones["neutral"])
    st.markdown(
        f"""
        <span style="
            display:inline-block;
            padding:0.25rem 0.55rem;
            border-radius:999px;
            font-size:0.80rem;
            font-weight:600;
            color:{fg};
            background:{bg};
            border:1px solid rgba(0,0,0,0.06);
        ">{text}</span>
        """,
        unsafe_allow_html=True,
    )


def section_title(title: str, subtitle: str = ""):
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)


def _is_blank(x: str) -> bool:
    return not (x or "").strip()


def compute_completeness(ss: dict) -> dict:
    fields = {
        "title": not _is_blank(ss.get("decision_title", "")),
        "context": not _is_blank(ss.get("decision_context", "")),
        "owner": not _is_blank(ss.get("decision_owner", "")),
        "responsibility": bool(ss.get("responsibility_confirmed", False)),
        "stakeholders": not _is_blank(ss.get("stakeholders_text", "")),
        "assumptions": not _is_blank(ss.get("assumptions_text", "")),
        "unknowns": not _is_blank(ss.get("unknowns_text", "")),
        "review_date": bool(ss.get("review_date", None)),
    }
    total = len(fields)
    done = sum(1 for v in fields.values() if v)
    pct = int((done / total) * 100)
    missing = [k for k, v in fields.items() if not v]
    return {"pct": pct, "done": done, "total": total, "missing": missing, "fields": fields}


def context_quality_hints(title: str, context: str) -> list:
    hints = []
    t = (title or "").strip()
    c = (context or "").strip()

    if len(t) < 10:
        hints.append("Make the title specific (what + for whom + timeframe).")
    if len(c) < 40:
        hints.append("Add: why now, constraints (budget/time/compliance), and success criteria (KPI).")
    if "because" not in c.lower() and "due to" not in c.lower() and "why" not in c.lower():
        hints.append("Add a clear ‘why now’ reason (because/due to/why).")
    if "success" not in c.lower() and "metric" not in c.lower() and "kpi" not in c.lower():
        hints.append("Add success criteria (KPI/metric) so learning is measurable.")
    return hints


def suggested_stakeholders(decision_type: str) -> list:
    base = ["Finance", "Operations", "Legal/Compliance", "IT/Security"]
    by_type = {
        "Strategic": ["CEO/Founder", "Product", "Sales", "Finance"],
        "Financial": ["Finance", "Legal/Compliance"],
        "Hiring": ["HR", "Hiring Manager", "Finance"],
        "Operational": ["Operations", "IT/Security"],
        "Personal": ["Trusted Advisor"],
    }
    out = []
    out.extend(by_type.get(decision_type, []))
    for x in base:
        if x not in out:
            out.append(x)
    return out[:6]


def get_all_templates():
    custom = load_custom_templates()
    merged = dict(TEMPLATES)
    merged.update(custom)
    return merged


def get_template_options():
    all_templates = get_all_templates()
    return [(k, all_templates[k].template_name) for k in all_templates.keys()]


def render_explainability(explanation: dict):
    st.subheader("Explainability")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Lowest dimensions**")
        for item in explanation.get("lowest_dimensions", []):
            st.write(f"- {item.get('dimension')}: {item.get('score')}")
        st.markdown("**Top negative contributors (weighted)**")
        for item in explanation.get("top_negative_contributors", []):
            st.write(f"- {item.get('dimension')}: {item.get('weighted')}")

    with col2:
        st.markdown("**Highest dimensions**")
        for item in explanation.get("highest_dimensions", []):
            st.write(f"- {item.get('dimension')}: {item.get('score')}")
        st.markdown("**Top positive contributors (weighted)**")
        for item in explanation.get("top_positive_contributors", []):
            st.write(f"- {item.get('dimension')}: {item.get('weighted')}")


def render_playbook(playbook: dict, key_prefix: str):
    if not playbook:
        st.info("No playbook available.")
        return

    st.subheader("Decision Playbook (Recommended Next Steps)")
    st.write(playbook.get("summary", ""))

    flags = playbook.get("flags", [])
    if flags:
        st.warning("⚠️ Risk flags")
        for f in flags:
            st.write(f"- {f}")

    st.markdown("### Fix priority (Top 3)")
    for a in playbook.get("actions", [])[:3]:
        st.markdown(f"**{a.get('dimension')}** (score: {a.get('score')})")
        for step in a.get("recommended_actions", []):
            st.write(f"- {step}")

    st.markdown("### Checklist")
    for i, item in enumerate(playbook.get("checklist", [])):
        st.checkbox(item, value=False, key=f"{key_prefix}_chk_{i}")


def normalize_decision_id(raw: str) -> str:
    if not raw:
        return "no_id"
    return str(raw).replace(" ", "_").replace(":", "_")


def _clamp_0_10(x: float) -> float:
    return max(0.0, min(10.0, float(x)))


def build_validity_contract(assumptions: list, unknowns: list, review_date, decision_class: str):
    """
    Decision validity contract:
    - This is NOT legal advice. It's a practical enterprise pattern:
      "Our recommendation is valid if X remains true; re-evaluate if Y changes."
    """
    valid_if = []
    for a in (assumptions or [])[:6]:
        if a.strip():
            valid_if.append(a.strip())

    invalidates_if = []
    for u in (unknowns or [])[:6]:
        if u.strip():
            invalidates_if.append(u.strip())

    # Enterprise-friendly generic triggers (not speculative, but governance-accurate)
    invalidates_if.extend(
        [
            "A material change occurs in budget, timeline, or compliance constraints",
            "New stakeholder constraints emerge that were not consulted during evaluation",
        ]
    )

    # Review cadence guidance (simple)
    cadence = "Revisit within 30 days" if decision_class == "Experimental" else "Revisit at the set review date"
    review_on = str(review_date) if review_date else ""

    return {
        "valid_if": valid_if,
        "invalidates_if": invalidates_if,
        "review_on": review_on,
        "cadence": cadence,
    }


def build_executive_recommendation(outcome: str, final_score: float, confidence: str, readiness, explanation: dict, playbook: dict, sst: dict):
    """
    Executive-grade recommendation language:
    - One clear headline
    - Short rationale: top positives + top negatives
    - Next steps: 72 hours / 7 days
    - Risk flags + conditions
    """
    if outcome == "GO":
        headline = "Recommendation: Proceed (GO)"
        tone = "good"
        summary = "Approve and execute with clear owners, milestones, and risk controls."
    elif outcome == "REVIEW":
        headline = "Recommendation: Proceed with revisions (REVIEW)"
        tone = "warn"
        summary = "Move forward only after addressing the key blockers and tightening assumptions."
    else:
        headline = "Recommendation: Do not proceed (NO-GO)"
        tone = "bad"
        summary = "Stop or redesign the decision. Current risk / feasibility profile is not acceptable."

    pos = [x for x in (explanation or {}).get("top_positive_contributors", [])][:3]
    neg = [x for x in (explanation or {}).get("top_negative_contributors", [])][:3]

    rationale_pos = [f"{p.get('dimension')}: strong signal ({p.get('weighted')})" for p in pos if p.get("dimension")]
    rationale_neg = [f"{n.get('dimension')}: drag / risk ({n.get('weighted')})" for n in neg if n.get("dimension")]

    actions = (playbook or {}).get("actions", [])[:3]
    next_steps_7d = []
    for a in actions:
        dim = a.get("dimension")
        steps = a.get("recommended_actions", [])[:2]
        if dim and steps:
            next_steps_7d.append(f"{dim}: {steps[0]}")

    flags = (playbook or {}).get("flags", [])
    spread = (sst or {}).get("spread", None)

    return {
        "headline": headline,
        "tone": tone,
        "summary": summary,
        "score_line": f"Final score: {final_score} / 10 • Confidence: {confidence} • Readiness: {getattr(readiness,'score', '—')}% ({getattr(readiness,'status','—')})",
        "rationale_positive": rationale_pos,
        "rationale_negative": rationale_neg,
        "next_steps_7d": next_steps_7d,
        "risk_flags": flags[:6] if flags else [],
        "stress_note": f"Scenario spread (Best–Worst): {spread}" if spread is not None else "",
    }


def render_executive_recommendation(exec_rec: dict):
    if not exec_rec:
        return
    st.markdown(
        f"""
<div class="exec-card">
  <div class="exec-title">{exec_rec.get('headline','')}</div>
  <div class="exec-sub">{exec_rec.get('summary','')}</div>
  <div class="exec-kv"><b>{exec_rec.get('score_line','')}</b></div>
</div>
""",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Why this recommendation**")
        if exec_rec.get("rationale_positive"):
            st.markdown("**Strengths**")
            for x in exec_rec["rationale_positive"]:
                st.write(f"- {x}")
        if exec_rec.get("rationale_negative"):
            st.markdown("**Risks / gaps**")
            for x in exec_rec["rationale_negative"]:
                st.write(f"- {x}")

    with c2:
        st.markdown("**Next actions (7 days)**")
        if exec_rec.get("next_steps_7d"):
            for x in exec_rec["next_steps_7d"]:
                st.write(f"- {x}")
        else:
            st.caption("No next steps generated.")

        if exec_rec.get("risk_flags"):
            st.markdown("**Risk flags**")
            for f in exec_rec["risk_flags"]:
                st.write(f"- {f}")

        if exec_rec.get("stress_note"):
            st.caption(exec_rec.get("stress_note"))


def render_validity_contract(vc: dict):
    if not vc:
        return
    st.subheader("Decision Validity Contract")
    st.caption("This recommendation stays valid only while the conditions below remain true. Re-evaluate if they change.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Valid if**")
        if vc.get("valid_if"):
            for x in vc["valid_if"]:
                st.write(f"- {x}")
        else:
            st.caption("No assumptions captured yet.")
    with c2:
        st.markdown("**Re-evaluate immediately if**")
        if vc.get("invalidates_if"):
            for x in vc["invalidates_if"]:
                st.write(f"- {x}")
        else:
            st.caption("No risks captured yet.")
    if vc.get("review_on"):
        st.caption(f"Planned review date: {vc.get('review_on')}")
    if vc.get("cadence"):
        st.caption(f"Cadence guidance: {vc.get('cadence')}")


def _today_plus(days: int) -> dt.date:
    return dt.date.today() + dt.timedelta(days=days)


def apply_preset(preset_key: str, template_default_key: str, rule):
    """
    Presets set:
    - Title / Context
    - Governance defaults
    - Assumptions / Unknowns
    - Suggested stakeholders
    - (Optionally) score defaults by dimension
    """
    st.session_state["tpl_select"] = template_default_key

    # common governance defaults
    st.session_state.setdefault("decision_owner", "")
    st.session_state["responsibility_confirmed"] = False
    st.session_state["review_date"] = _today_plus(30)

    if preset_key == "hire":
        st.session_state.update(
            {
                "decision_title": "Should we hire now or wait? (Next 90 days)",
                "decision_context": "We are deciding whether hiring now will accelerate execution without creating cashflow or productivity risk. Success criteria: measurable delivery speed improvement while maintaining costs within budget.",
                "decision_type": "Hiring",
                "decision_class": "One-way",
                "stakeholders_text": "Hiring Manager\nFinance\nHR\nOperations",
                "assumptions_text": "Workload will remain high for the next 3 months\nWe can attract qualified candidates within our salary range\nThe role directly improves delivery speed",
                "unknowns_text": "Time-to-productivity (ramp-up)\nRisk of mis-hire\nImpact on current team bandwidth during onboarding",
            }
        )

    elif preset_key == "marketing":
        st.session_state.update(
            {
                "decision_title": "Should we increase ad spend or cut it this month?",
                "decision_context": "We are deciding whether to scale marketing spend based on efficiency and lead quality. Success criteria: lower CAC or higher qualified conversions while staying within the monthly budget.",
                "decision_type": "Financial",
                "decision_class": "Experimental",
                "stakeholders_text": "Marketing\nSales\nFinance\nOperations",
                "assumptions_text": "Tracking and attribution is reliable enough for decisions\nLead quality remains stable at higher spend\nSales can handle additional demand",
                "unknowns_text": "Diminishing returns at scale\nConversion rate volatility\nLag between spend and revenue realization",
            }
        )

    elif preset_key == "vendor":
        st.session_state.update(
            {
                "decision_title": "Should we switch vendors / tools now?",
                "decision_context": "We are deciding whether switching vendors improves reliability, compliance, and cost. Success criteria: lower total cost of ownership or better uptime/support without operational disruption.",
                "decision_type": "Operational",
                "decision_class": "Two-way",
                "stakeholders_text": "Operations\nIT/Security\nFinance\nLegal/Compliance",
                "assumptions_text": "New vendor meets our functional requirements\nMigration effort is manageable within our timeline\nSupport and SLA are stronger than current vendor",
                "unknowns_text": "Hidden switching costs\nDowntime / disruption during migration\nContractual penalties or termination clauses",
            }
        )

    elif preset_key == "expansion":
        st.session_state.update(
            {
                "decision_title": "Should we open a new branch / enter a new market?",
                "decision_context": "We are deciding whether expansion is justified by demand and operational readiness. Success criteria: new location/market reaches break-even within a defined period without harming existing operations.",
                "decision_type": "Strategic",
                "decision_class": "One-way",
                "stakeholders_text": "CEO/Founder\nOperations\nFinance\nSales",
                "assumptions_text": "Demand exists in the target market\nWe can recruit/operate locally within plan\nOperating model can scale without quality loss",
                "unknowns_text": "Local competitor response\nRegulatory/permit delays\nHigher-than-expected operating costs",
            }
        )

    elif preset_key == "pricing":
        st.session_state.update(
            {
                "decision_title": "Should we raise prices now?",
                "decision_context": "We are deciding whether raising prices improves margin without unacceptable churn. Success criteria: improved profitability while maintaining retention and conversion rates within acceptable limits.",
                "decision_type": "Strategic",
                "decision_class": "Experimental",
                "stakeholders_text": "Sales\nFinance\nProduct\nCustomer Success",
                "assumptions_text": "Customers perceive strong enough value at higher price\nCompetitors will not undercut aggressively\nWe can communicate the change clearly",
                "unknowns_text": "Price sensitivity by segment\nChurn risk\nImpact on new customer acquisition conversion",
            }
        )

    # Default scoring per dimension (safe heuristic)
    for dim in rule.dimensions:
        key = f"score_{dim}"
        if key not in st.session_state:
            st.session_state[key] = 6.0

        # a gentle bias for common business dimensions if they exist
        d = dim.lower()
        if "value" in d or "impact" in d:
            st.session_state[key] = 7.0
        elif "feasib" in d or "execution" in d:
            st.session_state[key] = 6.5
        elif "risk" in d or "compliance" in d:
            st.session_state[key] = 6.0
        elif "urgency" in d or "timing" in d:
            st.session_state[key] = 6.5

    st.session_state.demo_mode = False
    st.success("Preset applied. Go through the tabs and refine details.")
    st.rerun()


def load_demo_decision(template_default_key: str, rule):
    """
    Read-only demo:
    - Populates fields
    - Runs evaluation immediately
    - Does NOT write to history
    - Locks form widgets
    """
    st.session_state["tpl_select"] = template_default_key
    st.session_state.demo_mode = True

    st.session_state.update(
        {
            "decision_title": "DEMO: Should we raise prices by 8% on our top 3 packages?",
            "decision_context": "We suspect our pricing is below market for the value delivered. This demo shows how DecisionOS structures the decision and produces an auditable recommendation. Success criteria: improve margin while keeping churn acceptable.",
            "decision_owner": "Owner (Demo)",
            "responsibility_confirmed": True,
            "decision_type": "Strategic",
            "decision_class": "Experimental",
            "stakeholders_text": "Sales\nFinance\nProduct\nCustomer Success",
            "assumptions_text": "Our differentiation remains strong\nWe can justify the price change with clear messaging\nCompetitor pricing is not significantly lower",
            "unknowns_text": "Churn impact on existing customers\nConversion rate impact on new customers\nCompetitive reaction timing",
            "assumptions_notes": "",
            "unknowns_notes": "",
            "review_date": _today_plus(21),
            "best_delta": 1.0,
            "expected_delta": 0.0,
            "worst_delta": -2.0,
        }
    )

    # score defaults for demo
    for dim in rule.dimensions:
        k = f"score_{dim}"
        d = dim.lower()
        if "value" in d or "impact" in d:
            st.session_state[k] = 7.5
        elif "feasib" in d or "execution" in d:
            st.session_state[k] = 6.5
        elif "risk" in d or "compliance" in d:
            st.session_state[k] = 5.5
        elif "urgency" in d or "timing" in d:
            st.session_state[k] = 6.0
        else:
            st.session_state[k] = 6.0

    # Run evaluation immediately (no save)
    stakeholders = [x.strip() for x in (st.session_state.get("stakeholders_text", "") or "").splitlines() if x.strip()]
    assumptions = [x.strip() for x in (st.session_state.get("assumptions_text", "") or "").splitlines() if x.strip()]
    unknowns = [x.strip() for x in (st.session_state.get("unknowns_text", "") or "").splitlines() if x.strip()]

    scores = {dim: float(st.session_state.get(f"score_{dim}", 6.0)) for dim in rule.dimensions}
    final_score = compute_weighted_score(rule, scores)
    outcome = determine_outcome(rule, final_score)
    confidence = confidence_band(final_score)
    explanation = explain_decision(rule, scores)
    playbook = build_playbook(rule, scores, final_score, outcome, explanation)

    readiness = calculate_decision_readiness(
        {
            "owner": (st.session_state.get("decision_owner", "") or "").strip(),
            "decision_type": st.session_state.get("decision_type"),
            "decision_class": st.session_state.get("decision_class"),
            "stakeholders": stakeholders,
            "assumptions": assumptions,
            "risks": unknowns,
            "confidence": confidence,
            "weights": rule.weights,
            "responsibility_confirmed": bool(st.session_state.get("responsibility_confirmed", False)),
        }
    )

    best_delta = float(st.session_state.get("best_delta", 1.0))
    expected_delta = float(st.session_state.get("expected_delta", 0.0))
    worst_delta = float(st.session_state.get("worst_delta", -2.0))

    expected_score = _clamp_0_10(final_score + expected_delta)
    best_score = _clamp_0_10(final_score + best_delta)
    worst_score = _clamp_0_10(final_score + worst_delta)

    scenario_results = {
        "expected": {
            "score": expected_score,
            "outcome": determine_outcome(rule, expected_score),
            "confidence": confidence_band(expected_score),
        },
        "best": {
            "score": best_score,
            "outcome": determine_outcome(rule, best_score),
            "confidence": confidence_band(best_score),
        },
        "worst": {
            "score": worst_score,
            "outcome": determine_outcome(rule, worst_score),
            "confidence": confidence_band(worst_score),
        },
    }
    scenario_stress_test = {
        "expected_delta": float(expected_delta),
        "best_delta": float(best_delta),
        "worst_delta": float(worst_delta),
        "results": scenario_results,
        "spread": round(best_score - worst_score, 2),
    }

    validity_contract = build_validity_contract(
        assumptions=assumptions,
        unknowns=unknowns,
        review_date=st.session_state.get("review_date", None),
        decision_class=st.session_state.get("decision_class", ""),
    )

    exec_rec = build_executive_recommendation(
        outcome=outcome,
        final_score=final_score,
        confidence=confidence,
        readiness=readiness,
        explanation=explanation,
        playbook=playbook,
        sst=scenario_stress_test,
    )

    demo_record = {
        "decision_id": "DEMO",
        "timestamp_utc": now_iso(),
        "schema_version": 2,
        "template_id": rule.template_id,
        "template_name": rule.template_name,
        "title": st.session_state.get("decision_title", ""),
        "context": st.session_state.get("decision_context", ""),
        "decision_type": st.session_state.get("decision_type", ""),
        "decision_class": st.session_state.get("decision_class", ""),
        "engine_version": ENGINE_VERSION,
        "ruleset_version": RULESET_VERSION,
        "decision_owner": st.session_state.get("decision_owner", ""),
        "stakeholders": stakeholders,
        "review_date": str(st.session_state.get("review_date")) if st.session_state.get("review_date") else "",
        "assumptions": assumptions,
        "unknowns": unknowns,
        "assumptions_notes": st.session_state.get("assumptions_notes", ""),
        "unknowns_notes": st.session_state.get("unknowns_notes", ""),
        "scores": scores,
        "final_score": final_score,
        "scenario_stress_test": scenario_stress_test,
        "outcome": outcome,
        "confidence": confidence,
        "explanation": explanation,
        "playbook": playbook,
        "parent_id": None,
        "version": 1,
        "readiness_score": readiness.score,
        "readiness_status": readiness.status,
        "readiness_min_required": readiness.min_required,
        "readiness_blockers": readiness.blockers,
        "readiness_issues": readiness.issues,
        "responsibility_confirmed": bool(st.session_state.get("responsibility_confirmed", False)),
        "is_demo": True,
        "validity_contract": validity_contract,
        "executive_recommendation": exec_rec,
    }

    st.session_state.last_record = demo_record
    st.session_state.last_playbook = playbook
    st.success("Demo loaded (read-only). Scroll down to see the executive output.")
    st.rerun()


def exit_demo_mode():
    st.session_state.demo_mode = False
    # keep fields (so user can continue), but unlock
    if st.session_state.last_record and st.session_state.last_record.get("is_demo"):
        st.session_state.last_record = None
        st.session_state.last_playbook = None
    st.success("Demo mode disabled. You can now edit and save decisions.")
    st.rerun()


# ----------------------------
# Pages
# ----------------------------
def page_about():
    st.subheader("What this is")
    st.write(
        """
DecisionOS helps teams make decisions using:
- Structured scoring
- Transparent rules
- Explainability (why the outcome happened)
- Audit trail (history)
- Executive-grade recommendations (what to do next + why)
- Decision validity contract (when this advice stays valid)

This MVP focuses on clarity, governance, and repeatability.
        """
    )
    st.subheader("Templates included")
    for k, name in get_template_options():
        st.write(f"- {name} ({k})")


def page_history():
    st.subheader("Decision History (Latest)")

    rows = read_jsonl(HISTORY_PATH, limit=500)
    st.caption(f"Records found: {len(rows)}")

    st.markdown("### Data repair")
    if st.button("Repair / Migrate History (safe)", key="btn_migrate_history"):
        try:
            summary = migrate_history_schema(HISTORY_PATH, target_schema_version=2) or {}
            st.session_state.migration_summary = summary
        except Exception as e:
            st.session_state.migration_summary = {"error": str(e)}
        st.rerun()

    if st.session_state.migration_summary:
        s = st.session_state.migration_summary or {}
        if s.get("error"):
            st.error(f"Migration failed: {s.get('error')}")
        else:
            st.success(
                f"Migration complete ✅ Total: {s.get('total', 0)} | Updated: {s.get('updated', 0)} | Written: {s.get('written', 0)}"
            )

    st.markdown("### Cleanup (optional)")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Delete titles containing 'test'", key="btn_del_test"):
            n = delete_decisions_by_title_contains(HISTORY_PATH, "test")
            st.success(f"Deleted {n} records.")
            st.rerun()
    with c2:
        if st.button("Delete titles containing 'trial'", key="btn_del_trial"):
            n = delete_decisions_by_title_contains(HISTORY_PATH, "trial")
            st.success(f"Deleted {n} records.")
            st.rerun()
    with c3:
        if st.button("Delete legacy (no decision_id)", key="btn_del_legacy"):
            n = delete_legacy_no_id(HISTORY_PATH)
            st.success(f"Deleted {n} records.")
            st.rerun()

    st.divider()

    if not rows:
        st.info("No decisions yet. Go to Home and create your first decision.")
        return

    rows = list(reversed(rows))

    for i, r in enumerate(rows[:30]):
        decision_id = normalize_decision_id(r.get("decision_id") or f"legacy_{i}_{r.get('timestamp_utc','')}")
        title = r.get("title", "Untitled")
        template = r.get("template_name", "")
        ts = r.get("timestamp_utc", "")
        version = r.get("version", 1)
        parent_id = r.get("parent_id")

        with st.expander(f"{title} • {template} • {ts}"):
            st.write("**Version:**", version)
            st.write("**Parent ID:**", parent_id)
            st.write("**Schema version:**", r.get("schema_version", "—"))
            st.write("**Engine version:**", r.get("engine_version", "—"))
            st.write("**Ruleset version:**", r.get("ruleset_version", "—"))
            st.write("**Decision Type:**", r.get("decision_type", "—"))
            st.write("**Decision Class:**", r.get("decision_class", "—"))

            # Executive recommendation (new)
            if r.get("executive_recommendation"):
                st.markdown("---")
                st.subheader("Executive Recommendation")
                render_executive_recommendation(r.get("executive_recommendation") or {})

            # Validity contract (new)
            if r.get("validity_contract"):
                st.markdown("---")
                render_validity_contract(r.get("validity_contract") or {})

            st.markdown("---")
            st.subheader("Ownership & Governance")
            st.write("**Owner:**", r.get("decision_owner") or "—")

            st.write("**Stakeholders:**")
            if r.get("stakeholders"):
                for s in r["stakeholders"]:
                    st.write("•", s)
            else:
                st.caption("No stakeholders captured.")
            st.write("**Review date:**", r.get("review_date") or "—")

            pdf_rel = f"{REPORTS_DIR}/{decision_id}.pdf"
            if st.button("Generate PDF report", key=f"pdf_{decision_id}"):
                path = write_pdf_report(pdf_rel, r)
                st.success("PDF generated.")
                with open(path, "rb") as f:
                    st.download_button(
                        "Download PDF",
                        data=f.read(),
                        file_name=f"DecisionOS_{decision_id}.pdf",
                        mime="application/pdf",
                        key=f"dl_{decision_id}",
                    )

            st.write("**Context:**", r.get("context", ""))
            st.write("**Final Score:**", r.get("final_score"))
            st.write("**Outcome:**", r.get("outcome"))
            st.write("**Confidence:**", r.get("confidence"))
            st.write("**Scores:**", r.get("scores", {}))

            st.markdown("---")
            st.subheader("Assumptions")
            if r.get("assumptions"):
                for a in r["assumptions"]:
                    st.write("•", a)
            else:
                st.caption("No assumptions captured.")
            if (r.get("assumptions_notes") or "").strip():
                st.caption(f"Notes: {r.get('assumptions_notes')}")

            st.subheader("Unknowns / Risks")
            if r.get("unknowns"):
                for u in r["unknowns"]:
                    st.write("•", u)
            else:
                st.caption("No unknowns/risks captured.")
            if (r.get("unknowns_notes") or "").strip():
                st.caption(f"Notes: {r.get('unknowns_notes')}")

            st.markdown("---")
            st.subheader("Scenario Stress Test")
            sst = r.get("scenario_stress_test") or {}
            results = (sst.get("results") or {})
            if results:
                st.write("**Spread (Best - Worst):**", sst.get("spread"))
                st.write("**Best:**", results.get("best"))
                st.write("**Expected:**", results.get("expected"))
                st.write("**Worst:**", results.get("worst"))
            else:
                st.caption("No scenario data captured.")

            exp = r.get("explanation")
            if exp:
                render_explainability(exp)

            pb = r.get("playbook")
            if pb:
                st.markdown("---")
                render_playbook(pb, key_prefix=f"{decision_id}_hist")

            st.markdown("---")
            st.subheader("Follow-up (Outcome Tracking)")

            existing = r.get("follow_up")
            if existing:
                st.info(
                    f"Already updated: {existing.get('outcome')} • {existing.get('updated_at_utc')}\n\n"
                    f"Notes: {existing.get('notes','')}"
                )

            outcome = st.selectbox(
                "What actually happened?",
                ["Not recorded yet", "Success", "Partial Success", "Failure"],
                key=f"outcome_{decision_id}",
            )
            notes = st.text_area("Notes (what happened + why)", value="", key=f"notes_{decision_id}")

            if st.button("Save follow-up", key=f"save_{decision_id}"):
                if outcome == "Not recorded yet":
                    st.warning("Please select Success / Partial Success / Failure.")
                else:
                    ok = update_decision_outcome(HISTORY_PATH, r.get("decision_id"), outcome, notes.strip())
                    if ok:
                        st.success("Follow-up saved. Refreshing…")
                        st.rerun()
                    else:
                        st.error("Could not find this decision in history.")

    st.caption("Tip: history is saved locally in data/decision_history.jsonl")


def page_dashboard():
    st.subheader("Dashboard")

    rows = read_jsonl(HISTORY_PATH, limit=5000)
    if not rows:
        st.info("No decisions yet. Create decisions first to see metrics.")
        return

    import pandas as pd

    st.markdown("### Filters")
    all_templates = sorted(list(set([r.get("template_name", "Unknown") for r in rows])))
    all_outcomes = sorted(list(set([r.get("outcome", "Unknown") for r in rows])))
    all_types = sorted(list(set([r.get("decision_type", "Unknown") for r in rows])))

    cF1, cF2, cF3, cF4 = st.columns(4)
    with cF1:
        template_filter = st.multiselect("Template", all_templates, default=all_templates)
    with cF2:
        outcome_filter = st.multiselect("Outcome", all_outcomes, default=all_outcomes)
    with cF3:
        min_score = st.slider("Min score", 0.0, 10.0, 0.0, 0.5)
    with cF4:
        type_filter = st.multiselect("Type", all_types, default=all_types)

    filtered = []
    for r in rows:
        if r.get("template_name", "Unknown") not in template_filter:
            continue
        if r.get("outcome", "Unknown") not in outcome_filter:
            continue
        if r.get("decision_type", "Unknown") not in type_filter:
            continue
        try:
            if float(r.get("final_score", 0)) < float(min_score):
                continue
        except Exception:
            continue
        filtered.append(r)

    st.caption(f"Filtered records: {len(filtered)}")
    if not filtered:
        st.warning("No records match filters.")
        return

    metrics = compute_metrics(filtered)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total decisions", metrics.get("total", 0))
    c2.metric("Avg score", metrics.get("avg_score") if metrics.get("avg_score") is not None else "—")
    c3.metric(
        "Follow-ups recorded",
        sum(metrics.get("followup_outcomes", {}).values()) if metrics.get("followup_outcomes") else 0,
    )

    st.markdown("### Charts")
    outcome_df = pd.DataFrame(list(metrics.get("outcomes", {}).items()), columns=["Outcome", "Count"]).sort_values(
        "Count", ascending=False
    )
    conf_df = pd.DataFrame(list(metrics.get("confidence", {}).items()), columns=["Confidence", "Count"]).sort_values(
        "Count", ascending=False
    )

    cA, cB = st.columns(2)

    with cA:
        st.subheader("Outcome distribution")
        if not outcome_df.empty and outcome_df["Count"].sum() > 0:
            chart_df = outcome_df.set_index("Outcome")[["Count"]]
            _ = st.bar_chart(chart_df)
        else:
            st.info("No outcome data available yet.")

    with cB:
        st.subheader("Confidence distribution")
        if not conf_df.empty and conf_df["Count"].sum() > 0:
            chart_df = conf_df.set_index("Confidence")[["Count"]]
            _ = st.bar_chart(chart_df)
        else:
            st.info("No confidence data available yet.")

    st.subheader("Most common weak dimensions")
    if metrics.get("weak_dimensions"):
        weak_df = pd.DataFrame(metrics["weak_dimensions"], columns=["Dimension", "Count"])
        _ = st.bar_chart(weak_df.set_index("Dimension"))
    else:
        st.write("Not enough data yet.")

    st.markdown("---")
    st.subheader("Decision Pattern Intelligence")
    insights = compute_pattern_insights(filtered)
    rows_ins = insights.get("rows") or []
    if not rows_ins:
        st.info("No data yet.")
    else:
        df_ins = pd.DataFrame(rows_ins)
        st.caption("Calibration Gap: positive = underconfident, negative = overconfident.")
        st.dataframe(df_ins, use_container_width=True)

        df_gap = df_ins.dropna(subset=["Calibration Gap"]).set_index("Type")[["Calibration Gap"]]
        if len(df_gap) > 0:
            st.subheader("Confidence calibration by type")
            _ = st.bar_chart(df_gap)

    st.markdown("---")
    st.subheader("Template Improvement Intelligence")
    tpl = compute_template_improvements(filtered)
    recs = tpl.get("recommendations") or []
    if not recs:
        st.success("No template issues detected yet.")
    else:
        df_tpl = pd.DataFrame(recs)
        st.dataframe(df_tpl, use_container_width=True)

    st.markdown("---")
    st.subheader("Compare Versions (Iteration Tracker)")
    groups = group_by_parent(filtered)
    shown = 0
    for parent_id, versions in groups.items():
        if len(versions) < 2:
            continue

        v1 = versions[0]
        vlast = versions[-1]
        title = v1.get("title", "Untitled")
        with st.expander(f"{title} • {len(versions)} versions"):
            st.write("**v1 score:**", v1.get("final_score"), " | **outcome:**", v1.get("outcome"))
            st.write("**latest score:**", vlast.get("final_score"), " | **outcome:**", vlast.get("outcome"))
            try:
                delta = float(vlast.get("final_score", 0)) - float(v1.get("final_score", 0))
                st.metric("Score improvement", f"{delta:+.2f}")
            except Exception:
                st.write("Could not compute score improvement.")

        shown += 1
        if shown >= 10:
            break

    st.markdown("---")
    st.subheader("Decision Accuracy Engine")
    acc = compute_accuracy_metrics(filtered)
    cA1, cA2, cA3, cA4 = st.columns(4)
    cA1.metric("Follow-ups recorded", acc.get("followups_total", 0))
    cA2.metric("Strict cases (GO/NO-GO)", acc.get("total_strict", 0))
    cA3.metric("False GO (FP)", acc.get("fp", 0))
    cA4.metric("False NO-GO (FN)", acc.get("fn", 0))
    st.caption(f"Review bucket: {acc.get('review_bucket', 0)}")

    st.markdown("---")
    st.subheader("Export (CSV)")
    flat = []
    for r in filtered:
        flat.append(
            {
                "decision_id": r.get("decision_id"),
                "parent_id": r.get("parent_id"),
                "version": r.get("version", 1),
                "timestamp_utc": r.get("timestamp_utc"),
                "template_name": r.get("template_name"),
                "title": r.get("title"),
                "final_score": r.get("final_score"),
                "outcome": r.get("outcome"),
                "confidence": r.get("confidence"),
                "followup_outcome": (r.get("follow_up") or {}).get("outcome"),
            }
        )

    import pandas as pd
    df = pd.DataFrame(flat)
    st.download_button(
        "Download CSV (filtered)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="DecisionOS_decisions_filtered.csv",
        mime="text/csv",
        key="btn_export_csv",
    )


def page_template_builder():
    st.subheader("Template Builder (No-code)")
    st.caption("Create reusable decision templates. Saved locally to data/custom_templates.json")

    CUSTOM = load_custom_templates()

    st.markdown("### Existing custom templates")
    if not CUSTOM:
        st.info("No custom templates yet.")
    else:
        for k, t in CUSTOM.items():
            st.write(f"- **{t.template_name}** (`{k}`) • {len(t.dimensions)} dimensions")

    st.markdown("---")
    st.markdown("### Create a new template")

    key = st.text_input("Template key (unique, no spaces)", value="my_template").strip()
    name = st.text_input("Template name", value="My Decision Template").strip()

    dims_text = st.text_area("Dimensions (one per line)", value="Value\nFeasibility\nRisk\nUrgency")
    dims = [d.strip() for d in dims_text.splitlines() if d.strip()]

    if not dims:
        st.warning("Add at least 1 dimension.")
        return

    st.markdown("### Weights")
    weights = {}
    for d in dims:
        weights[d] = float(st.number_input(f"Weight for {d}", min_value=0.1, value=1.0, step=0.1, key=f"wt_{d}"))

    st.markdown("### Thresholds (0–10)")
    go = float(st.number_input("GO threshold (>=)", min_value=0.0, max_value=10.0, value=8.0, step=0.5, key="go_th"))
    review = float(st.number_input("REVIEW threshold (>=)", min_value=0.0, max_value=10.0, value=6.0, step=0.5, key="review_th"))
    st.caption("Score >= GO → GO • Score >= REVIEW → REVIEW/REVISE • Else → NO-GO")

    colA, colB = st.columns(2)
    with colA:
        if st.button("Save template", key="btn_save_tpl"):
            if not key or " " in key:
                st.error("Template key must be non-empty and contain no spaces.")
                return

            CUSTOM[key] = TemplateRule(
                template_id=key,
                template_name=name,
                dimensions=dims,
                weights=weights,
                thresholds={"go": go, "review": review},
            )
            save_custom_templates(CUSTOM)
            st.success("Template saved! Go to Home and refresh dropdown.")
            st.rerun()

    with colB:
        if st.button("Delete this key", key="btn_del_tpl"):
            if key in CUSTOM:
                del CUSTOM[key]
                save_custom_templates(CUSTOM)
                st.success("Deleted.")
                st.rerun()
            else:
                st.info("No template with this key exists.")


def page_home():
    # Executive header
    head_l, head_r = st.columns([0.74, 0.26], vertical_alignment="center")
    with head_l:
        st.title("DecisionOS")
        st.caption("Explainable • Auditable • Governance-first Decision Intelligence for any business decision.")
        guided = st.toggle("Guided mode (recommended)", value=True, key="guided_mode")
    with head_r:
        badge(f"Engine {ENGINE_VERSION}", "info")
        st.write("")
        badge(f"Ruleset {RULESET_VERSION}", "info")

    ALL_TEMPLATES = get_all_templates()
    template_default_key = st.session_state.get("tpl_select", list(ALL_TEMPLATES.keys())[0])
    rule_default = ALL_TEMPLATES.get(template_default_key, list(ALL_TEMPLATES.values())[0])

    # ----------------------------
    # Decision Presets + Demo (NEW)
    # ----------------------------
    st.markdown("### What decision are you making right now?")
    st.caption("Click a preset to pre-fill the decision in a way business owners understand instantly.")

    p1, p2, p3, p4, p5, p6 = st.columns([1, 1, 1, 1, 1, 1])
    with p1:
        if st.button("🧑‍💼 Hire now / wait", use_container_width=True):
            apply_preset("hire", template_default_key, rule_default)
    with p2:
        if st.button("📢 Increase / cut ads", use_container_width=True):
            apply_preset("marketing", template_default_key, rule_default)
    with p3:
        if st.button("🔄 Switch vendor", use_container_width=True):
            apply_preset("vendor", template_default_key, rule_default)
    with p4:
        if st.button("🏢 Open new branch", use_container_width=True):
            apply_preset("expansion", template_default_key, rule_default)
    with p5:
        if st.button("💰 Raise prices", use_container_width=True):
            apply_preset("pricing", template_default_key, rule_default)
    with p6:
        if not st.session_state.demo_mode:
            if st.button("🎯 Try Demo (read-only)", use_container_width=True):
                load_demo_decision(template_default_key, rule_default)
        else:
            if st.button("Exit Demo", use_container_width=True):
                exit_demo_mode()

    if st.session_state.demo_mode:
        st.info("Demo mode is ON (read-only). You can review the output, but saving is disabled.")

    st.divider()

    form_col, summary_col = st.columns([0.66, 0.34], gap="large")

    demo_lock = bool(st.session_state.get("demo_mode", False))

    # ----------------------------
    # LEFT: Form
    # ----------------------------
    with form_col:
        with st.form("home_decision_form", clear_on_submit=False, border=True):
            tabs = st.tabs(["1) Define", "2) Governance", "3) Scoring", "4) Assumptions", "5) Stress Test", "6) Review"])

            # TAB 1: Define
            with tabs[0]:
                section_title("Define the decision", "Give clarity first. The AI assists quality checks automatically.")

                template_key = st.selectbox(
                    "Template",
                    options=list(ALL_TEMPLATES.keys()),
                    format_func=lambda k: ALL_TEMPLATES[k].template_name,
                    key="tpl_select",
                    disabled=demo_lock,
                )

                st.text_input(
                    "Decision title",
                    key="decision_title",
                    placeholder="e.g., Should we raise prices? Should we hire? Should we open a branch?",
                    disabled=demo_lock,
                )
                st.text_area(
                    "Decision context (1–3 lines)",
                    key="decision_context",
                    placeholder="Include: why now, constraints, success criteria (KPI).",
                    disabled=demo_lock,
                )

                if guided:
                    hints = context_quality_hints(
                        st.session_state.get("decision_title", ""),
                        st.session_state.get("decision_context", ""),
                    )
                    if hints:
                        st.info("AI Assist — improve decision clarity:")
                        for h in hints[:3]:
                            st.write(f"• {h}")

            # TAB 2: Governance
            with tabs[1]:
                section_title("Ownership & governance", "Enterprise-grade accountability and stakeholder traceability.")

                st.text_input(
                    "Decision owner (accountable person / role)",
                    key="decision_owner",
                    placeholder="e.g., Founder / CEO / Head of Ops / CFO",
                    disabled=demo_lock,
                )

                st.checkbox(
                    "I confirm I am accountable for this decision and accept ownership of the outcome.",
                    help="Governance readiness can block evaluation if accountability is missing.",
                    key="responsibility_confirmed",
                    disabled=demo_lock,
                )

                c1, c2 = st.columns(2)
                with c1:
                    st.selectbox(
                        "Decision type",
                        ["Strategic", "Financial", "Hiring", "Operational", "Personal"],
                        key="decision_type",
                        disabled=demo_lock,
                    )
                with c2:
                    st.selectbox(
                        "Decision class (reversibility)",
                        ["One-way", "Two-way", "Experimental"],
                        index=1,
                        help="One-way = hard to reverse (higher scrutiny). Two-way = reversible. Experimental = controlled trial.",
                        key="decision_class",
                        disabled=demo_lock,
                    )

                # Button BEFORE the stakeholders widget (important for session_state updates)
                add_roles = st.form_submit_button(
                    "Add suggested stakeholder roles",
                    use_container_width=True,
                    key="btn_add_stakeholder_roles",
                    disabled=demo_lock,
                )
                if add_roles:
                    dtype = st.session_state.get("decision_type", "Strategic")
                    sugg = suggested_stakeholders(dtype)
                    existing = (st.session_state.get("stakeholders_text", "") or "").strip()
                    merged = (existing + "\n" if existing else "") + "\n".join(sugg)
                    st.session_state["stakeholders_text"] = merged
                    st.rerun()

                st.text_area(
                    "Stakeholders (one per line)",
                    help="Who was consulted / impacted / must be informed (roles are ok).",
                    key="stakeholders_text",
                    placeholder="e.g.\nSales\nFinance\nOperations\nLegal/Compliance",
                    disabled=demo_lock,
                )

                st.date_input(
                    "Review date (when should we revisit this decision?)",
                    value=st.session_state.get("review_date", None),
                    key="review_date",
                    disabled=demo_lock,
                )

            # TAB 3: Scoring
            with tabs[2]:
                section_title("Score the dimensions", "0–10 scoring with transparent weights.")
                st.caption("10 = best. If a dimension represents risk/complexity, score lower when risk is higher.")

                rule = ALL_TEMPLATES[st.session_state.get("tpl_select", list(ALL_TEMPLATES.keys())[0])]
                for dim in rule.dimensions:
                    st.slider(
                        dim,
                        min_value=0.0,
                        max_value=10.0,
                        value=float(st.session_state.get(f"score_{dim}", 5.0)),
                        step=0.5,
                        key=f"score_{dim}",
                        disabled=demo_lock,
                    )

            # TAB 4: Assumptions
            with tabs[3]:
                section_title("Assumptions & unknowns", "Captured for audit + later learning.")
                st.text_area(
                    "Assumptions (one per line)",
                    key="assumptions_text",
                    placeholder="e.g.\nWe can deliver within budget\nCustomers will accept the change\nWe have capacity to execute",
                    disabled=demo_lock,
                )
                st.text_area(
                    "Unknowns / Risks (one per line)",
                    key="unknowns_text",
                    placeholder="e.g.\nChurn risk\nExecution delays\nCompliance issues",
                    disabled=demo_lock,
                )
                if guided:
                    with st.expander("What should I write here?"):
                        st.write("**Assumptions** = what you believe is true. **Unknowns/Risks** = missing info or threats.")

                st.text_area("Assumptions Notes (optional)", key="assumptions_notes", disabled=demo_lock)
                st.text_area("Unknowns Notes (optional)", key="unknowns_notes", disabled=demo_lock)

            # TAB 5: Stress Test
            with tabs[4]:
                section_title("Scenario stress testing", "Simulate best/expected/worst outcomes.")
                cS1, cS2, cS3 = st.columns(3)
                with cS1:
                    st.number_input(
                        "Best-case delta (+)",
                        value=float(st.session_state.get("best_delta", 1.0)),
                        step=0.5,
                        key="best_delta",
                        disabled=demo_lock,
                    )
                with cS2:
                    st.number_input(
                        "Expected delta",
                        value=float(st.session_state.get("expected_delta", 0.0)),
                        step=0.5,
                        key="expected_delta",
                        disabled=demo_lock,
                    )
                with cS3:
                    st.number_input(
                        "Worst-case delta (-)",
                        value=float(st.session_state.get("worst_delta", -2.0)),
                        step=0.5,
                        key="worst_delta",
                        disabled=demo_lock,
                    )

            # TAB 6: Review (Finalize button only here)
            with tabs[5]:
                section_title("Readiness review", "Governance gate before evaluation.")

                rule = ALL_TEMPLATES[st.session_state.get("tpl_select", list(ALL_TEMPLATES.keys())[0])]
                scores = {dim: float(st.session_state.get(f"score_{dim}", 5.0)) for dim in rule.dimensions}

                preview_final = compute_weighted_score(rule, scores)
                preview_conf = confidence_band(preview_final)

                stakeholders = [x.strip() for x in (st.session_state.get("stakeholders_text", "") or "").splitlines() if x.strip()]
                assumptions = [x.strip() for x in (st.session_state.get("assumptions_text", "") or "").splitlines() if x.strip()]
                unknowns = [x.strip() for x in (st.session_state.get("unknowns_text", "") or "").splitlines() if x.strip()]

                readiness = calculate_decision_readiness(
                    {
                        "owner": (st.session_state.get("decision_owner", "") or "").strip(),
                        "decision_type": st.session_state.get("decision_type"),
                        "decision_class": st.session_state.get("decision_class"),
                        "stakeholders": stakeholders,
                        "assumptions": assumptions,
                        "risks": unknowns,
                        "confidence": preview_conf,
                        "weights": rule.weights,
                        "responsibility_confirmed": bool(st.session_state.get("responsibility_confirmed", False)),
                    }
                )

                tone = "good" if readiness.status == "APPROVE" else "warn" if readiness.status == "REVIEW" else "bad"
                badge(f"Readiness: {readiness.score}% — {readiness.status}", tone)
                st.caption(f"Minimum required: {readiness.min_required}%")

                if readiness.blockers:
                    st.info("Hard blockers:")
                    for b in readiness.blockers:
                        st.write(f"- {b}")
                if readiness.issues:
                    st.info("What to improve:")
                    for i in readiness.issues:
                        st.write(f"- {i}")

                # Preview: validity contract (even before finalize)
                vc_preview = build_validity_contract(
                    assumptions=assumptions,
                    unknowns=unknowns,
                    review_date=st.session_state.get("review_date", None),
                    decision_class=st.session_state.get("decision_class", ""),
                )
                st.markdown("---")
                render_validity_contract(vc_preview)

                # Finalize
                submitted = st.form_submit_button(
                    "Finalize Decision",
                    use_container_width=True,
                    disabled=(readiness.status == "BLOCK" or demo_lock),
                    help="Finalize only after governance checks are complete.",
                    key="btn_finalize_decision",
                )

                if submitted:
                    template_key = st.session_state.get("tpl_select", list(ALL_TEMPLATES.keys())[0])
                    rule = ALL_TEMPLATES[template_key]

                    title = (st.session_state.get("decision_title", "") or "").strip() or "Untitled Decision"
                    context = (st.session_state.get("decision_context", "") or "").strip() or "N/A"
                    decision_owner = (st.session_state.get("decision_owner", "") or "").strip()
                    responsibility_confirmed = bool(st.session_state.get("responsibility_confirmed", False))

                    stakeholders = [x.strip() for x in (st.session_state.get("stakeholders_text", "") or "").splitlines() if x.strip()]
                    review_date = st.session_state.get("review_date", None)
                    decision_type = st.session_state.get("decision_type")
                    decision_class = st.session_state.get("decision_class")

                    assumptions = [x.strip() for x in (st.session_state.get("assumptions_text", "") or "").splitlines() if x.strip()]
                    unknowns = [x.strip() for x in (st.session_state.get("unknowns_text", "") or "").splitlines() if x.strip()]
                    assumptions_notes = (st.session_state.get("assumptions_notes", "") or "").strip()
                    unknowns_notes = (st.session_state.get("unknowns_notes", "") or "").strip()

                    best_delta = float(st.session_state.get("best_delta", 1.0))
                    expected_delta = float(st.session_state.get("expected_delta", 0.0))
                    worst_delta = float(st.session_state.get("worst_delta", -2.0))

                    scores = {dim: float(st.session_state.get(f"score_{dim}", 5.0)) for dim in rule.dimensions}

                    final_score = compute_weighted_score(rule, scores)
                    outcome = determine_outcome(rule, final_score)
                    confidence = confidence_band(final_score)
                    explanation = explain_decision(rule, scores)
                    playbook = build_playbook(rule, scores, final_score, outcome, explanation)

                    expected_score = _clamp_0_10(final_score + expected_delta)
                    best_score = _clamp_0_10(final_score + best_delta)
                    worst_score = _clamp_0_10(final_score + worst_delta)

                    scenario_results = {
                        "expected": {
                            "score": expected_score,
                            "outcome": determine_outcome(rule, expected_score),
                            "confidence": confidence_band(expected_score),
                        },
                        "best": {
                            "score": best_score,
                            "outcome": determine_outcome(rule, best_score),
                            "confidence": confidence_band(best_score),
                        },
                        "worst": {
                            "score": worst_score,
                            "outcome": determine_outcome(rule, worst_score),
                            "confidence": confidence_band(worst_score),
                        },
                    }

                    scenario_stress_test = {
                        "expected_delta": float(expected_delta),
                        "best_delta": float(best_delta),
                        "worst_delta": float(worst_delta),
                        "results": scenario_results,
                        "spread": round(best_score - worst_score, 2),
                    }

                    validity_contract = build_validity_contract(
                        assumptions=assumptions,
                        unknowns=unknowns,
                        review_date=review_date,
                        decision_class=decision_class,
                    )

                    exec_rec = build_executive_recommendation(
                        outcome=outcome,
                        final_score=final_score,
                        confidence=confidence,
                        readiness=readiness,
                        explanation=explanation,
                        playbook=playbook,
                        sst=scenario_stress_test,
                    )

                    record = {
                        "decision_id": new_id(),
                        "timestamp_utc": now_iso(),
                        "schema_version": 2,
                        "template_id": rule.template_id,
                        "template_name": rule.template_name,
                        "title": title,
                        "context": context,
                        "decision_type": decision_type,
                        "decision_class": decision_class,
                        "engine_version": ENGINE_VERSION,
                        "ruleset_version": RULESET_VERSION,
                        "decision_owner": decision_owner,
                        "stakeholders": stakeholders,
                        "review_date": str(review_date) if review_date else "",
                        "assumptions": assumptions,
                        "unknowns": unknowns,
                        "assumptions_notes": assumptions_notes,
                        "unknowns_notes": unknowns_notes,
                        "scores": scores,
                        "final_score": final_score,
                        "scenario_stress_test": scenario_stress_test,
                        "outcome": outcome,
                        "confidence": confidence,
                        "explanation": explanation,
                        "playbook": playbook,
                        "parent_id": None,
                        "version": 1,
                        "readiness_score": readiness.score,
                        "readiness_status": readiness.status,
                        "readiness_min_required": readiness.min_required,
                        "readiness_blockers": readiness.blockers,
                        "readiness_issues": readiness.issues,
                        "responsibility_confirmed": responsibility_confirmed,
                        # NEW: enterprise outputs
                        "validity_contract": validity_contract,
                        "executive_recommendation": exec_rec,
                    }

                    append_jsonl(HISTORY_PATH, record)
                    st.session_state.last_record = record
                    st.session_state.last_playbook = playbook
                    st.success("Decision evaluated and saved to history.")

    # ----------------------------
    # RIGHT: Summary panel
    # ----------------------------
    with summary_col:
        st.markdown("#### Decision Summary")
        st.caption("Executive snapshot (draft + last evaluation).")

        if st.session_state.demo_mode:
            badge("DEMO (read-only)", "warn")
        else:
            badge("Draft", "info")

        st.write("**Title:**", (st.session_state.get("decision_title", "") or "").strip() or "—")
        st.write("**Owner:**", (st.session_state.get("decision_owner", "") or "").strip() or "—")
        st.write("**Type:**", st.session_state.get("decision_type", "—"))
        st.write("**Class:**", st.session_state.get("decision_class", "—"))

        comp = compute_completeness(st.session_state)
        st.progress(comp["pct"] / 100, text=f"Completeness: {comp['pct']}% ({comp['done']}/{comp['total']})")
        if guided and comp["missing"]:
            st.caption("Missing essentials:")
            st.write("• " + "\n• ".join(comp["missing"][:5]))

        st.divider()

        st.markdown("#### Last Evaluation")
        if st.session_state.last_record:
            r = st.session_state.last_record
            tone = "good" if r.get("outcome") == "GO" else "warn" if r.get("outcome") == "REVIEW" else "bad"
            badge(f"{r.get('outcome')}", tone)
            st.metric("Final Score", f"{r.get('final_score')} / 10")
            st.write("**Confidence:**", r.get("confidence"))
            st.caption(f"Saved: {r.get('timestamp_utc')}")
            if r.get("is_demo"):
                st.caption("Note: demo result is not saved to history.")
        else:
            st.caption("No evaluation yet in this session.")

    # ----------------------------
    # Results section (Executive-grade + contract)
    # ----------------------------
    if st.session_state.last_record:
        r = st.session_state.last_record
        pb = st.session_state.last_playbook
        decision_id = normalize_decision_id(r.get("decision_id"))

        st.divider()
        st.subheader("Executive Output")

        # Executive recommendation (NEW)
        exec_rec = r.get("executive_recommendation")
        if exec_rec:
            render_executive_recommendation(exec_rec)
        else:
            # fallback for older records
            tone = "good" if r.get("outcome") == "GO" else "warn" if r.get("outcome") == "REVIEW" else "bad"
            badge(f"{r.get('outcome')}", tone)
            st.metric("Final Score", f"{r.get('final_score')} / 10")

        st.divider()

        # Validity contract (NEW)
        vc = r.get("validity_contract")
        if vc:
            render_validity_contract(vc)

        st.divider()

        # Explainability + playbook
        render_explainability(r.get("explanation") or {})
        st.divider()
        render_playbook(pb, key_prefix=f"{decision_id}_home")

        st.divider()
        st.subheader("Scenario Stress Test")
        sst = r.get("scenario_stress_test") or {}
        results = (sst.get("results") or {})
        if results:
            import pandas as pd

            rows_s = []
            for name in ["best", "expected", "worst"]:
                rr = results.get(name, {})
                rows_s.append(
                    {"Scenario": name.title(), "Score": rr.get("score"), "Outcome": rr.get("outcome"), "Confidence": rr.get("confidence")}
                )
            df_s = pd.DataFrame(rows_s)
            st.dataframe(df_s, use_container_width=True)
            st.metric("Scenario spread (Best - Worst)", sst.get("spread", "—"))
            st.bar_chart(df_s[["Scenario", "Score"]].set_index("Scenario"))

    # ----------------------------
    # Iteration (v2)
    # ----------------------------
    st.divider()
    st.subheader("Iteration (Re-score after fixes)")
    st.caption("Creates a new v2 record linked to your last evaluated decision.")

    if st.session_state.demo_mode:
        st.info("Demo mode: iteration is disabled. Exit demo to create real versions.")
    else:
        if st.button("Create a revised version (v2)", key="btn_make_v2"):
            base = st.session_state.get("last_record", None)
            if base is None:
                st.warning("No prior decision found. Evaluate a decision first.")
            else:
                revised = dict(base)
                revised["decision_id"] = new_id()
                revised["timestamp_utc"] = now_iso()
                revised["parent_id"] = base["decision_id"]
                revised["version"] = int(base.get("version", 1)) + 1
                revised["title"] = f"{base.get('title','Untitled')} (Revised v{revised['version']})"
                revised["engine_version"] = ENGINE_VERSION
                revised["ruleset_version"] = RULESET_VERSION

                append_jsonl(HISTORY_PATH, revised)
                st.success("Revised version saved. Go to History or Dashboard → Compare.")


# ----------------------------
# Main app shell
# ----------------------------
st.caption("DecisionOS — Explainable decision intelligence for any business.")

page = st.sidebar.radio("Navigate", ["Home", "History", "Dashboard", "Template Builder", "About"], key="nav")

if page == "About":
    page_about()
elif page == "History":
    page_history()
elif page == "Dashboard":
    page_dashboard()
elif page == "Template Builder":
    page_template_builder()
else:
    page_home()