"""
PhemexEngine — The Orchestrator.

Bootstraps REST + WS + Adapter into a single, clean API.

Usage:
    from refined_engine import PhemexEngine

    engine = PhemexEngine(symbol="BTCUSDT")
    engine.boot()
    print(engine.price)
    print(engine.wallet)
    result = engine.limit_buy(0.001, 50000)
    engine.cancel_order(result.order_id)
    engine.shutdown()
"""

from __future__ import annotations
import time
from typing import Optional

from .config import API_KEY, API_SECRET, IS_TESTNET, NETWORK
from .models import (
    Candle, TickerData, Product, Wallet, Position, Order,
    OrderResult, OrderbookSnapshot, PlaceOrderRequest,
    AmendOrderRequest, CancelOrderRequest,
)
from .rest_client import RestClient
from .ws_client import WSClient
from .adapter import PhemexAdapter


def _log(msg: str):
    print(f"[Engine] {msg}")

def _warn(msg: str):
    print(f"[Engine] ⚠ {msg}")


class PhemexEngine:
    """
    Top-level orchestrator. Boots REST, WS, and Adapter,
    provides clean accessors and execution methods.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        resolution: int = 60,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ):
        self._symbol = symbol
        self._resolution = resolution
        self._api_key = api_key or API_KEY or ""
        self._api_secret = api_secret or API_SECRET or ""
        self._booted = False

        # State
        self._price: float = 0.0
        self._candles: list[Candle] = []
        self._ticker: Optional[TickerData] = None
        self._products: list[Product] = []
        self._wallet = Wallet()
        self._positions: list[Position] = []
        self._orders: list[Order] = []

        # Components
        self.rest = RestClient()
        self.ws = WSClient()
        self.adapter = PhemexAdapter(self._api_key, self._api_secret)

        if not self._api_key or not self._api_secret:
            _warn("No API credentials. Execution will fail.")

        # Wire up WS callbacks
        self.ws.on_price_update = self._on_price
        self.ws.on_candle_update = self._on_candles
        self.ws.on_ticker_update = self._on_ticker
        self.ws.on_wallet_update = self._on_wallet
        self.ws.on_positions_update = self._on_positions

    # ═════════════════════════════════════════════════════════════════════════
    #  Lifecycle
    # ═════════════════════════════════════════════════════════════════════════

    def boot(self):
        """
        Boot sequence:
        1. Fetch products (REST)
        2. Fetch initial ticker (REST)
        3. Connect WS + Auth + Subscribe
        4. Fetch historical candles (REST)
        5. Hydrate account state (REST)
        """
        if self._booted:
            return
        _log(f"Booting ({NETWORK})...")

        # 1. Products
        self._products = self.rest.fetch_products()

        # 2. Initial ticker
        try:
            ticker = self.rest.fetch_ticker(self._symbol)
            if ticker:
                self._ticker = ticker
                p = ticker.last_price or ticker.mark_price
                if p > 0:
                    self._price = p
        except Exception:
            pass

        # 3. WebSocket
        self.ws.set_credentials(self._api_key, self._api_secret)
        self.ws.connect(self._symbol, self._resolution)

        # 4. Historical candles
        try:
            now = int(time.time())
            candles = self.rest.fetch_historical_candles(
                self._symbol, now, self._resolution, 500
            )
            if candles:
                self._candles = sorted(candles, key=lambda c: c.time)
                if self._price == 0 and self._candles:
                    self._price = self._candles[-1].close
        except Exception as e:
            _warn(f"Failed to fetch candles: {e}")

        # 5. Hydrate account
        self._hydrate_account()

        self._booted = True
        _log("✅ Boot complete. Engine ready.")

    def shutdown(self):
        """Clean shutdown."""
        self.ws.disconnect()
        self._booted = False
        _log("Shutdown complete.")

    def switch_symbol(self, symbol: str):
        """Switch active trading symbol."""
        if self._symbol == symbol:
            return
        _log(f"Switching to {symbol}...")
        self._symbol = symbol
        self.ws.update_subscription(symbol, self._resolution)
        self._refresh_orders()

    def switch_timeframe(self, resolution: int):
        """Switch candle timeframe (seconds)."""
        if self._resolution == resolution:
            return
        _log(f"Switching to {resolution}s...")
        self._resolution = resolution
        self.ws.update_subscription(self._symbol, resolution)

    # ═════════════════════════════════════════════════════════════════════════
    #  Data Accessors
    # ═════════════════════════════════════════════════════════════════════════

    @property
    def price(self) -> float:
        return self._price

    @property
    def candles(self) -> list[Candle]:
        return self._candles

    @property
    def ticker(self) -> Optional[TickerData]:
        return self._ticker

    @property
    def products(self) -> list[Product]:
        return self._products

    @property
    def positions(self) -> list[Position]:
        return self._positions

    @property
    def orders(self) -> list[Order]:
        return self._orders

    @property
    def wallet(self) -> Wallet:
        return self._wallet

    @property
    def active_symbol(self) -> str:
        return self._symbol

    @property
    def booted(self) -> bool:
        return self._booted

    # ═════════════════════════════════════════════════════════════════════════
    #  Execution Methods
    # ═════════════════════════════════════════════════════════════════════════

    def market_buy(self, qty: float) -> OrderResult:
        return self._place("Buy", "Market", qty)

    def market_sell(self, qty: float) -> OrderResult:
        return self._place("Sell", "Market", qty)

    def limit_buy(self, qty: float, price: float) -> OrderResult:
        return self._place("Buy", "Limit", qty, price)

    def limit_sell(self, qty: float, price: float) -> OrderResult:
        return self._place("Sell", "Limit", qty, price)

    def cancel_order(self, order_id: str, pos_side: str = "Merged") -> None:
        self.adapter.cancel_order(CancelOrderRequest(
            symbol=self._symbol, order_id=order_id, pos_side=pos_side
        ))

    def cancel_orders(self, order_ids: list[str], pos_side: str = "Merged") -> None:
        """Bulk cancel specific orders."""
        self.adapter.cancel_orders(self._symbol, order_ids, pos_side=pos_side)

    def cancel_all(self) -> None:
        self.adapter.cancel_all(self._symbol)

    def amend_order(
        self, order_id: str,
        new_price: Optional[float] = None,
        new_qty: Optional[float] = None,
        pos_side: str = "Merged",
    ) -> OrderResult:
        return self.adapter.amend_order(AmendOrderRequest(
            symbol=self._symbol, order_id=order_id,
            price=new_price, qty=new_qty, pos_side=pos_side,
        ))

    def set_leverage(self, leverage: int) -> None:
        self.adapter.set_leverage(self._symbol, leverage)

    def switch_position_mode(self, mode: str) -> None:
        """Switch between OneWay and Hedged position mode."""
        self.adapter.switch_position_mode(self._symbol, mode)

    def assign_position_balance(self, balance: float, pos_side: str = "Merged") -> None:
        """Adjust margin for an isolated-mode position."""
        self.adapter.assign_position_balance(self._symbol, pos_side, balance)

    def get_orderbook(self) -> OrderbookSnapshot:
        return self.adapter.query_orderbook(self._symbol)

    def get_order_history(self, limit: int = 20, offset: int = 0,
                          start: Optional[int] = None, end: Optional[int] = None) -> list[dict]:
        """Query order history (ms timestamps for start/end)."""
        return self.adapter.query_order_history(self._symbol, limit, offset, start, end)

    def get_trades(self, limit: int = 20, offset: int = 0,
                   start: Optional[int] = None, end: Optional[int] = None) -> list[dict]:
        """Query fill/execution history (ms timestamps, max 90 days)."""
        return self.adapter.query_trades_history(self._symbol, limit, offset, start, end)

    def get_funding_fees(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Query funding fee payment history."""
        return self.adapter.query_funding_fees(self._symbol, limit, offset)

    def get_closed_positions(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Query closed position history (PnL, ROI, fees)."""
        return self.adapter.query_closed_positions(self._symbol, limit, offset)

    def get_state(self) -> dict:
        """Dump full state for debugging."""
        return {
            "symbol": self._symbol,
            "price": self._price,
            "candles": len(self._candles),
            "ticker": self._ticker,
            "wallet": self._wallet,
            "positions": self._positions,
            "orders": self._orders,
            "ws_connected": self.ws.connected,
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _place(self, side, order_type, qty, price=None, pos_side="Merged"):
        return self.adapter.place_order(PlaceOrderRequest(
            symbol=self._symbol, side=side, type=order_type,
            qty=qty, price=price, pos_side=pos_side,
        ))

    def _hydrate_account(self):
        try:
            info = self.adapter.get_account_info()
            self._wallet = Wallet(
                currency="USDT", balance=info.balance.total,
                available=info.balance.available, used=info.balance.used,
            )
            self._positions = [
                Position(
                    symbol=p.symbol, side=p.side, size=p.size,
                    entry_price=p.entry_price, mark_price=p.entry_price,
                    liquidation_price=p.liquidation_price,
                    leverage=p.leverage, unrealized_pnl=p.unrealized_pnl,
                    margin=p.margin,
                    pos_side=p.pos_side,
                ) for p in info.positions
            ]
            
            # Auto-detect posSide for orders if active positions exist
            active = [p for p in self._positions if abs(p.size) > 0]
            if active:
                # If we see any Hedge mode positions, we might want to default to that
                # but for now we just use the first active one's mode
                pass

            self._refresh_orders()
            _log(f"Account: ${info.balance.total:,.2f} | "
                 f"{len(self._positions)} positions | "
                 f"{len(self._orders)} orders")
        except Exception as e:
            _warn(f"Failed to hydrate: {e}")

    def _refresh_orders(self):
        try:
            raw = self.adapter.query_open_orders(self._symbol)
            self._orders = [
                Order(
                    order_id=o.order_id, symbol=o.symbol,
                    side=o.side, type=o.order_type,
                    qty=float(o.qty), price=float(o.price),
                    status="New" if o.status == "Created" else o.status,
                    trigger_price=float(o.stop_price),
                ) for o in raw
            ]
        except Exception:
            pass

    # ── WS Event Handlers ────────────────────────────────────────────────────

    def _on_price(self, price: float):
        self._price = price
        # Optimization: Client-side PnL calculation to avoid polling
        for p in self._positions:
            if not p.size:
                continue
            
            # Hedge Mode: Long=Buy, Short=Sell
            # One-Way Mode: side=Buy (Long), side=Sell (Short)
            is_long = (p.pos_side == "Long") or (p.pos_side == "Merged" and p.side == "Buy")
            
            if is_long:
                p.unrealized_pnl = (price - p.entry_price) * p.size
            else:
                p.unrealized_pnl = (p.entry_price - price) * p.size
                
    def _on_candles(self, candles: list[Candle]):
        for c in candles:
            existing = next((x for x in self._candles if x.time == c.time), None)
            if existing:
                idx = self._candles.index(existing)
                self._candles[idx] = c
            else:
                self._candles.append(c)
                self._candles.sort(key=lambda x: x.time)

    def _on_ticker(self, ticker: TickerData):
        self._ticker = ticker

    def _on_wallet(self, wallet: Wallet):
        self._wallet = wallet

    def _on_positions(self, positions: list[Position]):
        self._positions = positions
