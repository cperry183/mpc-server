#!/usr/bin/env python3
import argparse
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

MODULUS = (2**61) - 1  # large prime-like Mersenne modulus for demo
DEFAULT_TIMEOUT_S = 60
POLL_INTERVAL_S = 0.5


@dataclass(frozen=True)
class ClientConfig:
    server: str
    session_id: str
    party_id: str
    all_parties: List[str]
    value: int
    timeout_s: int = DEFAULT_TIMEOUT_S

    @property
    def others(self) -> List[str]:
        return [p for p in self.all_parties if p != self.party_id]


class ApiError(RuntimeError):
    pass


def http_post(url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(url, headers=headers, json=body, timeout=20)
    if not response.ok:
        raise ApiError(f"POST {url} failed: {response.status_code} {response.text}")
    return response.json()


def http_get(url: str, headers: Dict[str, str], params: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.get(url, headers=headers, params=params, timeout=20)
    if not response.ok:
        raise ApiError(f"GET {url} failed: {response.status_code} {response.text}")
    return response.json()


def parse_payload(msg: Dict[str, Any]) -> Dict[str, Any]:
    payload = msg.get("payload")
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"raw": payload}
    return {"raw": payload}


def poll_until(
    server: str,
    session_id: str,
    token: str,
    channel: str,
    want_predicate: Callable[[List[Dict[str, Any]]], bool],
    timeout_s: int,
    poll_interval: float = POLL_INTERVAL_S,
) -> Tuple[str, List[Dict[str, Any]]]:
    last_id = "0-0"
    headers = {"authorization": f"Bearer {token}"}
    started = time.time()
    accumulated: List[Dict[str, Any]] = []

    while True:
        data = http_get(
            f"{server}/sessions/{session_id}/poll",
            headers=headers,
            params={"channel": channel, "last_id": last_id},
        )
        last_id = data.get("last_id", last_id)
        msgs = data.get("messages", []) or []
        accumulated.extend(msgs)

        if want_predicate(accumulated):
            return last_id, accumulated

        if time.time() - started > timeout_s:
            raise TimeoutError(f"Timeout waiting on channel={channel}")

        time.sleep(poll_interval)


def join_session(server: str, session_id: str, party_id: str) -> Dict[str, Any]:
    return http_post(
        f"{server}/sessions/{session_id}/join",
        headers={"content-type": "application/json"},
        body={"party_id": party_id},
    )


def auth_headers(token: str) -> Dict[str, str]:
    return {"authorization": f"Bearer {token}", "content-type": "application/json"}


def send_message(
    server: str,
    session_id: str,
    token: str,
    channel: str,
    payload: Dict[str, Any],
    to_party: Optional[str] = None,
) -> None:
    body: Dict[str, Any] = {"channel": channel, "payload": payload}
    if to_party:
        body["to_party"] = to_party
    http_post(f"{server}/sessions/{session_id}/send", headers=auth_headers(token), body=body)


def collect_inbound_masks(
    config: ClientConfig,
    token: str,
) -> Dict[str, int]:
    want_count = len(config.others)

    def have_all_inbound(msgs: List[Dict[str, Any]]) -> bool:
        inbound: Dict[str, int] = {}
        for msg in msgs:
            payload = parse_payload(msg)
            if payload.get("type") == "mask" and payload.get("to") == config.party_id:
                inbound[payload.get("from")] = int(payload.get("mask"))
        return len(inbound) >= want_count

    _, inbound_msgs = poll_until(
        server=config.server,
        session_id=config.session_id,
        token=token,
        channel=f"mask_to:{config.party_id}",
        want_predicate=have_all_inbound,
        timeout_s=config.timeout_s,
    )

    inbound: Dict[str, int] = {}
    for msg in inbound_msgs:
        payload = parse_payload(msg)
        if payload.get("type") == "mask" and payload.get("to") == config.party_id:
            inbound[payload.get("from")] = int(payload.get("mask"))
    return inbound


def collect_masked_values(config: ClientConfig, token: str) -> Dict[str, int]:
    def have_all_masked(msgs: List[Dict[str, Any]]) -> bool:
        seen = set()
        for msg in msgs:
            payload = parse_payload(msg)
            if payload.get("type") == "masked":
                seen.add(payload.get("from"))
        return len(seen) >= len(config.all_parties)

    _, masked_msgs = poll_until(
        server=config.server,
        session_id=config.session_id,
        token=token,
        channel="masked",
        want_predicate=have_all_masked,
        timeout_s=config.timeout_s,
    )

    masked_by_party: Dict[str, int] = {}
    for msg in masked_msgs:
        payload = parse_payload(msg)
        if payload.get("type") == "masked":
            masked_by_party[payload.get("from")] = int(payload.get("masked"))
    return masked_by_party


def compute_masked_value(value: int, out_masks: Dict[str, int], in_masks: Dict[str, int]) -> int:
    sum_out = sum(out_masks.values()) % MODULUS
    sum_in = sum(in_masks.values()) % MODULUS
    return (value + sum_out - sum_in) % MODULUS


def send_outbound_masks(config: ClientConfig, token: str) -> Dict[str, int]:
    rng = random.SystemRandom()
    out_masks: Dict[str, int] = {}
    for party in config.others:
        out_masks[party] = rng.randrange(0, MODULUS)
        send_message(
            server=config.server,
            session_id=config.session_id,
            token=token,
            channel=f"mask_to:{party}",
            payload={"type": "mask", "from": config.party_id, "to": party, "mask": out_masks[party]},
            to_party=party,
        )
    return out_masks


def parse_args() -> ClientConfig:
    ap = argparse.ArgumentParser(description="Secure aggregation party client (demo).")
    ap.add_argument("--server", required=True, help="Base URL, e.g. http://localhost:8000")
    ap.add_argument("--session", required=True, help="Session ID")
    ap.add_argument("--party", required=True, help="Party ID, e.g. A")
    ap.add_argument("--value", required=True, type=int, help="Private value v_i")
    ap.add_argument("--parties", required=True, help="Comma-separated party IDs, e.g. A,B,C")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="Timeout seconds for each phase")
    args = ap.parse_args()

    all_parties = [p.strip() for p in args.parties.split(",") if p.strip()]
    if args.party not in all_parties:
        raise SystemExit("Your --party must be included in --parties")

    return ClientConfig(
        server=args.server.rstrip("/"),
        session_id=args.session,
        party_id=args.party,
        all_parties=all_parties,
        value=args.value % MODULUS,
        timeout_s=args.timeout,
    )


def run_client(config: ClientConfig) -> None:
    join = join_session(config.server, config.session_id, config.party_id)
    token = join["party_token"]

    print(f"[{config.party_id}] joined session={config.session_id}; expected={join['expected_parties']}")

    out_masks = send_outbound_masks(config, token)
    print(f"[{config.party_id}] sent masks to {config.others}")

    inbound_masks = collect_inbound_masks(config, token)
    masked_value = compute_masked_value(config.value, out_masks, inbound_masks)

    send_message(
        server=config.server,
        session_id=config.session_id,
        token=token,
        channel="masked",
        payload={"type": "masked", "from": config.party_id, "masked": masked_value},
    )
    print(f"[{config.party_id}] published masked value")

    masked_by_party = collect_masked_values(config, token)
    aggregate = sum(masked_by_party.values()) % MODULUS

    print(f"[{config.party_id}] aggregate(sum of private values) mod M = {aggregate}")
    print(f"[{config.party_id}] (for verification) my v_i={config.value}")


def main() -> None:
    config = parse_args()
    run_client(config)


if __name__ == "__main__":
    main()
