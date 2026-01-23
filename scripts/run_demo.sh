#!/usr/bin/env bash
set -euo pipefail

SERVER="${SERVER:-http://localhost:8000}"
PARTIES="A,B,C"

# Choose 3 private values (edit as you like)
A_VAL="${A_VAL:-10}"
B_VAL="${B_VAL:-20}"
C_VAL="${C_VAL:-30}"

echo "[*] Creating session..."
SESSION_JSON=$(curl -s -X POST "$SERVER/sessions" \
  -H 'content-type: application/json' \
  -d '{"parties":3,"meta":{"demo":"secure-aggregation"}}')

SESSION_ID=$(python3 - <<PY
import json
print(json.loads('''$SESSION_JSON''')["session_id"])
PY
)

echo "[*] Session ID: $SESSION_ID"
echo "[*] Starting parties..."

python3 clients/secure_agg_party.py --server "$SERVER" --session "$SESSION_ID" --party A --value "$A_VAL" --parties "$PARTIES" &
PID_A=$!
python3 clients/secure_agg_party.py --server "$SERVER" --session "$SESSION_ID" --party B --value "$B_VAL" --parties "$PARTIES" &
PID_B=$!
python3 clients/secure_agg_party.py --server "$SERVER" --session "$SESSION_ID" --party C --value "$C_VAL" --parties "$PARTIES" &
PID_C=$!

wait $PID_A $PID_B $PID_C

echo "[*] Expected aggregate = $((A_VAL + B_VAL + C_VAL)) (mod M)"

