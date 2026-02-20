#!/bin/bash
# Build and push multi-architecture Docker image to GitHub Container Registry AND Docker Hub
#
# Usage:
#   ./docker-publish.sh [version] [--parallel] [--ghcr-only] [--dockerhub-only]
#
# Examples:
#   ./docker-publish.sh 0.1.9b            # Sequential build, push to both registries
#   ./docker-publish.sh 0.1.9b --parallel # Build both archs simultaneously
#   ./docker-publish.sh 0.1.9b --ghcr-only    # Only push to GHCR
#   ./docker-publish.sh 0.1.9b --dockerhub-only # Only push to Docker Hub
#
# Note: Stable versions are also tagged as 'latest'. Beta versions (ending in 'b') are not.
#
# Prerequisites:
#   1. Log in to ghcr.io:
#      echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin
#
#   2. Log in to Docker Hub:
#      docker login -u YOUR_USERNAME
#
#   3. Create a GitHub Personal Access Token with 'write:packages' scope:
#      https://github.com/settings/tokens/new?scopes=write:packages
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

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
VERSION=""
PARALLEL=false
PUSH_GHCR=true
PUSH_DOCKERHUB=true
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
        *)
            if [ -z "$VERSION" ]; then
                VERSION="$arg"
            fi
            ;;
    esac
done

if [ -z "$VERSION" ]; then
    echo -e "${YELLOW}Usage: $0 <version> [--parallel] [--ghcr-only] [--dockerhub-only]${NC}"
    echo "Example: $0 0.1.9b"
    echo "         $0 0.1.9b --parallel     # Build both architectures simultaneously"
    echo "         $0 0.1.9b --ghcr-only    # Only push to GitHub Container Registry"
    echo "         $0 0.1.9b --dockerhub-only # Only push to Docker Hub"
    exit 1
fi

# Get CPU count
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Building multi-arch image${NC}"
echo -e "${GREEN}  Version: ${VERSION}${NC}"
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

# Only tag as 'latest' for stable releases (not beta versions ending in 'b')
TAG_LATEST=true
if [[ "$VERSION" =~ b[0-9]*$ ]]; then
    TAG_LATEST=false
    echo -e "${YELLOW}Beta version detected — skipping 'latest' tag${NC}"
fi

# Build tags for all target registries
TAGS=""
if [ "$PUSH_GHCR" = true ]; then
    TAGS="$TAGS -t ${GHCR_IMAGE}:${VERSION}"
    [ "$TAG_LATEST" = true ] && TAGS="$TAGS -t ${GHCR_IMAGE}:latest"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    TAGS="$TAGS -t ${DOCKERHUB_IMAGE}:${VERSION}"
    [ "$TAG_LATEST" = true ] && TAGS="$TAGS -t ${DOCKERHUB_IMAGE}:latest"
fi

echo -e "${BLUE}[3/4] Building and pushing...${NC}"

# Common build args (no cache to ensure clean builds)
BUILD_ARGS="--provenance=false --sbom=false --no-cache --pull"

if [ "$PARALLEL" = true ]; then
    # Parallel build: Build each architecture separately then combine manifests
    echo -e "${YELLOW}Building amd64 and arm64 in parallel (${CPU_COUNT} cores each, no cache)...${NC}"

    # Build per-arch staging tags for each target registry
    ARCH_TAGS_AMD64=""
    ARCH_TAGS_ARM64=""
    if [ "$PUSH_GHCR" = true ]; then
        ARCH_TAGS_AMD64="$ARCH_TAGS_AMD64 -t ${GHCR_IMAGE}:${VERSION}-amd64"
        ARCH_TAGS_ARM64="$ARCH_TAGS_ARM64 -t ${GHCR_IMAGE}:${VERSION}-arm64"
    fi
    if [ "$PUSH_DOCKERHUB" = true ]; then
        ARCH_TAGS_AMD64="$ARCH_TAGS_AMD64 -t ${DOCKERHUB_IMAGE}:${VERSION}-amd64"
        ARCH_TAGS_ARM64="$ARCH_TAGS_ARM64 -t ${DOCKERHUB_IMAGE}:${VERSION}-arm64"
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
        GHCR_MANIFEST_TAGS="-t ${GHCR_IMAGE}:${VERSION}"
        [ "$TAG_LATEST" = true ] && GHCR_MANIFEST_TAGS="$GHCR_MANIFEST_TAGS -t ${GHCR_IMAGE}:latest"
        docker buildx imagetools create \
            $GHCR_MANIFEST_TAGS \
            "${GHCR_IMAGE}:${VERSION}-amd64" \
            "${GHCR_IMAGE}:${VERSION}-arm64"
    fi
    if [ "$PUSH_DOCKERHUB" = true ]; then
        echo -e "${BLUE}  Creating Docker Hub manifest...${NC}"
        DH_MANIFEST_TAGS="-t ${DOCKERHUB_IMAGE}:${VERSION}"
        [ "$TAG_LATEST" = true ] && DH_MANIFEST_TAGS="$DH_MANIFEST_TAGS -t ${DOCKERHUB_IMAGE}:latest"
        docker buildx imagetools create \
            $DH_MANIFEST_TAGS \
            "${DOCKERHUB_IMAGE}:${VERSION}-amd64" \
            "${DOCKERHUB_IMAGE}:${VERSION}-arm64"
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

echo -e "${BLUE}[4/4] Verifying manifests...${NC}"
if [ "$PUSH_GHCR" = true ]; then
    echo -e "${BLUE}GHCR:${NC}"
    docker buildx imagetools inspect "${GHCR_IMAGE}:${VERSION}"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    echo -e "${BLUE}Docker Hub:${NC}"
    docker buildx imagetools inspect "${DOCKERHUB_IMAGE}:${VERSION}"
fi

echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Successfully pushed multi-arch image:${NC}"
echo -e "${GREEN}================================================${NC}"
if [ "$PUSH_GHCR" = true ]; then
    echo "  GHCR:"
    echo "    - ${GHCR_IMAGE}:${VERSION}"
    [ "$TAG_LATEST" = true ] && echo "    - ${GHCR_IMAGE}:latest"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    echo "  Docker Hub:"
    echo "    - ${DOCKERHUB_IMAGE}:${VERSION}"
    [ "$TAG_LATEST" = true ] && echo "    - ${DOCKERHUB_IMAGE}:latest"
fi
echo ""
echo -e "${BLUE}Supported platforms:${NC}"
echo "  - linux/amd64 (Intel/AMD servers, desktops)"
echo "  - linux/arm64 (Raspberry Pi 4/5, Apple Silicon)"
echo ""
echo -e "${GREEN}Users can now run:${NC}"
if [ "$PUSH_GHCR" = true ]; then
    echo "  docker pull ${GHCR_IMAGE}:${VERSION}"
fi
if [ "$PUSH_DOCKERHUB" = true ]; then
    echo "  docker pull ${DOCKERHUB_IMAGE}:${VERSION}"
    echo "  docker pull ${IMAGE_NAME}:${VERSION}  # shorthand"
fi
