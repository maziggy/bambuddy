---
name: farm-deploy
description: Deploy a change from this repo to Airtho's production farm controller on airtho-server. Use before or when asked to deploy, push a fix live, or restart the BamBuddy service.
---

# Deploying to airtho-server

Deploying restarts a production service that's mid-print on physical, unattended
hardware during business hours. **Confirm with the user before running the restart
step** unless they've already explicitly asked for exactly this deploy.

## Pre-flight checklist

1. **Is this change committed and pushed to `origin/main`?** Deploy pulls from
   `origin` (`airthos/bambuddy`), not from a local working tree or branch.
2. **Did this change touch `frontend/`?** If yes, confirm `npm run build` was run in
   `frontend/` and the regenerated `static/assets/index-*.js` (and `static/index.html`
   if changed) are part of the commit being deployed. Deploy does **not** rebuild the
   frontend — see Fix 5 in `docs/airtho/fixes.md`. Check with
   `git diff HEAD~1 -- static/` (or against whatever the last-deployed commit was) if
   unsure whether the bundle is current.
3. **Is any printer mid-print right now?** A service restart doesn't stop an in-progress
   print, but it does drop the MQTT connection momentarily and re-establishes printer
   state from scratch — ask the user if now is a safe time, especially if the change
   touches dispatch/scheduler/MQTT code.

## Deploy

```
ssh airtho-server
echo '<sudo password>' | sudo -S git -C /opt/bambuddy pull origin main
echo '<sudo password>' | sudo -S systemctl restart bambuddy.service
```

Run `git` as **root** (via `sudo`), not as `sudo -u bambuddy` — some `.git/objects` are
root-owned from earlier deploys and a `bambuddy`-user pull can fail with "insufficient
permission for adding an object." Credentials are not in this repo; get them from the
user or the team's credential store.

This should be a clean fast-forward — the server's working tree normally only has
untracked build caches. If `git pull` reports local modifications to tracked files,
stop and investigate what changed on the server before stashing/resetting anything;
don't discard server-side changes without understanding why they're there.

## Post-deploy check

- `systemctl status bambuddy.service` (or check the app log) to confirm it came back up
  cleanly.
- If the change affects printer state/dispatch, watch `logs/bambuddy.log` for a minute
  to confirm printers reconnect and report state normally.
- If this deploy fixed something documented in `docs/airtho/known-issues.md`, move that
  entry to `docs/airtho/fixes.md` with the commit hash, in a follow-up commit.
