"""
Test Suite: Candles — Validates kline data quality and parsing.
Checks timestamps, OHLCV values, ordering, and gap detection.
"""

import time
import requests


def run(config: dict) -> list[dict]:
    """Run all candle diagnostic tests."""
    results = []
    base = config["rest_base"]

    results.append(_test_candle_fetch(base, "BTCUSDT", 3600, 100))
    results.append(_test_candle_ordering(base, "BTCUSDT", 3600, 100))
    results.append(_test_candle_ohlcv_sanity(base, "BTCUSDT", 3600, 50))
    results.append(_test_candle_multi_timeframe(base, "BTCUSDT"))
    results.append(_test_candle_gap_detection(base, "BTCUSDT", 3600, 200))

    return results


def _fetch_candles(base: str, symbol: str, resolution: int, limit: int) -> list:
    """Fetch candles from /kline/last. Returns list of rows."""
    now = int(time.time())
    url = (
        f"{base}/exchange/public/md/v2/kline/last"
        f"?symbol={symbol}&to={now}&resolution={resolution}&limit={limit}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    assert data.get("code") == 0, f"Kline error: {data.get('msg')}"
    rows = data.get("data", {}).get("rows", [])
    return rows


def _test_candle_fetch(base: str, symbol: str, resolution: int, limit: int) -> dict:
    name = f"Candles: Fetch {symbol} {resolution // 60}m x{limit}"
    try:
        rows = _fetch_candles(base, symbol, resolution, limit)
        assert len(rows) > 0, "No candle rows returned"
        assert len(rows) >= min(limit, 10), f"Too few candles: {len(rows)} (requested {limit})"

        return _pass(name, f"{len(rows)} candles returned")

    except Exception as e:
        return _fail(name, str(e))


def _test_candle_ordering(base: str, symbol: str, resolution: int, limit: int) -> dict:
    name = f"Candles: Time Ordering ({symbol})"
    try:
        rows = _fetch_candles(base, symbol, resolution, limit)
        assert len(rows) > 1, "Need >1 candles to check ordering"

        timestamps = []
        for row in rows:
            ts = int(row[0])
            if ts > 2_000_000_000:
                ts = ts // 1000
            timestamps.append(ts)

        # Check ascending or descending
        is_ascending = all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))
        is_descending = all(timestamps[i] >= timestamps[i + 1] for i in range(len(timestamps) - 1))

        assert is_ascending or is_descending, "Timestamps are neither ascending nor descending"
        order = "ascending" if is_ascending else "descending"

        # No duplicates
        unique = set(timestamps)
        assert len(unique) == len(timestamps), f"{len(timestamps) - len(unique)} duplicate timestamps"

        return _pass(name, f"{order}, {len(timestamps)} unique timestamps")

    except Exception as e:
        return _fail(name, str(e))


def _test_candle_ohlcv_sanity(base: str, symbol: str, resolution: int, limit: int) -> dict:
    name = f"Candles: OHLCV Sanity ({symbol})"
    try:
        rows = _fetch_candles(base, symbol, resolution, limit)
        issues = []

        for i, row in enumerate(rows):
            # row: [timestamp, interval, last_close, open, high, low, close, volume, ...]
            o = float(row[3])
            h = float(row[4])
            l = float(row[5])
            c = float(row[6])
            v = float(row[7]) if len(row) > 7 else 0

            if h < l:
                issues.append(f"Row {i}: high ({h}) < low ({l})")
            if o <= 0 or c <= 0:
                issues.append(f"Row {i}: zero price (O={o}, C={c})")
            if h < o or h < c:
                issues.append(f"Row {i}: high ({h}) < open ({o}) or close ({c})")
            if l > o or l > c:
                issues.append(f"Row {i}: low ({l}) > open ({o}) or close ({c})")
            if v < 0:
                issues.append(f"Row {i}: negative volume ({v})")

        if issues:
            return _fail(name, f"{len(issues)} issues: " + "; ".join(issues[:3]))

        return _pass(name, f"{len(rows)} candles all valid OHLCV")

    except Exception as e:
        return _fail(name, str(e))


def _test_candle_multi_timeframe(base: str, symbol: str) -> dict:
    name = f"Candles: Multi-Timeframe ({symbol})"
    try:
        timeframes = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }

        fetched = {}
        for label, resolution in timeframes.items():
            rows = _fetch_candles(base, symbol, resolution, 10)
            fetched[label] = len(rows)
            time.sleep(0.1)  # Polite delay

        success = [f"{k}:{v}" for k, v in fetched.items() if v > 0]
        failed = [k for k, v in fetched.items() if v == 0]

        if failed:
            return _fail(name, f"Failed timeframes: {', '.join(failed)}")

        return _pass(name, ", ".join(success))

    except Exception as e:
        return _fail(name, str(e))


def _test_candle_gap_detection(base: str, symbol: str, resolution: int, limit: int) -> dict:
    name = f"Candles: Gap Detection ({symbol} {resolution // 60}m)"
    try:
        # Use /kline/list with from+to for larger fetches (avoids limit caps)
        now = int(time.time())
        _from = now - (limit * resolution)
        url = (
            f"{base}/exchange/public/md/v2/kline/list"
            f"?symbol={symbol}&from={_from}&to={now}&resolution={resolution}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            return _fail(name, f"Kline/list error: {data.get('msg')}")

        rows = data.get("data", {}).get("rows", [])
        if len(rows) < 2:
            return _pass(name, "Not enough candles to check gaps")

        timestamps = []
        for row in rows:
            ts = int(row[0])
            if ts > 2_000_000_000:
                ts = ts // 1000
            timestamps.append(ts)

        timestamps.sort()
        gaps = []
        for i in range(1, len(timestamps)):
            diff = timestamps[i] - timestamps[i - 1]
            if diff > resolution * 1.5:  # Allow 1.5x tolerance
                gap_hours = diff / 3600
                gaps.append(f"{gap_hours:.1f}h gap at {timestamps[i-1]}")

        if gaps:
            return _pass(name, f"⚠ {len(gaps)} gaps found: " + "; ".join(gaps[:3]))
        else:
            return _pass(name, f"No gaps in {len(timestamps)} candles")

    except Exception as e:
        return _fail(name, str(e))


# ── Result Helpers ───────────────────────────────────────────────────────────


def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "passed": True, "detail": detail}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "passed": False, "detail": reason}
