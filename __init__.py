"""
Refined Engine — Phemex API Client

Usage:
    from refined_engine import PhemexEngine

    engine = PhemexEngine(symbol="BTCUSDT")
    engine.boot()
    print(engine.price)
    result = engine.limit_buy(0.001, 50000)
    engine.cancel_order(result.order_id)
    engine.shutdown()
"""

from .engine import PhemexEngine
from .rest_client import RestClient
from .ws_client import WSClient
from .adapter import PhemexAdapter
from .models import (
    Candle, TickerData, Product, Wallet, Position, Order,
    OrderResult, OrderbookSnapshot, Balance, PositionInfo, AccountInfo,
    PlaceOrderRequest, AmendOrderRequest, CancelOrderRequest,
)

__all__ = [
    "PhemexEngine",
    "RestClient",
    "WSClient",
    "PhemexAdapter",
    "Candle", "TickerData", "Product", "Wallet", "Position", "Order",
    "OrderResult", "OrderbookSnapshot", "Balance", "PositionInfo", "AccountInfo",
    "PlaceOrderRequest", "AmendOrderRequest", "CancelOrderRequest",
]
