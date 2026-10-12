# fleetcheck

**Behavioral audit for AI agent fleets.**

Your agent has 47 cron jobs, 416 skills, 10 plugins and 56 listening ports. Six of those jobs are quietly broken, two are orphaned, three are carrying 100KB of context they don't need, and two services are bound to `0.0.0.0` with no consumer. Dashboards show green. fleetcheck tells you the truth.

The difference: **dashboards report topology. fleetcheck reports behavior.** A job that "runs" is not a job that *works*.

## Install

Not on PyPI yet. Install from source:

```bash
git clone https://github.com/Crow0077/fleetcheck
cd fleetcheck && pip install .
```

## Commands

```bash
fleetcheck audit              # scorecard + ranked findings
fleetcheck audit --json       # machine-readable
fleetcheck audit --md         # markdown report
fleetcheck cost               # per-job token spend and waste
fleetcheck snapshot           # record state for drift detection
fleetcheck history            # score over time
fleetcheck watch              # alert on regressions since last snapshot
fleetcheck drift              # what changed in jobs/skills
fleetcheck explain --finding <id>
```

Exit code is non-zero when findings at or above `--fail-on` (default `high`) exist, so it works in CI.

## What it catches

| Category | Example finding |
|---|---|
| **failure** | Job failed its last run; failure streak of 2+; systemd unit in failed state |
| **dead** | Scheduled job that has never fired |
| **stale** | Daily job whose last output is 14 days old |
| **orphan** | Output directory for a job that no longer exists |
| **broken** | Job references a script that isn't on disk |
| **cost** | Job burning tokens on fires that deliver nothing |
| **dormant** | Skill with no description — cannot be routed reliably |
| **exposure** | Service bound to `0.0.0.0` with no consumer |
| **clutter** | Completed one-shot jobs still sitting in the schedule |

## Cost attribution

`fleetcheck cost` reads the harness's own usage log and reports spend per job:

```
fleetcheck cost — 427 fires across 21 jobs
TOTAL: 261,036,373 tokens, $188.73

  6ee438763c23       63 fires    88,652,475 tokens  $  114.61
  919e4631bf47       77 fires    76,907,399 tokens  $   16.25  (silent: 1)
```

Three waste signals:

- **silent fires** — a job that burns tokens and delivers nothing
- **context bloat** — 1,000,000 prompt tokens for 1,400 output tokens (720:1)
- **concentration** — one job accounting for 61% of the whole bill

Prices are a default table. Override with `--prices my_prices.json`. These are derived from token counts, not from a provider invoice.

## Drift and regression alerting

```bash
fleetcheck snapshot
# ... a job breaks ...
fleetcheck watch
```

```
status: regression
score: 80 -> 45 (-35)
REGRESSIONS
  CRITICAL  Job 'always-fails' has failed 4 runs in a row
```

`fleetcheck history` shows the score over time. `watch` exits non-zero on regression, so you can schedule it.

## Supported harnesses

- **Hermes Agent** (`~/.hermes`)
- **OpenClaw** (`~/.openclaw`)
- **nanobot** (`~/.nanobot`)

The root is auto-detected. Pass `--root` to point at any install.

## Privacy

Everything runs locally against files on your machine. No network calls, no telemetry, no account. The only external commands are `ss` and `systemctl`, used to read live system state.

## Fleet health score

Starts at 100, reduced by severity: critical −25, high −10, medium −4, low −1.

A score of 40 means the fleet is functional but has real rot. That is normal for anyone running agents for more than a few months.

## Tests

```bash
python3 tests/test_fleetcheck.py   # 9 synthetic defects, 0 false positives
python3 tests/test_drift.py        # regression detection loop
```

## Status

- Public repo, CI green (3 test suites, Python 3.9–3.14)
- Not yet on PyPI — `pip install fleetcheck` does not work until it is published
- No billing yet; Pro tier is planned, not available

## Pricing

- **Free**: the full CLI, local, unlimited. Stays free.
- **Pro** ($19/mo, coming soon): hosted drift history across multiple machines, alerting to Slack/Telegram/email, fleet dashboards, CSV export.

The free tier is not a trial of the core feature. The core feature is free. Pro is history, alerting and multi-host.

## License

MIT
