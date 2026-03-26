#!/bin/bash
# Daily beta build: build Docker image, push to registries, create/update GitHub prerelease
#
# Usage:
#   ./docker-publish-daily-beta.sh [--parallel] [--ghcr-only] [--dockerhub-only] [--skip-release]
#
# Examples:
#   ./docker-publish-daily-beta.sh                  # Full daily beta workflow
#   ./docker-publish-daily-beta.sh --parallel       # Build both archs simultaneously
#   ./docker-publish-daily-beta.sh --ghcr-only      # Only push to GHCR
#   ./docker-publish-daily-beta.sh --dockerhub-only # Only push to Docker Hub
#   ./docker-publish-daily-beta.sh --skip-release   # Build+push without GitHub release
#
# Reads APP_VERSION from backend/app/core/config.py (must be a beta version like 0.2.2b1).
# Builds and pushes a multi-arch Docker image tagged as 'daily'. Each push overwrites the
# previous 'daily' image. A GitHub prerelease is created with a date-stamped tag for history.
#
# Users can stay up to date by pulling the 'daily' tag or using Watchtower:
#   docker pull ghcr.io/maziggy/bambuddy:daily
#
# Prerequisites:
#   1. Log in to ghcr.io:
#      echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin
#
#   2. Log in to Docker Hub:
#      docker login -u YOUR_USERNAME
#
#   3. GitHub CLI (gh) authenticated for creating releases
#
# Supported architectures:
#   - linux/amd64 (x86_64, most servers/desktops)
#   - linux/arm64 (Raspberry Pi 4/5, Apple Silicon via emulation)

set -e

# Configuration
GHCR_REGISTRY="ghcr.io"
DOCKERHUB_REGISTRY="docker.io"
IMAGE_NAME="maziggy/bambuddy"
GHCR_IMAGE="${GHCR_REGISTRY}/${IMAGE_NAME}"
DOCKERHUB_IMAGE="${DOCKERHUB_REGISTRY}/${IMAGE_NAME}"
PLATFORMS="linux/amd64,linux/arm64"
BUILDER_NAME="bambuddy-builder"
CONFIG_FILE="backend/app/core/config.py"
CHANGELOG_FILE="CHANGELOG.md"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
PARALLEL=false
PUSH_GHCR=true
PUSH_DOCKERHUB=true
SKIP_RELEASE=false
for arg in "$@"; do
    case $arg in
        --parallel)
            PARALLEL=true
            ;;
        --ghcr-only)
            PUSH_DOCKERHUB=false
            ;;
        --dockerhub-only)
            PUSH_GHCR=false
            ;;
        --skip-release)
            SKIP_RELEASE=true
            ;;
        --help|-h)
            echo "Usage: $0 [--parallel] [--ghcr-only] [--dockerhub-only] [--skip-release]"
            echo ""
            echo "Build and publish a daily beta Docker image using the APP_VERSION from config.py."
            echo ""
            echo "Options:"
            echo "  --parallel       Build both architectures simultaneously"
            echo "  --ghcr-only      Only push to GitHub Container Registry"
            echo "  --dockerhub-only Only push to Docker Hub"
            echo "  --skip-release   Build+push without creating/updating GitHub release"
            echo "  --help, -h       Show this help"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown argument: $arg${NC}"
            echo "Run $0 --help for usage"
            exit 1
            ;;
    esac
done

# ============================================================
# Step 1: Read and validate APP_VERSION
# ============================================================
echo -e "${BLUE}[1/4] Validating APP_VERSION...${NC}"

VERSION=$(grep -oP 'APP_VERSION = "\K[^"]+' "$CONFIG_FILE")

if [ -z "$VERSION" ]; then
    echo -e "${RED}Error: Could not read APP_VERSION from ${CONFIG_FILE}${NC}"
    exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+b[0-9]+$ ]]; then
    echo -e "${RED}Error: APP_VERSION '${VERSION}' is not a beta version (expected X.Y.Zb<N>)${NC}"
    exit 1
fi

# Date-stamped tag for GitHub releases only (not used as Docker tag)
DAILY_DATE=$(date +%Y%m%d)
DAILY_TAG="${VERSION}-daily.${DAILY_DATE}"

echo -e "${GREEN}  APP_VERSION: ${VERSION}${NC}"
echo -e "${GREEN}  Docker tag:  daily${NC}"
echo -e "${GREEN}  Release tag: v${DAILY_TAG}${NC}"

# ============================================================
# Step 2: Build & push Docker images
# ============================================================
echo ""

# Get CPU count
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Daily beta build${NC}"
echo -e "${GREEN}  Version:   ${VERSION}${NC}"
echo -e "${GREEN}  Docker tag: daily${NC}"
echo -e "${GREEN}  Platforms: ${PLATFORMS}${NC}"
echo -e "${GREEN}  CPU cores: ${CPU_COUNT}${NC}"
if [ "$PARALLEL" = true ]; then
    echo -e "${GREEN}  Mode: PARALLEL (both archs simultaneously)${NC}"
else
    echo -e "${GREEN}  Mode: Sequential (amd64 → arm64)${NC}"
fi
echo -e "${GREEN}  Registries:${NC}"
if [ "$PUSH_GHCR" = true ]; then
    echo -e "${GREEN}    - ${GHCR_IMAGE}${NC}"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    echo -e "${GREEN}    - ${DOCKERHUB_IMAGE}${NC}"
fi
echo -e "${GREEN}================================================${NC}"
echo ""

# Check registry logins
if [ "$PUSH_GHCR" = true ]; then
    if ! grep -q "ghcr.io" ~/.docker/config.json 2>/dev/null; then
        echo -e "${YELLOW}Warning: You may not be logged in to ghcr.io${NC}"
        echo "Run: echo \$GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin"
        echo ""
    fi
fi

if [ "$PUSH_DOCKERHUB" = true ]; then
    if ! grep -q "index.docker.io\|docker.io" ~/.docker/config.json 2>/dev/null; then
        echo -e "${RED}Error: You are not logged in to Docker Hub${NC}"
        echo "Run: docker login -u YOUR_USERNAME"
        echo ""
        exit 1
    fi
fi

# Setup buildx builder if not exists
echo -e "${BLUE}[2/4] Setting up Docker Buildx and building...${NC}"
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
    echo "Creating new buildx builder: $BUILDER_NAME (optimized for ${CPU_COUNT} cores)"
    docker buildx create \
        --name "$BUILDER_NAME" \
        --driver docker-container \
        --driver-opt network=host \
        --driver-opt "env.BUILDKIT_STEP_LOG_MAX_SIZE=10000000" \
        --buildkitd-flags "--allow-insecure-entitlement network.host --oci-worker-gc=false" \
        --config /dev/stdin <<EOF
[worker.oci]
  max-parallelism = ${CPU_COUNT}
EOF
    docker buildx inspect --bootstrap "$BUILDER_NAME"
fi
docker buildx use "$BUILDER_NAME"

# Verify builder supports multi-platform
if ! docker buildx inspect --bootstrap | grep -q "linux/arm64"; then
    echo -e "${YELLOW}Installing QEMU for cross-platform builds...${NC}"
    docker run --privileged --rm tonistiigi/binfmt --install all
fi

# Beta versions get 'daily' tag (never 'latest')
echo -e "${YELLOW}Beta version — tagging as 'daily' (not 'latest')${NC}"

# Build tags for all target registries
TAGS=""
if [ "$PUSH_GHCR" = true ]; then
    TAGS="$TAGS -t ${GHCR_IMAGE}:daily"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    TAGS="$TAGS -t ${DOCKERHUB_IMAGE}:daily"
fi

# Common build args (no cache to ensure clean builds)
BUILD_ARGS="--provenance=false --sbom=false --no-cache --pull"

if [ "$PARALLEL" = true ]; then
    # Parallel build: Build each architecture separately then combine manifests
    echo -e "${YELLOW}Building amd64 and arm64 in parallel (${CPU_COUNT} cores each, no cache)...${NC}"

    # Build per-arch staging tags for each target registry
    ARCH_TAGS_AMD64=""
    ARCH_TAGS_ARM64=""
    if [ "$PUSH_GHCR" = true ]; then
        ARCH_TAGS_AMD64="$ARCH_TAGS_AMD64 -t ${GHCR_IMAGE}:daily-amd64"
        ARCH_TAGS_ARM64="$ARCH_TAGS_ARM64 -t ${GHCR_IMAGE}:daily-arm64"
    fi
    if [ "$PUSH_DOCKERHUB" = true ]; then
        ARCH_TAGS_AMD64="$ARCH_TAGS_AMD64 -t ${DOCKERHUB_IMAGE}:daily-amd64"
        ARCH_TAGS_ARM64="$ARCH_TAGS_ARM64 -t ${DOCKERHUB_IMAGE}:daily-arm64"
    fi

    # Build amd64 in background
    (
        echo -e "${BLUE}[amd64] Starting build...${NC}"
        docker buildx build \
            --platform linux/amd64 \
            ${ARCH_TAGS_AMD64} \
            ${BUILD_ARGS} \
            --push \
            . 2>&1 | sed 's/^/[amd64] /'
        echo -e "${GREEN}[amd64] Complete!${NC}"
    ) &
    PID_AMD64=$!

    # Build arm64 in background
    (
        echo -e "${BLUE}[arm64] Starting build...${NC}"
        docker buildx build \
            --platform linux/arm64 \
            ${ARCH_TAGS_ARM64} \
            ${BUILD_ARGS} \
            --push \
            . 2>&1 | sed 's/^/[arm64] /'
        echo -e "${GREEN}[arm64] Complete!${NC}"
    ) &
    PID_ARM64=$!

    # Wait for both builds
    echo "Waiting for parallel builds to complete..."
    wait $PID_AMD64
    wait $PID_ARM64

    # Create multi-arch manifests per registry (no cross-registry blob copies)
    echo -e "${BLUE}Creating multi-arch manifests...${NC}"

    if [ "$PUSH_GHCR" = true ]; then
        echo -e "${BLUE}  Creating GHCR manifest...${NC}"
        docker buildx imagetools create \
            -t "${GHCR_IMAGE}:daily" \
            "${GHCR_IMAGE}:daily-amd64" \
            "${GHCR_IMAGE}:daily-arm64"
    fi
    if [ "$PUSH_DOCKERHUB" = true ]; then
        echo -e "${BLUE}  Creating Docker Hub manifest...${NC}"
        docker buildx imagetools create \
            -t "${DOCKERHUB_IMAGE}:daily" \
            "${DOCKERHUB_IMAGE}:daily-amd64" \
            "${DOCKERHUB_IMAGE}:daily-arm64"
    fi
else
    # Sequential build (default): Build both platforms in one command
    echo -e "${YELLOW}Building sequentially with ${CPU_COUNT} cores (no cache)...${NC}"
    DOCKER_BUILDKIT=1 docker buildx build \
        --platform "$PLATFORMS" \
        ${BUILD_ARGS} \
        $TAGS \
        --push \
        .
fi

# ============================================================
# Step 3: Create/update GitHub release
# ============================================================
if [ "$SKIP_RELEASE" = true ]; then
    echo -e "${YELLOW}[3/4] Skipping GitHub release (--skip-release)${NC}"
else
    echo -e "${BLUE}[3/4] Creating/updating GitHub release...${NC}"

    # Extract release notes from CHANGELOG: content between ## [<version>] and the next ## [ heading
    CHANGELOG_NOTES=$(sed -n "/^## \[${VERSION}\]/,/^## \[/{/^## \[/!p}" "$CHANGELOG_FILE" | sed '/^$/d; 1{/^$/d}')

    # Strip @mentions so GitHub doesn't auto-generate a "Contributors" section
    CHANGELOG_NOTES=$(echo "$CHANGELOG_NOTES" | sed 's/@\([a-zA-Z0-9_-]*\)/\1/g')

    if [ -z "$CHANGELOG_NOTES" ]; then
        echo -e "${YELLOW}  Warning: No changelog notes found for ${VERSION}${NC}"
        CHANGELOG_NOTES="No changelog notes available for this release."
    fi

    # Build pull commands for the release body
    PULL_COMMANDS=""
    if [ "$PUSH_GHCR" = true ]; then
        PULL_COMMANDS="docker pull ghcr.io/maziggy/bambuddy:daily"
    fi
    if [ "$PUSH_DOCKERHUB" = true ]; then
        if [ -n "$PULL_COMMANDS" ]; then
            PULL_COMMANDS="${PULL_COMMANDS}
# or
docker pull maziggy/bambuddy:daily"
        else
            PULL_COMMANDS="docker pull maziggy/bambuddy:daily"
        fi
    fi

    # Create the release body
    TODAY=$(date +%Y-%m-%d)
    RELEASE_BODY=$(cat <<EOF
> [!NOTE]
> This is a **daily beta build** (${TODAY}). It contains the latest fixes and improvements but may have undiscovered issues.
>
> **Docker users:** Update by pulling the new image:
> \`\`\`
> ${PULL_COMMANDS}
> \`\`\`
>
> **Tip:** Use [Watchtower](https://containrrr.dev/watchtower/) to automatically update when new daily builds are pushed.

---

${CHANGELOG_NOTES}
EOF
    )

    # Delete ALL old daily releases — only the latest daily build should exist
    echo "  Cleaning up old daily releases..."
    OLD_DAILY_RELEASES=$(gh release list --limit 100 --json tagName --jq '.[] | select(.tagName | test("-daily\\.")) | .tagName' 2>/dev/null || true)
    if [ -n "$OLD_DAILY_RELEASES" ]; then
        while IFS= read -r old_tag; do
            echo "  Deleting old daily release: ${old_tag}..."
            gh release delete "$old_tag" --yes --cleanup-tag 2>/dev/null || true
        done <<< "$OLD_DAILY_RELEASES"
    fi

    # Create/move tag to current HEAD and push
    echo "  Tagging current HEAD as v${DAILY_TAG}..."
    git tag -f "v${DAILY_TAG}"
    git push origin "v${DAILY_TAG}" --force

    echo "  Creating release v${DAILY_TAG}..."
    gh release create "v${DAILY_TAG}" \
        --title "Daily Beta Build v${DAILY_TAG}" \
        --prerelease \
        --generate-notes=false \
        --notes "$RELEASE_BODY"
    echo -e "${GREEN}  Created GitHub release: v${DAILY_TAG}${NC}"
fi

# ============================================================
# Step 4: Verify
# ============================================================
echo -e "${BLUE}[4/4] Verifying...${NC}"

if [ "$PUSH_GHCR" = true ]; then
    echo -e "${BLUE}GHCR manifest:${NC}"
    docker buildx imagetools inspect "${GHCR_IMAGE}:daily"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    echo -e "${BLUE}Docker Hub manifest:${NC}"
    docker buildx imagetools inspect "${DOCKERHUB_IMAGE}:daily"
fi

if [ "$SKIP_RELEASE" != true ]; then
    echo ""
    echo -e "${BLUE}GitHub release:${NC}"
    gh release view "v${DAILY_TAG}"
fi

# ============================================================
# Summary
# ============================================================
echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Daily beta build complete!${NC}"
echo -e "${GREEN}  Version: ${VERSION}${NC}"
echo -e "${GREEN}================================================${NC}"
if [ "$PUSH_GHCR" = true ]; then
    echo "  GHCR:       ${GHCR_IMAGE}:daily"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    echo "  Docker Hub: ${DOCKERHUB_IMAGE}:daily"
fi
if [ "$SKIP_RELEASE" != true ]; then
    echo "  Release:    https://github.com/${IMAGE_NAME}/releases/tag/v${DAILY_TAG}"
fi
echo ""
echo -e "${BLUE}Supported platforms:${NC}"
echo "  - linux/amd64 (Intel/AMD servers, desktops)"
echo "  - linux/arm64 (Raspberry Pi 4/5, Apple Silicon)"
echo ""
echo -e "${GREEN}Users can now run:${NC}"
if [ "$PUSH_GHCR" = true ]; then
    echo "  docker pull ${GHCR_IMAGE}:daily"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    echo "  docker pull ${DOCKERHUB_IMAGE}:daily"
    echo "  docker pull ${IMAGE_NAME}:daily  # shorthand"
fi
