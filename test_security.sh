#!/usr/bin/env bash
#
# Local security scanning - mirrors GitHub Actions pipeline
# Runs all scans in parallel and shows a consolidated summary.
#
# Usage:
#   ./test_security.sh              # Run fast scans (bandit, pip-audit, npm-audit)
#   ./test_security.sh --full       # Run full pipeline (all scans below)
#   ./test_security.sh bandit       # Run a specific scan
#   ./test_security.sh codeql trivy # Run multiple specific scans
#
# Available scans:
#   bandit          Python static security analysis (SAST)
#   codeql          CodeQL analysis (Actions + JavaScript + Python)
#   codeql-actions  CodeQL GitHub Actions only
#   codeql-python   CodeQL Python only
#   codeql-js       CodeQL JavaScript/TypeScript only
#   trivy           Trivy container image + Dockerfile/IaC scan
#   trivy-image     Trivy container image scan only
#   trivy-config    Trivy Dockerfile/IaC scan only
#   pip-audit       Python dependency vulnerability audit
#   npm-audit       Frontend dependency vulnerability audit
#
# Prerequisites:
#   pip install bandit[sarif] pip-audit     # Python tools
#   gh extension install github/gh-codeql   # CodeQL CLI
#   curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh  # Trivy
#

set -uo pipefail

# Navigate to project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Temp directory for scan output ───────────────────────────────────────

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# Parallel job tracking
declare -A PIDS=()       # scan_name -> PID
declare -A RESULTS=()    # scan_name -> PASS|FAIL|SKIP
declare -A DURATIONS=()  # scan_name -> seconds

# Scan display order
SCAN_ORDER=()

# ── SARIF parser (used for CodeQL result display) ────────────────────────

parse_sarif() {
    local sarif_file="$1"
    python3 << PYEOF
import json
from collections import defaultdict

with open("$sarif_file") as f:
    data = json.load(f)

rule_desc = {}
for run in data.get("runs", []):
    for rule in run.get("tool", {}).get("driver", {}).get("rules", []):
        rid = rule.get("id", "")
        desc = rule.get("shortDescription", {}).get("text", "")
        rule_desc[rid] = desc

by_rule = defaultdict(list)
for run in data.get("runs", []):
    for result in run.get("results", []):
        rule_id = result.get("ruleId", "unknown")
        msg = result.get("message", {}).get("text", "")
        locs = result.get("locations", [])
        loc = ""
        if locs:
            pl = locs[0].get("physicalLocation", {})
            uri = pl.get("artifactLocation", {}).get("uri", "")
            line = pl.get("region", {}).get("startLine", "")
            loc = f"{uri}:{line}" if line else uri
        by_rule[rule_id].append((loc, msg))

total = sum(len(v) for v in by_rule.values())
if total == 0:
    print("No findings.")
else:
    print(f"{total} findings:")
    print()
    for rule_id, findings in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        desc = rule_desc.get(rule_id, "")
        print(f"  {rule_id} ({len(findings)}) -- {desc}")
        for loc, msg in findings:
            short_msg = msg[:100] + "..." if len(msg) > 100 else msg
            print(f"    {loc:60s} {short_msg}")
        print()
PYEOF
}

# ── Scan functions (write to stdout, return exit code) ───────────────────

check_command() {
    command -v "$1" &>/dev/null
}

has_codeql() {
    check_command gh && gh codeql version &>/dev/null
}

scan_bandit() {
    if ! check_command bandit; then
        echo "SKIP: 'bandit' not found. Install: pip install bandit[sarif]"
        return 2
    fi
    bandit -r backend/ --severity-level medium -x backend/tests 2>&1
}

scan_codeql_python() {
    local sarif="$PROJECT_ROOT/codeql-python-results.sarif"
    if ! has_codeql; then
        echo "SKIP: CodeQL CLI not installed. Install: gh extension install github/gh-codeql"
        return 2
    fi
    echo "Creating database..."
    gh codeql database create --overwrite --language=python --threads=0 /tmp/bambuddy-codeql-python &>/dev/null
    echo "Analyzing..."
    gh codeql database analyze /tmp/bambuddy-codeql-python \
        "$PROJECT_ROOT/.codeql/python-bambuddy.qls" \
        --threads=0 --format=sarifv2.1.0 --output="$sarif" &>/dev/null
    echo ""
    parse_sarif "$sarif"
}

scan_codeql_js() {
    local sarif="$PROJECT_ROOT/codeql-javascript-results.sarif"
    if ! has_codeql; then
        echo "SKIP: CodeQL CLI not installed."
        return 2
    fi
    echo "Creating database..."
    gh codeql database create --overwrite --language=javascript --source-root=frontend --threads=0 /tmp/bambuddy-codeql-javascript &>/dev/null
    echo "Analyzing..."
    gh codeql database analyze /tmp/bambuddy-codeql-javascript \
        "$PROJECT_ROOT/.codeql/javascript-bambuddy.qls" \
        --threads=0 --format=sarifv2.1.0 --output="$sarif" &>/dev/null
    echo ""
    parse_sarif "$sarif"
}

scan_codeql_actions() {
    local sarif="$PROJECT_ROOT/codeql-actions-results.sarif"
    if ! has_codeql; then
        echo "SKIP: CodeQL CLI not installed."
        return 2
    fi
    echo "Creating database..."
    gh codeql database create --overwrite --language=actions --threads=0 /tmp/bambuddy-codeql-actions &>/dev/null
    echo "Analyzing..."
    gh codeql database analyze /tmp/bambuddy-codeql-actions \
        codeql/actions-queries \
        --threads=0 --format=sarifv2.1.0 --output="$sarif" &>/dev/null
    echo ""
    parse_sarif "$sarif"
}

scan_trivy_image() {
    if ! check_command trivy; then
        echo "SKIP: 'trivy' not found. Install: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh"
        return 2
    fi
    if ! check_command docker; then
        echo "SKIP: 'docker' not found."
        return 2
    fi
    echo "Building Docker image..."
    docker build -t bambuddy:security-scan . 2>&1
    echo ""
    trivy image --severity CRITICAL,HIGH,MEDIUM bambuddy:security-scan 2>&1
}

scan_trivy_config() {
    if ! check_command trivy; then
        echo "SKIP: 'trivy' not found. Install: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh"
        return 2
    fi
    trivy config --severity CRITICAL,HIGH,MEDIUM . 2>&1
}

scan_pip_audit() {
    if ! check_command pip-audit; then
        echo "SKIP: 'pip-audit' not found. Install: pip install pip-audit"
        return 2
    fi
    pip-audit --desc on 2>&1
}

scan_npm_audit() {
    if ! check_command npm; then
        echo "SKIP: 'npm' not found. Install Node.js"
        return 2
    fi
    (cd frontend && npm audit --audit-level=high) 2>&1
}

# ── Job launcher (streams output live with prefix, captures to log) ──────

launch_scan() {
    local name="$1"
    local func="$2"
    local prefix
    prefix=$(printf "${DIM}[%-14s]${NC} " "$name")

    SCAN_ORDER+=("$name")

    (
        set -o pipefail
        local start_time
        start_time=$(date +%s)

        "$func" 2>&1 | tee "$WORK_DIR/${name}.log" | sed "s|^|${prefix}|"
        local exit_code=${PIPESTATUS[0]}

        echo $(( $(date +%s) - start_time )) > "$WORK_DIR/${name}.duration"
        exit "$exit_code"
    ) &
    PIDS["$name"]=$!
}

# ── Wait for all scans ───────────────────────────────────────────────────

wait_for_scans() {
    local total=${#PIDS[@]}
    local completed=0

    while [ "$completed" -lt "$total" ]; do
        for name in "${SCAN_ORDER[@]}"; do
            local pid=${PIDS[$name]:-}
            [ -z "$pid" ] && continue

            if ! kill -0 "$pid" 2>/dev/null; then
                wait "$pid" 2>/dev/null
                local exit_code=$?

                if [ "$exit_code" -eq 2 ]; then
                    RESULTS["$name"]="SKIP"
                elif [ "$exit_code" -eq 0 ]; then
                    RESULTS["$name"]="PASS"
                else
                    RESULTS["$name"]="FAIL"
                fi

                if [ -f "$WORK_DIR/${name}.duration" ]; then
                    DURATIONS["$name"]=$(cat "$WORK_DIR/${name}.duration")
                else
                    DURATIONS["$name"]="?"
                fi

                local status_color
                case "${RESULTS[$name]}" in
                    PASS) status_color="$GREEN" ;;
                    FAIL) status_color="$RED" ;;
                    SKIP) status_color="$YELLOW" ;;
                esac
                echo -e "${status_color}${BOLD}[${RESULTS[$name]}]${NC} ${name} ${DIM}(${DURATIONS[$name]}s)${NC}"

                unset "PIDS[$name]"
                completed=$((completed + 1))
            fi
        done
        sleep 0.5
    done
}

# ── Summary ──────────────────────────────────────────────────────────────

print_summary() {
    local pass=0 fail=0 skip=0

    for name in "${SCAN_ORDER[@]}"; do
        case "${RESULTS[$name]}" in
            PASS) pass=$((pass + 1)) ;;
            FAIL) fail=$((fail + 1)) ;;
            SKIP) skip=$((skip + 1)) ;;
        esac
    done

    # ── Results table ────────────────────────────────────────────────────

    echo ""
    echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}${BOLD}  Security Scan Results${NC}"
    echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════${NC}"
    echo ""
    printf "  ${BOLD}%-6s  %-24s  %s${NC}\n" "Status" "Scan" "Duration"
    printf "  %-6s  %-24s  %s\n" "──────" "────────────────────────" "────────"

    for name in "${SCAN_ORDER[@]}"; do
        local status="${RESULTS[$name]}"
        local duration="${DURATIONS[$name]:-?}s"
        local status_color
        case "$status" in
            PASS) status_color="$GREEN" ;;
            FAIL) status_color="$RED" ;;
            SKIP) status_color="$YELLOW" ;;
        esac
        printf "  ${status_color}%-6s${NC}  %-24s  ${DIM}%s${NC}\n" "$status" "$name" "$duration"
    done

    echo ""
    echo -e "  ${GREEN}$pass passed${NC}  ${RED}$fail failed${NC}  ${YELLOW}$skip skipped${NC}"

    # ── Full output per scan ─────────────────────────────────────────────

    for name in "${SCAN_ORDER[@]}"; do
        local log="$WORK_DIR/${name}.log"
        [ ! -f "$log" ] && continue

        local status="${RESULTS[$name]}"
        local status_color

        case "$status" in
            PASS) status_color="$GREEN" ;;
            FAIL) status_color="$RED" ;;
            SKIP) status_color="$YELLOW" ;;
        esac

        echo ""
        echo -e "${CYAN}──────────────────────────────────────────────────────────────${NC}"
        echo -e "${BOLD}  $name${NC}  ${status_color}[$status]${NC}"
        echo -e "${CYAN}──────────────────────────────────────────────────────────────${NC}"

        sed 's/^/  /' "$log"
    done

    echo ""
    echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════${NC}"
    echo ""

    if [ "$fail" -gt 0 ]; then
        exit 1
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    head -29 "$0" | tail -27
    exit 0
fi

echo -e "${BOLD}Bambuddy Security Scanner${NC}"
echo -e "${DIM}$(date '+%Y-%m-%d %H:%M:%S')  •  $(nproc) CPU cores available${NC}"
echo ""

SCANS_TO_RUN=()

if [ $# -eq 0 ]; then
    SCANS_TO_RUN=(bandit pip-audit npm-audit)
elif [ "$1" = "--full" ]; then
    SCANS_TO_RUN=(bandit pip-audit npm-audit codeql-actions codeql-python codeql-js trivy-image trivy-config)
else
    for scan in "$@"; do
        case "$scan" in
            codeql) SCANS_TO_RUN+=(codeql-actions codeql-python codeql-js) ;;
            trivy)  SCANS_TO_RUN+=(trivy-image trivy-config) ;;
            bandit|codeql-actions|codeql-python|codeql-js|trivy-image|trivy-config|pip-audit|npm-audit)
                SCANS_TO_RUN+=("$scan") ;;
            *)
                echo -e "${RED}Unknown scan: $scan${NC}"
                echo "Run with --help for available scans"
                exit 1
                ;;
        esac
    done
fi

# Launch all scans in parallel
for scan in "${SCANS_TO_RUN[@]}"; do
    case "$scan" in
        bandit)         launch_scan "bandit"         scan_bandit ;;
        codeql-actions) launch_scan "codeql-actions" scan_codeql_actions ;;
        codeql-python)  launch_scan "codeql-python"  scan_codeql_python ;;
        codeql-js)      launch_scan "codeql-js"      scan_codeql_js ;;
        trivy-image)    launch_scan "trivy-image"    scan_trivy_image ;;
        trivy-config)   launch_scan "trivy-config"   scan_trivy_config ;;
        pip-audit)      launch_scan "pip-audit"      scan_pip_audit ;;
        npm-audit)      launch_scan "npm-audit"      scan_npm_audit ;;
    esac
done

echo -e "${BOLD}Running ${#SCANS_TO_RUN[@]} scan(s) in parallel...${NC}"
echo ""

wait_for_scans
print_summary
