"""
Expected Schemas — Defines the expected shape of Phemex API responses.
Used by test suites to validate that responses match known structure.
"""

# ── Products Response ────────────────────────────────────────────────────────

PRODUCTS_RESPONSE_SCHEMA = {
    "required_keys": ["code", "data"],
    "data_required_keys": [],  # either products or perpProductsV2
    "data_has_one_of": ["products", "perpProductsV2"],
}

PRODUCT_FIELDS = [
    "symbol",
    "baseCurrency",
    "quoteCurrency",
    "priceScale",
    "type",
    "status",
]

# ── Ticker Response (v3) ────────────────────────────────────────────────────

TICKER_RESPONSE_SCHEMA = {
    "required_keys": ["result"],
    "result_fields": [
        "symbol",
        "lastPrice",
        "markPrice",
        "indexPrice",
        "high24h",
        "low24h",
        "volume24h",
        "openInterest",
        "fundingRate",
    ],
    # Phemex may use different field names (Rp/Rv/Ep suffixes), so we check flexibly
    "flexible_names": {
        "lastPrice": ["lastRp", "last", "lastPrice", "closeRp"],
        "markPrice": ["markRp", "markPrice", "markPriceRp"],
        "indexPrice": ["indexRp", "indexPrice", "indexLastPriceRp"],
        "volume24h": ["volumeRq", "volume", "volume24h", "turnoverRv"],
        "high24h": ["highRp", "high", "highPriceRp"],
        "low24h": ["lowRp", "low", "lowPriceRp"],
        "fundingRate": ["fundingRateRr", "fundingRate"],
        "openInterest": ["openInterestRv", "openInterest"],
    },
}

# ── Kline (Candle) Response ──────────────────────────────────────────────────

KLINE_RESPONSE_SCHEMA = {
    "required_keys": ["code", "data"],
    "data_required_keys": ["rows"],
    # Each row is an array: [timestamp, interval, last_close, open, high, low, close, volume, turnover]
    "row_min_length": 8,
}

# ── Orderbook Response ───────────────────────────────────────────────────────

ORDERBOOK_RESPONSE_SCHEMA = {
    "required_keys": ["result"],
    "result_must_contain": ["orderbook_p"],
    "orderbook_must_contain": ["asks", "bids"],
}

# ── Account Positions Response ───────────────────────────────────────────────

ACCOUNT_RESPONSE_SCHEMA = {
    "required_keys": ["code", "data"],
    "data_must_contain": ["account", "positions"],
    "account_fields": ["accountBalanceRv", "totalUsedBalanceRv"],
}

# ── Order Response ───────────────────────────────────────────────────────────

ORDER_RESPONSE_SCHEMA = {
    "required_keys": ["code", "data"],
    "order_fields": ["orderID", "ordStatus", "side", "ordType"],
}
