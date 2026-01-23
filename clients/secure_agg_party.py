#!/usr/bin/env python3
import argparse
import ast
import json
import os
import random
import time
from typing import Dict, List, Tuple, Any

import requests

MODULUS = (2**61) - 1  # large prime-like Mersenne modulus for demo


def http_post(url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(url, headers=headers, json=body, timeout=20)
    r.raise_for_status()
    return r.json()


def http_get(url: str, headers: Dict[str, str], params: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_payload_field(msg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Server stores payload as a string (str(dict)) in message field "payload".
    Convert it back to dict safely.
    """
    payload_str = msg.get("payload", "")
    if not payload_str:
        return {}
    try:
        # payload looks like "{'type': 'mask', 'from': 'A', ...}"
        return ast.literal_eval(payload_str)
    except Exception:
        # fallback: maybe it's JSON string
        try:
            return json.loads(payload_str)
        except Exception:
            return {"raw": payload_str}


def poll_until(
    server: str,
    session_id: str,
    token: str,
    channel: str,
    want_predicate,
    timeout_s: int = 60,
    poll_interval: float = 0.5,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Poll a channel until want_predicate(messages) is True or timeout.
    Returns (last_id, messages_accumulated)
    """
    last_id = "0-0"
    headers = {"authorization": f"Bearer {token}"}
    started = time.time()
    acc: List[Dict[str, Any]] = []

    while True:
        data = http_get(
            f"{server}/sessions/{session_id}/poll",
            headers=headers,
            params={"channel": channel, "last_id": last_id},
        )
        last_id = data.get("last_id", last_id)
        msgs = data.get("messages", []) or []

        if msgs:
            acc.extend(msgs)

        if want_predicate(acc):
            return last_id, acc

        if time.time() - started > timeout_s:
            raise TimeoutError(f"Timeout waiting on channel={channel}")

        time.sleep(poll_interval)


def main():
    ap = argparse.ArgumentParser(description="Secure aggregation party client (3-party demo).")
    ap.add_argument("--server", required=True, help="Base URL, e.g. http://localhost:8000")
    ap.add_argument("--session", required=True, help="Session ID")
    ap.add_argument("--party", required=True, help="Party ID, e.g. A")
    ap.add_argument("--value", required=True, type=int, help="Private value v_i")
    ap.add_argument("--parties", required=True, help="Comma-separated party IDs, e.g. A,B,C")
    ap.add_argument("--timeout", type=int, default=60, help="Timeout seconds for each phase")
    args = ap.parse_args()

    server = args.server.rstrip("/")
    session_id = args.session
    party_id = args.party
    all_parties = [p.strip() for p in args.parties.split(",") if p.strip()]
    if party_id not in all_parties:
        raise SystemExit("Your --party must be included in --parties")

    others = [p for p in all_parties if p != party_id]
    v_i = args.value % MODULUS

    # 1) Join session -> party token
    join = http_post(
        f"{server}/sessions/{session_id}/join",
        headers={"content-type": "application/json"},
        body={"party_id": party_id},
    )
    token = join["party_token"]
    authz = {"authorization": f"Bearer {token}", "content-type": "application/json"}

    print(f"[{party_id}] joined session={session_id}; expected={join['expected_parties']}")

    # 2) Generate pairwise masks r_{i->j} and send them to recipients
    # Use cryptographic RNG in real systems. For demo, random.SystemRandom is fine.
    rng = random.SystemRandom()
    out_masks: Dict[str, int] = {}
    for j in others:
        out_masks[j] = rng.randrange(0, MODULUS)

        # Send mask to recipient-specific channel
        # Channel naming: "mask_to:<recipient>"
        http_post(
            f"{server}/sessions/{session_id}/send",
            headers=authz,
            body={
                "channel": f"mask_to:{j}",
                "payload": {"type": "mask", "from": party_id, "to": j, "mask": out_masks[j]},
            },
        )
    print(f"[{party_id}] sent masks to {others}")

    # 3) Receive inbound masks r_{j->i}
    want = len(others)

    def have_all_inbound(msgs: List[Dict[str, Any]]) -> bool:
        inbound = {}
        for m in msgs:
            p = parse_payload_field(m)
            if p.get("type") == "mask" and p.get("to") == party_id:
                inbound[p.get("from")] = int(p.get("mask"))
        return len(inbound) >= want

    _, inbound_msgs = poll_until(
        server=server,
        session_id=session_id,
        token=token,
        channel=f"mask_to:{party_id}",
        want_predicate=have_all_inbound,
        timeout_s=args.timeout,
    )

    in_masks: Dict[str, int] = {}
    for m in inbound_msgs:
        p = parse_payload_field(m)
        if p.get("type") == "mask" and p.get("to") == party_id:
            in_masks[p.get("from")] = int(p.get("mask"))

    # 4) Compute masked value m_i = v_i + sum_out - sum_in (mod M)
    sum_out = sum(out_masks.values()) % MODULUS
    sum_in = sum(in_masks.values()) % MODULUS
    m_i = (v_i + sum_out - sum_in) % MODULUS

    http_post(
        f"{server}/sessions/{session_id}/send",
        headers=authz,
        body={"channel": "masked", "payload": {"type": "masked", "from": party_id, "masked": m_i}},
    )
    print(f"[{party_id}] published masked value")

    # 5) Wait for all masked values; compute aggregate sum(v_i) mod M
    def have_all_masked(msgs: List[Dict[str, Any]]) -> bool:
        seen = set()
        for m in msgs:
            p = parse_payload_field(m)
            if p.get("type") == "masked":
                seen.add(p.get("from"))
        return len(seen) >= len(all_parties)

    _, masked_msgs = poll_until(
        server=server,
        session_id=session_id,
        token=token,
        channel="masked",
        want_predicate=have_all_masked,
        timeout_s=args.timeout,
    )

    masked_by_party: Dict[str, int] = {}
    for m in masked_msgs:
        p = parse_payload_field(m)
        if p.get("type") == "masked":
            masked_by_party[p.get("from")] = int(p.get("masked"))

    agg = sum(masked_by_party.values()) % MODULUS
    print(f"[{party_id}] aggregate(sum of private values) mod M = {agg}")
    print(f"[{party_id}] (for verification) my v_i={v_i}")


if __name__ == "__main__":
    main()

