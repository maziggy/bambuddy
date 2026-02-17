#!/bin/bash
# Build and push multi-architecture Docker image to GHCR (private beta)
#
# Usage:
#   ./docker-publish-beta.sh [version] [--parallel]
#
# Examples:
#   ./docker-publish-beta.sh 0.2.0b            # Sequential build
#   ./docker-publish-beta.sh 0.2.0b --parallel # Build both archs simultaneously
#
# All versions are also tagged as 'beta'
#
# Prerequisites:
#   1. Log in to ghcr.io:
#      echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin
#
#   2. Create a GitHub Personal Access Token with 'write:packages' scope:
#      https://github.com/settings/tokens/new?scopes=write:packages
#
#   3. After first push, set package to Private in GitHub → Packages → Settings
#      and add beta testers via Manage Access
#
# Supported architectures:
#   - linux/amd64 (x86_64, most servers/desktops)
#   - linux/arm64 (Raspberry Pi 4/5, Apple Silicon via emulation)

set -e

# Configuration
GHCR_REGISTRY="ghcr.io"
IMAGE_NAME="maziggy/bambuddy-beta"
GHCR_IMAGE="${GHCR_REGISTRY}/${IMAGE_NAME}"
PLATFORMS="linux/amd64,linux/arm64"
BUILDER_NAME="bambuddy-builder"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
VERSION=""
PARALLEL=false
for arg in "$@"; do
    case $arg in
        --parallel)
            PARALLEL=true
            ;;
        *)
            if [ -z "$VERSION" ]; then
                VERSION="$arg"
            fi
            ;;
    esac
done

if [ -z "$VERSION" ]; then
    echo -e "${YELLOW}Usage: $0 <version> [--parallel]${NC}"
    echo "Example: $0 0.2.0b"
    echo "         $0 0.2.0b --parallel  # Build both architectures simultaneously"
    exit 1
fi

# Get CPU count
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Building multi-arch BETA image${NC}"
echo -e "${GREEN}  Version: ${VERSION}${NC}"
echo -e "${GREEN}  Platforms: ${PLATFORMS}${NC}"
echo -e "${GREEN}  CPU cores: ${CPU_COUNT}${NC}"
if [ "$PARALLEL" = true ]; then
    echo -e "${GREEN}  Mode: PARALLEL (both archs simultaneously)${NC}"
else
    echo -e "${GREEN}  Mode: Sequential (amd64 → arm64)${NC}"
fi
echo -e "${GREEN}  Registry: ${GHCR_IMAGE}${NC}"
echo -e "${GREEN}================================================${NC}"
echo ""

# Check registry login
if ! grep -q "ghcr.io" ~/.docker/config.json 2>/dev/null; then
    echo -e "${YELLOW}Warning: You may not be logged in to ghcr.io${NC}"
    echo "Run: echo \$GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin"
    echo ""
fi

# Setup buildx builder if not exists
echo -e "${BLUE}[1/4] Setting up Docker Buildx...${NC}"
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
echo -e "${BLUE}[2/4] Verifying multi-platform support...${NC}"
if ! docker buildx inspect --bootstrap | grep -q "linux/arm64"; then
    echo -e "${YELLOW}Installing QEMU for cross-platform builds...${NC}"
    docker run --privileged --rm tonistiigi/binfmt --install all
fi

# Build tags — version + beta (not latest)
TAGS="-t ${GHCR_IMAGE}:${VERSION} -t ${GHCR_IMAGE}:beta"

echo -e "${BLUE}[3/4] Building and pushing...${NC}"

# Common build args (no cache to ensure clean builds)
BUILD_ARGS="--provenance=false --sbom=false --no-cache --pull"

if [ "$PARALLEL" = true ]; then
    # Parallel build: Build each architecture separately then combine manifests
    echo -e "${YELLOW}Building amd64 and arm64 in parallel (${CPU_COUNT} cores each, no cache)...${NC}"

    # Build amd64 in background
    (
        echo -e "${BLUE}[amd64] Starting build...${NC}"
        docker buildx build \
            --platform linux/amd64 \
            -t "${GHCR_IMAGE}:${VERSION}-amd64" \
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
            -t "${GHCR_IMAGE}:${VERSION}-arm64" \
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

    # Create multi-arch manifest
    echo -e "${BLUE}Creating multi-arch manifest...${NC}"
    docker buildx imagetools create \
        -t "${GHCR_IMAGE}:${VERSION}" -t "${GHCR_IMAGE}:beta" \
        "${GHCR_IMAGE}:${VERSION}-amd64" \
        "${GHCR_IMAGE}:${VERSION}-arm64"
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

echo -e "${BLUE}[4/4] Verifying manifest...${NC}"
docker buildx imagetools inspect "${GHCR_IMAGE}:${VERSION}"

echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Successfully pushed multi-arch BETA image:${NC}"
echo -e "${GREEN}================================================${NC}"
echo "  ${GHCR_IMAGE}:${VERSION}"
echo "  ${GHCR_IMAGE}:beta"
echo ""
echo -e "${BLUE}Supported platforms:${NC}"
echo "  - linux/amd64 (Intel/AMD servers, desktops)"
echo "  - linux/arm64 (Raspberry Pi 4/5, Apple Silicon)"
echo ""
echo -e "${GREEN}Beta testers can run:${NC}"
echo "  docker pull ${GHCR_IMAGE}:${VERSION}"
echo "  docker pull ${GHCR_IMAGE}:beta"
echo ""
echo -e "${YELLOW}Reminder: Set package to Private in GitHub → Packages → Settings${NC}"
