"""
Test Suite: Account — Balance, positions, and wallet state.
Requires valid API credentials (tests authenticated endpoints).
"""

import time
import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config import sign_hmac


def run(config: dict) -> list[dict]:
    """Run all account diagnostic tests."""
    results = []

    api_key = config["api_key"]
    api_secret = config["api_secret"]
    base = config["rest_base"]

    if not api_key or not api_secret:
        return [_fail("Account: Pre-check", "No API credentials")]

    # ── Test: Account Info ───────────────────────────────────────────────────
    results.append(_test_account_info(base, api_key, api_secret))

    # ── Test: Positions ──────────────────────────────────────────────────────
    results.append(_test_positions(base, api_key, api_secret))

    # ── Test: Open Orders ────────────────────────────────────────────────────
    results.append(_test_open_orders(base, api_key, api_secret, "BTCUSDT"))

    return results


def _signed_get(base: str, endpoint: str, params: dict, api_key: str, api_secret: str) -> dict:
    """Helper: Make a signed GET request to Phemex."""
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    expiry = int(time.time()) + 60
    sign_string = f"{endpoint}{query_string}{expiry}"
    signature = sign_hmac(api_secret, sign_string)

    url = f"{base}{endpoint}" + (f"?{query_string}" if query_string else "")
    headers = {
        "x-phemex-access-token": api_key,
        "x-phemex-request-expiry": str(expiry),
        "x-phemex-request-signature": signature,
    }

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _test_account_info(base: str, api_key: str, api_secret: str) -> dict:
    name = "Account: Balance Info"
    try:
        data = _signed_get(base, "/g-accounts/accountPositions", {"currency": "USDT"}, api_key, api_secret)
        assert data.get("code") == 0, f"API error: {data.get('msg')}"

        account = data["data"]["account"]
        balance = float(account.get("accountBalanceRv", "0"))
        used = float(account.get("totalUsedBalanceRv", "0"))
        available = balance - used

        detail = f"total=${balance:.2f}, used=${used:.2f}, available=${available:.2f}"
        return _pass(name, detail)

    except Exception as e:
        return _fail(name, str(e))


def _test_positions(base: str, api_key: str, api_secret: str) -> dict:
    name = "Account: Positions"
    try:
        data = _signed_get(base, "/g-accounts/accountPositions", {"currency": "USDT"}, api_key, api_secret)
        assert data.get("code") == 0, f"API error: {data.get('msg')}"

        positions = data["data"].get("positions", [])

        # Filter to non-zero positions
        active = [
            p for p in positions
            if abs(float(p.get("size", "0"))) > 0
        ]

        if active:
            details = []
            for p in active[:3]:  # Show first 3
                sym = p.get("symbol", "?")
                side = p.get("side", "?")
                size = p.get("size", "0")
                entry = p.get("avgEntryPriceRp", "0")
                pnl = p.get("unrealisedPnlRv", "0")
                details.append(f"{sym} {side} {size} @ {entry} (PnL: {pnl})")
            return _pass(name, f"{len(active)} active: " + "; ".join(details))
        else:
            return _pass(name, f"No active positions ({len(positions)} total slots)")

    except Exception as e:
        return _fail(name, str(e))


def _test_open_orders(base: str, api_key: str, api_secret: str, symbol: str) -> dict:
    name = f"Account: Open Orders ({symbol})"
    try:
        data = _signed_get(base, "/g-orders/activeList", {"symbol": symbol}, api_key, api_secret)
        assert data.get("code") == 0, f"API error: {data.get('msg')}"

        rows = data.get("data", {}).get("rows", [])

        if rows:
            details = []
            for o in rows[:3]:
                oid = o.get("orderID", "?")[:8]
                side = o.get("side", "?")
                price = o.get("priceRp", o.get("priceEp", "?"))
                qty = o.get("orderQtyRq", o.get("orderQty", "?"))
                status = o.get("ordStatus", "?")
                details.append(f"{oid}.. {side} {qty}@{price} [{status}]")
            return _pass(name, f"{len(rows)} orders: " + "; ".join(details))
        else:
            return _pass(name, "No open orders")

    except Exception as e:
        return _fail(name, str(e))


# ── Result Helpers ───────────────────────────────────────────────────────────


def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "passed": True, "detail": detail}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "passed": False, "detail": reason}
