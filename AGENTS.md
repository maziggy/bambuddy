# Agent Guide — Airtho's BamBuddy Fork

This is `airthos/bambuddy`, a fork of [maziggy/bambuddy](https://github.com/maziggy/bambuddy)
(currently tracking `v0.2.4.3` + fork-specific commits) that runs Airtho's in-house 3D print
farm. This file is the entry point for **any agent, on any computer, in any harness**
(Claude Code, Cursor, Codex, plain chat with repo access, etc.) picking up work here.

Read this file first. It links to `docs/airtho/` for depth. Don't re-derive facts that
are already written down — read them, verify anything load-bearing against current
code/state, and correct the doc if it's stale. See "Keeping this knowledge base honest"
below — that section is not optional.

## What this fork is for

Upstream BamBuddy is a general-purpose self-hosted Bambu Lab printer manager. Airtho
runs a "farm mode" on top of it: a queue of jobs dispatched unattended, around the clock,
across multiple P1S printers, with a post-processing script that adds a bed-cooldown +
part-push-off sequence so a finished plate clears itself for the next job. Almost all
fork-specific work is in service of that lights-out farm loop staying unattended and
not silently getting stuck.

Full narrative history (every custom feature and bug fix, with commit hashes and root
causes) is in [`docs/airtho/`](docs/airtho/README.md). Read that before touching
scheduler, dispatch, HMS, or farm-post-processor code — most "new" bugs in this area
are a rediscovery of one already fixed or documented there.

## Orientation, fast

- **Deployment target:** `airtho-server`, reachable over Tailscale, runs `bambuddy.service`
  (systemd) on port 8000 as an unprivileged `bambuddy` user. Repo is deployed at
  `/opt/bambuddy`, git remote = this repo.
- **Printers:** three Bambu Lab P1S units (`Airtho 3DP 2`, `3DP 3`, `3DP 4`) on the LAN,
  see [`docs/airtho/printers.md`](docs/airtho/printers.md) for IPs/serials.
- **Deploy:** `sudo git -C /opt/bambuddy pull origin main && sudo systemctl restart bambuddy.service`
  — must be run as root, not as the `bambuddy` user (permission trap, see
  [`docs/airtho/infrastructure.md`](docs/airtho/infrastructure.md)).
- **Credentials** (SSH, sudo, BamBuddy login) are **not** in this repo — it's public.
  Ask Brendan (brendan@airtho.com) or check the team's password manager. Never commit
  secrets here even in a doc; if you're an agent with access to a memory/notes system
  that already has them, keep them there, not in git.
- **Frontend is pre-built and committed** at `static/assets/index-*.js` — if you touch
  anything under `frontend/`, you must `npm run build` in `frontend/` and commit the new
  bundle, or the server will keep serving stale JS after deploy (this has bitten this
  fork multiple times, see Fix 5 in [`docs/airtho/fixes.md`](docs/airtho/fixes.md)).
- **Tests:** `./test_backend.sh` / `./test_frontend.sh` / `./test_all.sh` at repo root.

## Working conventions specific to this fork

1. **Match BamBuddy's existing patterns. Do not build parallel machinery.** The one
   documented instance of this going wrong (SD-card HMS auto-clear, first draft) added a
   whole new state machine, cooldown dict, settings toggle, and frontend control for
   something that upstream already had a pattern for (`_HMS_FAILURE_REASONS` /
   `_HMS_NOTIFICATION_SUPPRESS`-style module-level sets). It was rejected and reverted.
   The accepted version was a 28-line additive diff reusing the existing pattern. Before
   adding a new subsystem, grep for an existing one that does something similar and
   extend it instead.
2. **This is unattended farm hardware.** Anything that can leave a queue item stuck
   forever, dispatch onto an unclean bed, or silently stop notifying, is a P0-shaped bug
   even if it looks cosmetic. Read
   [`docs/airtho/known-issues.md`](docs/airtho/known-issues.md) before assuming a gap is
   unnoticed — it may be known and deliberately deferred, with a documented reason.
3. **P1S sends delta MQTT, not full state.** Stale in-memory fields are a recurring bug
   class here (two of the seven documented fixes are this). If you're touching printer
   state handling, check what happens to a field when the printer *stops* reporting it,
   not just when it reports a new value.
4. **Deploy is manual, not CI/CD.** Nothing pushes to `main` and reaches the farm
   automatically. If you make a change intended for production, say so explicitly and
   confirm before deploying — the printers are physical machines mid-print during
   business hours.
5. **Don't touch the printers' physical state or send print commands as part of
   "just checking."** Read-only diagnosis (SSH into airtho-server, read logs/DB, MQTT
   traffic already captured) is fine and encouraged. Sending MQTT commands, clearing HMS
   errors, or dispatching prints is an action with physical consequences — treat it like
   any other risky/hard-to-reverse action and confirm with Brendan first unless he's
   already asked for exactly that.

## Keeping this knowledge base honest

This project has burned real time and tokens on agents re-discovering the same root
causes across sessions. `docs/airtho/` exists to stop that. It only works if every agent
treats it as a living document:

- **Before investigating anything** (a bug, a "why does X behave like Y" question, a
  feature request) — grep `docs/airtho/` for related terms first. A structural issue,
  fix, or rejected design may already be documented with the answer.
- **Docs are point-in-time, not gospel.** They say when they were last touched. If you
  rely on a specific fact (a file path, a line number, a config value, "printer 2's IP
  is X") for something consequential, verify it against current code/state before
  acting — then fix the doc if it was wrong, instead of silently working around the
  staleness.
- **After you fix a bug, ship a feature, or root-cause an incident**, update the
  relevant file in `docs/airtho/` in the *same commit* — not as a follow-up someone else
  does later. Add: what the symptom was, what the actual root cause was, what you
  changed, and what you deliberately decided *not* to do (and why, if it's non-obvious).
  Follow the existing style in that folder: dated, references commit hashes, states the
  "why" as well as the "what."
- **If you seriously consider an approach and reject it**, write down why in the
  relevant doc, briefly — see the SD-card auto-clear entry in
  [`docs/airtho/features.md`](docs/airtho/features.md) for the format. This is the
  single highest-leverage thing you can write, because it's the thing a future agent is
  most likely to re-propose from scratch.
- **New structural gap found but not fixed?** Add it to
  [`docs/airtho/known-issues.md`](docs/airtho/known-issues.md) with root cause and a
  resolution sketch, even if you don't fix it now. An undocumented known-but-deferred
  issue looks identical to an undiscovered one, and gets "rediscovered" at full cost.
- **Ideas floated but not built** go in
  [`docs/airtho/ideas-not-implemented.md`](docs/airtho/ideas-not-implemented.md) so they
  don't get re-proposed as if new, and so whoever eventually builds one has the context
  from when it was first discussed.

If you notice this file or `docs/airtho/` is wrong, stale, or missing something you had
to re-derive at nontrivial cost — fix it. That fix is part of the task, not a nice-to-have.

## Where things live

| Area | Path |
|---|---|
| Farm dispatch / queue logic | `backend/app/services/print_scheduler.py` |
| MQTT / printer state handling | `backend/app/services/bambu_mqtt.py` |
| HMS error handling, notifications | `backend/app/main.py` (search `_HMS_`) |
| Farm post-processor (bed cooldown, push-off) | `scripts/farm_process.py` |
| Print/queue frontend | `frontend/src/components/modals/PrintModal.tsx`, `frontend/src/pages/PrintersPage.tsx` |
| Fork-specific doc knowledge base | `docs/airtho/` |
| Upstream project docs (auth, spoolman, etc.) | `docs/` (everything outside `docs/airtho/`) |

## Claude Code skills (bonus, Claude-Code-specific)

`.claude/skills/farm-diagnose` and `.claude/skills/farm-deploy` formalize the
investigation and deploy checklists above as Claude Code skills, auto-invoked from the
matching intent. They're a convenience layer on top of `docs/airtho/`, not a separate
source of truth — every fact in them also exists as prose in `docs/airtho/`, so agents
on other harnesses aren't missing anything by not having them.

## Repo-level facts (not Airtho-specific)

For upstream architecture, contribution rules, and general BamBuddy docs, see
[`README.md`](README.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), and the rest of
[`docs/`](docs/). This file and `docs/airtho/` only cover what's specific to running
this fork for Airtho.
