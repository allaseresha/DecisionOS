import os
import json
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
CUSTOM_TEMPLATES_PATH = "data/custom_templates.json"
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


# ----------------------------
# Helpers
# ----------------------------
def badge(text: str, tone: str = "neutral"):
    tones = {
        "neutral": ("#111827", "#E5E7EB"),
        "good":    ("#065F46", "#D1FAE5"),
        "warn":    ("#92400E", "#FEF3C7"),
        "bad":     ("#7F1D1D", "#FEE2E2"),
        "info":    ("#1E3A8A", "#DBEAFE"),
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
            ">
            {text}
        </span>
        """,
        unsafe_allow_html=True,
    )


def section_title(title: str, subtitle: str = ""):
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)
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
    """Render playbook safely with unique Streamlit keys."""
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


def _norm_set(items):
    return set([str(x).strip() for x in (items or []) if str(x).strip()])


def _clamp_0_10(x: float) -> float:
    return max(0.0, min(10.0, float(x)))


# ----------------------------
# Pages
# ----------------------------
def page_about():
    st.subheader("What this is")
    st.write(
        """
DecisionOS helps teams make high-stakes decisions using:
- Structured scoring
- Transparent rules
- Explainability (why the outcome happened)
- An audit trail (decision history)

This is an MVP built to be simple, universal, and easy to adopt.
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

    st.markdown("---")

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
        st.bar_chart(outcome_df.set_index("Outcome")) if len(outcome_df) else st.write("No outcome data.")
    with cB:
        st.subheader("Confidence distribution")
        st.bar_chart(conf_df.set_index("Confidence")) if len(conf_df) else st.write("No confidence data.")

    st.subheader("Most common weak dimensions")
    if metrics.get("weak_dimensions"):
        weak_df = pd.DataFrame(metrics["weak_dimensions"], columns=["Dimension", "Count"])
        st.bar_chart(weak_df.set_index("Dimension"))
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
            st.bar_chart(df_gap)

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

            st.markdown("---")
            st.subheader("Assumptions & Unknowns changes (v1 → latest)")
            a1 = v1.get("assumptions", []) or []
            a2 = vlast.get("assumptions", []) or []
            u1 = v1.get("unknowns", []) or []
            u2 = vlast.get("unknowns", []) or []

            a_added = _norm_set(a2) - _norm_set(a1)
            a_removed = _norm_set(a1) - _norm_set(a2)
            u_added = _norm_set(u2) - _norm_set(u1)
            u_removed = _norm_set(u1) - _norm_set(u2)

            colX1, colX2 = st.columns(2)
            with colX1:
                st.markdown("### Assumptions")
                if not (a_added or a_removed):
                    st.caption("No changes")
                if a_added:
                    st.success("Added")
                    for x in sorted(list(a_added)):
                        st.write("•", x)
                if a_removed:
                    st.error("Removed")
                    for x in sorted(list(a_removed)):
                        st.write("•", x)

            with colX2:
                st.markdown("### Unknowns / Risks")
                if not (u_added or u_removed):
                    st.caption("No changes")
                if u_added:
                    st.success("Added")
                    for x in sorted(list(u_added)):
                        st.write("•", x)
                if u_removed:
                    st.error("Removed")
                    for x in sorted(list(u_removed)):
                        st.write("•", x)

            st.write("Versions timeline:")
            for v in versions:
                st.write(
                    f"- v{v.get('version',1)} • {v.get('timestamp_utc','')} • score={v.get('final_score')} • {v.get('outcome')}"
                )

        shown += 1
        if shown >= 10:
            break

    if shown == 0:
        st.info("No decisions with iterations yet. Create a v2 from Home first.")

    st.markdown("---")
    st.subheader("Decision Accuracy Engine")
    acc = compute_accuracy_metrics(filtered)
    cA1, cA2, cA3, cA4 = st.columns(4)
    cA1.metric("Follow-ups recorded", acc.get("followups_total", 0))
    cA2.metric("Strict cases (GO/NO-GO)", acc.get("total_strict", 0))
    cA3.metric("False GO (FP)", acc.get("fp", 0))
    cA4.metric("False NO-GO (FN)", acc.get("fn", 0))
    st.caption(f"Review bucket (predicted REVIEW/REVISE or actual Partial Success): {acc.get('review_bucket', 0)}")

    calib = acc.get("calibration") or {}
    if calib:
        df_cal = pd.DataFrame(list(calib.items()), columns=["Calibration", "Count"]).set_index("Calibration")
        st.bar_chart(df_cal)
    else:
        st.caption("No calibration data yet.")

    st.markdown("---")
    st.subheader("Export")
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
                "followup_updated_at": (r.get("follow_up") or {}).get("updated_at_utc"),
            }
        )

    df = pd.DataFrame(flat)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV (filtered)",
        data=csv_bytes,
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
    # ---------- Executive header ----------
    head_l, head_r = st.columns([0.72, 0.28], vertical_alignment="center")
    with head_l:
        st.title("DecisionOS")
        st.caption("Explainable • Auditable • Governance-First Decision Intelligence for High-Stakes Decisions")

    with head_r:
        badge(f"Engine {ENGINE_VERSION}", "info")
        st.write("")
        badge(f"Ruleset {RULESET_VERSION}", "info")

    st.divider()

    ALL_TEMPLATES = get_all_templates()

    # Two-column enterprise layout
    form_col, summary_col = st.columns([0.66, 0.34], gap="large")

    # =========================
    # LEFT: Guided Workflow Form
    # =========================
    with form_col:
        with st.form("home_decision_form", clear_on_submit=False, border=True):

            tabs = st.tabs([
                "1) Define",
                "2) Governance",
                "3) Scoring",
                "4) Assumptions",
                "5) Stress Test",
                "6) Review",
            ])

            # ----------------------------
            # TAB 1: Define
            # ----------------------------
            with tabs[0]:
                section_title("Define the decision", "Start with scope and template selection.")

                template_key = st.selectbox(
                    "Select template",
                    options=list(ALL_TEMPLATES.keys()),
                    format_func=lambda k: ALL_TEMPLATES[k].template_name,
                    key="tpl_select",
                )
                rule = ALL_TEMPLATES[template_key]

                title = st.text_input("Decision title (short)", value="", key="decision_title")
                context = st.text_area("Decision context (1–3 lines)", value="", key="decision_context")

            # ----------------------------
            # TAB 2: Governance
            # ----------------------------
            with tabs[1]:
                section_title("Ownership & governance", "Enterprise decisions require explicit accountability.")

                decision_owner = st.text_input("Decision owner (who is accountable?)", value="", key="decision_owner")

                responsibility_confirmed = st.checkbox(
                    "I confirm I am accountable for this decision and accept ownership of the outcome.",
                    help="Industry-grade governance requires explicit ownership. Approval is blocked without this.",
                    key="responsibility_confirmed",
                )

                stakeholders_text = st.text_area(
                    "Stakeholders (one per line)",
                    value="",
                    help="People who were involved, consulted, or need to be informed.",
                    key="stakeholders_text",
                )
                stakeholders = [x.strip() for x in stakeholders_text.splitlines() if x.strip()]

                review_date = st.date_input(
                    "Review date (when should we revisit this decision?)",
                    value=None,
                    key="review_date",
                )

                c1, c2 = st.columns(2)
                with c1:
                    decision_type = st.selectbox(
                        "Decision type",
                        ["Strategic", "Financial", "Hiring", "Operational", "Personal"],
                        key="decision_type",
                    )
                with c2:
                    decision_class = st.selectbox(
                        "Decision class (reversibility)",
                        ["One-way", "Two-way", "Experimental"],
                        index=1,
                        help="One-way is hard to reverse and requires higher scrutiny. Two-way is reversible. Experimental is a controlled trial.",
                        key="decision_class",
                    )

            # ----------------------------
            # TAB 3: Scoring
            # ----------------------------
            with tabs[2]:
                section_title("Score the dimensions", "0–10 scoring with transparent weights.")
                st.caption("10 = best. If a dimension represents risk/complexity, give a lower score when risk/complexity is high.")

                # rule must be available; if user hasn't opened Define tab yet, we still have template_key set
                # because it's in the form. Ensure rule exists:
                rule = ALL_TEMPLATES[st.session_state.get("tpl_select", list(ALL_TEMPLATES.keys())[0])]

                scores = {}
                for dim in rule.dimensions:
                    scores[dim] = st.slider(
                        dim, min_value=0.0, max_value=10.0, value=5.0, step=0.5, key=f"score_{dim}"
                    )

            # ----------------------------
            # TAB 4: Assumptions & Unknowns
            # ----------------------------
            with tabs[3]:
                section_title("Assumptions & unknowns", "Captured for auditability and later learning.")
                st.caption("Write one item per line. These are saved for reports and history.")

                assumptions_text = st.text_area("Assumptions (one per line)", value="", key="assumptions_text")
                unknowns_text = st.text_area("Unknowns / Risks (one per line)", value="", key="unknowns_text")

                assumptions = [x.strip() for x in assumptions_text.splitlines() if x.strip()]
                unknowns = [x.strip() for x in unknowns_text.splitlines() if x.strip()]

                assumptions_notes = st.text_area("Assumptions Notes (optional)", value="", key="assumptions_notes")
                unknowns_notes = st.text_area("Unknowns Notes (optional)", value="", key="unknowns_notes")

            # ----------------------------
            # TAB 5: Stress Test
            # ----------------------------
            with tabs[4]:
                section_title("Scenario stress testing", "Simulate best/expected/worst outcomes.")
                st.caption("We adjust the final score by +/- deltas to simulate outcomes.")

                cS1, cS2, cS3 = st.columns(3)
                with cS1:
                    best_delta = st.number_input("Best-case delta (+)", value=1.0, step=0.5, key="best_delta")
                with cS2:
                    expected_delta = st.number_input("Expected delta", value=0.0, step=0.5, key="expected_delta")
                with cS3:
                    worst_delta = st.number_input("Worst-case delta (-)", value=-2.0, step=0.5, key="worst_delta")

            # ----------------------------
            # TAB 6: Review (Readiness)
            # ----------------------------
            with tabs[5]:
                section_title("Readiness review", "Governance gate before evaluation.")

                # Re-resolve values (safe)
                rule = ALL_TEMPLATES[st.session_state.get("tpl_select", list(ALL_TEMPLATES.keys())[0])]

                # Build scores from session_state sliders
                scores = {}
                for dim in rule.dimensions:
                    scores[dim] = float(st.session_state.get(f"score_{dim}", 5.0))

                preview_final_score = compute_weighted_score(rule, scores)
                preview_confidence = confidence_band(preview_final_score)

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
                        "confidence": preview_confidence,
                        "weights": rule.weights,
                        "responsibility_confirmed": bool(st.session_state.get("responsibility_confirmed", False)),
                    }
                )

                if readiness.status == "APPROVE":
                    badge(f"Readiness: {readiness.score}% — APPROVE", "good")
                    st.caption(f"Minimum required: {readiness.min_required}%")
                elif readiness.status == "REVIEW":
                    badge(f"Readiness: {readiness.score}% — REVIEW", "warn")
                    st.caption(f"Minimum required: {readiness.min_required}%")
                else:
                    badge(f"Readiness: {readiness.score}% — BLOCKED", "bad")
                    st.caption(f"Minimum required: {readiness.min_required}%")

                if readiness.blockers:
                    st.info("Hard blockers:")
                    for b in readiness.blockers:
                        st.write(f"- {b}")

                if readiness.issues:
                    st.info("What to improve:")
                    for i in readiness.issues:
                        st.write(f"- {i}")

            # ----------------------------
            # Single submit button
            # ----------------------------
            submitted = st.form_submit_button("Evaluate Decision", use_container_width=True)

            # ----------------------------
            # Submit handling
            # ----------------------------
            if submitted:
                # Resolve all values once (clean)
                template_key = st.session_state.get("tpl_select", list(ALL_TEMPLATES.keys())[0])
                rule = ALL_TEMPLATES[template_key]

                t = (st.session_state.get("decision_title", "") or "").strip() or "Untitled Decision"
                c = (st.session_state.get("decision_context", "") or "").strip() or "N/A"

                decision_owner = (st.session_state.get("decision_owner", "") or "").strip()
                responsibility_confirmed = bool(st.session_state.get("responsibility_confirmed", False))

                stakeholders_text = st.session_state.get("stakeholders_text", "") or ""
                stakeholders = [x.strip() for x in stakeholders_text.splitlines() if x.strip()]

                review_date = st.session_state.get("review_date", None)
                decision_type = st.session_state.get("decision_type")
                decision_class = st.session_state.get("decision_class")

                assumptions_text = st.session_state.get("assumptions_text", "") or ""
                unknowns_text = st.session_state.get("unknowns_text", "") or ""
                assumptions = [x.strip() for x in assumptions_text.splitlines() if x.strip()]
                unknowns = [x.strip() for x in unknowns_text.splitlines() if x.strip()]

                assumptions_notes = (st.session_state.get("assumptions_notes", "") or "").strip()
                unknowns_notes = (st.session_state.get("unknowns_notes", "") or "").strip()

                best_delta = float(st.session_state.get("best_delta", 1.0))
                expected_delta = float(st.session_state.get("expected_delta", 0.0))
                worst_delta = float(st.session_state.get("worst_delta", -2.0))

                scores = {}
                for dim in rule.dimensions:
                    scores[dim] = float(st.session_state.get(f"score_{dim}", 5.0))

                # Readiness gating (enforced at submit)
                preview_final_score = compute_weighted_score(rule, scores)
                preview_confidence = confidence_band(preview_final_score)

                readiness = calculate_decision_readiness(
                    {
                        "owner": decision_owner,
                        "decision_type": decision_type,
                        "decision_class": decision_class,
                        "stakeholders": stakeholders,
                        "assumptions": assumptions,
                        "risks": unknowns,
                        "confidence": preview_confidence,
                        "weights": rule.weights,
                        "responsibility_confirmed": responsibility_confirmed,
                    }
                )

                if readiness.status == "BLOCK":
                    st.error("Evaluation blocked by governance readiness. Fix blockers/issues in the Review tab.")
                    st.session_state.last_record = None
                    st.session_state.last_playbook = None
                return

                # Run engine (same as your old logic)
                final_score = compute_weighted_score(rule, scores)
                outcome = determine_outcome(rule, final_score)
                confidence = confidence_band(final_score)
                explanation = explain_decision(rule, scores)
                playbook = build_playbook(rule, scores, final_score, outcome, explanation)

                expected_score = _clamp_0_10(final_score + expected_delta)
                best_score = _clamp_0_10(final_score + best_delta)
                worst_score = _clamp_0_10(final_score + worst_delta)

                scenario_results = {
                    "expected": {"score": expected_score, "outcome": determine_outcome(rule, expected_score), "confidence": confidence_band(expected_score)},
                    "best": {"score": best_score, "outcome": determine_outcome(rule, best_score), "confidence": confidence_band(best_score)},
                    "worst": {"score": worst_score, "outcome": determine_outcome(rule, worst_score), "confidence": confidence_band(worst_score)},
                }

                scenario_stress_test = {
                    "expected_delta": float(expected_delta),
                    "best_delta": float(best_delta),
                    "worst_delta": float(worst_delta),
                    "results": scenario_results,
                    "spread": round(best_score - worst_score, 2),
                }

                record = {
                    "decision_id": new_id(),
                    "timestamp_utc": now_iso(),
                    "schema_version": 2,
                    "template_id": rule.template_id,
                    "template_name": rule.template_name,
                    "title": t,
                    "context": c,
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
                }

                append_jsonl(HISTORY_PATH, record)
                st.session_state.last_record = record
                st.session_state.last_playbook = playbook
                st.success("Decision evaluated and saved to history.")

    # =========================
    # RIGHT: Decision Summary Panel
    # =========================
    with summary_col:
        st.markdown("#### Decision Summary")
        st.caption("Executive snapshot (draft values + last evaluation).")

        # Draft snapshot (session_state)
        badge("Draft", "info")
        st.write("")
        st.write("**Title:**", (st.session_state.get("decision_title", "") or "").strip() or "—")
        st.write("**Owner:**", (st.session_state.get("decision_owner", "") or "").strip() or "—")
        st.write("**Type:**", st.session_state.get("decision_type", "—"))
        st.write("**Class:**", st.session_state.get("decision_class", "—"))

        st.divider()

        # Last evaluation snapshot
        st.markdown("#### Last Evaluation")
        if st.session_state.last_record:
            r = st.session_state.last_record
            badge(f"{r.get('outcome')}", "good" if r.get("outcome") == "GO" else "warn" if r.get("outcome") == "REVIEW" else "bad")
            st.metric("Final Score", f"{r.get('final_score')} / 10")
            st.write("**Confidence:**", r.get("confidence"))
            st.caption(f"Saved: {r.get('timestamp_utc')}")
        else:
            st.caption("No evaluation yet in this session.")

    # =========================
    # Show latest result (unchanged logic, just kept below)
    # =========================
    if st.session_state.last_record:
        r = st.session_state.last_record
        pb = st.session_state.last_playbook
        decision_id = normalize_decision_id(r.get("decision_id"))

        st.markdown("---")
        st.subheader("Result")
        c1, c2, c3 = st.columns(3)
        c1.metric("Final Score", f"{r.get('final_score')} / 10")
        c2.metric("Outcome", r.get("outcome"))
        c3.metric("Confidence", r.get("confidence"))

        render_explainability(r.get("explanation") or {})

        st.markdown("---")
        render_playbook(pb, key_prefix=f"{decision_id}_home")

        st.markdown("---")
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
        else:
            st.caption("No scenario data captured.")

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

    # ----------------------------
    # Iteration
    # ----------------------------
    st.markdown("---")
    st.subheader("Iteration (Re-score after fixes)")
    st.caption("This saves a new v2 record linked to the last decision you evaluated.")

    if st.button("Create a revised version (v2)", key="btn_make_v2"):
       base = st.session_state.get("last_record", None)  # <-- your current session key

       if not base:
        st.warning("No prior decision found. Evaluate a decision first.")
    else:
        revised = dict(base)
        revised["decision_id"] = new_id()
        revised["timestamp_utc"] = now_iso()
        revised["parent_id"] = base["decision_id"]
        revised["version"] = int(base.get("version", 1)) + 1
        revised["title"] = f"{base.get('title','Untitled')} (Revised v{revised['version']})"
        revised["schema_version"] = base.get("schema_version", 2)
        revised["engine_version"] = ENGINE_VERSION
        revised["ruleset_version"] = RULESET_VERSION

        append_jsonl(HISTORY_PATH, revised)
        st.success("Revised version saved. Go to History or Dashboard → Compare.")

# ----------------------------
# Main app shell
# ----------------------------
st.title("DecisionOS — Decision Intelligence (MVP)")
st.caption("Explainable, auditable decision templates for any business.")

page = st.sidebar.radio("Navigate", ["Home", "History", "Dashboard", "Template Builder", "About"], key="nav")

if page == "About":
    page_about()
    st.stop()

if page == "History":
    page_history()
    st.stop()

if page == "Dashboard":
    page_dashboard()
    st.stop()

if page == "Template Builder":
    page_template_builder()
    st.stop()

page_home()