#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/bambuddy}"
SERVICE_NAME="${SERVICE_NAME:-bambuddy}"
BRANCH="${BRANCH:-}"
VENV_PIP="${VENV_PIP:-$INSTALL_DIR/venv/bin/pip}"
FRONTEND_DIR="${FRONTEND_DIR:-$INSTALL_DIR/frontend}"
BACKUP_DIR="${BACKUP_DIR:-$INSTALL_DIR/backups}"
BAMBUDDY_API_URL="${BAMBUDDY_API_URL:-http://127.0.0.1:8000/api/v1}"
BAMBUDDY_API_KEY="${BAMBUDDY_API_KEY:-}"
BACKUP_MODE="${BACKUP_MODE:-auto}" # auto|require|skip
BACKUP_KEEP_COUNT=5
FORCE="${FORCE:-0}"

SERVICE_STOPPED=0
CODE_UPDATED=0
old_commit=""

log() {
  printf '[bambuddy-update] %s\n' "$*"
}

warn() {
  printf '[bambuddy-update] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[bambuddy-update] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

cleanup_old_backups() {
  local -a backup_files
  local max_count="$1"

  [ "$max_count" -gt 0 ] || return 0

  mapfile -t backup_files < <(ls -1t "$BACKUP_DIR"/bambuddy-backup-*.zip 2>/dev/null || true)
  if [ "${#backup_files[@]}" -le "$max_count" ]; then
    return 0
  fi

  for old_file in "${backup_files[@]:$max_count}"; do
    rm -f "$old_file"
  done

  log "Pruned old backups, kept newest $max_count file(s)"
}

on_error() {
  local exit_code="$1"

  if [ "$SERVICE_STOPPED" -eq 1 ]; then
    if [ "$CODE_UPDATED" -eq 1 ] && [ -n "$old_commit" ]; then
      warn "Update failed after code change, attempting rollback to $old_commit"
      git reset --hard "$old_commit" || warn "Rollback reset failed"
    fi

    warn "Update failed, attempting to restart service: $SERVICE_NAME"
    systemctl start "$SERVICE_NAME" || true
  fi

  exit "$exit_code"
}
trap 'on_error $?' ERR

create_backup() {
  local ts backup_file
  local -a auth_args=()

  if [ "$BACKUP_MODE" = "skip" ]; then
    log "Skipping backup (BACKUP_MODE=skip)"
    return 0
  fi

  if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    if [ "$BACKUP_MODE" = "require" ]; then
      die "Service is not running; cannot call built-in backup API."
    fi
    warn "Service is not running; skipping built-in backup API call."
    return 0
  fi

  mkdir -p "$BACKUP_DIR"
  ts="$(date +%Y%m%d-%H%M%S)"
  backup_file="$BACKUP_DIR/bambuddy-backup-$ts.zip"

  [ -n "$BAMBUDDY_API_KEY" ] && auth_args=(-H "X-API-Key: $BAMBUDDY_API_KEY")

  log "Creating built-in backup via API: $backup_file"
  if curl --silent --show-error --fail --location \
    --connect-timeout 5 --max-time 900 \
    "${auth_args[@]}" \
    "$BAMBUDDY_API_URL/settings/backup" \
    --output "$backup_file"; then
    log "Backup created successfully"
    cleanup_old_backups "$BACKUP_KEEP_COUNT"
    return 0
  fi

  rm -f "$backup_file"
  if [ "$BACKUP_MODE" = "require" ]; then
    die "Built-in backup API call failed (BACKUP_MODE=require)."
  fi
  warn "Built-in backup API call failed. Continuing because BACKUP_MODE=auto."
}

[ "${EUID:-$(id -u)}" -eq 0 ] || die "Run as root (or with sudo)."

case "$BACKUP_MODE" in
  auto|require|skip) ;;
  *) die "Invalid BACKUP_MODE '$BACKUP_MODE' (expected: auto, require, skip)." ;;
esac

require_cmd git
require_cmd systemctl
require_cmd curl

[ -d "$INSTALL_DIR" ] || die "Install directory not found: $INSTALL_DIR"
cd "$INSTALL_DIR"
[ -d .git ] || die "No git repository found in: $INSTALL_DIR"

if [ -z "$BRANCH" ]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  [ "$BRANCH" = "HEAD" ] && BRANCH="main"
fi

load_state="$(systemctl show "$SERVICE_NAME" --property=LoadState --value 2>/dev/null || true)"
if [ -z "$load_state" ] || [ "$load_state" = "not-found" ]; then
  die "Service not found: ${SERVICE_NAME}.service"
fi

old_commit="$(git rev-parse --short HEAD || true)"

log "Fetching latest code from origin/$BRANCH"
git fetch --prune origin

remote_commit="$(git rev-parse --short "origin/$BRANCH" || true)"
log "Current commit: ${old_commit:-unknown}"
log "Remote commit: ${remote_commit:-unknown}"

if git diff --quiet HEAD "origin/$BRANCH"; then
  log "You are already running the latest version of Bambuddy."
  read -r -p "Do you want to run the update process anyway? [y/N]: " run_anyway
  case "${run_anyway:-}" in
    y|Y|yes|YES) ;;
    *) exit 0 ;;
  esac
else
  read -r -p "An update for Bambuddy is available. Install now? [y/N]: " install_now
  case "${install_now:-}" in
    y|Y|yes|YES) ;;
    *) exit 0 ;;
  esac
fi

if [ -n "$(git status --porcelain)" ]; then
  if [ "$FORCE" != "1" ]; then
    read -r -p "Local edits were detected in your installation. Updating now will overwrite those edits. Continue? [y/N]: " answer
    case "${answer:-}" in
      y|Y|yes|YES) ;;
      *) die "Update cancelled by user." ;;
    esac
  else
    warn "Proceeding without prompt because FORCE=1."
  fi
fi

create_backup

log "Stopping service: $SERVICE_NAME"
systemctl stop "$SERVICE_NAME"
SERVICE_STOPPED=1

log "Updating code to origin/$BRANCH"
git reset --hard "origin/$BRANCH"
CODE_UPDATED=1

if [ -x "$VENV_PIP" ] && [ -f requirements.txt ]; then
  log "Updating Python dependencies"
  "$VENV_PIP" install -r requirements.txt
else
  warn "Skipping Python dependency update (venv pip or requirements.txt missing)."
fi

if [ -f "$FRONTEND_DIR/package.json" ]; then
  if command -v npm >/dev/null 2>&1; then
    log "Building frontend"
    (
      cd "$FRONTEND_DIR"
      npm ci
      npm run build
    )
  else
    warn "Skipping frontend build (npm not installed)."
  fi
else
  warn "Skipping frontend build (frontend/package.json not found)."
fi

log "Starting service: $SERVICE_NAME"
systemctl start "$SERVICE_NAME"
SERVICE_STOPPED=0
systemctl --no-pager --lines=8 status "$SERVICE_NAME"

new_commit="$(git rev-parse --short HEAD || true)"
log "Update complete: ${old_commit:-unknown} -> ${new_commit:-unknown}"
