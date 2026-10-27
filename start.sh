#!/usr/bin/env bash
# Starts WhyChain on macOS or Linux: the Zetaris platform (if ZETARIS_DIR is set in .env), the ERP database, the
# Meterless memory service and the web app, then warms them up. The same steps as start.cmd on Windows.
# Run from the project folder after the setup in the README:  ./start.sh   (Ctrl+C stops the bridge and the app)
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || { echo "No .venv found: run the Quick start in the README first."; exit 1; }

ZETARIS_DIR=$(grep -E '^ZETARIS_DIR=' .env 2>/dev/null | head -1 | cut -d= -f2- || true)
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if [ -n "${ZETARIS_DIR:-}" ] && [ -d "$ZETARIS_DIR" ]; then
    echo "Starting Zetaris from $ZETARIS_DIR ..."
    (cd "$ZETARIS_DIR" && docker compose up -d)
  else
    echo "ZETARIS_DIR is not set in .env: Zetaris not started (WhyChain uses its local harness)."
  fi
  echo "Starting the ERP database..."
  docker compose up -d
else
  echo "Docker is not running: the order system is read from its CSV copy and queries use the local harness."
fi

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

if [ -d meterless_bridge/node_modules ]; then
  echo "Starting the Meterless memory service..."
  (cd meterless_bridge && npm start >../meterless_bridge.log 2>&1) &
  pids+=($!)
else
  echo "meterless_bridge/node_modules is missing (cd meterless_bridge && npm ci): memory falls back to a local file."
fi

echo "Starting the web app..."
"$PY" -m whychain.api >whychain_api.log 2>&1 &
pids+=($!)

echo "Warming up (a cold Zetaris takes a few minutes)..."
"$PY" scripts/warmup.py || true
echo "WhyChain is running at http://localhost:8000  (logs: whychain_api.log, meterless_bridge.log; Ctrl+C to stop)"
wait
