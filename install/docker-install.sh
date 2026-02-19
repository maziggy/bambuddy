#!/usr/bin/env bash
#
# BamBuddy Docker Installation Script
# Supports: Linux (all distros), macOS
#
# Usage:
#   Interactive:  curl -fsSL https://raw.githubusercontent.com/maziggy/bambuddy/main/install/docker-install.sh -o docker-install.sh && chmod +x docker-install.sh && ./docker-install.sh
#   Unattended:   ./docker-install.sh --path /opt/bambuddy --port 8000 --yes
#
# Options:
#   --path PATH        Installation directory (default: /opt/bambuddy)
#   --port PORT        Port to expose (default: 8000)
#   --bind ADDRESS     Bind address: 0.0.0.0 (network) or 127.0.0.1 (local only)
#   --tz TIMEZONE      Timezone (default: system timezone or UTC)
#   --build            Build from source instead of using pre-built image
#   --yes, -y          Non-interactive mode, accept defaults
#   --redirect-990     Add iptables redirect from 990 -> 9990 (Linux only)
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

# Script variables
INSTALL_PATH=""
PORT=""
BIND_ADDRESS=""
TIMEZONE=""
BUILD_FROM_SOURCE="false"
NON_INTERACTIVE="false"
OS_TYPE=""
DOCKER_CMD=""
REDIRECT_990="false"

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
    echo "║            Docker Installation Script                  ║"
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
    echo "BamBuddy Docker Installation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --path PATH        Installation directory (default: /opt/bambuddy)"
    echo "  --port PORT        Port to expose (default: 8000)"
    echo "  --bind ADDRESS     Bind address: 0.0.0.0 (network) or 127.0.0.1 (local only)"
    echo "  --tz TIMEZONE      Timezone (default: system timezone or UTC)"
    echo "  --build            Build from source instead of using pre-built image"
    echo "  --yes, -y          Non-interactive mode, accept defaults"
    echo "  --redirect-990     Add iptables redirect from 990 -> 9990 (Linux only)"
    echo "  --help, -h         Show this help message"
    echo ""
    echo "Examples:"
    echo "  Interactive installation:"
    echo "    ./docker-install.sh"
    echo ""
    echo "  Unattended installation with custom settings:"
    echo "    ./docker-install.sh --path /srv/bambuddy --port 3000 --tz America/New_York --yes"
    echo ""
    echo "  Build from source:"
    echo "    ./docker-install.sh --build --yes"
    exit 0
}

# -----------------------------------------------------------------------------
# System Detection
# -----------------------------------------------------------------------------

detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS_TYPE="macos"
        return
    fi

    if [[ -f /etc/os-release ]]; then
        OS_TYPE="linux"
    else
        log_error "Cannot detect operating system"
        exit 1
    fi
}

detect_docker() {
    # Check for docker compose (v2) or docker-compose (v1)
    if docker compose version &>/dev/null 2>&1; then
        DOCKER_CMD="docker compose"
        log_success "Found Docker Compose v2"
        return 0
    elif docker-compose --version &>/dev/null 2>&1; then
        DOCKER_CMD="docker-compose"
        log_success "Found Docker Compose v1"
        return 0
    fi
    return 1
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
# Installation Functions
# -----------------------------------------------------------------------------

install_docker() {
    log_info "Docker not found, installing..."

    case "$OS_TYPE" in
        linux)
            # Use Docker's convenience script
            curl -fsSL https://get.docker.com | sh

            # Add current user to docker group
            if [[ -n "$SUDO_USER" ]]; then
                sudo usermod -aG docker "$SUDO_USER"
                log_warn "Added $SUDO_USER to docker group. You may need to log out and back in."
            else
                sudo usermod -aG docker "$USER"
                log_warn "Added $USER to docker group. You may need to log out and back in."
            fi

            # Start Docker service
            sudo systemctl enable docker
            sudo systemctl start docker
            ;;
        macos)
            log_error "Docker Desktop not found."
            log_error "Please install Docker Desktop for Mac from: https://www.docker.com/products/docker-desktop"
            exit 1
            ;;
    esac

    log_success "Docker installed"
}

create_install_dir() {
    log_info "Creating installation directory..."

    mkdir -p "$INSTALL_PATH"
    cd "$INSTALL_PATH"

    log_success "Directory created: $INSTALL_PATH"
}

download_compose_file() {
    log_info "Downloading docker-compose.yml..."

    if [[ "$BUILD_FROM_SOURCE" == "true" ]]; then
        # Clone the full repo for building
        if [[ -d ".git" ]]; then
            log_info "Existing repository found, updating..."
            git fetch origin
            git reset --hard origin/main
        else
            git clone https://github.com/maziggy/bambuddy.git .
        fi
    else
        # Just download the compose file
        curl -fsSL -o docker-compose.yml \
            https://raw.githubusercontent.com/maziggy/bambuddy/main/docker-compose.yml
    fi

    log_success "docker-compose.yml ready"
}

create_env_file() {
    log_info "Creating environment configuration..."

    cat > .env << EOF
# BamBuddy Docker Configuration
# Generated by docker-install.sh on $(date)

# Port BamBuddy runs on
PORT=$PORT

# Timezone
TZ=$TIMEZONE
EOF

    log_success "Environment file created"
}

customize_compose() {
    # Detect if we need to disable host networking (macOS/Windows in Docker Desktop)
    if [[ "$OS_TYPE" == "macos" ]]; then
        log_warn "Docker Desktop detected. Host networking is not supported."
        log_info "Modifying docker-compose.yml for port mapping..."

        # Create a modified compose file for macOS
        if [[ -f docker-compose.yml ]]; then
            # Comment out network_mode: host and uncomment ports section
            sed -i.bak \
                -e 's/^[[:space:]]*network_mode: host/#    network_mode: host/' \
                -e 's/^[[:space:]]*#ports:/    ports:/' \
                -e 's/^[[:space:]]*#[[:space:]]*- "\${PORT:-8000}:8000"/      - "\${PORT:-8000}:8000"/' \
                docker-compose.yml

            log_warn "Printer discovery may not work. Add printers manually by IP address."
        fi
    fi
}

start_container() {
    log_info "Starting BamBuddy..."

    if [[ "$BUILD_FROM_SOURCE" == "true" ]]; then
        $DOCKER_CMD up -d --build
    else
        $DOCKER_CMD up -d
    fi

    # Wait for container to start
    log_info "Waiting for container to start..."
    local max_attempts=15
    local attempt=0

    while [[ $attempt -lt $max_attempts ]]; do
        # Check if container is running (Up)
        if $DOCKER_CMD ps | grep -q "Up"; then
            log_success "BamBuddy container is running"
            return 0
        fi

        # Check if container failed
        if $DOCKER_CMD ps -a | grep -q "Exited"; then
            log_error "Container failed to start"
            log_info "Check logs with: $DOCKER_CMD logs bambuddy"
            return 1
        fi

        sleep 2
        ((attempt++))
    done

    log_warn "Container may still be starting. Check with: $DOCKER_CMD ps"
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
            --build)
                BUILD_FROM_SOURCE="true"
                shift
                ;;
            --yes|-y)
                NON_INTERACTIVE="true"
                shift
                ;;
            --redirect-990)
                REDIRECT_990="true"
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

check_sudo() {
    if ! command -v sudo &>/dev/null; then
        log_error "sudo is required for iptables redirect but is not installed. Skipping iptables redirect."
        return 1
    fi
    if ! command -v iptables &>/dev/null; then
        log_error "iptables is required for iptables redirect but is not installed. Skipping iptables redirect."
        return 1
    fi
    return 0
}

configure_iptables_redirect() {
    if [[ "$OS_TYPE" != "linux" ]]; then
        log_warn "iptables redirect only supported on Linux. Skipping."
        return
    fi

    if [[ "$REDIRECT_990" != "true" ]]; then
        return
    fi

    if ! check_sudo; then
        return
    fi

    log_info "Configuring iptables redirect: 990 -> 9990"

    # Check if rule already exists
    if sudo iptables -t nat -C PREROUTING -p tcp --dport 990 -j REDIRECT --to-port 9990 2>/dev/null; then
        log_warn "PREROUTING rule already exists. Skipping."
    else
        sudo iptables -t nat -A PREROUTING -p tcp --dport 990 -j REDIRECT --to-port 9990
        log_success "Added PREROUTING redirect rule"
    fi

    if sudo iptables -t nat -C OUTPUT -o lo -p tcp --dport 990 -j REDIRECT --to-port 9990 2>/dev/null; then
        log_warn "OUTPUT rule already exists. Skipping."
    else
        sudo iptables -t nat -A OUTPUT -o lo -p tcp --dport 990 -j REDIRECT --to-port 9990
        log_success "Added OUTPUT redirect rule"
    fi

    log_warn "Note: iptables rules are NOT persistent after reboot."
    log_warn "To persist them, install iptables-persistent or use a firewall manager."
}

gather_config() {
    echo ""
    echo -e "${BOLD}Installation Configuration${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo ""

    # Installation path
    [[ -z "$INSTALL_PATH" ]] && prompt "Installation directory" "$DEFAULT_INSTALL_PATH" INSTALL_PATH

    # Port
    [[ -z "$PORT" ]] && prompt "Port to expose" "$DEFAULT_PORT" PORT

    # Bind address
    if [[ -z "$BIND_ADDRESS" ]]; then
        echo ""
        echo "Network access:"
        echo "  0.0.0.0   - Accessible from other devices on your network (recommended)"
        echo "  127.0.0.1 - Only accessible from this machine"
        prompt "Bind address" "$DEFAULT_BIND_ADDRESS" BIND_ADDRESS
    fi

    # Redirect port 990 -> 9990 (Linux only)
    if [[ "$OS_TYPE" == "linux" ]]; then
        if [[ "$NON_INTERACTIVE" == "true" ]]; then
            # In non-interactive mode, only set REDIRECT_990 if explicitly passed
            : # Do nothing, REDIRECT_990 is set only if --redirect-990 is passed
        elif [[ "$REDIRECT_990" != "true" ]]; then
            echo ""
            echo "Optional network configuration:"
            if prompt_yes_no "Add iptables redirect (990 -> 9990)?" "n"; then
                REDIRECT_990="true"
            fi
        fi
    fi

    # Timezone
    detect_timezone
    prompt "Timezone" "$TIMEZONE" TIMEZONE

    # Build from source?
    if [[ "$BUILD_FROM_SOURCE" != "true" ]] && [[ "$NON_INTERACTIVE" != "true" ]]; then
        if prompt_yes_no "Build from source? (No = use pre-built image)" "n"; then
            BUILD_FROM_SOURCE="true"
        fi
    fi

    # Confirm
    echo ""
    echo -e "${BOLD}Installation Summary${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo -e "  Install path:  ${GREEN}$INSTALL_PATH${NC}"
    echo -e "  Port:          ${GREEN}$PORT${NC}"
    echo -e "  Bind address:  ${GREEN}$BIND_ADDRESS${NC}"
    echo -e "  Timezone:      ${GREEN}$TIMEZONE${NC}"
    echo -e "  Build source:  ${GREEN}$BUILD_FROM_SOURCE${NC}"
    echo -e "  Redirect 990:  ${GREEN}$REDIRECT_990${NC}"
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
        echo "    curl -fsSL URL -o docker-install.sh && chmod +x docker-install.sh && ./docker-install.sh"
        echo ""
        exit 1
    fi

    # Detect system
    log_info "Detecting system..."
    detect_os
    log_success "Detected: $OS_TYPE"

    # Check for Docker
    if ! command -v docker &>/dev/null; then
        install_docker
    fi

    if ! detect_docker; then
        log_error "Docker Compose not found. Please install Docker Compose."
        exit 1
    fi

    # Check if Docker daemon is running
    if ! docker info &>/dev/null; then
        log_error "Docker daemon is not running. Please start Docker and try again."
        exit 1
    fi

    # Gather configuration
    gather_config

    # Install steps
    echo ""
    echo -e "${BOLD}Starting Installation${NC}"
    echo -e "${CYAN}─────────────────────────────────────────${NC}"
    echo ""

    create_install_dir
    download_compose_file
    create_env_file
    customize_compose
    start_container
    configure_iptables_redirect

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
    echo -e "  ${BOLD}Manage container:${NC}"
    echo -e "    Status:  cd $INSTALL_PATH && $DOCKER_CMD ps"
    echo -e "    Logs:    cd $INSTALL_PATH && $DOCKER_CMD logs -f bambuddy"
    echo -e "    Stop:    cd $INSTALL_PATH && $DOCKER_CMD down"
    echo -e "    Start:   cd $INSTALL_PATH && $DOCKER_CMD up -d"
    echo -e "    Restart: cd $INSTALL_PATH && $DOCKER_CMD restart"
    echo ""
    echo -e "  ${BOLD}Update BamBuddy:${NC}"
    if [[ "$BUILD_FROM_SOURCE" == "true" ]]; then
        echo -e "    cd $INSTALL_PATH && git pull && $DOCKER_CMD up -d --build"
    else
        echo -e "    cd $INSTALL_PATH && $DOCKER_CMD pull && $DOCKER_CMD up -d"
    fi
    echo ""
    echo -e "  ${BOLD}Data location:${NC}  Docker volumes (bambuddy_data, bambuddy_logs)"
    echo ""
    echo -e "  ${BOLD}Documentation:${NC}  ${CYAN}https://wiki.bambuddy.cool${NC}"
    echo ""

    # Warn about iptables persistence
    if [[ "$REDIRECT_990" == "true" ]] && [[ "$OS_TYPE" == "linux" ]]; then
        echo -e "  ${YELLOW}Note:${NC} iptables redirect rules do NOT survive reboot."
        echo -e "        Install 'iptables-persistent' if persistence is required."
        echo ""
    fi

    if [[ "$OS_TYPE" == "macos" ]]; then
        echo -e "  ${YELLOW}Note:${NC} Printer discovery may not work with Docker Desktop."
        echo -e "        Add printers manually using their IP address."
        echo ""
    fi
}

main "$@"
