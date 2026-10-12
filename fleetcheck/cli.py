#!/usr/bin/env python3
"""
fleetcheck — behavioral audit for AI agent fleets.

Dashboards tell you what is *running*. fleetcheck tells you what is *working*,
and what is quietly broken, orphaned, dormant, or costing you money.

Works against Hermes and OpenClaw installs. Runs locally; nothing leaves the box.

  fleetcheck audit                 # full audit, scorecard to stdout
  fleetcheck audit --json          # machine-readable findings
  fleetcheck audit --md            # markdown report
  fleetcheck snapshot              # save a state snapshot for drift detection
  fleetcheck drift                 # diff current state against last snapshot
  fleetcheck explain <finding-id>  # remediation for one finding
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .cost import analyze_cost, DEFAULT_PRICES

VERSION = "0.1.0"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def days_since(dt):
    if dt is None:
        return None
    return (now_utc() - dt).days


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------

def detect_root(explicit):
    if explicit:
        return Path(explicit)
    for cand in (Path.home() / ".hermes", Path.home() / ".openclaw", Path.home() / ".nanobot"):
        if cand.exists():
            return cand
    return None


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def cron_jobs(root):
    """Normalize cron/scheduled job definitions across harnesses."""
    jobs = []
    for rel in ("cron/jobs.json", "jobs.json", "cron/jobs.jsonl"):
        p = root / rel
        if not p.exists():
            continue
        data = load_json(p)
        if data is None:
            continue
        if isinstance(data, dict):
            data = data.get("jobs") or data.get("items") or []
        if isinstance(data, list):
            for j in data:
                if isinstance(j, dict):
                    jobs.append(j)
    return jobs


def schedule_kind(job):
    sched = job.get("schedule")
    if isinstance(sched, dict):
        return sched.get("kind") or sched.get("display") or "unknown"
    if isinstance(sched, str):
        return "cron"
    return "unknown"


def skill_inventory(root):
    skills = []
    base = root / "skills"
    if not base.exists():
        return skills
    for p in base.rglob("SKILL.md"):
        text = p.read_text(errors="ignore")
        front = text.split("---", 2)[1] if text.startswith("---") else ""
        name = ""
        desc = ""
        for line in front.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip('"')
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip('"')
        skills.append({
            "name": name or p.parent.name,
            "path": str(p),
            "description": desc,
            "size": p.stat().st_size,
            "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc),
            "references": len(list((p.parent / "references").glob("*"))) if (p.parent / "references").exists() else 0,
        })
    return skills


def plugin_inventory(root):
    out = []
    base = root / "plugins"
    if not base.exists():
        return out
    for d in base.iterdir():
        if d.is_dir():
            out.append({"name": d.name, "path": str(d)})
    return out


def output_dirs(root):
    base = root / "cron" / "output"
    if not base.exists():
        return {}
    result = {}
    for d in base.iterdir():
        if d.is_dir() and d.name != "_prompts":
            files = sorted(d.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                result[d.name] = {
                    "count": len(files),
                    "last_run": datetime.fromtimestamp(files[0].stat().st_mtime, timezone.utc),
                }
    return result


def listeners():
    try:
        out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    result = []
    for line in out.splitlines()[1:]:
        m = re.search(r"(\d+\.\d+\.\d+\.\d+|\[::\]):(\d+)", line)
        if m:
            result.append({"addr": m.group(1), "port": int(m.group(2)), "raw": line.strip()})
    return result


def failed_units():
    try:
        out = subprocess.run(["systemctl", "--user", "--failed", "list-units"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    units = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service"):
            units.append(parts[0])
    return units


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

SYSTEM_PORTS = {
    22: "ssh", 53: "dns", 67: "dhcp", 68: "dhcp", 123: "ntp",
    5353: "mdns", 5355: "mdns", 631: "ipp", 111: "rpcbind",
}


def is_system_service(raw, port):
    if port in SYSTEM_PORTS:
        return True
    lowered = raw.lower()
    return any(k in lowered for k in ("sshd", "systemd", "dbus", "avahi", "networkmanager"))


def expected_cadence_days(expr):
    """Rough cadence from a cron expression. Returns days between fires."""
    try:
        parts = expr.split()
        if len(parts) < 5:
            return None
        minute, hour, dom, month, dow = parts[:5]
        if dom == "*" and month == "*" and dow == "*":
            return 1
        if dom == "*" and dow == "*":
            return 1
        if dom == "*" and dow != "*":
            return 7
        return 30
    except Exception:
        return None


def find_skill_file(root, name):
    for p in root.rglob("SKILL.md"):
        if p.parent.name == name:
            return p
    return None


def context_weight(root, job):
    """Estimate the context a job carries each run: prompt + loaded skills."""
    chars = 0
    parts = []
    prompt = job.get("prompt") or ""
    if prompt:
        chars += len(prompt)
        parts.append(("prompt", len(prompt)))
    names = list(job.get("skills") or [])
    if job.get("skill"):
        names.append(job["skill"])
    for name in names:
        sp = find_skill_file(root, name)
        if sp and sp.exists():
            n = sp.stat().st_size
            chars += n
            parts.append((f"skill:{name}", n))
    return chars, parts


def analyze(root):
    findings = []
    jobs = cron_jobs(root)
    outputs = output_dirs(root)
    skills = skill_inventory(root)
    plugins = plugin_inventory(root)

    job_ids = {j.get("id") for j in jobs}

    # 1. Orphaned job output — evidence of a job that no longer exists
    for oid, meta in outputs.items():
        if oid not in job_ids:
            findings.append({
                "id": f"orphan-output-{oid}",
                "category": "orphan",
                "severity": "medium",
                "title": f"Output directory for job {oid} exists but the job is gone",
                "detail": f"{meta['count']} run files, last written {meta['last_run'].date()}. "
                          "A deleted or renamed job left state behind.",
                "remedy": "Confirm the job is intentionally removed, then archive the output directory.",
            })

    # 2. Scheduled jobs that never ran
    for j in jobs:
        status = j.get("last_status")
        completed = (j.get("repeat") or {}).get("completed", 0)
        last = parse_ts(j.get("last_run_at"))
        kind = schedule_kind(j)

        if status == "error":
            findings.append({
                "id": f"job-error-{j.get('id')}",
                "category": "failure",
                "severity": "high",
                "title": f"Job '{j.get('name')}' last run failed",
                "detail": (j.get("last_error") or "no error detail recorded")[:300],
                "remedy": "Run the job manually and inspect the output before the next scheduled fire.",
            })

        streak = j.get("failure_streak") or 0
        if streak >= 2:
            findings.append({
                "id": f"failure-streak-{j.get('id')}",
                "category": "failure",
                "severity": "critical",
                "title": f"Job '{j.get('name')}' has failed {streak} runs in a row",
                "detail": str(j.get("last_failure") or j.get("last_error") or "")[:300],
                "remedy": "A job failing repeatedly is burning scheduled runs. Fix or pause it.",
            })

        if kind == "cron" and completed == 0 and last is None:
            findings.append({
                "id": f"job-never-ran-{j.get('id')}",
                "category": "dead",
                "severity": "high",
                "title": f"Job '{j.get('name')}' is scheduled but has never run",
                "detail": f"Schedule: {j.get('schedule', {}).get('display', 'unknown')}",
                "remedy": "Verify the scheduler is running and the job is enabled.",
            })

        # 3. Stale recurring jobs
        if kind == "cron" and last is not None:
            expr = (j.get("schedule") or {}).get("expr", "")
            daily = expr.count("*") >= 2 and expr.split()[0] != "*"
            if daily and days_since(last) > 3:
                findings.append({
                    "id": f"job-stale-{j.get('id')}",
                    "category": "stale",
                    "severity": "medium",
                    "title": f"Job '{j.get('name')}' is daily but last ran {days_since(last)} days ago",
                    "detail": f"Schedule {expr}; last run {last.date()}",
                    "remedy": "Check the scheduler heartbeat and delivery target.",
                })

        # 4. One-shot jobs that are still scheduled
        if kind == "once" and completed >= 1:
            findings.append({
                "id": f"job-oneshot-leftover-{j.get('id')}",
                "category": "clutter",
                "severity": "low",
                "title": f"One-shot job '{j.get('name')}' completed but remains in the schedule",
                "detail": f"Ran {last.date() if last else 'unknown'}",
                "remedy": "Remove it. Leftover one-shots make the schedule hard to reason about.",
            })

        # 5. Script referenced but missing
        script = j.get("script")
        if script:
            sp = root / "scripts" / script
            if not sp.exists():
                findings.append({
                    "id": f"job-missing-script-{j.get('id')}",
                    "category": "broken",
                    "severity": "critical",
                    "title": f"Job '{j.get('name')}' references script {script} which does not exist",
                    "detail": f"Expected at {sp}",
                    "remedy": "Restore the script or remove the job.",
                })

    # 6. Skills with no description (routing failure)
    for s in skills:
        if not s["description"]:
            findings.append({
                "id": f"skill-nodesc-{s['name']}",
                "category": "dormant",
                "severity": "medium",
                "title": f"Skill '{s['name']}' has no description",
                "detail": "Skills without descriptions cannot be routed to reliably.",
                "remedy": "Add a description line to the frontmatter.",
            })

    # 7. Publicly bound listeners with no local consumer
    for l in listeners():
        if l["addr"] == "0.0.0.0" and not is_system_service(l["raw"], l["port"]):
            findings.append({
                "id": f"listener-open-{l['port']}",
                "category": "exposure",
                "severity": "high",
                "title": f"Port {l['port']} is bound to 0.0.0.0",
                "detail": l["raw"],
                "remedy": "Bind to 127.0.0.1 unless the service is intentionally network-facing.",
            })

    # 8. Behavior freshness: a recurring job whose output is older than its cadence
    for j in jobs:
        oid = j.get("id")
        meta = outputs.get(oid)
        if not meta:
            continue
        expr = (j.get("schedule") or {}).get("expr", "")
        if not expr:
            continue
        cadence = expected_cadence_days(expr)
        gap = (now_utc() - meta["last_run"]).days
        if cadence and gap > cadence * 2:
            findings.append({
                "id": f"behavior-stale-{oid}",
                "category": "stale",
                "severity": "high",
                "title": f"Job '{j.get('name')}' produces no output despite a {cadence}-day cadence",
                "detail": f"Last output written {gap} days ago. Status says '{j.get('last_status')}'.",
                "remedy": "The job fires but produces nothing. Inspect the last output file.",
            })

    # 9. Context weight: jobs carrying more prompt than they need
    weights = {}
    for j in jobs:
        chars, parts = context_weight(root, j)
        weights[j.get("id")] = {"chars": chars, "parts": parts}
        completed = (j.get("repeat") or {}).get("completed", 0)
        if chars > 12000 and completed > 0:
            biggest = max(parts, key=lambda p: p[1]) if parts else ("prompt", chars)
            share = biggest[1] / chars * 100
            findings.append({
                "id": f"context-heavy-{j.get('id')}",
                "category": "cost",
                "severity": "medium",
                "title": f"Job '{j.get('name')}' carries {chars:,} chars of context every run",
                "detail": f"{biggest[0]} accounts for {share:.0f}% of it. "
                          f"Across {completed} runs that is {chars * completed:,} chars of context.",
                "remedy": "Trim the prompt or load only the skills the job actually needs.",
            })

    # 10. Failed systemd user units
    for u in failed_units():
        findings.append({
            "id": f"unit-failed-{u}",
            "category": "failure",
            "severity": "high",
            "title": f"Systemd user unit {u} is in a failed state",
            "detail": "Failed units are the silent failure surface: dashboards show green while a timer is broken.",
            "remedy": f"Run: systemctl --user status {u}",
        })

    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["title"]))

    summary = {
        "jobs": len(jobs),
        "context_chars_total": sum(w["chars"] for w in weights.values()),
        "skills": len(skills),
        "plugins": len(plugins),
        "listeners": len(listeners()),
        "findings": len(findings),
        "by_severity": {},
        "by_category": {},
    }
    for f in findings:
        summary["by_severity"][f["severity"]] = summary["by_severity"].get(f["severity"], 0) + 1
        summary["by_category"][f["category"]] = summary["by_category"].get(f["category"], 0) + 1

    return summary, findings, {"jobs": jobs, "skills": skills, "outputs": outputs}


# --------------------------------------------------------------------------
# Snapshot / drift
# --------------------------------------------------------------------------

def snapshot_path(root):
    return root / "fleetcheck" / "snapshot.json"


def take_snapshot(root, state):
    sp = snapshot_path(root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "taken_at": now_utc().isoformat(),
        "job_ids": sorted(j.get("id") for j in state["jobs"]),
        "skill_names": sorted(s["name"] for s in state["skills"]),
        "output_dirs": sorted(state["outputs"].keys()),
    }
    sp.write_text(json.dumps(payload, indent=2))
    return sp


def compute_drift(root, state):
    sp = snapshot_path(root)
    if not sp.exists():
        return None
    prev = json.loads(sp.read_text())
    cur_jobs = set(j.get("id") for j in state["jobs"])
    prev_jobs = set(prev["job_ids"])
    cur_skills = set(s["name"] for s in state["skills"])
    prev_skills = set(prev["skill_names"])
    return {
        "previous_snapshot": prev["taken_at"],
        "jobs_added": sorted(cur_jobs - prev_jobs),
        "jobs_removed": sorted(prev_jobs - cur_jobs),
        "skills_added": sorted(cur_skills - prev_skills),
        "skills_removed": sorted(prev_skills - cur_skills),
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def render_scorecard(summary, findings):
    lines = []
    lines.append(f"fleetcheck {VERSION}")
    lines.append("")
    score = 100
    weight = {"critical": 25, "high": 10, "medium": 4, "low": 1, "info": 0}
    for f in findings:
        score -= weight[f["severity"]]
    score = max(0, score)
    lines.append(f"FLEET HEALTH SCORE: {score}/100")
    lines.append("")
    lines.append(f"  jobs: {summary['jobs']}  skills: {summary['skills']}  "
                 f"plugins: {summary['plugins']}  listeners: {summary['listeners']}")
    lines.append("")
    if not findings:
        lines.append("No findings. The fleet is clean.")
        return "\n".join(lines)

    lines.append("FINDINGS")
    for i, f in enumerate(findings, 1):
        lines.append(f"  [{i}] {f['severity'].upper():8}  {f['title']}")
        lines.append(f"      {f['detail']}")
        lines.append(f"      fix: {f['remedy']}")
        lines.append("")
    return "\n".join(lines)


def render_md(summary, findings):
    out = [f"# fleetcheck audit — {now_utc().strftime('%Y-%m-%d %H:%M UTC')}", ""]
    out.append(f"**Fleet health score:** {sum(1 for _ in findings)} findings across "
               f"{summary['jobs']} jobs, {summary['skills']} skills, {summary['plugins']} plugins.")
    out.append("")
    out.append("| Severity | Count |")
    out.append("|---|---|")
    for sev in SEVERITY_ORDER:
        if summary["by_severity"].get(sev):
            out.append(f"| {sev} | {summary['by_severity'][sev]} |")
    out.append("")
    out.append("## Findings")
    for f in findings:
        out.append(f"### {f['severity'].upper()} — {f['title']}")
        out.append(f"{f['detail']}")
        out.append(f"**Fix:** {f['remedy']}")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Behavioral audit for AI agent fleets.")
    ap.add_argument("command", nargs="?", default="audit",
                    choices=["audit", "snapshot", "drift", "explain", "cost"])
    ap.add_argument("--root", help="Agent state directory (default: auto-detect ~/.hermes or ~/.openclaw)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--prices", help="Path to a JSON model price table overriding defaults")
    ap.add_argument("--fail-on", default="high", choices=["critical", "high", "medium", "never"],
                    help="Exit non-zero when a finding of this severity or above exists (default: high)")
    ap.add_argument("--finding", help="For 'explain': the finding id to get remediation for")
    args = ap.parse_args()

    root = detect_root(args.root)
    if not root:
        print("No agent state directory found. Pass --root.", file=sys.stderr)
        sys.exit(2)

    prices = DEFAULT_PRICES
    if args.prices:
        try:
            prices = json.loads(Path(args.prices).read_text())
        except Exception as e:
            print(f"Bad price table: {e}", file=sys.stderr)
            sys.exit(2)

    if args.command == "cost":
        report = analyze_cost(root, prices)
        if report is None:
            print("No usage log found. Cost attribution needs the harness's "
                  "token usage records (cron/usage_audit.jsonl).")
            sys.exit(0)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"fleetcheck cost — {report['fires']} fires across {report['jobs']} jobs")
            print(f"TOTAL: {report['total_tokens']:,} tokens, ${report['total_cost']:.2f}")
            print("")
            for t in report["top_jobs"]:
                print(f"  {t['job']:16} {t['fires']:4} fires  {t['tokens']:>12,} tokens  "
                      f"${t['cost']:>8.2f}  {'(silent: %d)' % t['silent'] if t['silent'] else ''}")
            if report["findings"]:
                print("")
                print("FINDINGS")
                for f in report["findings"]:
                    print(f"  {f['severity'].upper():8}  {f['title']}")
                    print(f"           {f['detail']}")
                    print(f"           fix: {f['remedy']}")
        sys.exit(1 if any(f["severity"] in ("critical", "high") for f in report["findings"]) else 0)

    summary, findings, state = analyze(root)

    if args.command == "snapshot":
        sp = take_snapshot(root, state)
        print(f"snapshot written to {sp}")
        return

    if args.command == "drift":
        drift = compute_drift(root, state)
        if drift is None:
            print("No previous snapshot. Run `fleetcheck snapshot` first.")
            return
        print(json.dumps(drift, indent=2))
        return

    if args.command == "explain":
        if not args.finding:
            print("Pass --finding <id>", file=sys.stderr)
            sys.exit(2)
        match = next((f for f in findings if f["id"] == args.finding), None)
        if not match:
            print(f"No finding with id {args.finding}", file=sys.stderr)
            sys.exit(1)
        print(f"{match['severity'].upper()} — {match['title']}")
        print(f"category: {match['category']}")
        print(f"detail:   {match['detail']}")
        print(f"fix:      {match['remedy']}")
        return

    if args.json:
        print(json.dumps({"summary": summary, "findings": findings}, indent=2))
    elif args.md:
        print(render_md(summary, findings))
    else:
        print(render_scorecard(summary, findings))

    if args.fail_on == "never":
        sys.exit(0)
    threshold = SEVERITY_ORDER[args.fail_on]
    triggered = [f for f in findings if SEVERITY_ORDER[f["severity"]] <= threshold]
    sys.exit(1 if triggered else 0)


if __name__ == "__main__":
    main()
