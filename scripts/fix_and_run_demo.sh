#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVER_URL="${SERVER_URL:-http://localhost:8000}"

echo "[*] Patching app/main.py to read Authorization header via FastAPI Header(...)"

MAIN="app/main.py"
if [[ ! -f "$MAIN" ]]; then
  echo "[!] $MAIN not found. Run this from the repo root."
  exit 1
fi

# 1) Ensure Header is imported
if ! grep -q "from fastapi .*Header" "$MAIN"; then
  # add Header to the fastapi import line if present; otherwise add a new import
  if grep -q "^from fastapi import " "$MAIN"; then
    # Append Header to existing import line if not already present
    perl -0777 -i -pe 's/^from fastapi import ([^\n]+)\n/from fastapi import $1, Header\n/m unless /from fastapi import .*Header/m' "$MAIN"
  else
    # Insert a new import near top (safe fallback)
    perl -0777 -i -pe 's/^/from fastapi import Header\n\n/' "$MAIN"
  fi
fi

# 2) Patch send_message authorization param to Header("")
#    Replace: authorization: str = ""
#    With:    authorization: str = Header("")
perl -0777 -i -pe 's/(\bdef\s+send_message\([\s\S]*?\bauthorization:\s*str\s*=\s*)""/\1Header("")/m' "$MAIN"

# 3) Patch poll authorization param similarly
perl -0777 -i -pe 's/(\bdef\s+poll\([\s\S]*?\bauthorization:\s*str\s*=\s*)""/\1Header("")/m' "$MAIN"

echo "[*] Rebuilding and restarting docker compose..."
docker compose down
docker compose up --build -d

echo "[*] Waiting for server readiness at $SERVER_URL/readyz ..."
for i in {1..30}; do
  if curl -fsS "$SERVER_URL/readyz" >/dev/null 2>&1; then
    echo "[*] Server is ready."
    break
  fi
  sleep 1
  if [[ $i -eq 30 ]]; then
    echo "[!] Server did not become ready in time. Check logs:"
    docker compose logs --tail=200 mpc-server || true
    exit 1
  fi
done

echo "[*] Ensuring local client deps (requests) via venv..."
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip -q install --upgrade pip >/dev/null
pip -q install requests >/dev/null

echo "[*] Running demo..."
chmod +x clients/run_demo.sh clients/secure_agg_party.py
SERVER="$SERVER_URL" ./clients/run_demo.sh

