#!/usr/bin/env bash
# First-time or daily dev start: ensure .env exists, then run Docker Compose.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  if [[ ! -f .env.example ]]; then
    echo "error: .env.example is missing" >&2
    exit 1
  fi
  cp .env.example .env
  echo "Created .env from .env.example — edit MONGO_URI if you use MongoDB Atlas (shared data on every PC)."
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "error: install Docker: https://docs.docker.com/get-docker/" >&2
  exit 1
fi

echo "Building and starting RotaShift (detached)..."
docker compose up -d --build

PORT=8000
if [[ -f .env ]] && grep -qE '^[[:space:]]*ROTASHIFT_PORT[[:space:]]*=' .env; then
  PORT="$(grep -E '^[[:space:]]*ROTASHIFT_PORT[[:space:]]*=' .env | head -1 | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//')"
fi

echo ""
echo "RotaShift is running."
echo "  App:    http://localhost:${PORT}"
echo "  Health: http://localhost:${PORT}/health/live"
echo "  Meta:   http://localhost:${PORT}/api/meta/registration"
echo ""
echo "Stop:  docker compose down"
echo "Logs:  docker compose logs -f api"
