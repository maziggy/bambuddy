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
cp .env.example .env       # edit ports / versions if you like

# OrcaSlicer only (default profile):
docker compose up -d
curl http://localhost:3003/health

# Both slicers:
docker compose --profile bambu up -d
curl http://localhost:3001/health   # bambu-studio-api
curl http://localhost:3003/health   # orca-slicer-api
```

First build downloads the slicer's AppImage (~110 MB OrcaSlicer, ~220 MB
BambuStudio) and compiles the Node wrapper. Takes 3–8 minutes per service.
Subsequent runs reuse the local image — instant start.

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

## Where the source lives

Both images build from the
[`maziggy/orca-slicer-api`](https://github.com/maziggy/orca-slicer-api)
fork (`bambuddy/profile-resolver` branch). The Compose file uses
Docker's git build context, so you don't need to clone it manually —
Docker pulls the repo at build time.

The fork patches AFKFelix's upstream wrapper with the `inherits:`
chain resolver, `from: "User"` → `"system"` rewrite, `# ` clone-prefix
strip, and sentinel-value strip — all empirically required to slice
real GUI exports without segfaulting the CLI. Once those land
upstream, this Compose file can be flipped to pull from
`ghcr.io/afkfelix/orca-slicer-api` directly.

## Updating

Bump the versions in `.env`, then:

```bash
docker compose --profile bambu build --no-cache
docker compose --profile bambu up -d
```

`--no-cache` is needed because the Dockerfile downloads the AppImage
inline; Docker won't re-fetch it on a version change otherwise.

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
