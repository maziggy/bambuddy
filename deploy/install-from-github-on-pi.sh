#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_URL="${1:-}"
APP_DIR="${2:-$HOME/bambuddy}"

if [ -z "$ARCHIVE_URL" ]; then
  echo "Usage:"
  echo "  ./deploy/install-from-github-on-pi.sh <github-archive-url> [app-dir]"
  echo ""
  echo "Example:"
  echo "  ./deploy/install-from-github-on-pi.sh https://github.com/<user>/<repo>/archive/refs/heads/main.zip ~/bambuddy"
  exit 1
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "Downloading Bambuddy source from GitHub..."
curl -L "$ARCHIVE_URL" -o "$TMP_DIR/source.zip"

echo "Extracting archive..."
python3 - "$TMP_DIR/source.zip" "$TMP_DIR/source" <<'PY'
import sys
import zipfile
from pathlib import Path

archive = Path(sys.argv[1])
target = Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as zf:
    zf.extractall(target)
PY

SOURCE_DIR="$(find "$TMP_DIR/source" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$SOURCE_DIR" ]; then
  echo "Could not find extracted source directory."
  exit 1
fi

echo "Installing source into $APP_DIR..."
python3 - "$SOURCE_DIR" "$APP_DIR" <<'PY'
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2]).expanduser()
preserve = {"data", "logs", "virtual_printer", ".env", "docker-compose.override.yml"}
destination.mkdir(parents=True, exist_ok=True)

for item in source.iterdir():
    if item.name in preserve:
        continue
    target = destination / item.name
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    if item.is_dir():
        shutil.copytree(item, target)
    else:
        shutil.copy2(item, target)
PY

# Some source archives do not contain .git metadata. The Dockerfile expects
# .git/HEAD, so provide a harmless placeholder for archive-based installs.
mkdir -p "$APP_DIR/.git"
printf 'ref: refs/heads/main\n' > "$APP_DIR/.git/HEAD"

echo "Building and starting Bambuddy..."
cd "$APP_DIR"
docker compose up -d --build

echo ""
echo "Done."
echo "Bambuddy should now be available on the same host and port as before."
