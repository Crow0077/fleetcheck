#!/usr/bin/env python3
"""Test fleetcheck against a synthetic fleet with known defects.

Builds a fake agent state directory containing exactly the failure modes the
tool claims to detect, then asserts each one is caught.
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOL = Path(__file__).parent.parent / "fleetcheck" / "cli.py"


def build_fixture(root):
    now = datetime.now(timezone.utc)

    jobs = [
        # 1. never ran
        {"id": "j1", "name": "never-ran", "schedule": {"kind": "cron", "expr": "0 9 * * *"},
         "repeat": {"times": None, "completed": 0}, "last_run_at": None, "last_status": None,
         "prompt": "do the thing"},
        # 2. failing repeatedly
        {"id": "j2", "name": "always-fails", "schedule": {"kind": "cron", "expr": "0 9 * * *"},
         "repeat": {"times": None, "completed": 30}, "last_run_at": now.isoformat(),
         "last_status": "error", "failure_streak": 4, "last_failure": "gateway timeout",
         "prompt": "short prompt"},
        # 3. references a missing script
        {"id": "j3", "name": "missing-script", "schedule": {"kind": "cron", "expr": "0 9 * * *"},
         "repeat": {"times": None, "completed": 5}, "last_run_at": now.isoformat(),
         "last_status": "ok", "script": "gone.py", "prompt": "prompt"},
        # 4. stale daily job
        {"id": "j4", "name": "stale-daily", "schedule": {"kind": "cron", "expr": "0 9 * * *"},
         "repeat": {"times": None, "completed": 20},
         "last_run_at": (now - timedelta(days=12)).isoformat(), "last_status": "ok",
         "prompt": "prompt"},
        # 5. leftover one-shot
        {"id": "j5", "name": "old-one-shot", "schedule": {"kind": "once", "run_at": "2026-01-01T09:00:00"},
         "repeat": {"times": 1, "completed": 1}, "last_run_at": "2026-01-01T09:01:00",
         "last_status": "ok", "prompt": "prompt"},
        # 6. heavy context
        {"id": "j6", "name": "context-hog", "schedule": {"kind": "cron", "expr": "0 9 * * *"},
         "repeat": {"times": None, "completed": 10}, "last_run_at": now.isoformat(),
         "last_status": "ok", "prompt": "x" * 13000, "skills": ["huge-skill"]},
    ]

    (root / "cron").mkdir(parents=True, exist_ok=True)
    (root / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}, indent=2))

    # orphan output dir for a job that does not exist
    (root / "cron" / "output" / "ghost-job").mkdir(parents=True)
    (root / "cron" / "output" / "ghost-job" / "run.txt").write_text("old output")

    # stale output for j4
    (root / "cron" / "output" / "j4").mkdir(parents=True)
    stale = root / "cron" / "output" / "j4" / "run.txt"
    stale.write_text("old")
    old_ts = (now - timedelta(days=14)).timestamp()
    import os
    os.utime(stale, (old_ts, old_ts))

    # skill with no description
    (root / "skills" / "nodesc").mkdir(parents=True)
    (root / "skills" / "nodesc" / "SKILL.md").write_text("---\nname: nodesc\n---\n\nbody text\n")

    # huge skill loaded by the context hog
    (root / "skills" / "huge-skill").mkdir(parents=True)
    (root / "skills" / "huge-skill" / "SKILL.md").write_text("---\nname: huge-skill\ndescription: big\n---\n" + "y" * 20000)

    # cost attribution: a usage log with a silent, expensive fire
    (root / "cron" / "usage_audit.jsonl").write_text("\n".join([
        json.dumps({"ts": now.isoformat(), "job_id": "j2", "fire_id": "f1",
                    "prompt_tokens": 900000, "completion_tokens": 1000,
                    "total_tokens": 901000, "response_silent": True,
                    "model": "deepseek-v4-flash", "duration_ms": 10000, "error": None}),
        json.dumps({"ts": now.isoformat(), "job_id": "j6", "fire_id": "f2",
                    "prompt_tokens": 600000, "completion_tokens": 900,
                    "total_tokens": 600900, "response_silent": False,
                    "model": "deepseek-v4-flash", "duration_ms": 5000, "error": None}),
    ]))

    return jobs


def run(args):
    return subprocess.run([sys.executable, "-m", "fleetcheck.cli"] + args,
                          capture_output=True, text=True,
                          cwd=str(Path(__file__).parent.parent))


def main():
    root = Path(tempfile.mkdtemp())
    build_fixture(root)

    r = run(["audit", "--root", str(root), "--json"])
    data = json.loads(r.stdout)
    ids = {f["id"] for f in data["findings"]}

    expected = {
        "job-never-ran-j1": "scheduled job that never fired",
        "failure-streak-j2": "repeated failure streak",
        "job-error-j2": "last run failed",
        "job-missing-script-j3": "job references a missing script",
        "behavior-stale-j4": "daily job with stale output",
        "job-oneshot-leftover-j5": "completed one-shot left in schedule",
        "context-heavy-j6": "job carrying excessive context",
        "orphan-output-ghost-job": "output dir for a deleted job",
        "skill-nodesc-nodesc": "skill with no description",
    }

    failures = []
    for fid, label in expected.items():
        if fid not in ids:
            failures.append(f"MISSING: {label} ({fid})")

    # false-positive guard: a healthy job should not be flagged
    if "behavior-stale-j2" in ids:
        failures.append("FALSE POSITIVE: healthy job j2 flagged as stale")

    # cost attribution
    rc = run(["cost", "--root", str(root), "--json"])
    cost = json.loads(rc.stdout)
    if cost["total_cost"] <= 0:
        failures.append("MISSING: cost attribution produced no spend")
    cost_ids = {f["id"] for f in cost["findings"]}
    if "cost-silent-j2" not in cost_ids:
        failures.append("MISSING: silent expensive fire not flagged")
    if "cost-bloat-j6" not in cost_ids:
        failures.append("MISSING: context bloat not flagged")

    print("FINDINGS DETECTED:", len(ids))
    for f in sorted(data["findings"], key=lambda x: x["id"]):
        print(f"  {f['severity']:8}  {f['title']}")
    print()
    if failures:
        print("TEST FAILED")
        for f in failures:
            print("  " + f)
        sys.exit(1)

    print("ALL 9 EXPECTED DEFECTS DETECTED, 0 FALSE POSITIVES")
    print("exit code:", r.returncode)
    if r.returncode != 1:
        print("TEST FAILED: expected non-zero exit for critical/high findings")
        sys.exit(1)
    print("TEST PASSED")


if __name__ == "__main__":
    main()
