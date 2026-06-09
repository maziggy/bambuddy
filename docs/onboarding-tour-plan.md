# Bambuddy Onboarding Tour - Detailed Plan

**Status:** Draft for review
**Owner:** Onboarding admin
**Audience:** Designer + frontend dev implementing the tour overlay
**Scope:** Welcome modal + step-by-step in-app tour for fresh installs
**Mascot:** BB (poses + expressions reference in the character sheet shipped 2026-06-07)

---

## Goals

1. Eliminate the most common cause of closed-as-`invalid` issues: add-printer setup confusion (access code / LAN mode / Developer mode / discovery).
2. Walk a brand-new user from zero to "first print sent via Bambuddy" in under 10 minutes.
3. Surface all major Bambuddy features at the right depth (overview, not full docs) so users discover what's available without reading the wiki cover-to-cover.
4. Hand off cleanly to existing self-service surfaces (Connection Diagnostic, Log Health Scanner, System page, wiki, Discord) — the tour points; the diagnostic surfaces do the work.

## Out of scope

- Per-feature settings panels (Auth providers, Postgres migration, Tailscale wiring) — covered by feature-specific docs.
- AI/ML feature deep dives (Obico detection, failure response) — covered by per-printer opt-in flows that already exist.
- The detection heuristic (per-user DB flag + backfill migration) — decided separately.

---

## Phase 0 - First contact

### Step 0.1 - Welcome modal
**Anchor:** centered modal, no DOM anchor
**Conditions to show:** `users.onboarding_status IS NULL`
**Content:**
- BB mascot, "Let's get started" pose (1)
- Headline: "Hallo! Welcome to Bambuddy."
- Body: "Bambuddy replaces the Bambu Lab cloud with a local-first dashboard. Your data, prints, spools, and timelapses stay on your hardware. Want a 5-minute tour?"
- Three buttons:
  - `Tour starten` (primary, green)
  - `Ich bin erfahren` (secondary, ghost)
  - `Später erinnern` (text link, 7-day snooze)
**Execute on action:**
- Tour → continue to 0.2
- Experienced → `PATCH /api/users/me/onboarding {status: "dismissed"}`, close
- Snooze → write `onboarding_snoozed_until = now + 7d`, close

### Step 0.2 - What Bambuddy is (and isn't)
**Anchor:** modal, BB "Let me walk you through it" pose (2)
**Content:**
- Two-column comparison:
  - **What Bambuddy does:** local cloud replacement, AMS + inventory + RFID, print queue, archives, slicer integration via Virtual Printer, multi-user, HomeAssistant, optional Tailscale.
  - **What Bambuddy isn't (today):** not a slicer (uses BambuStudio/OrcaSlicer), not a cloud service, not a printer firmware tool, not a Klipper UI.
- One-line privacy note: "No telemetry. No accounts. bambuddy.cool only serves the docs."
**Links:** wiki home, GitHub repo, Discord, sponsor portal
**Buttons:** `Weiter` / `Überspringen`

---

## Phase 1 - Critical setup (everyone needs these)

### Step 1.1 - Authentication setup ~~(implementation)~~
**REMOVED from the live tour 2026-06-08.** The /setup page already prompts every fresh install for the auth choice, and users who deliberately ran with auth off should not be nudged to enable it. Step content kept here for design reference only; the auth-card anchor stays in the codebase in case a future surface (e.g. a privacy-and-security checkup) wants to reuse it.

**Original anchor:** Settings → Users tab → Auth toggle card (`/settings?tab=users`, `[data-tour="auth-card"]`).
**Conditions to show:** `auth_enabled === false` AND user is first admin
**Content:**
- BB "Thinking" expression
- Headline: "Lock the front door first"
- Body: "Bambuddy can run with or without authentication. If anyone else on your network (or your tailnet, or your reverse proxy) can reach this URL, turn auth on now — passwords, OIDC, SAML, and MFA are all built in."
- Inline severity callout (yellow): "Bambuddy can also control your printers, manage files, and read your camera feeds. Treat the URL like an admin panel."
**Buttons:**
- `Enable auth now` → navigates to `/settings?tab=auth`, tour pauses, resumes on success
- `Later (I'm on a private network)` → continue
**Links:** wiki/security/authentication

### Step 1.2 - Add your first printer (LOAD-BEARING)
**Anchor:** Printers page (`/`), `[data-tour="add-printer-button"]`
**Conditions to show:** `printers.count === 0`
**Content:**
- BB "Almost there!" pose (3)
- Headline: "Add your first printer"
- Body: "You'll need three things: model, IP address, and access code."
- Inline checklist with info popovers:
  1. **Model** — auto-detected on discovery, or pick manually (A1, A1 Mini, P1S, P1P, X1C, X1E, H2D, H2C, P2S).
  2. **IP address** — found on the printer LCD under Settings → WLAN. Tip: assign a DHCP reservation in your router so it doesn't change.
  3. **Access code** — printer LCD, model-specific path (see access-code popover below).
- **Embedded "Where's my access code?" popover** — one model-specific image + path per model:
  - A1 / A1 Mini: Settings → WLAN → info icon
  - P1S / P1P: Settings → General → LAN-only Mode → access code displayed
  - X1C / X1E: Settings → Network → LAN-only Mode → access code
  - H2D / H2C: same as X1 family
  - P2S: Settings → Network → LAN Mode
- **Critical pre-flight warnings (red border):**
  - "Enable **LAN-only mode** on the printer (X1 / H2 / P2S family) — without this, MQTT/FTP are blocked."
  - "Enable **Developer Mode** on the printer LCD — required for MQTT control on most models."
  - "Docker bridge mode users: discovery may not find the printer. Use **Add manually by IP**."
**Buttons:**
- `Add via discovery` → opens Add Printer modal with SSDP scan running
- `Add manually by IP` → opens Add Printer modal in manual mode
**On success (printer row appears in DB):** advance to 1.3
**Links:** wiki/getting-started/add-printer, wiki/troubleshooting/discovery
**Issue evidence this step prevents:** #1641, #1453, #1411, #1487, #1524, #1405

### Step 1.3 - Verify the connection
**Anchor:** newly-added printer card, `[data-tour="printer-status-pill"]`
**Conditions to show:** at least one printer just added in this session
**Content:**
- BB "Focused" expression
- Headline: "Let's make sure Bambuddy can talk to it"
- Live status pills animate as the connection establishes:
  - MQTT connect (port 8883)
  - Camera stream (RTSPS 322, X1 / H2 / P2S only)
  - File transfer (FTP 990)
- "All green within 30 seconds" → next button enables
- If yellow / red after 30s: surface a `Run full diagnostic` button
**Execute on action:**
- `POST /api/printers/{id}/diagnostic` → opens the existing Connection Diagnostic modal (the layer-1-through-8 triage feature shipped 2026-05-21).
- Tour parses the diagnostic verdict and either shows "All green — let's continue" or "Found {N} issues — open diagnostic for details" with a deep link.
**Links:** Connection Diagnostic, Log Health Scanner, wiki/troubleshooting

### Step 1.4 - Tour the printer card
**Anchor:** printer card, sequential highlights of each region
**Content — 5 sub-highlights:**
1. **Status row** — printer state, ETA, current stage. "Your at-a-glance status."
2. **AMS row** — slots, RFID auto-detection, drying button. "Bambuddy reads your AMS slot config live — colors and types come from RFID, the rest from your inventory."
3. **Camera tile** — live feed via RTSPS proxy. "Same stream BambuStudio uses, but local — no cloud round-trip."
4. **Controls** — pause / resume / cancel, lights, fans. "Same controls as the printer LCD."
5. **Customization** — "Right-click the card to rearrange tiles or hide what you don't need" (Printer Card Customization, see wiki/features/printer-card).
**Buttons:** `Weiter` / `Überspringen Rest des Tours`

---

## Phase 2 - Core workflows (everyone benefits)

### Step 2.1 - Inventory mode pick (irreversible)
**Anchor:** Inventory page (`/inventory`)
**Conditions to show:** `inventory_mode IS NULL` (not yet chosen)
**Content:**
- BB "Need help?" pose (4)
- Headline: "Track your filament"
- Body: "Bambuddy can keep tabs on your spools. Pick a mode now — switching later loses data."
- Three large radio cards:
  - **Internal (recommended)** — built-in inventory, mirrors AMS, reads RFID, auto-decrements weight as you print. Best for most users.
  - **Spoolman** — point at an existing Spoolman instance, Bambuddy syncs from it. Best if you already run Spoolman.
  - **None** — skip filament tracking entirely. You can change this later, but historical data won't backfill.
- Inline note (yellow): "#1556 footgun — switching modes later does not migrate data."
**Execute on action:** `PATCH /api/settings/inventory_mode {mode: "internal" | "spoolman" | "none"}`
**Links:** wiki/features/inventory, wiki/features/spoolman
**Issue evidence:** #1556, #1644, #1517, #1607, #1456

### Step 2.2 - Add your first spool
**Anchor:** Inventory page, `[data-tour="add-spool-button"]`
**Conditions to show:** `inventory_mode === "internal"` AND `spools.count === 0`
**Content:**
- "Add a spool the way that suits you:"
  - **RFID scan** (Bambu spools) — load it in the AMS, Bambuddy detects automatically. No manual entry needed.
  - **SpoolBuddy kiosk** — if you have a SpoolBuddy box, scan RFID write tag for non-Bambu spools.
  - **Manual entry** — brand, material, color, weight.
- One-line note: "Bambuddy ships with a color catalog covering the major brands — names autocomplete as you type."
**Buttons:** `Add manually` → opens Add Spool modal / `Use RFID` → goes to printer card highlighting AMS row / `Skip` → continue
**Links:** wiki/features/inventory, wiki/features/spoolbuddy

### Step 2.2b - Spoolman sync setup
**Anchor:** Settings → Spoolman card (`#card-spoolman`)
**Conditions to show:** `inventory_mode === "spoolman"`
**Content:**
- "Tell Bambuddy where Spoolman lives."
- Inline form: Spoolman URL + sync direction (Spoolman → Bambuddy, or bi-directional).
- "Bambuddy will pull your existing spool library and keep it in sync. RFID scans still work — they create new spools in Spoolman."
**Execute on action:** `POST /api/settings/spoolman/test` → green = continue, red = stay on step with error.

### Step 2.3 - Profile management (Bambu cloud sync)
**Anchor:** Profiles page (`/profiles`), `[data-tour="bambu-cloud-sync"]`
**Content:**
- BB "Helpful" pose
- Headline: "Sync your filament + print profiles from Bambu Lab"
- Body: "If you've created custom filament or print profiles in BambuStudio + the Bambu cloud, Bambuddy can pull them down so they're available everywhere — assigned via the web UI, sent via Virtual Printer, used by the queue."
- Inline form: Bambu Lab account email + password (or "Sign in later")
- One-line warning: "Bambuddy stores credentials encrypted at rest and only uses them against the official Bambu API. Source is open."
**Buttons:** `Sign in to Bambu` / `Skip (use built-in defaults)`
**Links:** wiki/features/profiles, wiki/security/credential-storage

### Step 2.4 - Sidebar overview (collapsed from former 2.4-2.7)

**Anchor:** sidebar, sequential 2-second focus highlights on each entry
**Content:**
- BB "Helpful" pose
- Headline: "Here's the rest of Bambuddy"
- Six one-line callouts, no full per-page sub-tour:
  1. **Print Queue** (`/queue`) — drag-and-drop jobs, auto-dispatch to idle printers, auto-drying for PETG/PA (Queue Auto-Drying, wiki/features/queue-drying).
  2. **Archives** (`/archives`) — every finished print stored with timelapse, finish photo, gcode, 3MF; re-print from any row.
  3. **Statistics** (`/stats`) — hours, filament by brand/material/color, energy cost (set electricity price once), success rate.
  4. **Maintenance** (`/maintenance`) — nozzle wear, belt tension, hotend swap, grease intervals; built-in defaults + custom tasks. Notifications via the same channel as print events (see Step 3.8).
  5. **Files** (`/files`) — 3MF/gcode/STL library, upload, tag, search, send-to-printer. External library folders for NAS mounts (see Phase 3.3).
  6. **Projects** (`/projects`) — group files into logical projects, track which plates are printed.
- Outro: "Every page has a `?` icon top-right that opens the matching wiki page in-context — full feature docs without leaving Bambuddy."
**Buttons:** `Weiter`
**Links:** wiki/features/queue, wiki/features/archives, wiki/features/statistics, wiki/features/maintenance, wiki/features/library, wiki/features/projects

**Rationale for the collapse (2026-06-08):** former steps 2.5/2.6/2.7 were "this page exists" content, not actionable setup. The inline `?` help icon (see Appendix G) carries that load without four extra mandatory modals.

---

## Phase 3 - Power features (offer, don't push)

Each Phase 3 step starts with an "Interested?" gate — if the user clicks `Skip`, they jump to the next step without seeing the detail. The mascot uses the "Curious" expression for these.

### Step 3.1 - Virtual Printer (intro only)
**Anchor:** Settings → Virtual Printer card
**Content:**
- "Want BambuStudio / OrcaSlicer to send prints to Bambuddy instead of the cloud?"
- Four-mode decision tree (one sentence each):
  - **Bridge** — drop-in cloud replacement; slicer sends, Bambuddy forwards to the real printer.
  - **Queue** — slicer sends to a virtual collector; Bambuddy queues for dispatch.
  - **Proxy** — slicer points at Bambuddy, Bambuddy passes through with full MQTT/FTP/RTSP rewrite (best for multi-slicer setups).
  - **Archive / Review** — slicer sends, Bambuddy stores but doesn't print. Audit / approval workflows.
- "VP picks a free IP on your bind interface so it looks like a real printer to the slicer."
- One-line warning: "Docker bridge mode needs port exposure — see the Docker wiki page for the FTP passive port slicing (#1646)."
**Buttons:** `Set up a Virtual Printer` → opens VP wizard / `Show me later`
**Links:** wiki/features/virtual-printer
**Issue evidence:** #1652, #1604, #1594, #1612, #1527

### Step 3.2 - Slicer API sidecar
**Anchor:** Settings → Slicer API card
**Conditions to show:** sidecar URL not configured
**Content:**
- "Slice directly inside Bambuddy from MakerWorld URLs or your library — no BambuStudio needed."
- "Requires the orca-slicer-api sidecar container (separate docker-compose, link below). Bambuddy talks to it over HTTP."
- One-line note: "Status: still maturing upstream (segfault on multi-filament 3MF being patched). Solid for single-filament / single-plate jobs today."
**Buttons:** `Configure sidecar` → opens slicer URL field / `Skip`
**Links:** github.com/maziggy/orca-slicer-api, wiki/features/slicer-api

### Step 3.3 - External library folders
**Anchor:** Settings → Library / external roots
**Content:**
- "Mount a NAS share, an external SSD, or a project drive — Bambuddy reads files in-place."
- "Set `BAMBUDDY_EXTERNAL_ROOTS` in `docker-compose.yml`, bind-mount the host path. Bambuddy auto-shows folders in the File Manager."
- One-line warning (red): "Use `:ro` (read-only) unless you specifically want users uploading back to the share."
**Buttons:** `Weiter`
**Links:** wiki/getting-started/docker, wiki/features/library-external

### Step 3.4 - MakerWorld integration
**Anchor:** MakerWorld page (`/makerworld`)
**Conditions to show:** `permissions.has("makerworld:view")`
**Content:**
- "Paste any MakerWorld URL — Bambuddy downloads the 3MF, adds it to your library."
- One-line note: "Direct search inside the UI was cut for this release — paste the URL from the MakerWorld site."
- "Imports respect your external-folder layout — pick where the file lands."
**Buttons:** `Try it now` → opens MakerWorld page / `Skip`
**Links:** wiki/features/makerworld

### Step 3.5 - Obico ML failure detection (opt-in per printer)
**Anchor:** printer card → settings → Obico section
**Content:**
- "Self-hosted ML print-failure detection — no Obico cloud account, no telemetry."
- "Bambuddy talks directly to your self-hosted Obico ML server. Opt-in per printer; off by default."
- One-line note: "Smoothing / dead-zone tuning lives on the printer's Obico panel."
**Buttons:** `Weiter`
**Links:** wiki/features/obico, github.com/TheSpaghettiDetective/obico-server

### Step 3.6 - HomeAssistant + webhooks
**Anchor:** Settings → Integrations (`#card-integrations`)
**Content:**
- "Bambuddy ships first-class HomeAssistant integration — sensors for every printer (state, temp, ETA, AMS slots), services to start / pause / cancel."
- "Webhooks fire on print events, queue events, archive events — useful for Discord bots, NodeRED, custom dashboards."
- One-line note: "Webhook signing secret in Settings → Integrations."
**Buttons:** `Weiter`
**Links:** wiki/features/homeassistant, wiki/features/webhooks

### Step 3.7 - Tailscale / remote access
**Anchor:** Settings → Tailscale card
**Conditions to show:** `/var/run/tailscale/tailscaled.sock` mounted OR `tailscale` binary detected on host
**Content:**
- "Access Bambuddy from anywhere via your tailnet — no port forwarding, no public exposure."
- "MagicDNS HTTPS via Let's Encrypt (Bambuddy requests certs via `tailscale cert`)."
- One-line note: "Read the Tailscale blog post about Bambuddy at [link] for the full setup walkthrough."
**Buttons:** `Open Tailscale settings` / `Skip`
**Links:** wiki/features/tailscale, tailscale blog post

### Step 3.8 - Notifications
**Anchor:** Notifications page (`/notifications`) and Settings → Notifications
**Content:**
- "Get told when prints finish, fail, or need attention."
- Channels: in-app, browser push, Discord, Telegram, Pushover, Gotify, ntfy, email (SMTP), webhook.
- "Per-event filters — only ping me on failures, route AMS humidity warnings to Discord, send finish photos via Telegram, etc."
**Buttons:** `Configure now` / `Skip`
**Links:** wiki/features/notifications

---

## Phase 4 - Multi-user setup (conditional)

### Step 4.1 - Invite users
**Anchor:** Settings → Users tab (`/settings?tab=users`)
**Conditions to show:** `auth_enabled === true` AND `users.count === 1`
**Content:**
- "Add accounts for the rest of your household / team."
- "Each user has their own permissions, print history, and notification settings. Print log shows who started which job (#1670 fix)."
**Buttons:** `Add user` / `Skip`
**Links:** wiki/features/multi-user

### Step 4.2 - Groups & permissions
**Anchor:** Settings → Users tab → Groups section
**Content:**
- "Group users by role. Bambuddy ships with default groups: Admin, Operator, Viewer."
- Quick permission matrix: who can add printers / send prints / view archives / change settings.
- "Build your own groups for custom roles (read-only kid account, full-access partner, etc.)."
**Buttons:** `Weiter`
**Links:** wiki/features/permissions

### Step 4.3 - SSO (OIDC / SAML) and MFA
**Anchor:** Settings → Auth tab
**Conditions to show:** more than 3 users OR admin opens this section explicitly
**Content:**
- "Bambuddy supports OIDC (Authentik, Authelia, Keycloak, Google, GitHub) and SAML 2.0 for org SSO."
- "Per-user MFA (TOTP). Encryption key auto-generates on first start (see #1219), override via env var for secret-manager workflows."
**Buttons:** `Configure OIDC` / `Configure SAML` / `Enable MFA on my account` / `Skip`
**Links:** wiki/security/authentication, wiki/security/oidc, wiki/security/saml, wiki/security/mfa

---

## Phase 5 - Outro

### Step 5.1 - Where help lives
**Anchor:** modal, BB "All set!" pose (5)
**Content:**
- Headline: "You're all set! Here's where to go when something's off."
- Quick reference card (icon + one line each):
  - **System page** (`/system`) — version, logs, debug bundle, support export.
  - **Connection Diagnostic** — printer won't connect / camera black / FTP fails — open from the printer card menu.
  - **Log Health Scanner** — recurring runtime issues with known-fix suggestions (shipped 2026-05-22).
  - **Wiki** — wiki.bambuddy.cool, full feature docs.
  - **Discord** — community help, faster than GitHub for usage questions.
  - **GitHub Issues** — actual bugs and feature requests.
**Buttons:** `Done`

### Step 5.2 - Dismiss + sidebar re-entry
**Anchor:** sidebar bottom, `[data-tour="help-icon"]`
**Content:**
- "Need to see this tour again? It lives here at the bottom of the sidebar (BB icon)."
- Single highlight on the BB icon for 3 seconds, then close.
**Execute on close:** `PATCH /api/users/me/onboarding {status: "completed_tour"}`

---

## Appendix A - Anchor selector strategy

All anchors use stable `data-tour="<step-id>"` attributes added to the underlying components, NOT text matching against translated strings.

Required selectors (full list — track in code review):
- `[data-tour="add-printer-button"]` — PrintersPage
- `[data-tour="printer-status-pill"]` — PrinterCard
- `[data-tour="auth-card"]` — SettingsPage auth tab
- `[data-tour="add-spool-button"]` — InventoryPage
- `[data-tour="bambu-cloud-sync"]` — ProfilesPage
- `[data-tour="sidebar-queue"]`, `[data-tour="sidebar-archives"]`, `[data-tour="sidebar-stats"]`, `[data-tour="sidebar-maintenance"]`, `[data-tour="sidebar-files"]`, `[data-tour="sidebar-projects"]` — Sidebar entries highlighted sequentially in Step 2.4. Wired via `data-tour={\`sidebar-${id}\`}` on the shared `NavLink`, so the attribute lands on every navItem; the tour script only targets these six.
- `[data-tour="help-icon"]` — Sidebar bottom, on the `TourLauncher` BB button. Clicking it sets status to `tour_in_progress:<first step>` so the engine relaunches from step 0.

**Anchor backstop test:** `frontend/src/__tests__/onboarding-anchors.test.ts` greps each anchor's source file and asserts presence. Source-level rather than DOM-render because most anchor hosts are gated by route + permission + sub-tab state that the engine's own tests will cover. Failures call out which anchor / which file so refactors can self-correct.

A vitest test walks the tour against the rendered DOM and asserts every anchor resolves. PRs that change a component carrying a tour anchor have to either keep the anchor or update the tour script.

---

## Appendix B - Tour state model

```typescript
type OnboardingStatus =
  | null                          // never seen the modal
  | "dismissed"                   // skipped at welcome modal
  | "snoozed"                     // 7-day defer (see onboarding_snoozed_until)
  | "completed_tour"              // finished phase 5
  | "tour_in_progress:<step_id>"  // resume from here on next session
  | "dismissed_at_migration";     // backfilled for existing installs

// PATCH /api/users/me/onboarding
//   body: { status: OnboardingStatus, snoozed_until?: ISO8601 }
```

- Mid-tour close = same as dismissed (`completed_tour`). No partial resume in v1.
- Sidebar re-entry ignores the flag and restarts from Step 0.2 (skipping the welcome modal — they've already seen it).
- Step 1.2 (Add Printer) and Step 2.1 (Inventory mode) skip themselves if the underlying state is already set (printer exists / inventory mode chosen) — useful when re-entering the tour after partial setup.

---

## Appendix C - i18n

All step text, button labels, and tooltip strings live in `frontend/src/i18n/locales/*.ts` under a new `onboarding.*` namespace.

Locales required at ship: de, en, es, fr, it, ja, ko, pt-BR, tr, zh-CN, zh-TW.

CI gate: `check-i18n-parity.mjs` Check 4 fails on any English leak. NO `IDENTICAL_TO_EN_ALLOWED` entries for onboarding strings — the tour body is exactly the surface where users notice missing translations.

---

## Appendix D - Backend additions needed

1. `users.onboarding_status` column (TEXT NULL) + Alembic migration that backfills `'dismissed_at_migration'` for all existing rows.
2. `users.onboarding_snoozed_until` column (TIMESTAMP NULL).
3. `PATCH /api/users/me/onboarding` route — body: `{status, snoozed_until?}`.
4. `GET /api/users/me/onboarding` route — returns current status (frontend polls on app boot).
5. Branch on `is_sqlite()` for the column types — `TEXT` and `TIMESTAMP` differ Postgres vs SQLite (`feedback_postgres_migration_types`, `feedback_sqlite_and_postgres_upfront`).

---

## Appendix E - Mascot asset inventory

Poses needed (from the character sheet):
1. Let's get started — Step 0.1, 5.1
2. Let me walk you through it — Step 0.2
3. Almost there — Step 1.2
4. Need help? — Step 2.1
5. All set — Step 5.1

Expressions needed:
- Happy — Phase 0, Phase 5
- Thinking — Step 1.1
- Focused — Step 1.3
- Excited — Step 2.2 ("first spool added!" celebration)
- Helpful — Steps 2.3, 2.4, 3.5
- Curious — Phase 3 gates

**No new warning expression (decided 2026-06-08).** Red callouts in Step 1.2 (LAN-only / Dev mode / Docker bridge) use the system warning triangle inside the callout chrome. BB stays in "Almost there" pose for the surrounding step — friendly mascot + warning glyph reads "important but not scary."

Branding elements: BB logo, leaf, filament spool, guidance arrow, setup checklist, foundation block — all already on the sheet.

---

## Appendix F - Issue evidence trail

GitHub `invalid`-tagged issues this tour explicitly addresses:

| Cluster | Tour step | Closed issues |
|---------|-----------|---------------|
| Access code / LAN mode / Dev mode | 1.2 | #1641, #1453, #1411, #1487, #1524, #1405 |
| Connection failure not triaged | 1.3 → Diagnostic | #1527, #1612, #1604 |
| VP mode confusion | 3.1 | #1652, #1604, #1594, #1612, #1527 |
| Inventory mode switch destructive | 2.1 | #1556 |
| Inventory not reflecting reality | 2.1 + 2.2 | #1644, #1517, #1607, #1456 |
| Slicer-side mistaken as Bambuddy | 5.1 → "is this Bambuddy?" | #1597, #1525, #1582, #1579, #1578 |
| Docker volume / data-loss | 1.2 inline warning | #1524, #1517, #1409 |

---

## Appendix G - Frontend `<WikiHelpIcon>` component

New shared component, rendered top-right of every sidebar page that previously had a dedicated tour step. Carries the discovery load that former Steps 2.5/2.6/2.7 used to carry inside the tour.

- Props: `path` (wiki page slug, e.g. `features/queue`).
- Glyph: small `?` icon, consistent across all pages.
- Click behaviour: opens the wiki page in an in-app modal iframe against `https://wiki.bambuddy.cool`; falls back to `target="_blank"` if iframe is blocked.
- Pages requiring it at ship: Queue, Archives, Statistics, Maintenance, Files, Projects, Inventory. Add to additional pages as they grow.

Backed by zero new backend routes — the wiki is already publicly hosted.

---

## Implementation status (2026-06-08)

**Shipped end-to-end:**
- Backend: `users.onboarding_status` + `users.onboarding_snoozed_until` columns, inline migration with `dismissed_at_migration` backfill, `GET` + `PATCH /api/v1/users/me/onboarding`, OnboardingResponse/OnboardingUpdate schemas with snooze-coherence validation. SQLite + Postgres both verified.
- Frontend anchors: 26 `data-tour="..."` attributes — every step in the engine resolves. Backed by `src/__tests__/onboarding-anchors.test.ts`.
- i18n: full `onboarding.*` namespace shipped across all 11 locales (en + de/es/fr/it/ja/ko/pt-BR/tr/zh-CN/zh-TW), 138 keys.
- `<WikiHelpIcon>`: shipped (Appendix G), integrated on Queue / Archives / Stats / Maintenance / Files / Projects / Inventory.
- `OnboardingProvider` context: GET on auth-settle, localStorage fallback when auth is off, `loadFailed` gate so backend outages do not pop the welcome modal.
- Phase 0 welcome + about modals.
- Tour engine: **25-step path** covering every plan phase that has a real UI to anchor. Per-step route navigation, anchor polling with 3s timeout, dimmed spotlight cutout, back / next / skip / Escape, modal positioning that flips around sidebar vs page anchors, pre-render skip gate so no flash of skipped content.
- Step order: add-printer → verify-connection → (card sub-tour ×5) → add-spool → bambu-cloud → (sidebar overview ×6) → vp → slicer-api → makerworld → obico → integrations → notifications → users → groups → sso → outro.
- Conditional skipping: `printerCount` (skips add-printer when one exists; skips verify-connection + card sub-tour when none); `hasPermission` (skips makerworld step when the user lacks `makerworld:view`); `authEnabled` (gates users / groups / sso). The onboarding overlay also suppresses itself on `/setup`, `/login`, `/spoolbuddy/*`, `/camera/*`, `/overlay/*` and while `requiresSetup === true`, so fresh installs walk through /setup uninterrupted.
- BB mascot: 5 distinct poses sliced from `screenshots/bb_bambuddy.webp` (started / walk / almost / allset / help) + hero, plus `MascotIcon` component. Per-step pose mapping in `tourSteps.ts`.
- `TourLauncher` BB icon in the sidebar footer that relaunches the tour from step 0.

**Intentionally NOT shipped:**
- Phase 3.3 external library roots — no UI card today, configuration is env-var only (`BAMBUDDY_EXTERNAL_ROOTS`). Add the step when the UI ships.
- Phase 3.7 Tailscale — no dedicated Settings card today. Add the step when one exists.
- Per-pose mascot expressions (Happy / Thinking / Focused / Excited / Helpful / Curious) — pose covers the major moments; the expressions row is overkill for this surface.
- Runtime verification against an auth-off Bambuddy — localStorage path is wired and unit-tested but not yet driven through a live browser session.

## Resolved design decisions (2026-06-08)

1. **Access-code pre-save test (Q1):** Not added. Step 1.3 verifies MQTT/FTP/RTSP immediately after save and Connection Diagnostic surfaces wrong-code errors cleanly — duplicate dry-run code path not worth the marginal time saving.
2. **Phase 3 placement (Q2):** Stays inline with per-step "Interested?" gates. Hiding power features behind a Phase 5 "show me more" link defeats Goal #3 (surface major features for discovery).
3. **SpoolBuddy step (Q3):** No dedicated step, no hardware-detection gate. SpoolBuddy stays as one of three input methods in Step 2.2 (RFID / SpoolBuddy kiosk / manual). Aligns with the [[spoolbuddy-what-it-is]] positioning — filament management, not a kiosk feature.
4. **Tour-vs-tooltip for former 2.4-2.7 (Q4):** Collapsed into the new single Step 2.4 "Sidebar overview." Each affected page gets a `<WikiHelpIcon>` (Appendix G). Cuts ~4 modals from the mandatory path.
5. **Mascot warning expression (Q5):** Not added. Red callouts in Step 1.2 use the system warning triangle; BB stays in "Almost there" pose for the surrounding step.
