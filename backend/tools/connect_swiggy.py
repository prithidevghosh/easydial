"""Local founder OAuth setup. Run in your terminal, never through an agent log.

Uses documented Swiggy DCR/PKCE. Does not modify carts or call checkout.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from easydial.providers.instamart import InstamartProvider

BASE = "https://mcp.swiggy.com"
CALLBACK = "http://localhost:8765/callback"
ENV = Path(__file__).resolve().parents[1] / ".env"


def save_settings(path: Path, updates: dict[str, str]):
    """Preserve unrelated settings; never print or put tokens in process arguments."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    result = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if key in remaining:
                result.append(f"{key}={json.dumps(remaining.pop(key), ensure_ascii=False)}")
        else:
            result.append(line)
    result.extend(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in remaining.items())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write("\n".join(result) + "\n")


def parse_callback(path: str, expected_state: str) -> str | None:
    parsed = urlparse(path)
    values = parse_qs(parsed.query)
    states, codes = values.get("state", []), values.get("code", [])
    if parsed.path != "/callback" or len(states) != 1 or len(codes) != 1:
        return None
    if not secrets.compare_digest(states[0], expected_state):
        return None
    return codes[0]


def authenticate() -> str:
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    code = None

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Callback URL contains a secret authorization code.

        def do_GET(self):
            nonlocal code
            received = parse_callback(self.path, state)
            self.send_response(200 if received else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(
                b"Return to your terminal to select an address." if received else b"Invalid callback."
            )
            if received:
                code = received

    with HTTPServer(("127.0.0.1", 8765), Callback) as server, httpx.Client(timeout=30) as client:
        server.timeout = 1
        registration = client.post(
            f"{BASE}/auth/register",
            json={
                "client_name": "EasyDial local development",
                "redirect_uris": [CALLBACK],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        registration.raise_for_status()
        client_id = registration.json()["client_id"]
        url = f"{BASE}/auth/authorize?" + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": CALLBACK,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": "mcp:tools",
            }
        )
        print("Opening Swiggy. Enter your phone/OTP only on Swiggy's page.")
        if not webbrowser.open(url):
            print("Browser could not open. Open this authorization link locally:")
            print(url)
        deadline = time.monotonic() + 600
        while code is None and time.monotonic() < deadline:
            server.handle_request()
        if code is None:
            raise RuntimeError("Login timed out. If callback is rejected, check localhost allowlisting.")
        response = client.post(
            f"{BASE}/auth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": CALLBACK,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


async def choose_address(token: str):
    provider = InstamartProvider(token, "")
    page = 1
    while True:
        data = await provider.call("get_addresses", {"page": page, "pageSize": 10})
        addresses = data.get("addresses", [])
        print("\nSaved delivery addresses (private: do not share this terminal output):")
        for number, address in enumerate(addresses, 1):
            print(f"{number}. {address['addressLine']}")
        more = data.get("pagination", {}).get("hasMore", False)
        choice = input("Choose address number" + (", n for next page" if more else "") + ", or q to quit: ")
        if choice == "q":
            return
        if choice == "n" and more:
            page += 1
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(addresses):
            print("Choose a listed address; add a saved address in Swiggy if none exist.")
            continue
        selected = addresses[int(choice) - 1]
        save_settings(
            ENV, {"SWIGGY_ADDRESS_ID": selected["id"], "LIVE_ADDRESS_LABEL": selected["addressLine"]}
        )
        print("Saved selected address to backend/.env. Commerce mode was not changed.")
        return


def main():
    try:
        token = authenticate()
        save_settings(ENV, {"SWIGGY_ACCESS_TOKEN": token})
        print("Access token saved to backend/.env without displaying it.")
        asyncio.run(choose_address(token))
        print(
            "Restart the backend after setting COMMERCE_MODE=live. Review syncs your live basket; Checkout stops at Pending."
        )
    except httpx.HTTPStatusError as error:
        print(f"Swiggy returned HTTP {error.response.status_code}. No response or token was logged.")
        print("Check official authentication documentation; rerun for expired sessions.")
        raise SystemExit(1) from None
    except (KeyboardInterrupt, Exception) as error:
        print(f"Setup stopped ({type(error).__name__}). Secrets were not displayed; retry locally.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
