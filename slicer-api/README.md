# Slicer-API sidecar (optional)

Self-contained Docker Compose stack that runs HTTP wrappers around the
OrcaSlicer and/or Bambu Studio CLI. Bambuddy's **Slice** action calls
these to slice models server-side, no desktop slicer required.

This folder is **optional**. Bambuddy works without it — Slice falls back
to opening the model in the user's local desktop slicer via URI scheme.
Enable the API path by:

1. Starting one or both services here
2. **Settings → Slicer → Use Slicer API** = on
3. Set **Slicer sidecar URL** for whichever slicer you've started

## Quick start

```bash
cd slicer-api/
cp .env.example .env       # edit ports if you like

# OrcaSlicer only (default profile):
docker compose up -d
curl http://localhost:3003/health

# Both slicers:
docker compose --profile bambu up -d
curl http://localhost:3001/health   # bambu-studio-api
curl http://localhost:3003/health   # orca-slicer-api
```

First start pulls pre-built images from GHCR (~110 MB OrcaSlicer,
~220 MB BambuStudio). No local build, no git in the BuildKit worker,
works on QNAP / Synology / Container Station out of the box.

Both images are `linux/amd64` only. OrcaSlicer's ARM64 build is on hold
pending an upstream extraction fix; BambuStudio doesn't publish ARM64
at all. For ARM64 hosts (Raspberry Pi 4/5, Apple Silicon Linux), run
the sidecar on a separate x86_64 box and point Bambuddy at it via the
**Sidecar URL** field — the sidecar doesn't need to live next to Bambuddy.

If a separate x86_64 box isn't an option, the amd64 images can be run on
an ARM64 host under emulation — experimental, roughly 3-6x slower than
native, and a stopgap until native ARM64 images ship. See
[Experimental setup for ARM64](#experimental-setup-for-arm64) below.

## Ports

| Service | Default host port | Why this port |
|---|---|---|
| `orca-slicer-api` | **3003** | Bambuddy's virtual-printer feature reserves 3000 and 3002 |
| `bambu-studio-api` | **3001** | First free port in that range |

Override via `ORCA_API_PORT` / `BAMBU_API_PORT` in `.env`.

## Bambuddy wiring

In the Bambuddy UI: **Settings → Slicer**:

- **Preferred Slicer**: pick OrcaSlicer or Bambu Studio.
- **Use Slicer API**: turn on.
- **Sidecar URL**: paste the full URL of the chosen slicer's sidecar.
  Default values match the Compose defaults:
  - OrcaSlicer: `http://localhost:3003`
  - Bambu Studio: `http://localhost:3001`

Leaving the URL field blank uses the `SLICER_API_URL` /
`BAMBU_STUDIO_API_URL` environment defaults from Bambuddy's config.

## Where the images live

Pre-built images are published to two registries on every Bambuddy
stable release:

- `ghcr.io/maziggy/orca-slicer-api:latest` / `docker.io/maziggy/orca-slicer-api:latest`
- `ghcr.io/maziggy/bambu-studio-api:latest` / `docker.io/maziggy/bambu-studio-api:latest`

Each release also publishes a versioned tag (`:bambuddy-X.Y.Z`) so you
can pin to the sidecar that shipped alongside a specific Bambuddy
release — set `SIDECAR_TAG=bambuddy-0.2.5` in `.env`.

Both images are built from the
[`maziggy/orca-slicer-api`](https://github.com/maziggy/orca-slicer-api)
fork (`bambuddy/profile-resolver` branch). The fork patches AFKFelix's
upstream wrapper with the `inherits:` chain resolver, `from: "User"`
→ `"system"` rewrite, `# ` clone-prefix strip, and sentinel-value
strip — all empirically required to slice real GUI exports without
segfaulting the CLI. Once those land upstream, the compose file can be
flipped back to `ghcr.io/afkfelix/orca-slicer-api`.

## Updating

OrcaSlicer only (the default):

```bash
docker compose pull
docker compose up -d
```

With the Bambu Studio sidecar — the profile flag belongs on **both**
commands:

```bash
docker compose --profile bambu pull
docker compose --profile bambu up -d
```

`bambu-studio-api` sits behind `profiles: [bambu]`, and a bare
`docker compose pull` skips profile-gated services silently: it reports
success, `restart: unless-stopped` keeps the old container serving, and
you stay on the old image no matter how often you repeat it. To update
one sidecar only, name it — `docker compose pull bambu-studio-api &&
docker compose up -d bambu-studio-api` — which enables its profile
implicitly.

Compose pulls the current `:latest` (or whatever `SIDECAR_TAG` you've
pinned to) and recreates the containers.

To roll back to the sidecar that shipped with a previous Bambuddy
release, set `SIDECAR_TAG=bambuddy-X.Y.Z` in `.env` and re-run the two
commands above.

## Experimental setup for ARM64

Runs the `linux/amd64` images on an ARM64 host under QEMU emulation, via
the `docker-compose.arm64.yml` override in this folder. Expect roughly
3-6x slower slicing than native, worsening with model complexity. This is
a stopgap until native ARM64 images ship, not a replacement for running
the sidecar on an x86_64 box — if you have one, use it.

**Set up QEMU binfmt on the host first**, or the containers fail with
`exec format error`. On Debian/Ubuntu that is `qemu-user-static` plus
`binfmt-support`; Docker Desktop on Apple Silicon already has it. The
per-distribution commands are in the
[wiki](https://wiki.bambuddy.cool/features/slicer-api/) — do that before
the steps below.

### Quick start for ARM64

```bash
cd slicer-api/
cp .env.example .env       # edit ports if you like

# Make every later `docker compose` command pick up the ARM64 override:
echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.arm64.yml' >> .env

# OrcaSlicer only (default profile):
docker compose up -d
curl http://localhost:3003/health

# Both slicers:
docker compose --profile bambu up -d
curl http://localhost:3001/health   # bambu-studio-api
curl http://localhost:3003/health   # orca-slicer-api
```

That `COMPOSE_FILE` line is what makes the rest of this README work
unchanged on ARM64 — **Updating** included. Without it every command has
to name both files (`docker compose -f docker-compose.yml -f
docker-compose.arm64.yml …`), and the first bare `docker compose pull` or
`up -d` drops the override: on a host with no amd64 emulation registered
for that image you get a manifest error, and once native ARM64 images
exist you would silently switch between them and back.

## Troubleshooting

- **`address already in use` on port 3000 or 3002** — Bambuddy's
  virtual-printer feature owns those. Don't change `ORCA_API_PORT` to
  3000 or 3002.
- **`/health` reports `version: "unknown"`** — cosmetic. The bundled
  binary works; the wrapper just couldn't parse the version string from
  the slicer's `--help` output (BambuStudio's format differs from
  OrcaSlicer's, which is what the wrapper was tuned for).
- **Slice returns "Failed to slice the model"** — the wrapper hides the
  CLI's stderr. Re-run inside the container to see it:

  ```bash
  docker exec orca-slicer-api /app/squashfs-root/AppRun --slice 1 \
      --load-settings "/path/to/printer.json;/path/to/preset.json" \
      --load-filaments /path/to/filament.json \
      --allow-newer-file --outputdir /tmp/out /path/to/model.3mf
  ```
