# Build frontend
FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /app/frontend

# Copy package files first for better caching
COPY frontend/package*.json ./

# Use cache mount for npm
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./
RUN npm run build

# Production image
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    iproute2 \
    libcap2-bin \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Allow binding to privileged ports (e.g. 990/FTPS) as non-root user.
# File capabilities are more reliable than Docker cap_add with user: directive,
# which depends on ambient capability support in the container runtime.
RUN setcap cap_net_bind_service=+ep "$(readlink -f /usr/local/bin/python3)"

# Install Python dependencies with cache mount
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --root-user-action=ignore -r requirements.txt

# Copy backend
COPY backend/ ./backend/

# Capture the current git branch at build time. `.git/HEAD` is the only
# .git metadata the build context lets through (see .dockerignore); it
# contains `ref: refs/heads/<branch>`, which the SpoolBuddy remote-update
# flow reads at runtime via detect_current_branch() in spoolbuddy_ssh.py.
# Without this, the production image has no git metadata at all and would
# always pull `main` on the remote device regardless of which branch
# Bambuddy itself was built from.
COPY .git/HEAD ./.git/HEAD

# Copy built frontend from builder stage
COPY --from=frontend-builder /app/static ./static

# Create data directory for persistent storage
# chmod 777 allows running as non-root user (e.g., with docker compose user: directive)
RUN mkdir -p /app/data /app/logs && chmod 777 /app/data /app/logs

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV LOG_DIR=/app/logs
ENV PORT=8000
# Provide a local username + home for tools that call getpass.getuser() /
# os.path.expanduser() under arbitrary PUIDs. With `user: "1001:1001"` the
# stock python:3.13-slim image has no /etc/passwd entry for that UID, so
# pwd.getpwuid() raises and breaks libraries that do host-level user lookups
# (notably asyncssh, which uses the local username for ~/.ssh/config host
# matching during the SpoolBuddy remote-update flow). Setting LOGNAME/USER
# makes getpass.getuser() resolve via env vars instead of the passwd db;
# HOME=/app gives a writable home that is guaranteed to exist.
ENV HOME=/app
ENV USER=bambuddy
ENV LOGNAME=bambuddy

EXPOSE 322
EXPOSE 990
EXPOSE 3000
EXPOSE 3002
EXPOSE 6000
EXPOSE 8000
EXPOSE 8883
EXPOSE 50000-50100

# Health check (uses PORT env var via shell)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"8000\")}/health')" || exit 1

# Run the application
# Use standard asyncio loop (uvloop has permission issues in some Docker environments)
# Port is configurable via PORT environment variable (default: 8000)
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000} --loop asyncio"]
