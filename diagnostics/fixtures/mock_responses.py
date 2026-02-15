"""
Mock Responses — Sample API responses for offline/unit testing.
Can be used to validate parsing logic without hitting the live API.
"""

MOCK_PRODUCTS_RESPONSE = {
    "code": 0,
    "msg": "",
    "data": {
        "perpProductsV2": [
            {
                "symbol": "BTCUSDT",
                "baseCurrency": "BTC",
                "quoteCurrency": "USDT",
                "priceScale": 4,
                "ratioScale": 8,
                "valueScale": 8,
                "type": "PerpetualV2",
                "status": "Listed",
            },
            {
                "symbol": "ETHUSDT",
                "baseCurrency": "ETH",
                "quoteCurrency": "USDT",
                "priceScale": 4,
                "ratioScale": 8,
                "valueScale": 8,
                "type": "PerpetualV2",
                "status": "Listed",
            },
        ]
    },
}

MOCK_TICKER_RESPONSE = {
    "error": None,
    "id": 0,
    "result": {
        "symbol": "BTCUSDT",
        "lastRp": "67000.5",
        "markRp": "67010.0",
        "indexRp": "67005.0",
        "highRp": "68000.0",
        "lowRp": "65500.0",
        "volumeRq": "12345678.90",
        "openInterestRv": "987654.32",
        "fundingRateRr": "0.0001",
        "predFundingRateRr": "0.00015",
        "bidRp": "67000.0",
        "askRp": "67001.0",
    },
}

MOCK_KLINE_RESPONSE = {
    "code": 0,
    "msg": "",
    "data": {
        "rows": [
            [1700000000, 3600, "66500", "66500", "67000", "66000", "66800", "1234.5", "82345678"],
            [1700003600, 3600, "66800", "66800", "67200", "66700", "67100", "987.3", "66234567"],
            [1700007200, 3600, "67100", "67100", "67500", "66900", "67400", "876.2", "59123456"],
        ]
    },
}

MOCK_ORDERBOOK_RESPONSE = {
    "error": None,
    "id": 0,
    "result": {
        "orderbook_p": {
            "asks": [
                ["67001.0", "1.234"],
                ["67002.0", "2.567"],
                ["67003.0", "3.891"],
            ],
            "bids": [
                ["67000.0", "1.111"],
                ["66999.0", "2.222"],
                ["66998.0", "3.333"],
            ],
        },
        "timestamp": 1700000000000,
    },
}

MOCK_ACCOUNT_RESPONSE = {
    "code": 0,
    "msg": "",
    "data": {
        "account": {
            "accountBalanceRv": "10000.00",
            "totalUsedBalanceRv": "2500.00",
            "bonusBalanceRv": "0.00",
        },
        "positions": [
            {
                "symbol": "BTCUSDT",
                "side": "Buy",
                "size": "0.01",
                "avgEntryPriceRp": "66500.0",
                "unrealisedPnlRv": "100.50",
                "leverageRr": "10",
                "liquidationPriceRp": "60000.0",
                "usedBalanceRv": "665.0",
            }
        ],
    },
}
