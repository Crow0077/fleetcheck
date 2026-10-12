# fleetcheck

**Behavioral audit for AI agent fleets.**

Your agent has 47 cron jobs, 416 skills, 10 plugins and 56 listening ports. Six of those jobs are quietly broken, two are orphaned, three are carrying 100KB of context they don't need, and two services are bound to `0.0.0.0` with no consumer. Dashboards show green. fleetcheck tells you the truth.

The difference: **dashboards report topology. fleetcheck reports behavior.** A job that "runs" is not a job that *works*.

## Install

```bash
pip install fleetcheck
```

Or run it directly:

```bash
python3 fleetcheck.py audit
```

## What it does

```bash
fleetcheck audit              # scorecard + ranked findings
fleetcheck audit --json       # machine-readable
fleetcheck audit --md         # markdown report
fleetcheck cost               # per-job token spend and waste
fleetcheck snapshot           # save state for drift detection
fleetcheck drift              # what changed since the last snapshot
fleetcheck explain --finding <id>
```

Exit code is non-zero when critical or high findings exist, so it works in CI.

## What it catches

| Category | Example finding |
|---|---|
| **failure** | Job failed its last run; failure streak of 2+; systemd unit in failed state |
| **dead** | Scheduled job that has never fired |
| **stale** | Daily job whose last output is 14 days old |
| **orphan** | Output directory for a job that no longer exists |
| **broken** | Job references a script that isn't on disk |
| **cost** | Job carrying 100KB of context every run, 92% of it a skill it doesn't need |
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

It flags three kinds of waste:

- **silent fires** — a job that burns tokens and delivers nothing
- **context bloat** — 1,000,000 prompt tokens for 1,400 output tokens (720:1)
- **concentration** — one job accounting for 61% of the whole bill

Prices are a default table. Override with `--prices my_prices.json`.

## Supported harnesses

- **Hermes Agent** (`~/.hermes`)
- **OpenClaw** (`~/.openclaw`)
- **nanobot** (`~/.nanobot`)

The root is auto-detected. Pass `--root` to point at any install.

## Privacy

Everything runs locally against files on your machine. No network calls, no telemetry, no account. The only external commands are `ss` and `systemctl`, used to read live system state.

## Fleet health score

Score starts at 100 and is reduced by finding severity: critical −25, high −10, medium −4, low −1.

A score of 52 means the fleet is functional but has real rot. That is normal for anyone running agents for more than a few months.

## Roadmap

- **Pro tier** ($19/mo): drift history over time, alerting on regressions, multi-host fleet reports, scheduled audits, CSV export.
- Free tier stays local and unlimited. Pro is history + alerting + fleet dashboards.

## License

MIT
