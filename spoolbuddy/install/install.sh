#!/usr/bin/env bash
#
# SpoolBuddy Installation Script for Raspberry Pi
#
# Supports two scenarios:
#   1) SpoolBuddy only — NFC/scale companion connecting to a remote Bambuddy instance
#   2) SpoolBuddy + Bambuddy — both running natively on this Raspberry Pi
#
# Usage:
#   Interactive:  curl -fsSL https://raw.githubusercontent.com/maziggy/bambuddy/main/spoolbuddy/install.sh -o install.sh && chmod +x install.sh && sudo ./install.sh
#   Unattended:   sudo ./install.sh --mode spoolbuddy --bambuddy-url http://192.168.1.100:8000 --api-key bb_xxx --yes
#
# Options:
#   --mode MODE          Installation mode: "spoolbuddy" (companion only) or "full" (both)
#   --repo URL           Git repository URL to install from (default: upstream repo)
#   --ref REF            Git ref to install (branch/tag/commit, default: main)
#   --bambuddy-url URL   Bambuddy server URL (required for spoolbuddy mode)
#   --api-key KEY        Bambuddy API key (required for spoolbuddy mode)
#   --path PATH          Installation directory (default: /opt/spoolbuddy or /opt/bambuddy)
#   --port PORT          Bambuddy port (full mode only, default: 8000)
#   --ssh-pubkey KEY     Bambuddy SSH public key for remote updates
#   --yes, -y            Non-interactive mode, accept defaults
#   --help, -h           Show this help message
#

set -e

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

GITHUB_REPO="https://github.com/maziggy/bambuddy.git"
SPOOLBUDDY_SERVICE_USER="spoolbuddy"
BAMBUDDY_SERVICE_USER="bambuddy"

# Packages needed for SpoolBuddy hardware (NFC reader + scale)
SYSTEM_PACKAGES="python3 python3-pip python3-venv python3-dev python3-spidev python3-libgpiod gpiod libgpiod-dev i2c-tools git"

# Python packages for SpoolBuddy daemon
SPOOLBUDDY_PIP_PACKAGES="spidev gpiod smbus2 httpx"

# ─────────────────────────────────────────────────────────────────────────────
# Variables (set by args or prompts)
# ─────────────────────────────────────────────────────────────────────────────

INSTALL_MODE=""          # "spoolbuddy" or "full"
INSTALL_PATH=""
INSTALL_REPO=""
INSTALL_REF=""
DETECTED_INSTALLER_REPO=""
DETECTED_INSTALLER_REF=""
BAMBUDDY_URL=""
API_KEY=""
BAMBUDDY_PORT="8000"
NON_INTERACTIVE="false"
REBOOT_NEEDED="false"
KIOSK_USER=""            # auto-detected from $SUDO_USER
KIOSK_URL=""             # derived from $BAMBUDDY_URL/spoolbuddy?token=$API_KEY
SSH_PUBKEY=""            # Bambuddy's SSH public key for remote updates

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

info()    { echo -e "${CYAN}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Run a long-running command with a spinner + live progress output.
# Usage: run_with_progress "description" command [args...]
run_with_progress() {
    local desc="$1"
    shift

    local log_file
    log_file=$(mktemp /tmp/spoolbuddy-install.XXXXXX)
    local start_time=$SECONDS

    # Run command in background, capture stdout+stderr
    "$@" > "$log_file" 2>&1 &
    local pid=$!

    # Spinner frames (braille pattern)
    local -a spin=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
    local i=0

    while kill -0 "$pid" 2>/dev/null; do
        local elapsed=$(( SECONDS - start_time ))
        local time_str
        if (( elapsed >= 60 )); then
            time_str="$(( elapsed / 60 ))m$(printf '%02d' $(( elapsed % 60 )))s"
        else
            time_str="${elapsed}s"
        fi

        # Last chunk of output (handles \r progress lines and regular \n lines)
        local last_line=""
        last_line=$(tail -c 4096 "$log_file" 2>/dev/null | tr '\r' '\n' | sed 's/\x1b\[[0-9;]*[mGKHJ]//g' | sed '/^[[:space:]]*$/d' | tail -1 | sed 's/^[[:space:]]*//' | cut -c1-50) || true

        printf "\r  ${spin[$((i % 10))]}  %-36s ${CYAN}%6s${NC}  %s\033[K" "$desc" "$time_str" "$last_line"
        i=$(( i + 1 ))
        sleep 0.15
    done

    local exit_code=0
    wait "$pid" || exit_code=$?

    # Clear spinner line
    printf "\r\033[K"

    # Format elapsed time for summary
    local elapsed=$(( SECONDS - start_time ))
    local time_suffix=""
    if (( elapsed >= 60 )); then
        time_suffix=" ($(( elapsed / 60 ))m $(( elapsed % 60 ))s)"
    elif (( elapsed >= 5 )); then
        time_suffix=" (${elapsed}s)"
    fi

    if [[ $exit_code -eq 0 ]]; then
        success "${desc}${time_suffix}"
        rm -f "$log_file"
    else
        echo -e "${RED}[FAIL]${NC} ${desc}${time_suffix}"
        echo ""
        echo -e "  ${YELLOW}Last 20 lines:${NC}"
        tail -20 "$log_file" 2>/dev/null | sed 's/^/    /'
        echo ""
        echo -e "  Full log: ${CYAN}$log_file${NC}"
        exit 1
    fi
}

prompt() {
    local prompt_text="$1"
    local default_value="$2"
    local var_name="$3"

    if [[ "$NON_INTERACTIVE" == "true" ]]; then
        eval "$var_name=\"$default_value\""
        return
    fi

    if [[ -n "$default_value" ]]; then
        echo -en "${BOLD}$prompt_text${NC} [${CYAN}$default_value${NC}]: "
    else
        echo -en "${BOLD}$prompt_text${NC}: "
    fi

    read -r input
    if [[ -z "$input" ]]; then
        eval "$var_name=\"$default_value\""
    else
        eval "$var_name=\"$input\""
    fi
}

prompt_yes_no() {
    local prompt_text="$1"
    local default="$2"

    if [[ "$NON_INTERACTIVE" == "true" ]]; then
        [[ "$default" == "y" ]] && return 0 || return 1
    fi

    local yn_hint="[y/n]"
    [[ "$default" == "y" ]] && yn_hint="[Y/n]"
    [[ "$default" == "n" ]] && yn_hint="[y/N]"

    while true; do
        echo -en "${BOLD}$prompt_text${NC} $yn_hint: "
        read -r yn
        [[ -z "$yn" ]] && yn="$default"
        case "$yn" in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "Please answer yes or no.";;
        esac
    done
}

show_help() {
    echo "SpoolBuddy Installation Script for Raspberry Pi"
    echo ""
    echo "Usage: sudo $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --mode MODE          \"spoolbuddy\" (companion only) or \"full\" (Bambuddy + SpoolBuddy)"
    echo "  --repo URL           Git repository URL to install from"
    echo "  --ref REF            Git ref to install (branch/tag/commit)"
    echo "  --bambuddy-url URL   Bambuddy server URL (required for spoolbuddy mode)"
    echo "  --api-key KEY        Bambuddy API key (required for spoolbuddy mode)"
    echo "  --path PATH          Installation directory (default: /opt/spoolbuddy or /opt/bambuddy)"
    echo "  --port PORT          Bambuddy port (full mode only, default: 8000)"
    echo "  --ssh-pubkey KEY     Bambuddy SSH public key for remote updates"
    echo "  --yes, -y            Non-interactive mode, accept defaults"
    echo "  --help, -h           Show this help message"
    echo ""
    echo "Examples:"
    echo "  Interactive:"
    echo "    sudo ./install.sh"
    echo ""
    echo "  SpoolBuddy companion (unattended):"
    echo "    sudo ./install.sh --mode spoolbuddy --bambuddy-url http://192.168.1.100:8000 --api-key bb_xxx -y"
    echo ""
    echo "  Full install (unattended):"
    echo "    sudo ./install.sh --mode full --port 8000 -y"
    exit 0
}

normalize_github_repo_url() {
    local url="$1"
    if [[ -z "$url" ]]; then
        echo ""
        return
    fi

    # Convert git@github.com:owner/repo(.git) to https://github.com/owner/repo.git
    if [[ "$url" =~ ^git@github.com:(.+)$ ]]; then
        url="https://github.com/${BASH_REMATCH[1]}"
    fi

    # Keep remote URL style consistent.
    url="${url%.git}"
    echo "${url}.git"
}

detect_installer_source_context() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if git -C "$script_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        DETECTED_INSTALLER_REF="$(git -C "$script_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
        local origin_url
        origin_url="$(git -C "$script_dir" remote get-url origin 2>/dev/null || true)"
        DETECTED_INSTALLER_REPO="$(normalize_github_repo_url "$origin_url")"
    fi

    # Optional environment overrides for raw-download installs.
    if [[ -n "${SPOOLBUDDY_INSTALL_REPO:-}" ]]; then
        DETECTED_INSTALLER_REPO="$(normalize_github_repo_url "$SPOOLBUDDY_INSTALL_REPO")"
    fi
    if [[ -n "${SPOOLBUDDY_INSTALL_REF:-}" ]]; then
        DETECTED_INSTALLER_REF="$SPOOLBUDDY_INSTALL_REF"
    fi

    if [[ -z "$INSTALL_REPO" ]]; then
        if [[ -n "$DETECTED_INSTALLER_REPO" ]]; then
            INSTALL_REPO="$DETECTED_INSTALLER_REPO"
        else
            INSTALL_REPO="$GITHUB_REPO"
        fi
    fi

    if [[ -z "$INSTALL_REF" ]]; then
        if [[ -n "$DETECTED_INSTALLER_REF" && "$DETECTED_INSTALLER_REF" != "HEAD" ]]; then
            INSTALL_REF="$DETECTED_INSTALLER_REF"
        else
            INSTALL_REF="main"
        fi
    fi
}

resolve_install_ref() {
    local ref="$1"
    # If ref exists on origin as a branch, track/reset it. Otherwise treat it as tag/commit.
    if git ls-remote --exit-code --heads origin "$ref" >/dev/null 2>&1; then
        git checkout -B "$ref" "origin/$ref" > /dev/null 2>&1
        git reset --hard "origin/$ref" > /dev/null 2>&1
    else
        git checkout "$ref" > /dev/null 2>&1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight Checks
# ─────────────────────────────────────────────────────────────────────────────

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root (use sudo)"
    fi
}

check_raspberry_pi() {
    if ! grep -q "Raspberry Pi\|BCM2" /proc/cpuinfo 2>/dev/null; then
        error "This script is designed for Raspberry Pi only"
    fi

    # Detect Pi model for hardware recommendations
    local model
    model=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null) || model="Unknown"
    success "Detected: $model"
}

check_raspberry_pi_os() {
    if [[ ! -f /etc/os-release ]]; then
        error "Cannot detect operating system"
    fi

    . /etc/os-release
    if [[ "$ID" != "raspbian" && "$ID" != "debian" ]]; then
        warn "Expected Raspberry Pi OS (Debian-based), found: $ID"
        if ! prompt_yes_no "Continue anyway?" "n"; then
            exit 0
        fi
    fi

    success "OS: $PRETTY_NAME"
}

detect_python() {
    local cmd=""
    if command -v python3 &>/dev/null; then
        cmd="python3"
    elif command -v python &>/dev/null; then
        local ver
        ver=$(python --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1)
        if [[ "$ver" -ge 3 ]]; then
            cmd="python"
        fi
    fi

    if [[ -z "$cmd" ]]; then
        return 1
    fi

    local version
    version=$($cmd -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    local major minor
    major=$(echo "$version" | cut -d'.' -f1)
    minor=$(echo "$version" | cut -d'.' -f2)

    if [[ "$major" -lt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -lt 10 ]]; }; then
        warn "Python $version found, but 3.10+ is required"
        return 1
    fi

    PYTHON_CMD="$cmd"
    success "Found Python $version"
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Raspberry Pi Hardware Configuration
# ─────────────────────────────────────────────────────────────────────────────

enable_spi() {
    if raspi-config nonint get_spi 2>/dev/null | grep -q "1"; then
        info "Enabling SPI..."
        raspi-config nonint do_spi 0
        REBOOT_NEEDED="true"
        success "SPI enabled"
    else
        success "SPI already enabled"
    fi
}

enable_i2c() {
    if raspi-config nonint get_i2c 2>/dev/null | grep -q "1"; then
        info "Enabling I2C..."
        raspi-config nonint do_i2c 0
        REBOOT_NEEDED="true"
        success "I2C enabled"
    else
        success "I2C already enabled"
    fi
}

configure_boot_config() {
    # Find the boot config file (Bookworm+ uses /boot/firmware/config.txt)
    local boot_config="/boot/firmware/config.txt"
    if [[ ! -f "$boot_config" ]]; then
        boot_config="/boot/config.txt"
    fi

    if [[ ! -f "$boot_config" ]]; then
        warn "Boot config not found at /boot/firmware/config.txt or /boot/config.txt"
        warn "You may need to manually add: dtparam=i2c_arm=on and dtoverlay=spi0-0cs"
        return
    fi

    info "Configuring $boot_config..."

    # Migrate legacy SpoolBuddy setting (bus 0 / i2c_vc) to bus 1 / i2c_arm.
    if grep -q "^dtparam=i2c_vc=on" "$boot_config"; then
        sed -i "s/^dtparam=i2c_vc=on$/# dtparam=i2c_vc=on (disabled by SpoolBuddy installer; use i2c_arm bus 1)/" "$boot_config"
        REBOOT_NEEDED="true"
        success "Disabled legacy dtparam=i2c_vc=on"
    fi

    if grep -q "^# SpoolBuddy: I2C bus 0 for NAU7802 scale (GPIO0/GPIO1)" "$boot_config"; then
        sed -i "s/^# SpoolBuddy: I2C bus 0 for NAU7802 scale (GPIO0\/GPIO1)$/# SpoolBuddy: I2C bus 1 for NAU7802 scale (GPIO2\/GPIO3)/" "$boot_config"
    fi

    # Ensure I2C bus 1 (GPIO2/GPIO3) is enabled for NAU7802 scale
    if ! grep -q "^dtparam=i2c_arm=on" "$boot_config"; then
        echo "" >> "$boot_config"
        echo "# SpoolBuddy: I2C bus 1 for NAU7802 scale (GPIO2/GPIO3)" >> "$boot_config"
        echo "dtparam=i2c_arm=on" >> "$boot_config"
        REBOOT_NEEDED="true"
        success "Added dtparam=i2c_arm=on"
    else
        success "dtparam=i2c_arm=on already set"
    fi

    # Disable SPI auto chip-select (manual CS on GPIO23 for PN5180)
    if ! grep -q "^dtoverlay=spi0-0cs" "$boot_config"; then
        echo "" >> "$boot_config"
        echo "# SpoolBuddy: Disable SPI auto CS (manual CS on GPIO23 for PN5180)" >> "$boot_config"
        echo "dtoverlay=spi0-0cs" >> "$boot_config"
        REBOOT_NEEDED="true"
        success "Added dtoverlay=spi0-0cs"
    else
        success "dtoverlay=spi0-0cs already set"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Package Installation
# ─────────────────────────────────────────────────────────────────────────────

install_system_packages() {
    run_with_progress "Updating package lists" apt-get update
    run_with_progress "Installing system packages" apt-get install -y $SYSTEM_PACKAGES
}

install_wifi_safeguard() {
    # Protect WiFi credentials from being wiped by apt upgrades.
    # Raspberry Pi OS Bookworm migrated from wpa_supplicant/dhcpcd to
    # NetworkManager, but certain package upgrades (raspberrypi-sys-mods,
    # raspi-config, NetworkManager itself) can delete saved connections
    # from /etc/NetworkManager/system-connections/.  This hook backs them
    # up before dpkg runs and restores them if they vanish.
    local hook_file="/etc/apt/apt.conf.d/80-preserve-wifi"

    if [[ -f "$hook_file" ]]; then
        success "WiFi safeguard already installed"
        return
    fi

    # Only install if NetworkManager is the active network manager
    if ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
        return
    fi

    # Write a helper script (avoids quote escaping issues in APT config)
    local helper="/usr/local/sbin/preserve-wifi"
    cat > "$helper" << 'HELPEREOF'
#!/bin/sh
# Called by APT hooks to preserve NetworkManager WiFi connections.
NM_DIR="/etc/NetworkManager/system-connections"
BAK_DIR="/etc/NetworkManager/system-connections.bak"
case "$1" in
  backup)
    if [ -d "$NM_DIR" ] && [ -n "$(ls -A "$NM_DIR" 2>/dev/null)" ]; then
      cp -a "$NM_DIR/" "$BAK_DIR/"
    fi
    ;;
  restore)
    if [ -d "$BAK_DIR" ] && [ -z "$(ls -A "$NM_DIR" 2>/dev/null)" ]; then
      cp -a "$BAK_DIR"/* "$NM_DIR"/
      nmcli general reload 2>/dev/null
    fi
    rm -rf "$BAK_DIR" 2>/dev/null
    ;;
esac
HELPEREOF
    chmod +x "$helper"

    cat > "$hook_file" << 'APTEOF'
// Preserve NetworkManager WiFi connections across apt upgrades.
// Installed by SpoolBuddy.
DPkg::Pre-Invoke {"/usr/local/sbin/preserve-wifi backup";};
DPkg::Post-Invoke {"/usr/local/sbin/preserve-wifi restore";};
APTEOF

    success "WiFi safeguard installed (${hook_file})"
}

upgrade_system_packages() {
    run_with_progress "Upgrading system packages" apt-get upgrade -y
}

# ─────────────────────────────────────────────────────────────────────────────
# SpoolBuddy Installation
# ─────────────────────────────────────────────────────────────────────────────

create_spoolbuddy_user() {
    if id "$SPOOLBUDDY_SERVICE_USER" &>/dev/null; then
        info "User '$SPOOLBUDDY_SERVICE_USER' already exists"
        # Ensure existing installs get a real shell for SSH access
        usermod --shell /bin/bash "$SPOOLBUDDY_SERVICE_USER" 2>/dev/null || true
    else
        info "Creating service user '$SPOOLBUDDY_SERVICE_USER'..."
        useradd --system --shell /bin/bash --home-dir "$INSTALL_PATH" "$SPOOLBUDDY_SERVICE_USER"
        success "Service user created"
    fi

    # Add to hardware access groups (gpio, spi, i2c, video for backlight)
    for group in gpio spi i2c video; do
        if getent group "$group" &>/dev/null; then
            usermod -aG "$group" "$SPOOLBUDDY_SERVICE_USER" 2>/dev/null || true
        fi
    done
    success "User added to gpio, spi, i2c, video groups"

    # Allow passwordless restart of daemon + kiosk (needed for SSH-based updates from Bambuddy)
    cat > /etc/sudoers.d/spoolbuddy << 'SUDOERS'
spoolbuddy ALL=(root) NOPASSWD: /usr/bin/systemctl restart spoolbuddy.service
spoolbuddy ALL=(root) NOPASSWD: /usr/bin/systemctl restart getty@tty1.service
spoolbuddy ALL=(root) NOPASSWD: /usr/bin/find /home -maxdepth 5 *
spoolbuddy ALL=(root) NOPASSWD: /sbin/reboot
spoolbuddy ALL=(root) NOPASSWD: /sbin/shutdown -h now
SUDOERS
    chmod 440 /etc/sudoers.d/spoolbuddy
    success "Sudoers entries created for service, kiosk restart, reboot and shutdown"
}

download_spoolbuddy() {
    if [[ -d "$INSTALL_PATH/.git" ]]; then
        info "Existing installation found, updating..."
        # Pre-emptively normalise tree ownership before git touches anything.
        # Stray root-owned files (e.g. static/assets/* left by an earlier
        # `sudo` run, or a frontend build that wrote as root) make
        # `git reset --hard` fail with EACCES on the unlink/replace step,
        # aborting the update before the post-install chown below ever
        # runs. The script already runs as root (see check_root), so this
        # is always safe.
        chown -R "$SPOOLBUDDY_SERVICE_USER:$SPOOLBUDDY_SERVICE_USER" "$INSTALL_PATH"
        git config --global --add safe.directory "$INSTALL_PATH" 2>/dev/null || true
        cd "$INSTALL_PATH"
        git remote set-url origin "$INSTALL_REPO" 2>/dev/null || true
        run_with_progress "Fetching updates" git fetch origin
        resolve_install_ref "$INSTALL_REF"
    else
        mkdir -p "$INSTALL_PATH"
        run_with_progress "Cloning repository" git clone "$INSTALL_REPO" "$INSTALL_PATH"
        cd "$INSTALL_PATH"
        resolve_install_ref "$INSTALL_REF"
    fi

    chown -R "$SPOOLBUDDY_SERVICE_USER:$SPOOLBUDDY_SERVICE_USER" "$INSTALL_PATH"
}

setup_spoolbuddy_venv() {
    cd "$INSTALL_PATH/spoolbuddy"

    run_with_progress "Creating SpoolBuddy venv" $PYTHON_CMD -m venv --system-site-packages venv
    run_with_progress "Upgrading pip" "$INSTALL_PATH/spoolbuddy/venv/bin/pip" install --upgrade pip
    run_with_progress "Installing SpoolBuddy packages" "$INSTALL_PATH/spoolbuddy/venv/bin/pip" install $SPOOLBUDDY_PIP_PACKAGES

    chown -R "$SPOOLBUDDY_SERVICE_USER:$SPOOLBUDDY_SERVICE_USER" "$INSTALL_PATH/spoolbuddy/venv"
}

create_spoolbuddy_env() {
    info "Creating SpoolBuddy configuration..."

    local env_file="$INSTALL_PATH/spoolbuddy/.env"

    cat > "$env_file" << EOF
# SpoolBuddy Configuration
# Generated by install.sh on $(date)

# Bambuddy backend URL
SPOOLBUDDY_BACKEND_URL=$BAMBUDDY_URL

# API key (create one in Bambuddy Settings -> API Keys)
SPOOLBUDDY_API_KEY=$API_KEY

# NAU7802 scale bus (RPi GPIO2/GPIO3)
SPOOLBUDDY_I2C_BUS=1
EOF

    chown "$SPOOLBUDDY_SERVICE_USER:$SPOOLBUDDY_SERVICE_USER" "$env_file"
    # Keep secrets owner-writable while allowing kiosk user (in spoolbuddy group)
    # to read backend URL/API key for dynamic launcher URL resolution.
    chgrp "$SPOOLBUDDY_SERVICE_USER" "$env_file"
    chmod 640 "$env_file"
    success "Configuration saved to $env_file"
}

ensure_kiosk_env_access() {
    local env_file="$INSTALL_PATH/spoolbuddy/.env"

    if [[ ! -f "$env_file" ]]; then
        warn "SpoolBuddy env file not found at $env_file"
        return
    fi

    # Ensure kiosk user is known even when this function is called outside setup_kiosk.
    if [[ -z "$KIOSK_USER" ]]; then
        KIOSK_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
    fi

    if id "$KIOSK_USER" &>/dev/null; then
        usermod -aG "$SPOOLBUDDY_SERVICE_USER" "$KIOSK_USER" 2>/dev/null || true
    fi

    chgrp "$SPOOLBUDDY_SERVICE_USER" "$env_file"
    chmod 640 "$env_file"

    if ! su -s /bin/sh -c "test -r '$env_file'" "$KIOSK_USER"; then
        error "Kiosk user '$KIOSK_USER' cannot read $env_file (required for dynamic kiosk URL). Check groups/permissions."
    fi

    success "Verified kiosk user '$KIOSK_USER' can read SpoolBuddy env"
}

setup_ssh_key() {
    info "Setting up SSH access for Bambuddy remote updates..."

    local ssh_dir="$INSTALL_PATH/.ssh"
    local auth_keys="$ssh_dir/authorized_keys"

    mkdir -p "$ssh_dir"
    chmod 700 "$ssh_dir"

    if [[ -n "$SSH_PUBKEY" ]]; then
        # Manual key provided via --ssh-pubkey flag
        if [[ -f "$auth_keys" ]] && grep -qF "$SSH_PUBKEY" "$auth_keys" 2>/dev/null; then
            info "SSH key already present in authorized_keys"
        else
            echo "$SSH_PUBKEY" >> "$auth_keys"
            success "SSH public key added"
        fi
    else
        # No manual key — the daemon will auto-deploy it on first registration
        info "SSH key will be deployed automatically when the daemon connects to Bambuddy"
        touch "$auth_keys"
    fi

    chmod 600 "$auth_keys"
    chown -R "$SPOOLBUDDY_SERVICE_USER:$SPOOLBUDDY_SERVICE_USER" "$ssh_dir"
}

create_spoolbuddy_service() {
    info "Creating SpoolBuddy systemd service..."

    local after_line="After=network-online.target"
    if [[ "$INSTALL_MODE" == "full" ]]; then
        after_line="After=network-online.target bambuddy.service"
    fi

    cat > /etc/systemd/system/spoolbuddy.service << EOF
[Unit]
Description=SpoolBuddy - NFC Spool Management Daemon
Documentation=https://github.com/maziggy/bambuddy
$after_line
Wants=network-online.target

[Service]
Type=simple
User=$SPOOLBUDDY_SERVICE_USER
WorkingDirectory=$INSTALL_PATH/spoolbuddy
EnvironmentFile=$INSTALL_PATH/spoolbuddy/.env
ExecStart=$INSTALL_PATH/spoolbuddy/venv/bin/python -m daemon.main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable spoolbuddy.service
    success "SpoolBuddy service created and enabled"
}

# ─────────────────────────────────────────────────────────────────────────────
# Bambuddy Installation (full mode only)
# ─────────────────────────────────────────────────────────────────────────────

create_bambuddy_user() {
    if id "$BAMBUDDY_SERVICE_USER" &>/dev/null; then
        info "User '$BAMBUDDY_SERVICE_USER' already exists"
        return
    fi

    info "Creating service user '$BAMBUDDY_SERVICE_USER'..."
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_PATH" "$BAMBUDDY_SERVICE_USER"
    success "Service user created"
}

setup_bambuddy_venv() {
    cd "$INSTALL_PATH"

    run_with_progress "Creating Bambuddy venv" $PYTHON_CMD -m venv venv
    run_with_progress "Upgrading pip" "$INSTALL_PATH/venv/bin/pip" install --upgrade pip
    run_with_progress "Installing Bambuddy dependencies" "$INSTALL_PATH/venv/bin/pip" install -r requirements.txt

    chown -R "$BAMBUDDY_SERVICE_USER:$BAMBUDDY_SERVICE_USER" "$INSTALL_PATH/venv"
}

install_nodejs() {
    if command -v node &>/dev/null; then
        local version
        version=$(node --version 2>/dev/null | sed 's/^v//')
        local major
        major=$(echo "$version" | cut -d'.' -f1)
        if [[ "$major" -ge 20 ]]; then
            success "Found Node.js v$version"
            return
        fi
    fi

    apt-get remove -y nodejs npm > /dev/null 2>&1 || true
    run_with_progress "Setting up Node.js repository" bash -c "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -"
    run_with_progress "Installing Node.js" apt-get install -y nodejs
    hash -r 2>/dev/null || true
    success "Node.js installed: $(node --version)"
}

build_frontend() {
    cd "$INSTALL_PATH/frontend"

    run_with_progress "Installing frontend dependencies" npm ci
    run_with_progress "Building frontend" npm run build
}

create_bambuddy_env() {
    info "Creating Bambuddy configuration..."

    local env_file="$INSTALL_PATH/.env"

    cat > "$env_file" << EOF
# Bambuddy Configuration
# Generated by install.sh on $(date)

DEBUG=false
LOG_LEVEL=INFO
LOG_TO_FILE=true
EOF

    chown "$BAMBUDDY_SERVICE_USER:$BAMBUDDY_SERVICE_USER" "$env_file"
    chmod 600 "$env_file"
    success "Configuration saved to $env_file"
}

create_bambuddy_directories() {
    mkdir -p "$INSTALL_PATH/data" "$INSTALL_PATH/logs"
    chown -R "$BAMBUDDY_SERVICE_USER:$BAMBUDDY_SERVICE_USER" "$INSTALL_PATH/data" "$INSTALL_PATH/logs"
    success "Data directories created"
}

create_bambuddy_service() {
    info "Creating Bambuddy systemd service..."

    cat > /etc/systemd/system/bambuddy.service << EOF
[Unit]
Description=Bambuddy - Bambu Lab Print Management
Documentation=https://github.com/maziggy/bambuddy
After=network.target

[Service]
Type=simple
User=$BAMBUDDY_SERVICE_USER
Group=$BAMBUDDY_SERVICE_USER
WorkingDirectory=$INSTALL_PATH
EnvironmentFile=$INSTALL_PATH/.env
Environment="DATA_DIR=$INSTALL_PATH/data"
Environment="LOG_DIR=$INSTALL_PATH/logs"
ExecStart=$INSTALL_PATH/venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port $BAMBUDDY_PORT
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_PATH/data $INSTALL_PATH/logs $INSTALL_PATH

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable bambuddy.service
    success "Bambuddy service created and enabled"
}

bootstrap_spoolbuddy_kiosk_key() {
    # Provision an API key for the local SpoolBuddy kiosk and write it into
    # spoolbuddy/.env. Runs against the Bambuddy DB directly (via the CLI),
    # so the bambuddy service does not need to be running yet.
    info "Provisioning SpoolBuddy kiosk API key..."

    local env_file="$INSTALL_PATH/spoolbuddy/.env"
    if [[ ! -f "$env_file" ]]; then
        warn "SpoolBuddy env file not found at $env_file — skipping kiosk key bootstrap"
        return
    fi

    # CWD must be $INSTALL_PATH so `python -m backend.app.cli` finds the backend
    # package on sys.path (matches the systemd unit's WorkingDirectory).
    local kiosk_key
    if ! kiosk_key="$(cd "$INSTALL_PATH" && sudo -u "$BAMBUDDY_SERVICE_USER" \
            env DATA_DIR="$INSTALL_PATH/data" LOG_DIR="$INSTALL_PATH/logs" \
            "$INSTALL_PATH/venv/bin/python" -m backend.app.cli kiosk-bootstrap --force)"; then
        error "Failed to bootstrap SpoolBuddy kiosk API key"
    fi

    if [[ -z "$kiosk_key" || "$kiosk_key" != bb_* ]]; then
        error "CLI returned an invalid API key (got: ${kiosk_key:0:8}...)"
    fi

    if ! grep -q '^SPOOLBUDDY_API_KEY=' "$env_file"; then
        error "Sentinel 'SPOOLBUDDY_API_KEY=' line missing in $env_file"
    fi

    # Escape for sed replacement (the key is base64url-safe, no slashes, but be defensive)
    local escaped_key
    escaped_key=$(printf '%s\n' "$kiosk_key" | sed -e 's/[\/&]/\\&/g')
    sed -i "s/^SPOOLBUDDY_API_KEY=.*/SPOOLBUDDY_API_KEY=${escaped_key}/" "$env_file"

    success "SpoolBuddy kiosk API key provisioned"
}

# ─────────────────────────────────────────────────────────────────────────────
# System Strip-Down (dedicated appliance — remove unnecessary services/packages)
# ─────────────────────────────────────────────────────────────────────────────

strip_services() {
    info "Disabling unnecessary services..."

    local services=(
        bluetooth.service
        lightdm.service
        cloud-init-local.service
        cloud-init.service
        cloud-init-network.service
        cloud-config.service
        cloud-final.service
        cloud-init-hotplugd.socket
        avahi-daemon.service
        avahi-daemon.socket
        ModemManager.service
        udisks2.service
        apparmor.service
        man-db.timer
        e2scrub_all.timer
        e2scrub_reap.service
        # Audio stack (no speakers on a spool reader)
        pipewire.service
        pipewire.socket
        pipewire-pulse.service
        pipewire-pulse.socket
        wireplumber.service
        # Printing
        cups.service
        cups.socket
        cups-browsed.service
        # Desktop services
        accounts-daemon.service
        upower.service
        polkit.service
        # Flatpak portals (not using Flatpak)
        xdg-desktop-portal.service
        xdg-desktop-portal-gtk.service
        xdg-document-portal.service
        xdg-permission-store.service
        # NFS/RPC (unnecessary + security surface)
        rpcbind.service
        rpcbind.socket
        # Bluetooth media proxy
        mpris-proxy.service
    )

    local disabled=0
    for svc in "${services[@]}"; do
        if systemctl is-enabled "$svc" &>/dev/null; then
            systemctl disable "$svc" 2>/dev/null || true
            systemctl mask "$svc" 2>/dev/null || true
            (( ++disabled ))
        fi
    done

    if (( disabled > 0 )); then
        success "Disabled $disabled unnecessary services"
    else
        success "No unnecessary services to disable"
    fi

    # Mask user-level services globally via /etc/systemd/user/ overrides.
    # The su-based approach doesn't reliably reach the user's systemd instance
    # when run from sudo, so we create global masks that apply before login.
    local user_services=(
        pipewire.service
        pipewire.socket
        pipewire-pulse.service
        pipewire-pulse.socket
        wireplumber.service
        xdg-desktop-portal.service
        xdg-desktop-portal-gtk.service
        xdg-document-portal.service
        xdg-permission-store.service
        mpris-proxy.service
    )
    mkdir -p /etc/systemd/user
    local user_masked=0
    for svc in "${user_services[@]}"; do
        if [[ ! -L "/etc/systemd/user/$svc" ]] || [[ "$(readlink /etc/systemd/user/$svc)" != "/dev/null" ]]; then
            ln -sf /dev/null "/etc/systemd/user/$svc"
            (( ++user_masked ))
        fi
    done
    if (( user_masked > 0 )); then
        success "Masked $user_masked unnecessary user services globally"
    fi
}

strip_packages() {
    info "Removing unnecessary packages..."

    local packages=(
        mkvtoolnix
        firmware-atheros
        firmware-mediatek
        cloud-init
        rpi-cloud-init-mods
        rpi-connect-lite
        avahi-daemon
        modemmanager
        udisks2
        pipewire
        pipewire-pulse
        wireplumber
        cups
        cups-browsed
        cups-common
        cups-client
        rpcbind
    )

    local to_remove=()
    for pkg in "${packages[@]}"; do
        if dpkg -l "$pkg" &>/dev/null 2>&1; then
            to_remove+=("$pkg")
        fi
    done

    if (( ${#to_remove[@]} > 0 )); then
        run_with_progress "Removing ${#to_remove[@]} packages" apt-get remove --purge -y "${to_remove[@]}"
        run_with_progress "Cleaning up dependencies" apt-get autoremove --purge -y
    else
        success "No unnecessary packages to remove"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Kiosk Setup (labwc + Chromium + Plymouth splash)
# ─────────────────────────────────────────────────────────────────────────────

setup_kiosk() {
    info "Setting up touchscreen kiosk..."

    # Detect kiosk user (the human user who ran sudo)
    KIOSK_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
    KIOSK_URL="${BAMBUDDY_URL}/spoolbuddy?token=${API_KEY}"
    local KIOSK_HOME
    KIOSK_HOME=$(eval echo "~$KIOSK_USER")

    info "Kiosk user: $KIOSK_USER (home: $KIOSK_HOME)"
    info "Kiosk URL:  $KIOSK_URL"

    # Allow kiosk user to read SpoolBuddy env so launcher can resolve backend URL
    # and API key dynamically instead of using stale install-time fallback values.
    local spoolbuddy_env="$INSTALL_PATH/spoolbuddy/.env"
    if [[ -f "$spoolbuddy_env" ]]; then
        usermod -aG "$SPOOLBUDDY_SERVICE_USER" "$KIOSK_USER" 2>/dev/null || true
        chgrp "$SPOOLBUDDY_SERVICE_USER" "$spoolbuddy_env" 2>/dev/null || true
        chmod 640 "$spoolbuddy_env" 2>/dev/null || true
    fi

    # ── Install kiosk packages ────────────────────────────────────────────
    # Temporarily block initramfs rebuilds during package install — we rebuild
    # once at the end after the Plymouth theme is configured, saving ~4 runs
    # (one per installed kernel per hook trigger).
    if [[ -x /usr/sbin/update-initramfs ]]; then
        dpkg-divert --local --rename --add /usr/sbin/update-initramfs >/dev/null 2>&1 || true
        ln -sf /bin/true /usr/sbin/update-initramfs
    fi
    run_with_progress "Installing kiosk packages" apt-get install -y labwc chromium plymouth wlr-randr swayidle wlopm jq curl
    # Restore real update-initramfs
    if dpkg-divert --list /usr/sbin/update-initramfs 2>/dev/null | grep -q local; then
        rm -f /usr/sbin/update-initramfs
        dpkg-divert --local --rename --remove /usr/sbin/update-initramfs >/dev/null 2>&1 || true
    fi

    # ── config.txt tweaks ─────────────────────────────────────────────────
    local boot_config="/boot/firmware/config.txt"
    if [[ ! -f "$boot_config" ]]; then
        boot_config="/boot/config.txt"
    fi

    if [[ -f "$boot_config" ]]; then
        info "Configuring $boot_config for kiosk..."

        # Disable audio (change existing on→off)
        sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$boot_config"

        # Disable camera auto-detect (change existing 1→0)
        sed -i 's/^camera_auto_detect=1/camera_auto_detect=0/' "$boot_config"

        # Append if missing: gpu_mem=32
        if ! grep -q "^gpu_mem=" "$boot_config"; then
            echo "" >> "$boot_config"
            echo "# Kiosk: Minimal GPU firmware memory (KMS uses CMA from system RAM)" >> "$boot_config"
            echo "gpu_mem=32" >> "$boot_config"
        fi

        # Append if missing: dtoverlay=disable-bt
        if ! grep -q "^dtoverlay=disable-bt" "$boot_config"; then
            echo "" >> "$boot_config"
            echo "# Kiosk: Disable Bluetooth hardware" >> "$boot_config"
            echo "dtoverlay=disable-bt" >> "$boot_config"
        fi

        # Append if missing: disable_splash=1
        if ! grep -q "^disable_splash=" "$boot_config"; then
            echo "" >> "$boot_config"
            echo "# Kiosk: Disable Raspberry Pi firmware splash, use custom splash.png" >> "$boot_config"
            echo "disable_splash=1" >> "$boot_config"
        fi

        success "Boot config updated"
    fi

    # ── cmdline.txt tweaks ────────────────────────────────────────────────
    local cmdline="/boot/firmware/cmdline.txt"
    if [[ ! -f "$cmdline" ]]; then
        cmdline="/boot/cmdline.txt"
    fi

    if [[ -f "$cmdline" ]]; then
        info "Configuring $cmdline for kiosk..."

        # Remove serial console (Plymouth needs tty-only console)
        sed -i 's/console=serial0,[0-9]* //' "$cmdline"

        # Disable console blanking (kernel default is 600s, can blank during boot transition)
        grep -q "consoleblank=" "$cmdline" || sed -i 's/$/ consoleblank=0/' "$cmdline"

        # Add splash quiet loglevel=3 logo.nologo if missing
        grep -q "splash" "$cmdline" || sed -i 's/$/ splash quiet loglevel=3 logo.nologo/' "$cmdline"

        # Add video mode if missing
        grep -q "video=HDMI-A-1" "$cmdline" || sed -i 's/$/ video=HDMI-A-1:1024x600@60/' "$cmdline"

        success "Kernel cmdline updated"
    fi

    # ── Plymouth splash theme ─────────────────────────────────────────────
    info "Installing Plymouth boot splash..."
    local theme_dir="/usr/share/plymouth/themes/spoolbuddy"
    mkdir -p "$theme_dir"

    # Copy bundled splash image from the install directory
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$script_dir/splash.png" ]]; then
        cp "$script_dir/splash.png" "$theme_dir/splash.png"
    elif [[ -f "$INSTALL_PATH/spoolbuddy/install/splash.png" ]]; then
        cp "$INSTALL_PATH/spoolbuddy/install/splash.png" "$theme_dir/splash.png"
    else
        warn "splash.png not found — Plymouth splash will not display an image"
    fi

    # Write .plymouth theme file
    cat > "$theme_dir/spoolbuddy.plymouth" << 'EOF'
[Plymouth Theme]
Name=SpoolBuddy
Description=SpoolBuddy boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/spoolbuddy
ScriptFile=/usr/share/plymouth/themes/spoolbuddy/spoolbuddy.script
EOF

    # Write .script theme file
    cat > "$theme_dir/spoolbuddy.script" << 'EOF'
wallpaper_image = Image("splash.png");
screen_width = Window.GetWidth();
screen_height = Window.GetHeight();
resized_wallpaper_image = wallpaper_image.Scale(screen_width, screen_height);
wallpaper_sprite = Sprite(resized_wallpaper_image);
wallpaper_sprite.SetZ(-100);
EOF

    plymouth-set-default-theme spoolbuddy
    run_with_progress "Updating initramfs" update-initramfs -u
    success "Plymouth splash installed"

    # ── Auto-login on tty1 ────────────────────────────────────────────────
    info "Configuring auto-login for $KIOSK_USER..."
    mkdir -p /etc/systemd/system/getty@tty1.service.d

    cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Unit]
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $KIOSK_USER --noclear %I \$TERM
EOF

    success "Auto-login configured"

    # ── labwc rc.xml (no decorations, no keybinds) ────────────────────────
    info "Configuring labwc window manager..."
    local labwc_dir="$KIOSK_HOME/.config/labwc"
    mkdir -p "$labwc_dir"

    cat > "$labwc_dir/rc.xml" << 'EOF'
<?xml version="1.0"?>
<labwc_config>

  <!-- Disable screen blanking — kiosk must stay on -->
  <core>
    <screenBlankTimeout>0</screenBlankTimeout>
  </core>

  <theme>
    <name></name>
    <cornerRadius>0</cornerRadius>
  </theme>

  <!-- Disable all keybindings - kiosk lockdown -->
  <keyboard>
  </keyboard>

  <!-- Disable right-click menu -->
  <mouse>
    <default />
  </mouse>

  <!-- Remove window decorations, maximize Chromium, prevent unfullscreen -->
  <windowRules>
    <windowRule identifier="*">
      <serverDecoration>no</serverDecoration>
    </windowRule>
    <windowRule identifier="chromium">
      <skipTaskbar>yes</skipTaskbar>
      <fixedPosition>yes</fixedPosition>
    </windowRule>
  </windowRules>

</labwc_config>
EOF

        # ── Override Debian/RPi Chromium defaults for kiosk performance ──────
        cat > /etc/chromium.d/spoolbuddy-kiosk << 'CHROMIUM_EOF'
# SpoolBuddy kiosk: add kiosk-specific flags on top of Pi defaults.
# Preserves Pi GPU settings (gpu-rasterization, ANGLE/GLES) for stability.
CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-smooth-scrolling"
CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-extensions"
CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-background-timer-throttling"
CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-renderer-backgrounding"
CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-crash-reporter"
CHROMIUM_EOF
        success "Chromium kiosk performance flags installed"

        # ── kiosk launcher (dynamic URL from spoolbuddy/.env) ─────────────────
        local kiosk_launcher="/usr/local/bin/spoolbuddy-kiosk-launch"
        cat > "$kiosk_launcher" << EOF
#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="$INSTALL_PATH/spoolbuddy/.env"
FALLBACK_URL="$KIOSK_URL"

backend_url=""
api_key=""

if [[ -r "\$ENV_FILE" ]]; then
    backend_url="\$(sed -n 's/^SPOOLBUDDY_BACKEND_URL=//p' "\$ENV_FILE" | tail -n1 | tr -d '\r')"
    api_key="\$(sed -n 's/^SPOOLBUDDY_API_KEY=//p' "\$ENV_FILE" | tail -n1 | tr -d '\r')"
    backend_url="\${backend_url%\"}"
    backend_url="\${backend_url#\"}"
    api_key="\${api_key%\"}"
    api_key="\${api_key#\"}"
elif [[ -f "\$ENV_FILE" ]]; then
    echo "spoolbuddy-kiosk-launch: ERROR: \$ENV_FILE exists but is not readable" >&2
    echo "spoolbuddy-kiosk-launch: Fix permissions (group-readable by kiosk user) and restart kiosk" >&2
    exit 1
fi

if [[ -n "\$backend_url" && -n "\$api_key" ]]; then
    backend_url="\${backend_url%/}"
    kiosk_url="\${backend_url}/spoolbuddy?token=\${api_key}"
else
    kiosk_url="\$FALLBACK_URL"
fi

# Wait for the Bambuddy backend to be reachable before launching Chromium.
# Without this the browser opens before uvicorn has bound to the port on a
# cold boot and the user sees an ERR_CONNECTION_REFUSED splash until they
# manually reload. Probe /health (no auth, no body) with a short timeout.
probe_url="\${backend_url:-http://localhost}/health"
for _i in \$(seq 1 60); do
    if curl -sf --max-time 2 "\$probe_url" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Ephemeral user-data-dir under /tmp + wipe on every launch.
#
# The kiosk has no per-user state worth persisting (the auth token is in
# the URL query, not a stored cookie), but the default profile at
# ~/.config/chromium was accumulating two specific kinds of state across
# reboots that broke deploys badly:
#
#   1. HTTP disk cache holding old index.html across browser restarts.
#      Chromium's heuristic-cache freshness window kept the old HTML
#      "fresh" for days, which referenced an old content-hashed bundle,
#      so newly deployed code never reached the running tab even after
#      pkill+relaunch. Reproduced in the wild during the #1133 rollout
#      — the kiosk kept showing the pre-fix picker for hours after every
#      cache-clear attempt because the persistent profile would re-seed
#      the cache from disk on next start.
#   2. A stuck Service Worker registration, which intercepted requests
#      with its own cache layer (CacheStorage), independent of the HTTP
#      cache. Even after \`rm -rf Default/Cache/*\` the SW could replay
#      stale responses from CacheStorage until explicitly unregistered.
#
# Wiping the user-data-dir on every launch is the simplest, most
# bulletproof escape hatch — every kiosk restart is now functionally
# equivalent to a private-window first-load. Future deploys propagate
# automatically: the next chromium launch picks up the latest bundle
# without any extra tooling. Trade-off is a slightly slower first paint
# (no warm cache) and zero offline support, neither of which matter for
# a single-purpose kiosk facing a backend on the same LAN.
USER_DATA_DIR="/tmp/spoolbuddy-kiosk-userdata"
rm -rf "\$USER_DATA_DIR"

exec chromium --kiosk --no-first-run --disable-infobars \
    --disable-session-crashed-bubble --disable-features=TranslateUI \
    --noerrdialogs --disable-component-update \
    --overscroll-history-navigation=0 \
    --ozone-platform=wayland \
    --disable-crash-reporter --disable-breakpad \
    --user-data-dir="\$USER_DATA_DIR" \
    "\$kiosk_url"
EOF

        chmod 755 "$kiosk_launcher"

        # Tiny self-check: ensure sed command substitutions were not expanded
        # while generating the launcher script.
        if ! grep -Fq 'backend_url="$(sed -n' "$kiosk_launcher" || ! grep -Fq 'api_key="$(sed -n' "$kiosk_launcher"; then
            error "Kiosk launcher generation failed: dynamic env parsing commands were expanded unexpectedly"
        fi

        # ── labwc autostart ───────────────────────────────────────────────────
        cat > "$labwc_dir/autostart" << EOF
# Force 1024x600 (panel doesn't advertise this natively)
wlr-randr --output HDMI-A-1 --custom-mode 1024x600@60 &

# Idle watchdog: powers off HDMI via wlopm after the configured inactivity
# timeout (SpoolBuddy Settings → Display → Screen blank timeout). Reads the
# current value from the backend on startup; UI changes take effect on the
# next reboot / kiosk restart.
$INSTALL_PATH/spoolbuddy/install/spoolbuddy-idle.sh &

# Launch Chromium via helper that resolves URL from spoolbuddy/.env
$kiosk_launcher &
EOF

    chown -R "$KIOSK_USER:$KIOSK_USER" "$labwc_dir"

    # ── .bash_profile (source .bashrc, exec labwc on tty1) ────────────────
    cat > "$KIOSK_HOME/.bash_profile" << 'EOF'
# Source .bashrc if it exists
if [ -f ~/.bashrc ]; then
  . ~/.bashrc
fi

# Auto-start kiosk on tty1
if [ "$(tty)" = "/dev/tty1" ]; then
  exec labwc
fi
EOF

    chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.bash_profile"

    REBOOT_NEEDED="true"
    success "Kiosk setup complete"
}

# ─────────────────────────────────────────────────────────────────────────────
# User Prompts
# ─────────────────────────────────────────────────────────────────────────────

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mode)
                INSTALL_MODE="$2"
                shift 2
                ;;
            --repo)
                INSTALL_REPO="$(normalize_github_repo_url "$2")"
                shift 2
                ;;
            --ref)
                INSTALL_REF="$2"
                shift 2
                ;;
            --bambuddy-url)
                BAMBUDDY_URL="$2"
                shift 2
                ;;
            --api-key)
                API_KEY="$2"
                shift 2
                ;;
            --path)
                INSTALL_PATH="$2"
                shift 2
                ;;
            --port)
                BAMBUDDY_PORT="$2"
                shift 2
                ;;
            --ssh-pubkey)
                SSH_PUBKEY="$2"
                shift 2
                ;;
            --yes|-y)
                NON_INTERACTIVE="true"
                shift
                ;;
            --help|-h)
                show_help
                ;;
            *)
                error "Unknown option: $1 (use --help for usage)"
                ;;
        esac
    done
}

ask_install_mode() {
    if [[ -n "$INSTALL_MODE" ]]; then
        return
    fi

    echo ""
    echo -e "${BOLD}How would you like to set up SpoolBuddy?${NC}"
    echo ""
    echo -e "  ${CYAN}1)${NC} SpoolBuddy only"
    echo "     NFC reader + scale on this RPi, Bambuddy runs on another device"
    echo ""
    echo -e "  ${CYAN}2)${NC} SpoolBuddy + Bambuddy"
    echo "     Both running natively on this Raspberry Pi"
    echo ""

    while true; do
        echo -en "${BOLD}Choose${NC} [${CYAN}1${NC}/${CYAN}2${NC}]: "
        read -r choice
        case "$choice" in
            1) INSTALL_MODE="spoolbuddy"; return;;
            2) INSTALL_MODE="full"; return;;
            *) echo "Please enter 1 or 2.";;
        esac
    done
}

gather_config() {
    echo ""
    echo -e "${BOLD}Configuration${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo ""

    # Set default install path based on mode
    if [[ -z "$INSTALL_PATH" ]]; then
        if [[ "$INSTALL_MODE" == "full" ]]; then
            INSTALL_PATH="/opt/bambuddy"
        else
            INSTALL_PATH="/opt/bambuddy"
        fi
    fi
    prompt "Installation directory" "$INSTALL_PATH" INSTALL_PATH

    if [[ -z "$INSTALL_REPO" ]]; then
        INSTALL_REPO="$GITHUB_REPO"
    fi
    prompt "Git repository URL" "$INSTALL_REPO" INSTALL_REPO
    INSTALL_REPO="$(normalize_github_repo_url "$INSTALL_REPO")"

    if [[ -z "$INSTALL_REF" ]]; then
        INSTALL_REF="main"
    fi

    if [[ "$NON_INTERACTIVE" != "true" && -n "$DETECTED_INSTALLER_REF" && "$DETECTED_INSTALLER_REF" != "HEAD" ]]; then
        echo ""
        echo -e "${BOLD}Install Source Ref${NC}"
        echo "1) main"
        echo "2) $DETECTED_INSTALLER_REF (detected from installer context)"
        echo "3) custom"
        while true; do
            echo -en "${BOLD}Choose${NC} [1/2/3]: "
            read -r ref_choice
            case "$ref_choice" in
                ""|1)
                    INSTALL_REF="main"
                    break
                    ;;
                2)
                    INSTALL_REF="$DETECTED_INSTALLER_REF"
                    break
                    ;;
                3)
                    prompt "Git ref (branch/tag/commit)" "$INSTALL_REF" INSTALL_REF
                    break
                    ;;
                *)
                    echo "Please enter 1, 2, or 3."
                    ;;
            esac
        done
    else
        prompt "Git ref (branch/tag/commit)" "$INSTALL_REF" INSTALL_REF
    fi

    if [[ "$INSTALL_MODE" == "spoolbuddy" ]]; then
        # Need remote Bambuddy URL and API key
        echo ""
        info "SpoolBuddy needs to connect to your Bambuddy server."
        info "You can find/create an API key in Bambuddy under Settings -> API Keys."
        echo ""

        while [[ -z "$BAMBUDDY_URL" ]]; do
            prompt "Bambuddy server URL (e.g. http://192.168.1.100:8000)" "" BAMBUDDY_URL
            if [[ -z "$BAMBUDDY_URL" ]]; then
                warn "Bambuddy URL is required"
            fi
        done

        while [[ -z "$API_KEY" ]]; do
            prompt "Bambuddy API key" "" API_KEY
            if [[ -z "$API_KEY" ]]; then
                warn "API key is required"
            fi
        done
    else
        # Full mode — Bambuddy runs locally
        prompt "Bambuddy port" "$BAMBUDDY_PORT" BAMBUDDY_PORT
        BAMBUDDY_URL="http://localhost:$BAMBUDDY_PORT"

        echo ""
        info "After installation, create an API key in Bambuddy (Settings -> API Keys)"
        info "and update it in: $INSTALL_PATH/spoolbuddy/.env"
        API_KEY="CHANGE_ME_AFTER_SETUP"
    fi

    # Summary
    echo ""
    echo -e "${BOLD}Installation Summary${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo -e "  Mode:           ${GREEN}$([ "$INSTALL_MODE" == "full" ] && echo "Bambuddy + SpoolBuddy" || echo "SpoolBuddy only")${NC}"
    echo -e "  Install path:   ${GREEN}$INSTALL_PATH${NC}"
    echo -e "  Git repo:       ${GREEN}$INSTALL_REPO${NC}"
    echo -e "  Git ref:        ${GREEN}$INSTALL_REF${NC}"
    if [[ "$INSTALL_MODE" == "full" ]]; then
        echo -e "  Bambuddy port:  ${GREEN}$BAMBUDDY_PORT${NC}"
        echo -e "  Bambuddy URL:   ${GREEN}$BAMBUDDY_URL${NC}"
    else
        echo -e "  Bambuddy URL:   ${GREEN}$BAMBUDDY_URL${NC}"
    fi
    echo ""

    if ! prompt_yes_no "Proceed with installation?" "y"; then
        echo "Installation cancelled."
        exit 0
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

main() {
    parse_args "$@"
    detect_installer_source_context

    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║                                                          ║${NC}"
    echo -e "${CYAN}║   ____                    _ ____            _     _       ║${NC}"
    echo -e "${CYAN}║  / ___| _ __   ___   ___ | | __ ) _   _  __| | __| |_   _ ║${NC}"
    echo -e "${CYAN}║  \\___ \\| '_ \\ / _ \\ / _ \\| |  _ \\| | | |/ _\` |/ _\` | | | |║${NC}"
    echo -e "${CYAN}║   ___) | |_) | (_) | (_) | | |_) | |_| | (_| | (_| | |_| |║${NC}"
    echo -e "${CYAN}║  |____/| .__/ \\___/ \\___/|_|____/ \\__,_|\\__,_|\\__,_|\\__, |║${NC}"
    echo -e "${CYAN}║        |_|                                          |___/ ║${NC}"
    echo -e "${CYAN}║                                                          ║${NC}"
    echo -e "${CYAN}║          NFC Spool Management for Bambuddy               ║${NC}"
    echo -e "${CYAN}║                                                          ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Check if running via pipe without -y
    if [[ ! -t 0 ]] && [[ "$NON_INTERACTIVE" != "true" ]]; then
        error "Interactive mode requires a terminal. Use -y for unattended install, or download and run directly."
    fi

    # Pre-flight checks
    check_root
    check_raspberry_pi
    check_raspberry_pi_os

    if ! detect_python; then
        info "Python 3.10+ not found, will install..."
    fi

    # Gather user preferences
    ask_install_mode
    gather_config

    # Validate mode
    if [[ "$INSTALL_MODE" != "spoolbuddy" && "$INSTALL_MODE" != "full" ]]; then
        error "Invalid mode: $INSTALL_MODE (must be 'spoolbuddy' or 'full')"
    fi

    echo ""
    echo -e "${BOLD}Starting Installation${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo ""

    # ── Step 1: Raspberry Pi hardware config ──────────────────────────────
    info "Configuring Raspberry Pi hardware..."
    enable_spi
    enable_i2c
    configure_boot_config
    echo ""

    # ── Step 2: System packages ───────────────────────────────────────────
    install_system_packages
    install_wifi_safeguard
    upgrade_system_packages
    detect_python || error "Failed to install Python 3.10+"
    echo ""

    # ── Step 2b: Strip unnecessary services & packages ────────────────────
    strip_services
    strip_packages
    echo ""

    # ── Step 3: Download source code ──────────────────────────────────────
    create_spoolbuddy_user
    download_spoolbuddy
    echo ""

    # ── Step 3b: Kiosk setup (labwc + Chromium + squeekboard + Plymouth) ──
    setup_kiosk
    echo ""

    # ── Step 4: SpoolBuddy setup ──────────────────────────────────────────
    info "Setting up SpoolBuddy..."
    setup_spoolbuddy_venv
    create_spoolbuddy_env
    # Kiosk env access: only needed if actual kiosk hardware is available
    if [[ -f /boot/firmware/config.txt ]] || [[ -f /boot/config.txt ]]; then
        ensure_kiosk_env_access
    fi
    setup_ssh_key
    create_spoolbuddy_service
    echo ""

    # ── Step 5: Bambuddy setup (full mode only) ───────────────────────────
    if [[ "$INSTALL_MODE" == "full" ]]; then
        info "Setting up Bambuddy..."
        create_bambuddy_user
        setup_bambuddy_venv
        install_nodejs
        build_frontend
        create_bambuddy_directories
        create_bambuddy_env
        create_bambuddy_service
        bootstrap_spoolbuddy_kiosk_key
        echo ""
    fi

    # ── Done ──────────────────────────────────────────────────────────────
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                                                          ║${NC}"
    echo -e "${GREEN}║              Installation Complete!                      ║${NC}"
    echo -e "${GREEN}║                                                          ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local ip_addr
    ip_addr=$(hostname -I 2>/dev/null | awk '{print $1}') || ip_addr="<your-ip>"

    if [[ "$INSTALL_MODE" == "full" ]]; then
        echo -e "  ${BOLD}Bambuddy:${NC}         ${CYAN}http://$ip_addr:$BAMBUDDY_PORT${NC}"
    else
        echo -e "  ${BOLD}SpoolBuddy:${NC}       Connecting to ${CYAN}$BAMBUDDY_URL${NC}"
    fi
    echo -e "  ${BOLD}Kiosk URL:${NC}        ${CYAN}$KIOSK_URL${NC}"
    echo -e "  ${BOLD}Kiosk user:${NC}       ${CYAN}$KIOSK_USER${NC}"
    echo ""

    if [[ "$INSTALL_MODE" == "full" ]]; then
        echo -e "  ${BOLD}Next steps:${NC}"
        echo -e "    1. Reboot (required for kiosk, Plymouth splash, and hardware changes)"
        echo -e "    2. The touchscreen kiosk will start automatically after reboot"
        echo -e "    3. On another device, open ${CYAN}http://$ip_addr:$BAMBUDDY_PORT${NC} to complete first-run admin setup"
    fi

    echo ""
    echo -e "  ${BOLD}Manage services:${NC}"
    echo -e "    SpoolBuddy status:   ${CYAN}sudo systemctl status spoolbuddy${NC}"
    echo -e "    SpoolBuddy logs:     ${CYAN}sudo journalctl -u spoolbuddy -f${NC}"
    if [[ "$INSTALL_MODE" == "full" ]]; then
        echo -e "    Bambuddy status:     ${CYAN}sudo systemctl status bambuddy${NC}"
        echo -e "    Bambuddy logs:       ${CYAN}sudo journalctl -u bambuddy -f${NC}"
    fi

    echo ""
    echo -e "  ${BOLD}Configuration:${NC}    ${CYAN}$INSTALL_PATH/spoolbuddy/.env${NC}"
    echo -e "  ${BOLD}Hardware wiring:${NC}  ${CYAN}$INSTALL_PATH/spoolbuddy/README.md${NC}"
    echo -e "  ${BOLD}Diagnostics:${NC}      ${CYAN}sudo $INSTALL_PATH/spoolbuddy/venv/bin/python $INSTALL_PATH/spoolbuddy/pn5180_diag.py${NC}"
    echo ""

    echo -e "  ${YELLOW}A reboot is required to apply all changes (kiosk, Plymouth splash, hardware).${NC}"
    echo ""
    if prompt_yes_no "Reboot now?" "y"; then
        reboot
    else
        echo -e "  Run ${CYAN}sudo reboot${NC} when ready."
    fi

    echo ""
}

main "$@"
