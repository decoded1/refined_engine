"""
Test Suite: Authentication — HMAC signing and authenticated endpoint access.
Tests that credentials + signing work correctly against the Phemex API.
"""

import time
import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config import sign_hmac


def run(config: dict) -> list[dict]:
    """Run all auth diagnostic tests."""
    results = []

    api_key = config["api_key"]
    api_secret = config["api_secret"]
    base = config["rest_base"]

    if not api_key or not api_secret:
        return [_fail("Auth: Credentials Check", "No API key/secret configured")]

    # ── Test: Credentials Present ────────────────────────────────────────────
    results.append(_test_credentials_present(api_key, api_secret))

    # ── Test: HMAC Signing ───────────────────────────────────────────────────
    results.append(_test_hmac_signing(api_secret))

    # ── Test: Authenticated GET (Account Positions) ──────────────────────────
    results.append(_test_auth_request(base, api_key, api_secret))

    return results


def _test_credentials_present(api_key: str, api_secret: str) -> dict:
    name = "Auth: Credentials Present"
    try:
        assert len(api_key) > 10, f"API key too short: {len(api_key)} chars"
        assert len(api_secret) > 20, f"API secret too short: {len(api_secret)} chars"
        return _pass(name, f"key={api_key[:6]}..., secret={len(api_secret)} chars")
    except Exception as e:
        return _fail(name, str(e))


def _test_hmac_signing(api_secret: str) -> dict:
    name = "Auth: HMAC-SHA256 Signing"
    try:
        test_msg = "test_message_12345"
        sig = sign_hmac(api_secret, test_msg)

        assert len(sig) == 64, f"Signature wrong length: {len(sig)} (expected 64 hex chars)"
        assert all(c in "0123456789abcdef" for c in sig), "Signature contains non-hex chars"

        # Deterministic check — same input should yield same output
        sig2 = sign_hmac(api_secret, test_msg)
        assert sig == sig2, "HMAC not deterministic"

        return _pass(name, f"sig={sig[:12]}...")
    except Exception as e:
        return _fail(name, str(e))


def _test_auth_request(base: str, api_key: str, api_secret: str) -> dict:
    name = "Auth: Authenticated GET"
    try:
        endpoint = "/g-accounts/accountPositions"
        params = {"currency": "USDT"}
        query_string = "&".join(f"{k}={v}" for k, v in params.items())

        expiry = int(time.time()) + 60
        sign_string = f"{endpoint}{query_string}{expiry}"
        signature = sign_hmac(api_secret, sign_string)

        url = f"{base}{endpoint}?{query_string}"
        headers = {
            "x-phemex-access-token": api_key,
            "x-phemex-request-expiry": str(expiry),
            "x-phemex-request-signature": signature,
        }

        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Phemex returns code 0 on success
        if data.get("code") != 0:
            return _fail(name, f"API error {data.get('code')}: {data.get('msg')}")

        # Verify we got account data
        assert "data" in data, "No 'data' in auth response"
        assert "account" in data["data"], "No 'account' in response data"

        bal = data["data"]["account"].get("accountBalanceRv", "0")
        return _pass(name, f"balance={bal}")

    except Exception as e:
        return _fail(name, str(e))


# ── Result Helpers ───────────────────────────────────────────────────────────


def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "passed": True, "detail": detail}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "passed": False, "detail": reason}
