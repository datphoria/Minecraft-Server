#!/usr/bin/env bash
#
# Sync this repo to an IONOS VPS and restart the Minecraft stack.
# Run from Git Bash or WSL on Windows, or any Linux/macOS shell.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log()  { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die()  { log "ERROR: $*"; exit 1; }

# ---------------------------------------------------------------------------
# Configuration (.env is optional locally; required keys must be set)
# ---------------------------------------------------------------------------
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${VPS_HOST:?Set VPS_HOST in .env (copy from .env.example)}"
: "${VPS_USER:?Set VPS_USER in .env}"
: "${VPS_PATH:?Set VPS_PATH in .env}"

SSH_TARGET="${VPS_USER}@${VPS_HOST}"
RSYNC_DELETE="${DEPLOY_RSYNC_DELETE:-false}"

# ---------------------------------------------------------------------------
# Discord webhook (optional)
# ---------------------------------------------------------------------------
notify_discord() {
  local message="${1:-The Minecraft server is restarting for an update. Back shortly!}"

  if [[ -z "${DISCORD_WEBHOOK_URL:-}" ]]; then
    log "DISCORD_WEBHOOK_URL not set — skipping player notification."
    return 0
  fi

  log "Sending Discord notification..."
  local payload
  payload=$(printf '{"content":"%s"}' "${message//\"/\\\"}")

  if curl -sf -X POST \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "$DISCORD_WEBHOOK_URL" >/dev/null; then
    log "Discord notification sent."
  else
    log "WARNING: Discord webhook request failed (deploy continues)."
  fi
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
command -v rsync >/dev/null 2>&1 || die "rsync is not installed."
command -v ssh   >/dev/null 2>&1 || die "ssh is not installed."

log "Deploy target: ${SSH_TARGET}:${VPS_PATH}"

# ---------------------------------------------------------------------------
# 1. Alert players
# ---------------------------------------------------------------------------
notify_discord "${DISCORD_RESTART_MESSAGE:-🔧 Server is restarting for a mod/config update. Please save and log off — back in a few minutes!}"

# ---------------------------------------------------------------------------
# 2. Sync repository (never sync world data or secrets)
# ---------------------------------------------------------------------------
log "Syncing project files (excluding minecraft_data and .env)..."

RSYNC_OPTS=(-avz --human-readable)
if [[ "$RSYNC_DELETE" == "true" ]]; then
  RSYNC_OPTS+=(--delete)
  log "DEPLOY_RSYNC_DELETE=true — remote files not in repo will be removed."
fi

rsync "${RSYNC_OPTS[@]}" \
  --exclude '.git/' \
  --exclude 'minecraft_data/' \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '*.swp' \
  --exclude '.DS_Store' \
  ./ "${SSH_TARGET}:${VPS_PATH}/"

log "Rsync complete."

# ---------------------------------------------------------------------------
# 3. Remote: graceful stop → pull → up
# ---------------------------------------------------------------------------
log "Running remote Docker Compose (stop → pull → up -d)..."

ssh -o BatchMode=yes "$SSH_TARGET" bash -s -- "$VPS_PATH" <<'REMOTE'
set -euo pipefail
VPS_PATH="$1"
cd "$VPS_PATH"

if [[ ! -f compose.yaml ]]; then
  echo "ERROR: compose.yaml not found in ${VPS_PATH}" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found on VPS. Create it from .env.example (CF_API_KEY, RCON_PASSWORD, etc.)." >&2
  exit 1
fi

log_remote() { printf '[remote %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

log_remote "Stopping server (graceful — uses STOP_SERVER_ANNOUNCE_DELAY from compose)..."
docker compose stop

log_remote "Pulling latest image..."
docker compose pull

log_remote "Starting server..."
docker compose up -d

log_remote "Container status:"
docker compose ps

log_remote "Tail of startup logs (last 30 lines):"
docker compose logs --tail=30
REMOTE

log "Deploy finished successfully."
log "Follow logs: ssh ${SSH_TARGET} 'cd ${VPS_PATH} && docker compose logs -f'"
