"""
Test Suite: Ticker — 24h ticker data validation.
Tests the /md/v3/ticker/24hr endpoint and validates data integrity.
"""

import requests


def run(config: dict) -> list[dict]:
    """Run all ticker diagnostic tests."""
    results = []
    base = config["rest_base"]
    is_testnet = config.get("is_testnet", False)

    results.append(_test_ticker_btc(base, is_testnet))
    results.append(_test_ticker_eth(base, is_testnet))
    results.append(_test_ticker_data_integrity(base, is_testnet))

    return results


def _test_ticker_btc(base: str, is_testnet: bool = False) -> dict:
    name = "Ticker: BTCUSDT"
    try:
        data = _fetch_ticker(base, "BTCUSDT")
        result = data["result"]

        last = _find_price(result, ["lastRp", "last", "lastPrice", "closeRp"])
        mark = _find_price(result, ["markRp", "markPrice", "markPriceRp"])
        high = _find_price(result, ["highRp", "high", "highPriceRp"])
        low = _find_price(result, ["lowRp", "low", "lowPriceRp"])

        # On testnet, lastPrice is often 0 (no active trading)
        # Use markPrice as a proxy for "the API is working"
        if is_testnet:
            assert mark > 0, "Mark price is 0 on testnet (exchange may be down)"
            if last == 0:
                detail = f"last=0 (testnet, normal), mark=${mark:,.2f}"
            else:
                detail = f"last=${last:,.2f}, mark=${mark:,.2f}, range=${low:,.2f}-{high:,.2f}"
        else:
            assert last > 0, "Last price is 0 or negative"
            assert mark > 0, "Mark price is 0 or negative"
            detail = f"last=${last:,.2f}, mark=${mark:,.2f}, range=${low:,.2f}-{high:,.2f}"

        return _pass(name, detail)

    except Exception as e:
        return _fail(name, str(e))


def _test_ticker_eth(base: str, is_testnet: bool = False) -> dict:
    name = "Ticker: ETHUSDT"
    try:
        data = _fetch_ticker(base, "ETHUSDT")
        result = data["result"]

        last = _find_price(result, ["lastRp", "last", "lastPrice", "closeRp"])
        mark = _find_price(result, ["markRp", "markPrice", "markPriceRp"])

        if is_testnet:
            assert mark > 0, "Mark price is 0 on testnet"
            return _pass(name, f"mark=${mark:,.2f}" + (f", last=${last:,.2f}" if last > 0 else " (last=0, testnet)"))
        else:
            assert last > 0, "Last price is 0 or negative"
            return _pass(name, f"last=${last:,.2f}")

    except Exception as e:
        return _fail(name, str(e))


def _test_ticker_data_integrity(base: str, is_testnet: bool = False) -> dict:
    name = "Ticker: Data Integrity Check"
    try:
        data = _fetch_ticker(base, "BTCUSDT")
        result = data["result"]

        last = _find_price(result, ["lastRp", "last", "lastPrice", "closeRp"])
        high = _find_price(result, ["highRp", "high", "highPriceRp"])
        low = _find_price(result, ["lowRp", "low", "lowPriceRp"])
        bid = _find_price(result, ["bidRp", "bid"])
        ask = _find_price(result, ["askRp", "ask"])

        checks = []

        # Sanity: high >= low (skip if testnet zeros)
        if high > 0 and low > 0:
            assert high >= low, f"High ({high}) < Low ({low})"
            checks.append("high>=low")

        # Sanity: last between high and low (with tolerance), skip on testnet if last=0
        if high > 0 and low > 0 and last > 0:
            tolerance = (high - low) * 0.1
            assert last >= low - tolerance, f"Last ({last}) below low ({low})"
            assert last <= high + tolerance, f"Last ({last}) above high ({high})"
            checks.append("last∈[low,high]")

        # Sanity: ask > bid
        if ask > 0 and bid > 0:
            assert ask >= bid, f"Ask ({ask}) < Bid ({bid})"
            checks.append("ask>=bid")

        # Funding rate should be small (< 1%)
        funding = _find_val(result, ["fundingRateRr", "fundingRate"])
        if funding is not None:
            fr = abs(funding)
            if fr > 1:
                fr = fr / 1e8  # Scaled value
            assert fr < 0.01, f"Funding rate too high: {fr}"
            checks.append(f"funding={fr:.6f}")

        return _pass(name, ", ".join(checks))

    except Exception as e:
        return _fail(name, str(e))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fetch_ticker(base: str, symbol: str) -> dict:
    url = f"{base}/md/v3/ticker/24hr?symbol={symbol}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    assert "result" in data, f"Missing 'result' in ticker response: {list(data.keys())}"
    return data


def _find_price(result: dict, keys: list[str]) -> float:
    for k in keys:
        if k in result:
            v = result[k]
            return float(v) if v else 0.0
    return 0.0


def _find_val(result: dict, keys: list[str]):
    for k in keys:
        if k in result:
            v = result[k]
            return float(v) if v else None
    return None


def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "passed": True, "detail": detail}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "passed": False, "detail": reason}
