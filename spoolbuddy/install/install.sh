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
#   --bambuddy-url URL   Bambuddy server URL (required for spoolbuddy mode)
#   --api-key KEY        Bambuddy API key (required for spoolbuddy mode)
#   --path PATH          Installation directory (default: /opt/spoolbuddy or /opt/bambuddy)
#   --port PORT          Bambuddy port (full mode only, default: 8000)
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
BAMBUDDY_URL=""
API_KEY=""
BAMBUDDY_PORT="8000"
NON_INTERACTIVE="false"
REBOOT_NEEDED="false"

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
    echo "  --bambuddy-url URL   Bambuddy server URL (required for spoolbuddy mode)"
    echo "  --api-key KEY        Bambuddy API key (required for spoolbuddy mode)"
    echo "  --path PATH          Installation directory (default: /opt/spoolbuddy or /opt/bambuddy)"
    echo "  --port PORT          Bambuddy port (full mode only, default: 8000)"
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
        warn "You may need to manually add: dtparam=i2c_vc=on and dtoverlay=spi0-0cs"
        return
    fi

    info "Configuring $boot_config..."

    # Enable I2C bus 0 (GPIO0/GPIO1) for NAU7802 scale
    if ! grep -q "^dtparam=i2c_vc=on" "$boot_config"; then
        echo "" >> "$boot_config"
        echo "# SpoolBuddy: I2C bus 0 for NAU7802 scale (GPIO0/GPIO1)" >> "$boot_config"
        echo "dtparam=i2c_vc=on" >> "$boot_config"
        REBOOT_NEEDED="true"
        success "Added dtparam=i2c_vc=on"
    else
        success "dtparam=i2c_vc=on already set"
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

# ─────────────────────────────────────────────────────────────────────────────
# SpoolBuddy Installation
# ─────────────────────────────────────────────────────────────────────────────

create_spoolbuddy_user() {
    if id "$SPOOLBUDDY_SERVICE_USER" &>/dev/null; then
        info "User '$SPOOLBUDDY_SERVICE_USER' already exists"
    else
        info "Creating service user '$SPOOLBUDDY_SERVICE_USER'..."
        useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_PATH" "$SPOOLBUDDY_SERVICE_USER"
        success "Service user created"
    fi

    # Add to hardware access groups (gpio, spi, i2c)
    for group in gpio spi i2c; do
        if getent group "$group" &>/dev/null; then
            usermod -aG "$group" "$SPOOLBUDDY_SERVICE_USER" 2>/dev/null || true
        fi
    done
    success "User added to gpio, spi, i2c groups"
}

download_spoolbuddy() {
    if [[ -d "$INSTALL_PATH/.git" ]]; then
        info "Existing installation found, updating..."
        git config --global --add safe.directory "$INSTALL_PATH" 2>/dev/null || true
        cd "$INSTALL_PATH"
        run_with_progress "Fetching updates" git fetch origin
        git reset --hard origin/main > /dev/null 2>&1
    else
        mkdir -p "$INSTALL_PATH"
        run_with_progress "Cloning repository" git clone "$GITHUB_REPO" "$INSTALL_PATH"
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
EOF

    chown "$SPOOLBUDDY_SERVICE_USER:$SPOOLBUDDY_SERVICE_USER" "$env_file"
    chmod 600 "$env_file"
    success "Configuration saved to $env_file"
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
    detect_python || error "Failed to install Python 3.10+"
    echo ""

    # ── Step 3: Download source code ──────────────────────────────────────
    create_spoolbuddy_user
    download_spoolbuddy
    echo ""

    # ── Step 4: SpoolBuddy setup ──────────────────────────────────────────
    info "Setting up SpoolBuddy..."
    setup_spoolbuddy_venv
    create_spoolbuddy_env
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

    if [[ "$INSTALL_MODE" == "full" ]]; then
        local ip_addr
        ip_addr=$(hostname -I 2>/dev/null | awk '{print $1}') || ip_addr="<your-ip>"

        echo -e "  ${BOLD}Bambuddy:${NC}         ${CYAN}http://$ip_addr:$BAMBUDDY_PORT${NC}"
        echo ""
        echo -e "  ${BOLD}Next steps:${NC}"
        echo -e "    1. Reboot to apply hardware changes"
        echo -e "    2. Open Bambuddy in your browser"
        echo -e "    3. Go to Settings -> API Keys and create an API key"
        echo -e "    4. Update the API key in: ${CYAN}$INSTALL_PATH/spoolbuddy/.env${NC}"
        echo -e "    5. Restart SpoolBuddy: ${CYAN}sudo systemctl restart spoolbuddy${NC}"
    else
        echo -e "  ${BOLD}SpoolBuddy:${NC}       Connecting to ${CYAN}$BAMBUDDY_URL${NC}"
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

    if [[ "$REBOOT_NEEDED" == "true" ]]; then
        echo -e "  ${YELLOW}A reboot is required to apply SPI/I2C changes.${NC}"
        echo ""
        if prompt_yes_no "Reboot now?" "y"; then
            reboot
        else
            echo -e "  Run ${CYAN}sudo reboot${NC} when ready."
        fi
    else
        # SPI/I2C already configured — start services now
        if prompt_yes_no "Start services now?" "y"; then
            if [[ "$INSTALL_MODE" == "full" ]]; then
                systemctl start bambuddy
                sleep 2
                if systemctl is-active --quiet bambuddy; then
                    success "Bambuddy is running"
                else
                    warn "Bambuddy may have failed to start. Check: sudo journalctl -u bambuddy -f"
                fi
            fi

            systemctl start spoolbuddy
            sleep 2
            if systemctl is-active --quiet spoolbuddy; then
                success "SpoolBuddy is running"
            else
                warn "SpoolBuddy may have failed to start. Check: sudo journalctl -u spoolbuddy -f"
            fi
        fi
    fi

    echo ""
}

main "$@"
