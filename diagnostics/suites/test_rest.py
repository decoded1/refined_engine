"""
Test Suite: REST API — Products, Candles (raw), Ticker, Orderbook.
Tests public endpoints that don't require authentication.
"""

import time
import requests
from ..fixtures.expected_schemas import (
    PRODUCTS_RESPONSE_SCHEMA,
    PRODUCT_FIELDS,
    KLINE_RESPONSE_SCHEMA,
    TICKER_RESPONSE_SCHEMA,
    ORDERBOOK_RESPONSE_SCHEMA,
)


def run(config: dict) -> list[dict]:
    """Run all REST diagnostic tests. Returns list of result dicts."""
    results = []
    base = config["rest_base"]

    # ── Test: Fetch Products ─────────────────────────────────────────────────
    results.append(_test_products(base))

    # ── Test: Fetch Ticker ───────────────────────────────────────────────────
    results.append(_test_ticker(base, "BTCUSDT"))

    # ── Test: Fetch Kline/Last (recent candles) ──────────────────────────────
    results.append(_test_kline_last(base, "BTCUSDT"))

    # ── Test: Fetch Kline/List (historical pagination) ───────────────────────
    results.append(_test_kline_list(base, "BTCUSDT"))

    # ── Test: Fetch Orderbook ────────────────────────────────────────────────
    results.append(_test_orderbook(base, "BTCUSDT"))

    return results


def _test_products(base: str) -> dict:
    name = "REST: Fetch Products"
    try:
        url = f"{base}/public/products"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Validate schema
        for key in PRODUCTS_RESPONSE_SCHEMA["required_keys"]:
            assert key in data, f"Missing top-level key: {key}"

        assert data["code"] == 0, f"API error code: {data.get('code')}, msg: {data.get('msg')}"

        inner = data["data"]
        has_products = any(
            k in inner for k in PRODUCTS_RESPONSE_SCHEMA["data_has_one_of"]
        )
        assert has_products, "No products or perpProductsV2 in response"

        # Get the perpetual list
        perp_list = inner.get("perpProductsV2") or inner.get("products") or []
        listed = [p for p in perp_list if p.get("status") == "Listed"]

        # Spot-check a few fields
        if listed:
            sample = listed[0]
            for field in PRODUCT_FIELDS:
                assert field in sample, f"Product missing field: {field}"

        return _pass(name, f"{len(listed)} listed perpetuals")

    except Exception as e:
        return _fail(name, str(e))


def _test_ticker(base: str, symbol: str) -> dict:
    name = f"REST: Fetch Ticker ({symbol})"
    try:
        url = f"{base}/md/v3/ticker/24hr?symbol={symbol}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        assert "result" in data, "Missing 'result' key in ticker response"
        result = data["result"]

        # Check we got at least some price data (field names vary)
        flex = TICKER_RESPONSE_SCHEMA["flexible_names"]
        found_fields = {}
        for canonical, variants in flex.items():
            for v in variants:
                if v in result:
                    found_fields[canonical] = result[v]
                    break

        assert "lastPrice" in found_fields, "No lastPrice variant found"

        detail = f"last={found_fields.get('lastPrice')}, mark={found_fields.get('markPrice', 'N/A')}"
        return _pass(name, detail)

    except Exception as e:
        return _fail(name, str(e))


def _test_kline_last(base: str, symbol: str) -> dict:
    name = f"REST: Fetch Kline/Last ({symbol} 1H)"
    try:
        now = int(time.time())
        url = (
            f"{base}/exchange/public/md/v2/kline/last"
            f"?symbol={symbol}&to={now}&resolution=3600&limit=100"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for key in KLINE_RESPONSE_SCHEMA["required_keys"]:
            assert key in data, f"Missing top-level key: {key}"

        assert data["code"] == 0, f"Kline error: {data.get('msg')}"
        rows = data.get("data", {}).get("rows", [])
        assert len(rows) > 0, "No candle rows returned"

        # Validate row structure
        sample = rows[0]
        assert (
            len(sample) >= KLINE_RESPONSE_SCHEMA["row_min_length"]
        ), f"Row too short: {len(sample)} fields, expected >={KLINE_RESPONSE_SCHEMA['row_min_length']}"

        # Check timestamps are sane (not 0, not future by >1 day)
        ts = int(sample[0])
        if ts > 2_000_000_000:
            ts = ts // 1000  # ms→s
        assert ts > 1_600_000_000, f"Timestamp too old: {ts}"
        assert ts < now + 86400, f"Timestamp in far future: {ts}"

        return _pass(name, f"{len(rows)} candles, latest ts={rows[-1][0]}")

    except Exception as e:
        return _fail(name, str(e))


def _test_kline_list(base: str, symbol: str) -> dict:
    name = f"REST: Fetch Kline/List ({symbol} historical)"
    try:
        now = int(time.time())
        _from = now - 3600 * 200  # ~200 hours back
        url = (
            f"{base}/exchange/public/md/v2/kline/list"
            f"?symbol={symbol}&from={_from}&to={now}&resolution=3600"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        assert data.get("code") == 0, f"Kline/list error: {data.get('msg')}"
        rows = data.get("data", {}).get("rows", [])
        assert len(rows) > 0, "No historical candle rows"

        return _pass(name, f"{len(rows)} historical candles")

    except Exception as e:
        return _fail(name, str(e))


def _test_orderbook(base: str, symbol: str) -> dict:
    name = f"REST: Fetch Orderbook ({symbol})"
    try:
        url = f"{base}/md/v2/orderbook?symbol={symbol}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        assert "result" in data, "Missing 'result' in orderbook response"
        result = data["result"]

        book_key = "orderbook_p"
        assert book_key in result, f"Missing '{book_key}' in result"

        book = result[book_key]
        asks = book.get("asks", [])
        bids = book.get("bids", [])

        assert len(asks) > 0, "No asks in orderbook"
        assert len(bids) > 0, "No bids in orderbook"

        # Validate ask/bid structure
        assert len(asks[0]) >= 2, f"Ask level too short: {asks[0]}"
        assert len(bids[0]) >= 2, f"Bid level too short: {bids[0]}"

        top_ask = float(asks[0][0])
        top_bid = float(bids[0][0])
        assert top_ask > top_bid, f"Ask ({top_ask}) not above bid ({top_bid})"

        return _pass(name, f"asks={len(asks)}, bids={len(bids)}, spread=${top_ask - top_bid:.2f}")

    except Exception as e:
        return _fail(name, str(e))


# ── Result Helpers ───────────────────────────────────────────────────────────


def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "passed": True, "detail": detail}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "passed": False, "detail": reason}
