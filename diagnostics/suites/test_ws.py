"""
Test Suite: WebSocket — Connection, authentication, and live data subscription.
Tests the Phemex WebSocket endpoint (wss://ws.phemex.com).
"""

import json
import time
import threading

try:
    import websocket  # websocket-client
    HAS_WS = True
except ImportError:
    HAS_WS = False

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config import sign_hmac


def run(config: dict) -> list[dict]:
    """Run all WebSocket diagnostic tests."""
    if not HAS_WS:
        return [_fail("WS: Dependency Check", "websocket-client not installed (pip install websocket-client)")]

    results = []

    # ── Test: Connection ─────────────────────────────────────────────────────
    results.append(_test_ws_connect(config["ws_url"]))

    # ── Test: Ping/Pong ──────────────────────────────────────────────────────
    results.append(_test_ws_ping(config["ws_url"]))

    # ── Test: Auth ───────────────────────────────────────────────────────────
    if config["api_key"] and config["api_secret"]:
        results.append(_test_ws_auth(config["ws_url"], config["api_key"], config["api_secret"]))
    else:
        results.append(_fail("WS: Authentication", "No credentials"))

    # ── Test: Kline Subscription ─────────────────────────────────────────────
    results.append(_test_ws_kline_sub(config["ws_url"], "BTCUSDT"))

    # ── Test: Ticker Subscription ────────────────────────────────────────────
    results.append(_test_ws_ticker_sub(config["ws_url"]))

    return results


def _test_ws_connect(ws_url: str) -> dict:
    name = "WS: Connection"
    try:
        ws = websocket.create_connection(ws_url, timeout=10)
        assert ws.connected, "WebSocket not connected"
        ws.close()
        return _pass(name, f"Connected to {ws_url}")
    except Exception as e:
        return _fail(name, str(e))


def _test_ws_ping(ws_url: str) -> dict:
    name = "WS: Ping/Pong"
    try:
        ws = websocket.create_connection(ws_url, timeout=10)

        ping = {"id": 0, "method": "server.ping", "params": []}
        ws.send(json.dumps(ping))

        # Wait for pong (with timeout)
        start = time.time()
        got_pong = False
        while time.time() - start < 5:
            data = ws.recv()
            msg = json.loads(data)
            if msg.get("result") == "pong":
                got_pong = True
                break

        ws.close()
        assert got_pong, "No pong received within 5 seconds"

        latency = (time.time() - start) * 1000
        return _pass(name, f"Pong received in {latency:.0f}ms")

    except Exception as e:
        return _fail(name, str(e))


def _test_ws_auth(ws_url: str, api_key: str, api_secret: str) -> dict:
    name = "WS: Authentication"
    try:
        ws = websocket.create_connection(ws_url, timeout=10)

        expiry = int(time.time()) + 60
        sign_string = f"{api_key}{expiry}"
        signature = sign_hmac(api_secret, sign_string)

        auth_msg = {
            "id": 99,
            "method": "user.auth",
            "params": ["API", api_key, signature, expiry],
        }
        ws.send(json.dumps(auth_msg))

        # Wait for auth response
        start = time.time()
        auth_ok = False
        error_msg = None

        while time.time() - start < 5:
            data = ws.recv()
            msg = json.loads(data)

            if msg.get("id") == 99:
                if msg.get("error"):
                    error_msg = f"Error {msg['error'].get('code')}: {msg['error'].get('message')}"
                    break
                if msg.get("result", {}).get("status") == "success":
                    auth_ok = True
                    break

        ws.close()

        if error_msg:
            return _fail(name, error_msg)

        assert auth_ok, "No auth success response received"
        return _pass(name, "Authenticated successfully")

    except Exception as e:
        return _fail(name, str(e))


def _test_ws_kline_sub(ws_url: str, symbol: str) -> dict:
    name = f"WS: Kline Subscription ({symbol})"
    try:
        ws = websocket.create_connection(ws_url, timeout=10)

        sub_msg = {
            "id": 101,
            "method": "kline_p.subscribe",
            "params": [symbol, 60],
        }
        ws.send(json.dumps(sub_msg))

        # Wait for kline data
        start = time.time()
        got_kline = False
        msg_count = 0

        while time.time() - start < 10:
            data = ws.recv()
            msg = json.loads(data)
            msg_count += 1

            # Check for kline data (includes snapshot + updates)
            if msg.get("kline_p") or msg.get("kline") or (
                isinstance(msg.get("method"), str) and "kline" in msg.get("method", "")
            ):
                got_kline = True
                break

            # Skip pong / subscription acks
            if msg.get("result") == "pong" or msg.get("id") == 101:
                continue

        ws.close()
        assert got_kline, f"No kline data after {msg_count} messages in 10s"
        return _pass(name, f"Kline data received ({msg_count} messages)")

    except Exception as e:
        return _fail(name, str(e))


def _test_ws_ticker_sub(ws_url: str) -> dict:
    name = "WS: Ticker Subscription"
    try:
        ws = websocket.create_connection(ws_url, timeout=10)

        sub_msg = {
            "id": 102,
            "method": "perp_market24h_pack_p.subscribe",
            "params": [],
        }
        ws.send(json.dumps(sub_msg))

        # Wait for ticker data
        start = time.time()
        got_ticker = False
        msg_count = 0

        while time.time() - start < 10:
            data = ws.recv()
            msg = json.loads(data)
            msg_count += 1

            if msg.get("method") == "perp_market24h_pack_p.update" or msg.get("fields"):
                got_ticker = True
                break

            if msg.get("id") == 102:
                continue

        ws.close()
        assert got_ticker, f"No ticker data after {msg_count} messages in 10s"
        return _pass(name, f"Ticker data received ({msg_count} messages)")

    except Exception as e:
        return _fail(name, str(e))


# ── Result Helpers ───────────────────────────────────────────────────────────


def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "passed": True, "detail": detail}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "passed": False, "detail": reason}
