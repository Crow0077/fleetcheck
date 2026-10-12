"""Drift history and regression alerting.

The free tier audits the present. The paid tier remembers the past and tells you
when the fleet gets worse.

Snapshots are stored as a rolling series under <root>/fleetcheck/history/.
`watch` compares the current findings against the previous snapshot and reports
what appeared, what disappeared, and the trend.
"""
import json
from datetime import datetime, timezone
from pathlib import Path


def history_dir(root):
    return root / "fleetcheck" / "history"


def record(root, summary, findings):
    hd = history_dir(root)
    hd.mkdir(parents=True, exist_ok=True)
    entry = {
        "taken_at": now_iso(),
        "score": score(summary, findings),
        "summary": summary,
        "findings": [
            {"id": f["id"], "severity": f["severity"], "title": f["title"]}
            for f in findings
        ],
    }
    path = hd / f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}.json"
    path.write_text(json.dumps(entry, indent=2))
    return path, entry


def score(summary, findings):
    weight = {"critical": 25, "high": 10, "medium": 4, "low": 1, "info": 0}
    return max(0, 100 - sum(weight[f["severity"]] for f in findings))


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_history(root, limit=50):
    hd = history_dir(root)
    if not hd.exists():
        return []
    entries = []
    for p in sorted(hd.glob("*.json"), reverse=True)[:limit]:
        try:
            entries.append(json.loads(p.read_text()))
        except Exception:
            pass
    return entries


def watch(root, summary, findings):
    history = load_history(root, limit=1)
    if not history:
        return {"status": "no_baseline", "message": "No previous snapshot. Run `fleetcheck snapshot`."}

    prev = history[0]
    prev_ids = {f["id"] for f in prev["findings"]}
    cur_ids = {f["id"] for f in findings}

    appeared = [f for f in findings if f["id"] not in prev_ids]
    resolved = [f for f in prev["findings"] if f["id"] not in cur_ids]

    cur_score = score(summary, findings)
    delta = cur_score - prev["score"]

    regressions = [f for f in appeared if f["severity"] in ("critical", "high")]

    return {
        "status": "regression" if regressions else ("improved" if delta > 0 else "stable"),
        "previous_score": prev["score"],
        "current_score": cur_score,
        "delta": delta,
        "new_findings": appeared,
        "resolved_findings": resolved,
        "regressions": regressions,
    }
