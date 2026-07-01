# Airtho Fork Knowledge Base

This folder is the accumulated, dated history of running this BamBuddy fork as Airtho's
print farm controller: infrastructure, custom features, every bug fix and why it was
needed, known-but-unfixed structural issues, and incident postmortems.

**Start at [`AGENTS.md`](../../AGENTS.md)** in the repo root for the agent-facing
summary and working conventions. This folder is the depth behind that summary.

## Index

| File | Covers |
|---|---|
| [`infrastructure.md`](infrastructure.md) | Server, service, deploy process, data layout |
| [`printers.md`](printers.md) | Printer inventory, P1S delta-MQTT quirk |
| [`features.md`](features.md) | Every custom feature added in this fork, with design rationale |
| [`fixes.md`](fixes.md) | Every bug fix, root cause, and commit hash |
| [`known-issues.md`](known-issues.md) | Structural gaps that are known and deliberately not (yet) fixed |
| [`print-quality-mqtt-calibration.md`](print-quality-mqtt-calibration.md) | Research on MQTT calibration flags vs. purge-line failures |
| [`ideas-not-implemented.md`](ideas-not-implemented.md) | Ideas discussed/considered but not built, and why |
| [`incidents/`](incidents/) | Dated postmortems for specific production incidents |

## How this knowledge base stays useful

See "Keeping this knowledge base honest" in [`AGENTS.md`](../../AGENTS.md). Short
version: read before you investigate, verify before you rely, write before you move on.
Every file below states when it was last meaningfully updated — treat anything older
than a few weeks as a hypothesis to confirm, not a fact to cite.
