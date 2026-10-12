# Launch plan for fleetcheck

Repo: https://github.com/Crow0077/fleetcheck
Status: public, CI green (3 test suites, Python 3.9–3.14)

## The one-line pitch

**Your agent fleet has 47 cron jobs and 416 skills. Six are broken, two are orphaned, and one job is 61% of your API bill. Dashboards show green. fleetcheck tells you the truth.**

## Launch post (Reddit r/openclaw, Hacker News, Lobsters)

> **I built a tool that finds the broken automations your agent dashboard says are fine**
>
> After ~6 months of running an agent fleet I kept hitting the same wall: a cron job
> would "run successfully" while producing nothing, or a job would be deleted but leave
> its output directory behind, or one job would quietly eat 61% of my token spend.
>
> Nothing on the market audits this. Observability tools (Langfuse, AgentOps, Datadog)
> assume you wrote the agent in Python with a tracing SDK. If you run OpenClaw or Hermes,
> you have a config file, a cron folder, and 400 skills you've never looked at.
>
> So I wrote fleetcheck. It reads your install and reports:
>
> - jobs scheduled but never fired
> - jobs failing in a streak
> - output directories for jobs that no longer exist
> - jobs referencing scripts that aren't on disk
> - daily jobs whose last output is 14 days old
> - skills with no description (they can't be routed)
> - services bound to 0.0.0.0 with no consumer
> - per-job token spend, and fires that burned tokens and delivered nothing
>
> On my own install it scored 40/100 and found 12 real problems, including two orphaned
> job directories and a job that sent 1,005,588 prompt tokens to produce 1,397 output
> tokens.
>
> Local only. No account, no telemetry. `pip install fleetcheck`.
>
> https://github.com/Crow0077/fleetcheck

## What makes it credible

- Real numbers from a real fleet, not a demo
- Test suite that builds a synthetic broken fleet and proves 9/9 defects detected, 0 false positives
- CI running across 6 Python versions
- MIT licensed, self-hosted, no signup

## Distribution sequence

1. **GitHub** — done. Repo public, CI green, README with real output.
2. **r/openclaw** — the community where the pain is already being discussed. Post as "I built this", not as marketing.
3. **Hacker News** — the "silent failure mode of agent fleets" angle is the hook.
4. **OpenClaw skill directories** — 5,400+ listed skills; fleetcheck can be listed as a skill wrapper that runs the CLI.
5. **The agent-harness repos themselves** — Hermes, nanobot. A "audit your install" section in their docs is the highest-leverage channel.

## Revenue model

Free CLI is the funnel. It stays free and unlimited — the core audit is not gated.

Pro ($19/mo) is the layer nobody can replicate locally:
- hosted drift history across multiple machines
- alerting to Slack/Telegram/email when the score drops
- fleet dashboards for people running several agents
- CSV export

The honest ceiling for this niche is **$1–5K MRR**, not a startup. The code took a day; distribution is the actual work.

## What's not done

- No PyPI upload yet (`pip install fleetcheck` currently fails — it must be published to PyPI before the README claim is true)
- No billing infrastructure
- No landing page
- No distribution yet

## Blockers requiring your input

1. **PyPI account** — publishing requires a PyPI username/password. Until then the README's `pip install fleetcheck` is aspirational.
2. **Which accounts to post from** — Reddit/HN posting is your identity, not something I should do on your behalf without a decision.
3. **Payment provider** — Stripe or Lemon Squeezy for the Pro tier.
