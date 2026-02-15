"""
Config — Loads .env credentials and exposes API endpoints.
Mirrors engine/config.ts logic for Python diagnostics.
"""

import os
import hmac
import hashlib
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env from same directory ────────────────────────────────────────────

_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)

# ── Credential Resolution ────────────────────────────────────────────────────

TESTNET_KEY = os.getenv("PHEMEX_TESTNET_KEY", "").strip()
TESTNET_SECRET = os.getenv("PHEMEX_TESTNET_SECRET", "").strip()
MAINNET_KEY = os.getenv("PHEMEX_MAINNET_KEY", "").strip()
MAINNET_SECRET = os.getenv("PHEMEX_MAINNET_SECRET", "").strip()

_has_testnet = bool(TESTNET_KEY and TESTNET_SECRET)
_has_mainnet = bool(MAINNET_KEY and MAINNET_SECRET)

IS_TESTNET = _has_testnet and not _has_mainnet

API_KEY = (TESTNET_KEY if IS_TESTNET else MAINNET_KEY).strip()
API_SECRET = (TESTNET_SECRET if IS_TESTNET else MAINNET_SECRET).strip()

# ── Endpoints ────────────────────────────────────────────────────────────────

REST_BASE = (
    "https://testnet-api.phemex.com" if IS_TESTNET else "https://api.phemex.com"
)
WS_URL = (
    "wss://testnet-api.phemex.com/ws" if IS_TESTNET else "wss://ws.phemex.com"
)
NETWORK = "TESTNET" if IS_TESTNET else "MAINNET"

EXCHANGE = {
    "rest": REST_BASE,
    "ws": WS_URL,
    "kline_method": "kline_p",
    "ticker_method": "perp_market24h_pack_p",
    "is_testnet": IS_TESTNET,
}

# ── HMAC Signing ─────────────────────────────────────────────────────────────


def sign_hmac(secret: str, message: str) -> str:
    """HMAC-SHA256 signature (hex). Same as UniversalHmac.ts."""
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ── Validation ───────────────────────────────────────────────────────────────


def validate_credentials() -> bool:
    if not API_KEY or not API_SECRET:
        print("[Config] ⚠ No API Credentials. Execution tests will fail.")
        return False
    return True


def print_config():
    masked = lambda s: s[:6] + "..." + s[-4:] if len(s) > 10 else s
    print()
    print(f"  ┌─ Refined Engine Config ──────────────────────┐")
    print(f"  │  Network:   {NETWORK:<34}│")
    print(f"  │  REST:      {REST_BASE:<34}│")
    print(f"  │  WS:        {WS_URL:<34}│")
    print(f"  │  API Key:   {masked(API_KEY):<34}│")
    print(f"  └───────────────────────────────────────────────┘")
    print()
