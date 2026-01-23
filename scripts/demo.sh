#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_URL="${SERVER_URL:-http://localhost:8000}"
MAIN_FILE="app/main.py"

log() {
  printf '%s\n' "$1"
}

ensure_main_file() {
  if [[ ! -f "$ROOT_DIR/$MAIN_FILE" ]]; then
    log "[!] $MAIN_FILE not found. Run this from the repo root."
    exit 1
  fi
}

ensure_header_import() {
  local target="$ROOT_DIR/$MAIN_FILE"
  if ! grep -q "from fastapi .*Header" "$target"; then
    if grep -q "^from fastapi import " "$target"; then
      perl -0777 -i -pe 's/^from fastapi import ([^\n]+)\n/from fastapi import $1, Header\n/m unless /from fastapi import .*Header/m' "$target"
    else
      perl -0777 -i -pe 's/^/from fastapi import Header\n\n/' "$target"
    fi
  fi
}

patch_auth_headers() {
  local target="$ROOT_DIR/$MAIN_FILE"
  perl -0777 -i -pe 's/(\bdef\s+send_message\([\s\S]*?\bauthorization:\s*str\s*=\s*)""/\1Header("")/m' "$target"
  perl -0777 -i -pe 's/(\bdef\s+poll\([\s\S]*?\bauthorization:\s*str\s*=\s*)""/\1Header("")/m' "$target"
}

rebuild_compose() {
  log "[*] Rebuilding and restarting docker compose..."
  docker compose down
  docker compose up --build -d
}

wait_for_ready() {
  log "[*] Waiting for server readiness at $SERVER_URL/readyz ..."
  for i in {1..30}; do
    if curl -fsS "$SERVER_URL/readyz" >/dev/null 2>&1; then
      log "[*] Server is ready."
      return
    fi
    sleep 1
    if [[ $i -eq 30 ]]; then
      log "[!] Server did not become ready in time. Check logs:"
      docker compose logs --tail=200 mpc-server || true
      exit 1
    fi
  done
}

ensure_venv() {
  log "[*] Ensuring local client deps (requests) via venv..."
  if [[ ! -d "$ROOT_DIR/.venv" ]]; then
    python3 -m venv "$ROOT_DIR/.venv"
  fi
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
  pip -q install --upgrade pip >/dev/null
  pip -q install requests >/dev/null
}

run_demo() {
  log "[*] Running demo..."
  chmod +x "$ROOT_DIR/clients/run_demo.sh" "$ROOT_DIR/clients/secure_agg_party.py"
  SERVER="$SERVER_URL" "$ROOT_DIR/clients/run_demo.sh"
}

main() {
  log "[*] Patching app/main.py to read Authorization header via FastAPI Header(...)"
  ensure_main_file
  ensure_header_import
  patch_auth_headers
  rebuild_compose
  wait_for_ready
  ensure_venv
  run_demo
}

main "$@"
