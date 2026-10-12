"""Cost attribution for agent fleets.

Reads the harness's own usage log (prompt/completion tokens per fire) and
answers the questions operators actually care about:

  - which job is spending the most
  - which fires produced no output at all (pure waste)
  - which jobs are carrying enormous context for a tiny response
  - what the fleet would cost if the worst job were fixed

Prices are per-million-token list prices. They are a default table, not a
contract: override with --prices pointing at a JSON file.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PRICES = {
    "gpt-5.6-luna-900k": {"input": 1.25, "output": 10.00},
    "gpt-6-luna-900k": {"input": 1.25, "output": 10.00},
    "deepseek-v4-flash": {"input": 0.20, "output": 1.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "qwen-flash-next-strata": {"input": 0.10, "output": 0.40},
}

DEFAULT_PRICE = {"input": 1.00, "output": 5.00}


def price_for(model, prices):
    key = (model or "").lower()
    for name, p in prices.items():
        if name.lower() in key:
            return p
    return DEFAULT_PRICE


def cost_of(row, prices):
    p = price_for(row.get("model"), prices)
    return ((row.get("prompt_tokens") or 0) / 1e6 * p["input"] +
            (row.get("completion_tokens") or 0) / 1e6 * p["output"])


def load_usage(root):
    for rel in ("cron/usage_audit.jsonl", "logs/usage.jsonl", "usage.jsonl"):
        p = root / rel
        if p.exists():
            rows = []
            for line in p.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
            if rows:
                return rows, str(p)
    return [], None


def analyze_cost(root, prices):
    rows, source = load_usage(root)
    if not rows:
        return None

    by_job = defaultdict(lambda: {"fires": 0, "tokens": 0, "cost": 0.0,
                                  "silent": 0, "errors": 0, "models": set(),
                                  "prompt": 0, "completion": 0, "ms": 0})
    for r in rows:
        j = by_job[r.get("job_id") or "unknown"]
        j["fires"] += 1
        j["tokens"] += r.get("total_tokens") or 0
        j["prompt"] += r.get("prompt_tokens") or 0
        j["completion"] += r.get("completion_tokens") or 0
        j["cost"] += cost_of(r, prices)
        j["ms"] += r.get("duration_ms") or 0
        if r.get("response_silent"):
            j["silent"] += 1
        if r.get("error"):
            j["errors"] += 1
        if r.get("model"):
            j["models"].add(r["model"])

    total_cost = sum(j["cost"] for j in by_job.values())
    total_tokens = sum(j["tokens"] for j in by_job.values())

    findings = []

    # Waste: fires that consumed tokens and produced nothing
    for jid, j in by_job.items():
        if j["silent"] and j["cost"] > 0.10:
            findings.append({
                "id": f"cost-silent-{jid}",
                "category": "cost",
                "severity": "high",
                "title": f"Job {jid} burned ${j['cost']:.2f} on {j['silent']} silent fires",
                "detail": f"{j['silent']} of {j['fires']} fires produced no deliverable output.",
                "remedy": "A fire that delivers nothing is pure spend. Gate the job on a precondition.",
            })

    # Context bloat: huge prompt for tiny output
    for jid, j in by_job.items():
        if j["prompt"] > 500000 and j["completion"] and j["prompt"] / max(j["completion"], 1) > 400:
            findings.append({
                "id": f"cost-bloat-{jid}",
                "category": "cost",
                "severity": "medium",
                "title": f"Job {jid} sends {j['prompt']:,} prompt tokens for {j['completion']:,} output tokens",
                "detail": f"Ratio {j['prompt'] / max(j['completion'], 1):.0f}:1 across {j['fires']} fires.",
                "remedy": "The job is loading far more context than the task needs.",
            })

    # Top spenders
    ranked = sorted(by_job.items(), key=lambda x: -x[1]["cost"])
    if ranked and total_cost > 0:
        top_share = ranked[0][1]["cost"] / total_cost * 100
        if top_share > 30:
            findings.append({
                "id": f"cost-concentration-{ranked[0][0]}",
                "category": "cost",
                "severity": "medium",
                "title": f"Job {ranked[0][0]} accounts for {top_share:.0f}% of fleet spend",
                "detail": f"${ranked[0][0] and ranked[0][1]['cost']:.2f} of ${total_cost:.2f} total.",
                "remedy": "Optimizing this single job moves the whole bill.",
            })

    return {
        "source": source,
        "fires": len(rows),
        "jobs": len(by_job),
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 2),
        "top_jobs": [
            {"job": jid, "fires": j["fires"], "tokens": j["tokens"],
             "cost": round(j["cost"], 2), "silent": j["silent"],
             "models": sorted(j["models"])}
            for jid, j in ranked[:10]
        ],
        "findings": findings,
    }
