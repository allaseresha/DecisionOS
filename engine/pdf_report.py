from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from datetime import datetime
import os

def safe_text(x: str) -> str:
    return (x or "").replace("\n", " ").strip()

def write_pdf_report(output_path: str, record: dict) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4

    y = height - 2 * cm

    # Header
    c.setFont("Helvetica-Bold", 18)
    c.drawString(2 * cm, y, "DecisionOS — Decision Report")
    y -= 1.0 * cm

    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, y, f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z")
    y -= 1.2 * cm

    # Meta
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Decision Summary")
    y -= 0.8 * cm

    c.setFont("Helvetica", 11)
    lines = [
        f"Title: {safe_text(record.get('title'))}",
        f"Template: {safe_text(record.get('template_name'))}",
        f"Timestamp (UTC): {safe_text(record.get('timestamp_utc'))}",
        f"Schema Version: {safe_text(str(record.get('schema_version', '—')))}",
        f"Engine Version: {safe_text(str(record.get('engine_version', '—')))}",
        f"Ruleset Version: {safe_text(str(record.get('ruleset_version', '—')))}",
        f"Final Score: {record.get('final_score')} / 10",
        f"Outcome: {safe_text(record.get('outcome'))}",
        f"Confidence: {safe_text(record.get('confidence'))}",
    ]
    for line in lines:
        c.drawString(2 * cm, y, line)
        y -= 0.6 * cm

    y -= 0.3 * cm

    # Context
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Context")
    y -= 0.8 * cm
    c.setFont("Helvetica", 11)
    context = safe_text(record.get("context", ""))
    if not context:
        context = "N/A"
    for chunk in split_text(context, 95):
        c.drawString(2 * cm, y, chunk)
        y -= 0.55 * cm

    y -= 0.3 * cm

    # ----------------------------
    # STEP 10 — Assumptions & Unknowns
    # ----------------------------
    assumptions = record.get("assumptions", []) or []
    unknowns = record.get("unknowns", []) or []
    assumptions_notes = safe_text(record.get("assumptions_notes", ""))
    unknowns_notes = safe_text(record.get("unknowns_notes", ""))

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Assumptions")
    y -= 0.8 * cm
    c.setFont("Helvetica", 11)

    if assumptions:
        for a in assumptions:
            for chunk in split_text(f"- {safe_text(str(a))}", 95):
                c.drawString(2 * cm, y, chunk)
                y -= 0.55 * cm
                if y < 3 * cm:
                    c.showPage()
                    y = height - 2 * cm
                    c.setFont("Helvetica", 11)
    else:
        c.drawString(2 * cm, y, "None captured.")
        y -= 0.55 * cm

    if assumptions_notes:
        c.drawString(2 * cm, y, "Notes:")
        y -= 0.55 * cm
        for chunk in split_text(assumptions_notes, 95):
            c.drawString(2 * cm, y, chunk)
            y -= 0.55 * cm
            if y < 3 * cm:
                c.showPage()
                y = height - 2 * cm
                c.setFont("Helvetica", 11)

    y -= 0.3 * cm
    if y < 3 * cm:
        c.showPage()
        y = height - 2 * cm
        c.setFont("Helvetica", 11)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Unknowns / Risks")
    y -= 0.8 * cm
    c.setFont("Helvetica", 11)

    if unknowns:
        for u in unknowns:
            for chunk in split_text(f"- {safe_text(str(u))}", 95):
                c.drawString(2 * cm, y, chunk)
                y -= 0.55 * cm
                if y < 3 * cm:
                    c.showPage()
                    y = height - 2 * cm
                    c.setFont("Helvetica", 11)
    else:
        c.drawString(2 * cm, y, "None captured.")
        y -= 0.55 * cm

    if unknowns_notes:
        c.drawString(2 * cm, y, "Notes:")
        y -= 0.55 * cm
        for chunk in split_text(unknowns_notes, 95):
            c.drawString(2 * cm, y, chunk)
            y -= 0.55 * cm
            if y < 3 * cm:
                c.showPage()
                y = height - 2 * cm
                c.setFont("Helvetica", 11)

    y -= 0.3 * cm

    # ----------------------------
    # STEP 11 — Scenario Stress Test
    # ----------------------------
    sst = record.get("scenario_stress_test") or {}
    results = sst.get("results") or {}

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Scenario Stress Test (Step 11)")
    y -= 0.8 * cm
    c.setFont("Helvetica", 11)

    if results:
        spread = sst.get("spread")
        c.drawString(2 * cm, y, f"Spread (Best - Worst): {spread}")
        y -= 0.55 * cm

        for label in ["best", "expected", "worst"]:
            rr = results.get(label, {})
            line = f"{label.title()}: score={rr.get('score')} | outcome={safe_text(rr.get('outcome'))} | confidence={safe_text(rr.get('confidence'))}"
            for chunk in split_text(line, 95):
                c.drawString(2 * cm, y, chunk)
                y -= 0.55 * cm
                if y < 3 * cm:
                    c.showPage()
                    y = height - 2 * cm
                    c.setFont("Helvetica", 11)
    else:
        c.drawString(2 * cm, y, "No scenario data captured.")
        y -= 0.55 * cm

    y -= 0.3 * cm

    # Scores
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Scores")
    y -= 0.8 * cm
    c.setFont("Helvetica", 11)
    scores = record.get("scores", {}) or {}
    for k, v in scores.items():
        c.drawString(2 * cm, y, f"- {k}: {v}")
        y -= 0.55 * cm
        if y < 3 * cm:
            c.showPage()
            y = height - 2 * cm

    y -= 0.3 * cm

    # Explainability
    exp = record.get("explanation") or {}
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Explainability")
    y -= 0.8 * cm
    c.setFont("Helvetica", 11)

    def draw_list(title, items, key1, key2=None):
        nonlocal y
        c.setFont("Helvetica-Bold", 11)
        c.drawString(2 * cm, y, title)
        y -= 0.6 * cm
        c.setFont("Helvetica", 11)
        for it in items:
            if key2:
                c.drawString(2 * cm, y, f"- {it.get(key1)}: {it.get(key2)}")
            else:
                c.drawString(2 * cm, y, f"- {it.get(key1)}")
            y -= 0.55 * cm

    draw_list("Lowest dimensions", exp.get("lowest_dimensions", []), "dimension", "score")
    y -= 0.2 * cm
    draw_list("Highest dimensions", exp.get("highest_dimensions", []), "dimension", "score")
    y -= 0.2 * cm
    draw_list("Top positive contributors (weighted)", exp.get("top_positive_contributors", []), "dimension", "weighted")
    y -= 0.2 * cm
    draw_list("Top negative contributors (weighted)", exp.get("top_negative_contributors", []), "dimension", "weighted")

    y -= 0.4 * cm

    # Follow-up
    follow = record.get("follow_up")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Follow-up (Outcome Tracking)")
    y -= 0.8 * cm
    c.setFont("Helvetica", 11)
    if follow:
        c.drawString(2 * cm, y, f"Outcome: {safe_text(follow.get('outcome'))}")
        y -= 0.55 * cm
        c.drawString(2 * cm, y, f"Updated at: {safe_text(follow.get('updated_at_utc'))}")
        y -= 0.55 * cm
        notes = safe_text(follow.get("notes", ""))
        if notes:
            c.drawString(2 * cm, y, "Notes:")
            y -= 0.55 * cm
            for chunk in split_text(notes, 95):
                c.drawString(2 * cm, y, chunk)
                y -= 0.55 * cm
    else:
        c.drawString(2 * cm, y, "Not recorded yet.")

    c.showPage()
    c.save()
    return output_path

def split_text(text: str, max_len: int):
    words = text.split()
    if not words:
        return []
    lines = []
    line = ""
    for w in words:
        if len(line) + len(w) + 1 <= max_len:
            line = (line + " " + w).strip()
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines
