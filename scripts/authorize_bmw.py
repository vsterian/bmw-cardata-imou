"""Complete BMW CarData device authorization and write token JSON."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

import requests

DEVICE_CODE_URL = "https://customer.bmwgroup.com/gcdm/oauth/device/code"
TOKEN_URL = "https://customer.bmwgroup.com/gcdm/oauth/token"
DEFAULT_SCOPE = "authenticate_user openid cardata:api:read cardata:streaming:read"


def code_verifier() -> str:
    while True:
        value = secrets.token_urlsafe(86)
        if 43 <= len(value) <= 128:
            return value


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--output", default="data/bmw-tokens.json")
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    args = parser.parse_args()

    verifier = code_verifier()
    response = requests.post(
        DEVICE_CODE_URL,
        data={
            "client_id": args.client_id,
            "response_type": "device_code",
            "scope": args.scope,
            "code_challenge": challenge(verifier),
            "code_challenge_method": "S256",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    device = response.json()
    print("Open:", device.get("verification_uri_complete") or device.get("verification_uri"))
    print("User code:", device.get("user_code"))
    print("Approve BMW device authorization. Waiting...")

    interval = int(device.get("interval", 5))
    deadline = time.monotonic() + int(device.get("expires_in", 900))
    while time.monotonic() < deadline:
        time.sleep(interval)
        token_response = requests.post(
            TOKEN_URL,
            data={
                "client_id": args.client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device["device_code"],
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        payload: dict[str, Any] = token_response.json()
        if token_response.status_code == 200:
            payload["client_id"] = args.client_id
            payload["received_at"] = time.time()
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            output.chmod(0o600)
            print(f"Tokens saved to {output}")
            print("Copy client_id, gcid, refresh_token into .env.")
            return
        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise SystemExit(f"BMW authorization failed: {error or token_response.status_code}")
    raise SystemExit("BMW device authorization expired")


if __name__ == "__main__":
    main()
