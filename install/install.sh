#!/usr/bin/env bash
#
# BamBuddy Native Installation Script
# Supports: Debian/Ubuntu, RHEL/Fedora/CentOS, Arch Linux, macOS
#
# Usage:
#   Interactive:  curl -fsSL https://raw.githubusercontent.com/maziggy/bambuddy/main/install/install.sh | bash
#   Unattended:   ./install.sh --path /opt/bambuddy --port 8000 --yes
#
# Options:
#   --path PATH        Installation directory (default: /opt/bambuddy)
#   --port PORT        Port to listen on (default: 8000)
#   --bind ADDRESS     Bind address: 0.0.0.0 (network) or 127.0.0.1 (local only)
#   --tz TIMEZONE      Timezone (default: system timezone or UTC)
#   --data-dir PATH    Data directory (default: INSTALL_PATH/data)
#   --log-dir PATH     Log directory (default: INSTALL_PATH/logs)
#   --debug            Enable debug mode
#   --log-level LEVEL  Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
#   --no-service       Skip systemd service setup (Linux only)
#   --set-system-tz    Set system timezone to match (for unattended installs)
#   --yes, -y          Non-interactive mode, accept defaults
#   --help, -h         Show this help message
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Default values
DEFAULT_INSTALL_PATH="/opt/bambuddy"
DEFAULT_PORT="8000"
DEFAULT_BIND_ADDRESS="0.0.0.0"
DEFAULT_LOG_LEVEL="INFO"
DEFAULT_DEBUG="false"

# Script variables
INSTALL_PATH=""
PORT=""
BIND_ADDRESS=""
TIMEZONE=""
DATA_DIR=""
LOG_DIR=""
DEBUG_MODE=""
LOG_LEVEL=""
SKIP_SERVICE="false"
SET_SYSTEM_TZ=""
NON_INTERACTIVE="false"
OS_TYPE=""
PKG_MANAGER=""
PYTHON_CMD=""
SERVICE_USER="bambuddy"

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

print_banner() {
    echo -e "${CYAN}"
    echo "╔════════════════════════════════════════════════════════╗"
    echo "║                                                        ║"
    echo "║   ____                  _               _     _        ║"
    echo "║  | __ )  __ _ _ __ ___ | |__  _   _  __| | __| |_   _  ║"
    echo "║  |  _ \\ / _\` | '_ \` _ \\| '_ \\| | | |/ _\` |/ _\` | | | | ║"
    echo "║  | |_) | (_| | | | | | | |_) | |_| | (_| | (_| | |_| | ║"
    echo "║  |____/ \\__,_|_| |_| |_|_.__/ \\__,_|\\__,_|\\__,_|\\__, | ║"
    echo "║                                                 |___/  ║"
    echo "║                                                        ║"
    echo "║            Native Installation Script                  ║"
    echo "║                                                        ║"
    echo "╚════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
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
    local default="$2"  # y or n

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
    echo "BamBuddy Native Installation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --path PATH        Installation directory (default: /opt/bambuddy)"
    echo "  --port PORT        Port to listen on (default: 8000)"
    echo "  --bind ADDRESS     Bind address: 0.0.0.0 (network) or 127.0.0.1 (local only)"
    echo "  --tz TIMEZONE      Timezone (default: system timezone or UTC)"
    echo "  --data-dir PATH    Data directory (default: INSTALL_PATH/data)"
    echo "  --log-dir PATH     Log directory (default: INSTALL_PATH/logs)"
    echo "  --debug            Enable debug mode"
    echo "  --log-level LEVEL  Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)"
    echo "  --no-service       Skip systemd service setup (Linux only)"
    echo "  --set-system-tz    Set system timezone to match (for unattended installs)"
    echo "  --yes, -y          Non-interactive mode, accept defaults"
    echo "  --help, -h         Show this help message"
    echo ""
    echo "Examples:"
    echo "  Interactive installation:"
    echo "    ./install.sh"
    echo ""
    echo "  Unattended installation with custom settings:"
    echo "    ./install.sh --path /srv/bambuddy --port 3000 --tz America/New_York --yes"
    echo ""
    echo "  Minimal unattended installation:"
    echo "    ./install.sh -y"
    exit 0
}

# -----------------------------------------------------------------------------
# System Detection
# -----------------------------------------------------------------------------

detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS_TYPE="macos"
        PKG_MANAGER="brew"
        return
    fi

    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|raspbian|linuxmint|pop)
                OS_TYPE="debian"
                PKG_MANAGER="apt"
                ;;
            fedora|rhel|centos|rocky|almalinux|ol)
                OS_TYPE="rhel"
                if command -v dnf &>/dev/null; then
                    PKG_MANAGER="dnf"
                else
                    PKG_MANAGER="yum"
                fi
                ;;
            arch|manjaro|endeavouros)
                OS_TYPE="arch"
                PKG_MANAGER="pacman"
                ;;
            opensuse*|sles)
                OS_TYPE="suse"
                PKG_MANAGER="zypper"
                ;;
            *)
                log_error "Unsupported Linux distribution: $ID"
                exit 1
                ;;
        esac
    else
        log_error "Cannot detect operating system"
        exit 1
    fi
}

detect_python() {
    # Try python3 first, then python
    if command -v python3 &>/dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &>/dev/null; then
        local version
        version=$(python --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1)
        if [[ "$version" -ge 3 ]]; then
            PYTHON_CMD="python"
        fi
    fi

    if [[ -z "$PYTHON_CMD" ]]; then
        return 1
    fi

    # Check version >= 3.10
    local version
    version=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    local major minor
    major=$(echo "$version" | cut -d'.' -f1)
    minor=$(echo "$version" | cut -d'.' -f2)

    if [[ "$major" -lt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -lt 10 ]]; }; then
        log_warn "Python $version found, but 3.10+ is required"
        return 1
    fi

    log_success "Found Python $version"
    return 0
}

detect_timezone() {
    if [[ -n "$TIMEZONE" ]]; then
        return 0
    fi

    # Try to get system timezone (with error handling for set -e)
    TIMEZONE=""
    if [[ -f /etc/timezone ]]; then
        TIMEZONE=$(cat /etc/timezone 2>/dev/null) || true
    fi

    if [[ -z "$TIMEZONE" ]] && [[ -L /etc/localtime ]]; then
        TIMEZONE=$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||') || true
    fi

    if [[ -z "$TIMEZONE" ]] && command -v timedatectl &>/dev/null; then
        TIMEZONE=$(timedatectl show --property=Timezone --value 2>/dev/null) || true
    fi

    # Default to UTC if not found (use if/then to avoid set -e issue with &&)
    if [[ -z "$TIMEZONE" ]]; then
        TIMEZONE="UTC"
    fi
    return 0
}

# -----------------------------------------------------------------------------
# Package Installation
# -----------------------------------------------------------------------------

install_dependencies() {
    log_info "Installing system dependencies..."

    case "$PKG_MANAGER" in
        apt)
            sudo apt-get update
            sudo apt-get install -y python3 python3-pip python3-venv git curl ffmpeg
            ;;
        dnf|yum)
            sudo $PKG_MANAGER install -y python3 python3-pip git curl ffmpeg
            ;;
        pacman)
            sudo pacman -Sy --noconfirm python python-pip git curl ffmpeg
            ;;
        zypper)
            sudo zypper install -y python3 python3-pip git curl ffmpeg
            ;;
        brew)
            # Check if Homebrew is installed
            if ! command -v brew &>/dev/null; then
                log_error "Homebrew not found. Please install it first: https://brew.sh"
                exit 1
            fi
            brew install python git curl ffmpeg
            ;;
    esac

    log_success "System dependencies installed"
}

# -----------------------------------------------------------------------------
# Installation Steps
# -----------------------------------------------------------------------------

create_user() {
    if [[ "$OS_TYPE" == "macos" ]]; then
        return  # Skip user creation on macOS
    fi

    if id "$SERVICE_USER" &>/dev/null; then
        log_info "User '$SERVICE_USER' already exists"
        return
    fi

    log_info "Creating service user '$SERVICE_USER'..."
    sudo useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_PATH" "$SERVICE_USER"
    log_success "Service user created"
}

download_bambuddy() {
    log_info "Downloading BamBuddy..."

    if [[ -d "$INSTALL_PATH/.git" ]]; then
        log_info "Existing installation found, updating..."
        # Add safe.directory to avoid "dubious ownership" error when running as root
        git config --global --add safe.directory "$INSTALL_PATH" 2>/dev/null || true
        cd "$INSTALL_PATH"
        git fetch origin
        git reset --hard origin/main
        # Ensure correct ownership after update
        sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_PATH" 2>/dev/null || true
    else
        sudo mkdir -p "$INSTALL_PATH"
        sudo chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_PATH" 2>/dev/null || true
        git clone https://github.com/maziggy/bambuddy.git "$INSTALL_PATH"
        sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_PATH" 2>/dev/null || true
    fi

    log_success "BamBuddy downloaded to $INSTALL_PATH"
}

setup_virtualenv() {
    log_info "Setting up Python virtual environment..."

    cd "$INSTALL_PATH"

    if [[ "$OS_TYPE" == "macos" ]]; then
        $PYTHON_CMD -m venv venv
        source venv/bin/activate
    else
        sudo -u "$SERVICE_USER" $PYTHON_CMD -m venv venv 2>/dev/null || $PYTHON_CMD -m venv venv
        source venv/bin/activate
    fi

    pip install --upgrade pip
    pip install -r requirements.txt

    log_success "Virtual environment configured"
}

check_node_version() {
    # Returns 0 if Node.js 20+ is available, 1 otherwise
    if ! command -v node &>/dev/null; then
        return 1
    fi

    local version
    version=$(node --version 2>/dev/null | sed 's/^v//')
    local major
    major=$(echo "$version" | cut -d'.' -f1)

    if [[ "$major" -ge 20 ]]; then
        log_success "Found Node.js v$version"
        return 0
    else
        log_warn "Found Node.js v$version (need 20+)"
        return 1
    fi
}

install_nodejs() {
    log_info "Installing Node.js 22..."
    case "$PKG_MANAGER" in
        apt)
            # Remove old nodejs if present
            sudo apt-get remove -y nodejs npm 2>/dev/null || true
            curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
            sudo apt-get install -y nodejs
            ;;
        dnf|yum)
            sudo $PKG_MANAGER remove -y nodejs npm 2>/dev/null || true
            curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
            sudo $PKG_MANAGER install -y nodejs
            ;;
        pacman)
            sudo pacman -S --noconfirm nodejs npm
            ;;
        zypper)
            sudo zypper install -y nodejs22
            ;;
        brew)
            brew install node@22
            brew link --overwrite node@22
            ;;
        *)
            log_error "Please install Node.js 20+ manually: https://nodejs.org/"
            exit 1
            ;;
    esac
    # Refresh PATH
    hash -r 2>/dev/null || true
}

build_frontend() {
    log_info "Building frontend..."

    cd "$INSTALL_PATH/frontend"

    # Check for Node.js 20+
    if ! check_node_version; then
        install_nodejs
        # Verify installation
        if ! check_node_version; then
            log_error "Failed to install Node.js 20+. Please install manually."
            exit 1
        fi
    fi

    npm ci
    npm run build

    log_success "Frontend built"
}

create_directories() {
    log_info "Creating data directories..."

    sudo mkdir -p "$DATA_DIR" "$LOG_DIR"

    if [[ "$OS_TYPE" != "macos" ]]; then
        sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$LOG_DIR"
    fi

    log_success "Directories created"
}

create_env_file() {
    log_info "Creating environment configuration..."

    local env_file="$INSTALL_PATH/.env"

    # Note: Only include settings recognized by the app's pydantic Settings class
    # Other settings (PORT, BIND_ADDRESS, DATA_DIR, LOG_DIR, TZ) are set in systemd service
    cat > /tmp/bambuddy.env << EOF
# BamBuddy Configuration
# Generated by install.sh on $(date)

# Debug mode (true = verbose logging)
DEBUG=$DEBUG_MODE

# Log level (only used when DEBUG=false)
# Options: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=$LOG_LEVEL

# Enable file logging
LOG_TO_FILE=true
EOF

    sudo mv /tmp/bambuddy.env "$env_file"
    if [[ "$OS_TYPE" != "macos" ]]; then
        sudo chown "$SERVICE_USER:$SERVICE_USER" "$env_file"
    fi
    sudo chmod 600 "$env_file"

    log_success "Environment file created at $env_file"
}

create_systemd_service() {
    if [[ "$OS_TYPE" == "macos" ]] || [[ "$SKIP_SERVICE" == "true" ]]; then
        return
    fi

    log_info "Creating systemd service..."

    cat > /tmp/bambuddy.service << EOF
[Unit]
Description=BamBuddy - Bambu Lab Print Management
Documentation=https://github.com/maziggy/bambuddy
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_PATH

# App settings from .env file
EnvironmentFile=$INSTALL_PATH/.env

# Service settings (not in .env to avoid pydantic validation errors)
Environment="DATA_DIR=$DATA_DIR"
Environment="LOG_DIR=$LOG_DIR"
Environment="TZ=$TIMEZONE"

ExecStart=$INSTALL_PATH/venv/bin/uvicorn backend.app.main:app --host $BIND_ADDRESS --port $PORT
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR $LOG_DIR $INSTALL_PATH

[Install]
WantedBy=multi-user.target
EOF

    sudo mv /tmp/bambuddy.service /etc/systemd/system/bambuddy.service
    sudo systemctl daemon-reload

    log_success "Systemd service created"

    if prompt_yes_no "Enable BamBuddy to start on boot?" "y"; then
        sudo systemctl enable bambuddy
        log_success "Service enabled"
    fi

    if prompt_yes_no "Start BamBuddy now?" "y"; then
        sudo systemctl start bambuddy
        sleep 2
        if sudo systemctl is-active --quiet bambuddy; then
            log_success "BamBuddy is running"
        else
            log_warn "Service may have failed to start. Check: sudo journalctl -u bambuddy -f"
        fi
    fi
}

create_launchd_service() {
    if [[ "$OS_TYPE" != "macos" ]] || [[ "$SKIP_SERVICE" == "true" ]]; then
        return
    fi

    log_info "Creating launchd service..."

    local plist_path="$HOME/Library/LaunchAgents/com.bambuddy.app.plist"

    cat > "$plist_path" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.bambuddy.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_PATH/venv/bin/uvicorn</string>
        <string>backend.app.main:app</string>
        <string>--host</string>
        <string>$BIND_ADDRESS</string>
        <string>--port</string>
        <string>$PORT</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_PATH</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DEBUG</key>
        <string>$DEBUG_MODE</string>
        <key>LOG_LEVEL</key>
        <string>$LOG_LEVEL</string>
        <key>DATA_DIR</key>
        <string>$DATA_DIR</string>
        <key>LOG_DIR</key>
        <string>$LOG_DIR</string>
        <key>TZ</key>
        <string>$TIMEZONE</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/bambuddy.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/bambuddy.error.log</string>
</dict>
</plist>
EOF

    log_success "Launchd plist created at $plist_path"

    if prompt_yes_no "Load BamBuddy service now?" "y"; then
        launchctl load "$plist_path"
        sleep 2
        if launchctl list | grep -q "com.bambuddy.app"; then
            log_success "BamBuddy is running"
        else
            log_warn "Service may have failed to start. Check: cat $LOG_DIR/bambuddy.error.log"
        fi
    fi
}

# -----------------------------------------------------------------------------
# Main Installation Flow
# -----------------------------------------------------------------------------

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --path)
                INSTALL_PATH="$2"
                shift 2
                ;;
            --port)
                PORT="$2"
                shift 2
                ;;
            --bind)
                BIND_ADDRESS="$2"
                shift 2
                ;;
            --tz)
                TIMEZONE="$2"
                shift 2
                ;;
            --data-dir)
                DATA_DIR="$2"
                shift 2
                ;;
            --log-dir)
                LOG_DIR="$2"
                shift 2
                ;;
            --debug)
                DEBUG_MODE="true"
                shift
                ;;
            --log-level)
                LOG_LEVEL="$2"
                shift 2
                ;;
            --no-service)
                SKIP_SERVICE="true"
                shift
                ;;
            --set-system-tz)
                SET_SYSTEM_TZ="true"
                shift
                ;;
            --yes|-y)
                NON_INTERACTIVE="true"
                shift
                ;;
            --help|-h)
                show_help
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                ;;
        esac
    done
}

gather_config() {
    echo ""
    echo -e "${BOLD}Installation Configuration${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo ""

    # Installation path
    [[ -z "$INSTALL_PATH" ]] && prompt "Installation directory" "$DEFAULT_INSTALL_PATH" INSTALL_PATH

    # Port
    [[ -z "$PORT" ]] && prompt "Port to listen on" "$DEFAULT_PORT" PORT

    # Bind address
    if [[ -z "$BIND_ADDRESS" ]]; then
        echo ""
        echo "Network access:"
        echo "  0.0.0.0   - Accessible from other devices on your network (recommended)"
        echo "  127.0.0.1 - Only accessible from this machine"
        prompt "Bind address" "$DEFAULT_BIND_ADDRESS" BIND_ADDRESS
    fi

    # Timezone
    detect_timezone
    prompt "Timezone" "$TIMEZONE" TIMEZONE

    # Offer to set system timezone if different from current (skip if already set via --set-system-tz)
    if [[ -z "$SET_SYSTEM_TZ" ]]; then
        local current_tz
        current_tz=$(timedatectl show --property=Timezone --value 2>/dev/null) || true
        if [[ -n "$TIMEZONE" ]] && [[ "$TIMEZONE" != "$current_tz" ]]; then
            # Default to "n" so unattended installs don't change system TZ unless --set-system-tz is used
            if prompt_yes_no "Set system timezone to $TIMEZONE?" "n"; then
                SET_SYSTEM_TZ="true"
            else
                SET_SYSTEM_TZ="false"
            fi
        else
            SET_SYSTEM_TZ="false"
        fi
    fi

    # Data directory
    [[ -z "$DATA_DIR" ]] && DATA_DIR="$INSTALL_PATH/data"
    prompt "Data directory" "$DATA_DIR" DATA_DIR

    # Log directory
    [[ -z "$LOG_DIR" ]] && LOG_DIR="$INSTALL_PATH/logs"
    prompt "Log directory" "$LOG_DIR" LOG_DIR

    # Debug mode
    if [[ -z "$DEBUG_MODE" ]]; then
        if prompt_yes_no "Enable debug mode?" "n"; then
            DEBUG_MODE="true"
        else
            DEBUG_MODE="false"
        fi
    fi

    # Log level
    if [[ -z "$LOG_LEVEL" ]]; then
        echo ""
        echo "Log levels: DEBUG, INFO, WARNING, ERROR"
        prompt "Log level" "$DEFAULT_LOG_LEVEL" LOG_LEVEL
    fi

    # Confirm
    echo ""
    echo -e "${BOLD}Installation Summary${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo -e "  Install path:  ${GREEN}$INSTALL_PATH${NC}"
    echo -e "  Port:          ${GREEN}$PORT${NC}"
    echo -e "  Bind address:  ${GREEN}$BIND_ADDRESS${NC}"
    echo -e "  Timezone:      ${GREEN}$TIMEZONE${NC}"
    echo -e "  Data dir:      ${GREEN}$DATA_DIR${NC}"
    echo -e "  Log dir:       ${GREEN}$LOG_DIR${NC}"
    echo -e "  Debug mode:    ${GREEN}$DEBUG_MODE${NC}"
    echo -e "  Log level:     ${GREEN}$LOG_LEVEL${NC}"
    echo ""

    if ! prompt_yes_no "Proceed with installation?" "y"; then
        echo "Installation cancelled."
        exit 0
    fi
}

main() {
    parse_args "$@"
    print_banner

    # Check if running via pipe (curl | bash) - interactive mode won't work
    if [[ ! -t 0 ]] && [[ "$NON_INTERACTIVE" != "true" ]]; then
        log_error "Interactive mode requires a terminal."
        log_info "When using 'curl | bash', you must use non-interactive mode:"
        echo ""
        echo "    curl -fsSL URL | bash -s -- --yes"
        echo ""
        log_info "Or download and run directly:"
        echo ""
        echo "    curl -fsSL URL -o install.sh && chmod +x install.sh && ./install.sh"
        echo ""
        exit 1
    fi

    # Check for root (we need sudo for some operations)
    if [[ "$EUID" -eq 0 ]] && [[ "$OS_TYPE" != "macos" ]]; then
        log_warn "Running as root. Consider using a regular user with sudo privileges."
    fi

    # Detect system
    log_info "Detecting system..."
    detect_os
    log_success "Detected: $OS_TYPE (package manager: $PKG_MANAGER)"

    # Check/install Python
    if ! detect_python; then
        log_info "Python 3.10+ not found, will install..."
    fi

    # Gather configuration
    gather_config

    # Install steps
    echo ""
    echo -e "${BOLD}Starting Installation${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo ""

    install_dependencies
    detect_python || { log_error "Failed to install Python"; exit 1; }

    # Set system timezone if requested
    if [[ "$SET_SYSTEM_TZ" == "true" ]]; then
        log_info "Setting system timezone to $TIMEZONE..."
        if [[ "$OS_TYPE" == "macos" ]]; then
            sudo systemsetup -settimezone "$TIMEZONE" 2>/dev/null || true
        else
            sudo timedatectl set-timezone "$TIMEZONE" 2>/dev/null || true
        fi
        log_success "System timezone set to $TIMEZONE"
    fi

    if [[ "$OS_TYPE" != "macos" ]]; then
        create_user
    else
        SERVICE_USER="$USER"
    fi

    download_bambuddy
    setup_virtualenv
    build_frontend
    create_directories
    create_env_file

    if [[ "$OS_TYPE" == "macos" ]]; then
        create_launchd_service
    else
        create_systemd_service
    fi

    # Done!
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                                                              ║${NC}"
    echo -e "${GREEN}║              Installation Complete!                          ║${NC}"
    echo -e "${GREEN}║                                                              ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    # Show appropriate URL based on bind address
    if [[ "$BIND_ADDRESS" == "0.0.0.0" ]]; then
        local ip_addr
        ip_addr=$(hostname -I 2>/dev/null | awk '{print $1}') || ip_addr="<your-ip>"
        echo -e "  ${BOLD}Access BamBuddy:${NC}  ${CYAN}http://localhost:$PORT${NC}"
        echo -e "                    ${CYAN}http://$ip_addr:$PORT${NC} (from other devices)"
    else
        echo -e "  ${BOLD}Access BamBuddy:${NC}  ${CYAN}http://localhost:$PORT${NC}"
    fi
    echo ""
    if [[ "$OS_TYPE" == "macos" ]]; then
        echo -e "  ${BOLD}Manage service:${NC}"
        echo -e "    Start:   launchctl load ~/Library/LaunchAgents/com.bambuddy.app.plist"
        echo -e "    Stop:    launchctl unload ~/Library/LaunchAgents/com.bambuddy.app.plist"
        echo -e "    Logs:    tail -f $LOG_DIR/bambuddy.log"
    else
        echo -e "  ${BOLD}Manage service:${NC}"
        echo -e "    Status:  sudo systemctl status bambuddy"
        echo -e "    Start:   sudo systemctl start bambuddy"
        echo -e "    Stop:    sudo systemctl stop bambuddy"
        echo -e "    Logs:    sudo journalctl -u bambuddy -f"
    fi
    echo ""
    echo -e "  ${BOLD}Update BamBuddy:${NC}"
    echo -e "    cd $INSTALL_PATH && git pull && source venv/bin/activate"
    echo -e "    pip install -r requirements.txt && cd frontend && npm ci && npm run build"
    if [[ "$OS_TYPE" != "macos" ]]; then
        echo -e "    sudo systemctl restart bambuddy"
    fi
    echo ""
    echo -e "  ${BOLD}Documentation:${NC}  ${CYAN}https://wiki.bambuddy.cool${NC}"
    echo ""
}

main "$@"
