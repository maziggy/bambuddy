# Infrastructure

_Last updated: 2026-07-01._

## Server

- **Host:** `airtho-server`, reachable over Tailscale. Not exposed to the public
  internet. First SSH connection in a while may require a Tailscale re-auth via a
  browser URL that appears in the SSH output.
- **OS:** Ubuntu 24.04, Linux 6.17.0, x86_64.
- **Credentials:** SSH user, sudo password, and the BamBuddy web login are **not**
  documented in this repo (it's public). Get them from Brendan (brendan@airtho.com) or
  the team's credential store. Sudo over SSH has no PTY — use
  `echo '<password>' | sudo -S <command>`.

## Service

- **Unit:** `bambuddy.service` (systemd), runs as the unprivileged `bambuddy` user,
  serves the app via uvicorn on port 8000.
- **Repo on server:** `/opt/bambuddy`, owned by `bambuddy:bambuddy`, git remote =
  `https://github.com/airthos/bambuddy.git` (this repo, not upstream).
- **Venv:** `/opt/bambuddy/venv/`.
- **Data dir:** `/opt/bambuddy/data/` — SQLite WAL database `bambuddy.db`, print
  archives under `archive/`, archived library files under `archive/library/`.
- **Farm scripts:** `/opt/bambuddy/scripts/` — `farm_process.py` is the P1S farm
  post-processor (see [`features.md`](features.md)).
- **Logs:** `/opt/bambuddy/logs/bambuddy.log*`.

## Deploy

```
sudo git -C /opt/bambuddy pull origin main
sudo systemctl restart bambuddy.service
```

**Run git as root (`sudo git ...`), not as `sudo -u bambuddy`.** Some objects under
`.git/objects` are root-owned from earlier root-run deploys; a pull run as the
`bambuddy` user fails with "insufficient permission for adding an object." Root
operates fine on the `bambuddy`-owned directory without needing
`safe.directory` configuration.

This is normally a clean fast-forward — the working tree on the server only has
untracked build artifacts (`.cache/`, `.npm/`). If tracked files were hand-edited on the
server (should be rare/never), you'll need to stash or reset before pulling; investigate
what was changed and why before discarding it.

**Frontend bundles are committed, not built on deploy.** If a change touches
`frontend/`, run `npm run build` inside `frontend/` locally and commit the regenerated
`static/assets/index-*.js` (and `static/index.html` if it changed) as part of the same
change. Deploy does not rebuild the frontend — upstream merges have repeatedly
clobbered the fork's bundle with one missing farm-specific UI (see Fix 5 in
[`fixes.md`](fixes.md)); always check `git diff static/` after merging upstream.

**farm_process.py needs its executable bit re-applied on some clone paths.** Git does
track the bit via `git update-index --chmod=+x`, but if you ever hand-copy the file
instead of pulling, `chmod +x scripts/farm_process.py` on the server.

## Remotes

- `origin` → `https://github.com/airthos/bambuddy.git` (this fork; deploy target)
- `upstream` → `https://github.com/maziggy/bambuddy.git` (source project; pull
  occasionally to merge new releases — currently tracking a fork of `v0.2.4.3`)
