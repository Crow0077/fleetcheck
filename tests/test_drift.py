#!/usr/bin/env python3
"""Prove the drift/regression loop: baseline -> degradation -> detected regression.

1. Snapshot a clean-ish fleet.
2. Break something (a healthy job starts failing repeatedly).
3. `watch` must report a regression and exit non-zero.
4. Fix it and `watch` must report improvement.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CWD = Path(__file__).parent.parent


def run(args):
    return subprocess.run([sys.executable, "-m", "fleetcheck.cli"] + args,
                          capture_output=True, text=True, cwd=str(CWD))


def main():
    root = Path(tempfile.mkdtemp())
    now = "2026-10-12T12:00:00+00:00"

    jobs = [
        {"id": "ok1", "name": "healthy", "schedule": {"kind": "cron", "expr": "0 9 * * *"},
         "repeat": {"times": None, "completed": 10}, "last_run_at": now,
         "last_status": "ok", "prompt": "fine"},
    ]
    (root / "cron").mkdir(parents=True)
    (root / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))

    # 1. baseline
    r = run(["snapshot", "--root", str(root)])
    if r.returncode != 0:
        print("FAIL: snapshot errored:", r.stderr)
        sys.exit(1)
    print("baseline:", r.stdout.strip())

    files = list((root / "fleetcheck" / "history").glob("*.json"))
    if not files:
        print("FAIL: no history file written")
        sys.exit(1)
    baseline = json.loads(files[0].read_text())
    print("baseline score:", baseline["score"], "findings:", len(baseline["findings"]))

    # 2. degrade: the job starts failing in a streak
    jobs[0]["last_status"] = "error"
    jobs[0]["failure_streak"] = 3
    jobs[0]["last_failure"] = "upstream 502"
    (root / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))

    r = run(["watch", "--root", str(root), "--json"])
    result = json.loads(r.stdout)
    print("after degradation:", result["status"], "score", result["previous_score"], "->", result["current_score"])

    run(["snapshot", "--root", str(root)])

    ok = True
    if result["status"] != "regression":
        print("FAIL: regression not detected"); ok = False
    if not result["regressions"]:
        print("FAIL: no regression findings listed"); ok = False
    if r.returncode != 1:
        print("FAIL: watch should exit 1 on regression, got", r.returncode); ok = False

    # 3. fix it
    jobs[0]["last_status"] = "ok"
    jobs[0]["failure_streak"] = 0
    (root / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))

    run(["snapshot", "--root", str(root)])
    r = run(["watch", "--root", str(root), "--json"])
    fixed = json.loads(r.stdout)
    print("after fix:", fixed["status"], "score", fixed["previous_score"], "->", fixed["current_score"])
    if fixed["current_score"] >= fixed["previous_score"]:
        print("score recovered")
    else:
        print("FAIL: score did not recover"); ok = False

    # 4. history accumulates
    r = run(["history", "--root", str(root)])
    print("history entries:", r.stdout.strip().splitlines()[0])
    if len(list((root / "fleetcheck" / "history").glob("*.json"))) < 3:
        print("FAIL: history did not accumulate")
        ok = False

    print("TEST PASSED" if ok else "TEST FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
