#!/usr/bin/env python3
"""Verify the OpenClaw adapter against an OpenClaw-shaped state directory.

OpenClaw stores cron jobs under ~/.openclaw/cron/ and skills under
~/.openclaw/skills/<name>/SKILL.md. This builds that shape and checks the
adapter reads it.
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CWD = Path(__file__).parent.parent


def run(args):
    return subprocess.run([sys.executable, "-m", "fleetcheck.cli"] + args,
                          capture_output=True, text=True, cwd=str(CWD))


def main():
    root = Path(tempfile.mkdtemp())
    now = datetime.now(timezone.utc).isoformat()

    # OpenClaw shape
    (root / "cron").mkdir(parents=True)
    (root / "cron" / "jobs.json").write_text(json.dumps({
        "jobs": [
            {"id": "oc1", "name": "heartbeat",
             "schedule": {"kind": "cron", "expr": "0 * * * *"},
             "repeat": {"times": None, "completed": 12},
             "last_run_at": now, "last_status": "ok",
             "prompt": "check things"},
            {"id": "oc2", "name": "ghost",
             "schedule": {"kind": "cron", "expr": "0 9 * * *"},
             "repeat": {"times": None, "completed": 0},
             "last_run_at": None, "last_status": None,
             "prompt": "never fired"},
        ]
    }, indent=2))

    (root / "skills" / "good-skill").mkdir(parents=True)
    (root / "skills" / "good-skill" / "SKILL.md").write_text(
        "---\nname: good-skill\ndescription: does a thing.\n---\nbody")

    (root / "skills" / "bad-skill").mkdir(parents=True)
    (root / "skills" / "bad-skill" / "SKILL.md").write_text("---\nname: bad-skill\n---\nbody")

    r = run(["audit", "--root", str(root), "--json", "--fail-on", "never"])
    data = json.loads(r.stdout)
    ids = {f["id"] for f in data["findings"]}

    ok = True
    if data["summary"]["jobs"] != 2:
        print("FAIL: OpenClaw jobs not read (got %s)" % data["summary"]["jobs"]); ok = False
    if data["summary"]["skills"] != 2:
        print("FAIL: OpenClaw skills not read (got %s)" % data["summary"]["skills"]); ok = False
    if "job-never-ran-oc2" not in ids:
        print("FAIL: never-fired OpenClaw job not detected"); ok = False
    if "skill-nodesc-bad-skill" not in ids:
        print("FAIL: undescribed OpenClaw skill not detected"); ok = False

    print("OpenClaw-shaped fleet: %d jobs, %d skills, %d findings"
          % (data["summary"]["jobs"], data["summary"]["skills"], data["summary"]["findings"]))
    print("TEST PASSED" if ok else "TEST FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
