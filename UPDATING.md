# Updating Bambuddy

> **One-time note for 0.2.2.x → 0.2.3:** the in-app **Update** button does not
> reliably perform this specific migration. Do this one upgrade from the
> command line using the steps below. Once you're on 0.2.3, the in-app Update
> button works normally again for all future releases.

Pick the section that matches how Bambuddy was installed.

---

## Docker

```bash
# 1. Make sure your compose file isn't pinned to an old version.
#    The image line should read one of:
#      image: ghcr.io/maziggy/bambuddy:latest
#      image: ghcr.io/maziggy/bambuddy:0.2.3
#    If it pins an older tag (e.g. :0.2.2.2), edit it first.

# 2. Pull and restart
docker compose pull
docker compose up -d
```

**If your `docker-compose.yml` is older than 0.2.3,** also refresh it from the
repo — recent releases added `cap_add: NET_BIND_SERVICE`, extra virtual-printer
ports for bridge mode, and an optional Postgres block:

```bash
curl -fsSL https://raw.githubusercontent.com/maziggy/bambuddy/main/docker-compose.yml \
  -o docker-compose.yml.new
# Diff against yours, merge by hand, then:
docker compose up -d
```

---

## Native install (`install.sh` or manual `git clone`)

Both paths produce a git working tree at the install directory, so the update
is the same. Preferred:

```bash
sudo /opt/bambuddy/install/update.sh
```

`update.sh` stops the service, snapshots the database via the built-in backup
API, fast-forwards to `origin/main`, installs Python deps, rebuilds the
frontend, and restarts the service. It rolls back automatically if any step
fails.

### Manual equivalent

If you'd rather run the steps yourself:

```bash
cd /opt/bambuddy
sudo systemctl stop bambuddy
sudo -u bambuddy git fetch origin
sudo -u bambuddy git reset --hard origin/main
sudo -u bambuddy venv/bin/pip install -r requirements.txt
sudo systemctl start bambuddy
```

Replace `/opt/bambuddy` with your install path if different. Database schema
migrations run automatically on startup — no Alembic step is required.

---

## Installed from a GitHub ZIP or tarball download

These installs have no `.git` directory, so neither `update.sh` nor a plain
`git pull` will work. Reinstall cleanly:

```bash
# 1. Back up your stateful data
Create and download a backup via Bambuddy Settings -> Backup -> Local Backup

# 2. Remove the old install and reinstall via install.sh
sudo rm -rf /opt/bambuddy
curl -fsSL https://raw.githubusercontent.com/maziggy/bambuddy/main/install/install.sh \
  -o /tmp/install.sh && sudo bash /tmp/install.sh --path /opt/bambuddy

# 3. Restore your data
Restore your backup via Bambuddy -> Settings -> Backup -> Local Backup

# 4. Restart Bambuddy
sudo systemctl restart bambuddy
```

---

## Before you upgrade

Take a backup. Settings → Backup → **Create Backup** downloads a ZIP containing
the database and all stateful directories. Any bare-metal update via
`update.sh` does this automatically; Docker and manual upgrades do not.
