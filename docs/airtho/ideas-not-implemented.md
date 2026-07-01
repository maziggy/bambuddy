# Ideas Discussed But Not Built

_Last updated: 2026-07-01. Purpose: stop these from being "discovered" as new proposals
at full research cost. If you build one of these, move it to
[`features.md`](features.md) and delete the entry here._

## Powrr Flow / webhook integration on printer events

Idea floated: hook BamBuddy printer events (printer error, HMS fault, print completion,
etc.) into a generic outbound notification/automation flow (e.g. Power Automate /
"Powrr Flow"), so external systems can react — e.g. trigger a Teams/Slack alert or a
ticket on printer error, rather than relying on someone watching the BamBuddy UI.

**Not built.** No design work done beyond the idea itself. If picked up, the natural
integration point is wherever `notification_logs` entries are currently created
(`backend/app/main.py`, HMS/printer-error handling — the same area touched by the
SD-card auto-clear feature) — add an optional outbound webhook call alongside the
existing in-app notification, gated by a settings toggle, following the "match existing
patterns" convention in [`AGENTS.md`](../../AGENTS.md). Don't build a generic
event-bus/plugin system for this unless multiple concrete integrations are actually
needed — start with the one hook that was actually asked for.

## Auto-fail stuck "printing" items on prolonged printer disconnect

Not exactly a floated feature so much as the acknowledged fix for the structural gap in
[`known-issues.md`](known-issues.md) ("printer offline during print"). Listed here too
because it's the kind of thing worth checking hasn't already been half-built in a branch
somewhere before re-designing from scratch.
