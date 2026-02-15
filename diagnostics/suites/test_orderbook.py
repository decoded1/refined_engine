"""
Test Suite: Orderbook — Validates orderbook structure, pricing, and depth.
"""

import requests


def run(config: dict) -> list[dict]:
    """Run all orderbook diagnostic tests."""
    results = []
    base = config["rest_base"]

    results.append(_test_orderbook_structure(base, "BTCUSDT"))
    results.append(_test_orderbook_depth(base, "BTCUSDT"))
    results.append(_test_orderbook_pricing(base, "BTCUSDT"))

    return results


def _fetch_orderbook(base: str, symbol: str) -> dict:
    url = f"{base}/md/v2/orderbook?symbol={symbol}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    assert "result" in data, "Missing 'result' in orderbook"
    return data["result"]


def _test_orderbook_structure(base: str, symbol: str) -> dict:
    name = f"Orderbook: Structure ({symbol})"
    try:
        result = _fetch_orderbook(base, symbol)

        assert "orderbook_p" in result, "Missing 'orderbook_p'"
        book = result["orderbook_p"]

        assert "asks" in book, "Missing 'asks'"
        assert "bids" in book, "Missing 'bids'"
        assert isinstance(book["asks"], list), "'asks' is not a list"
        assert isinstance(book["bids"], list), "'bids' is not a list"

        # Each level should be [price, size]
        if book["asks"]:
            assert len(book["asks"][0]) >= 2, f"Ask level format wrong: {book['asks'][0]}"
        if book["bids"]:
            assert len(book["bids"][0]) >= 2, f"Bid level format wrong: {book['bids'][0]}"

        return _pass(name, f"asks={len(book['asks'])}, bids={len(book['bids'])}")

    except Exception as e:
        return _fail(name, str(e))


def _test_orderbook_depth(base: str, symbol: str) -> dict:
    name = f"Orderbook: Depth ({symbol})"
    try:
        result = _fetch_orderbook(base, symbol)
        book = result["orderbook_p"]

        asks = book.get("asks", [])
        bids = book.get("bids", [])

        assert len(asks) >= 5, f"Shallow ask-side: only {len(asks)} levels"
        assert len(bids) >= 5, f"Shallow bid-side: only {len(bids)} levels"

        # Calculate total depth
        ask_depth = sum(float(a[1]) for a in asks)
        bid_depth = sum(float(b[1]) for b in bids)

        return _pass(
            name,
            f"ask_depth={ask_depth:.4f}, bid_depth={bid_depth:.4f}, "
            f"levels: {len(asks)}a/{len(bids)}b"
        )

    except Exception as e:
        return _fail(name, str(e))


def _test_orderbook_pricing(base: str, symbol: str) -> dict:
    name = f"Orderbook: Pricing ({symbol})"
    try:
        result = _fetch_orderbook(base, symbol)
        book = result["orderbook_p"]

        asks = book.get("asks", [])
        bids = book.get("bids", [])

        assert len(asks) > 0 and len(bids) > 0, "Empty orderbook"

        best_ask = float(asks[0][0])
        best_bid = float(bids[0][0])
        spread = best_ask - best_bid
        spread_pct = (spread / best_bid) * 100 if best_bid > 0 else 0

        # Sanity: ask > bid
        assert best_ask > best_bid, f"Ask ({best_ask}) <= Bid ({best_bid})"

        # Sanity: spread should be reasonable (< 1% for BTC)
        assert spread_pct < 1.0, f"Spread too wide: {spread_pct:.4f}%"

        # Asks should be ascending
        for i in range(1, min(5, len(asks))):
            assert float(asks[i][0]) >= float(asks[i - 1][0]), \
                f"Asks not ascending at level {i}"

        # Bids should be descending
        for i in range(1, min(5, len(bids))):
            assert float(bids[i][0]) <= float(bids[i - 1][0]), \
                f"Bids not descending at level {i}"

        return _pass(
            name,
            f"best_ask=${best_ask:,.2f}, best_bid=${best_bid:,.2f}, "
            f"spread=${spread:.2f} ({spread_pct:.4f}%)"
        )

    except Exception as e:
        return _fail(name, str(e))


# ── Result Helpers ───────────────────────────────────────────────────────────


def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "passed": True, "detail": detail}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "passed": False, "detail": reason}
