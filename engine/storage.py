import json
import os
import uuid
from typing import Dict, List
from datetime import datetime

def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def new_id(prefix: str = "dec") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def append_jsonl(path: str, record: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def read_jsonl(path: str, limit: int = 200) -> List[Dict]:
    if not os.path.exists(path):
        return []
    rows: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-limit:]

def overwrite_jsonl(path: str, rows: List[Dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def update_decision_outcome(path: str, decision_id: str, outcome: str, notes: str) -> bool:
    """
    Updates an existing decision record by decision_id.
    Returns True if updated, False if not found.
    """
    rows = read_jsonl(path, limit=5000)
    updated = False

    for r in rows:
        if r.get("decision_id") == decision_id:
            r["follow_up"] = {
                "outcome": outcome,
                "notes": notes,
                "updated_at_utc": now_iso(),
            }
            updated = True
            break

    if updated:
        overwrite_jsonl(path, rows)
    return updated

def delete_decisions_by_title_contains(path: str, text: str) -> int:
    """
    Deletes records whose title contains the given text (case-insensitive).
    Returns number deleted.
    """
    rows = read_jsonl(path, limit=50000)
    if not rows:
        return 0

    text_l = text.lower().strip()
    kept = []
    deleted = 0

    for r in rows:
        title = str(r.get("title", "")).lower()
        if text_l and text_l in title:
            deleted += 1
        else:
            kept.append(r)

    if deleted > 0:
        overwrite_jsonl(path, kept)

    return deleted


def delete_legacy_no_id(path: str) -> int:
    """
    Deletes records that do not have decision_id.
    Returns number deleted.
    """
    rows = read_jsonl(path, limit=50000)
    if not rows:
        return 0

    kept = []
    deleted = 0

    for r in rows:
        if r.get("decision_id"):
            kept.append(r)
        else:
            deleted += 1

    if deleted > 0:
        overwrite_jsonl(path, kept)

    return deleted

def migrate_history_schema(path: str, target_schema_version: int = 2) -> dict:
    import json

    rows = read_jsonl(path, limit=10_000_000)  # read all
    if not rows:
        return {"total": 0, "updated": 0, "written": 0}

    updated = 0
    out_rows = []

    for r in rows:
        if not isinstance(r, dict):
            continue

        changed = False

        # schema version
        if r.get("schema_version") != target_schema_version:
            r["schema_version"] = target_schema_version
            changed = True

        # Step 10 defaults
        r.setdefault("assumptions", [])
        r.setdefault("unknowns", [])
        r.setdefault("assumptions_notes", "")
        r.setdefault("unknowns_notes", "")

        # Step 11 defaults
        r.setdefault("scenario_stress_test", {})

        # Step 13 defaults
        r.setdefault("decision_type", "Unknown")

        # Step 16 defaults
        r.setdefault("decision_owner", "")
        r.setdefault("stakeholders", [])
        r.setdefault("review_date", "")

        # follow-up structure stable
        fu = r.get("follow_up")
        if isinstance(fu, dict):
            fu.setdefault("outcome", "")
            fu.setdefault("notes", "")
            fu.setdefault("updated_at_utc", "")

        # check if any defaults were added
        # (setdefault doesn't tell us, so we treat it as changed if schema_version changed)
        if changed:
            updated += 1

        out_rows.append(r)

    # write back JSONL
    with open(path, "w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return {"total": len(rows), "updated": updated, "written": len(out_rows)}
