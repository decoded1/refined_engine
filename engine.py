"""
PhemexEngine — The Orchestrator.

Bootstraps REST + WS + Adapter into a single, clean API.

Usage:
    from refined_engine import PhemexEngine

    engine = PhemexEngine(symbol="BTCUSDT")
    engine.boot()
    print(engine.price)
    result = engine.limit_buy(0.001, 50000)
    engine.shutdown()
"""

from __future__ import annotations
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

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
        self._candle_map: dict[int, Candle] = {} # Internal O(1) storage
        self._ticker: Optional[TickerData] = None
        self._products: list[Product] = []
        self._wallet = Wallet()
        self._positions: list[Position] = []
        self._pos_map: dict[str, list[Position]] = {} # Optimized lookup
        self._orders: list[Order] = []
        self._orderbook = OrderbookSnapshot(symbol=symbol)

        # Components
        self.rest = RestClient()
        self.ws = WSClient()
        self.adapter = PhemexAdapter(self._api_key, self._api_secret)

        if not self._api_key or not self._api_secret:
            _warn("No API credentials. Execution will fail.")

        # Wire up WS callbacks
        self.ws.on_connected = self._on_ws_reconnect
        self.ws.on_price_update = lambda p: self._on_price(p, self._symbol)
        self.ws.on_candle_update = self._on_candles
        self.ws.on_ticker_update = self._on_ticker
        self.ws.on_wallet_update = self._on_wallet
        self.ws.on_positions_update = self._on_positions
        self.ws.on_orderbook = self._on_orderbook
        self.ws.on_tick = lambda p, s, t: self._on_price(p, s)

    # ═════════════════════════════════════════════════════════════════════════
    #  Lifecycle
    # ═════════════════════════════════════════════════════════════════════════

    def boot(self):
        """
        Boot sequence (Optimized):
        Parallelizes all REST requests (Products, Ticker, Candles, Account, Orders)
        to reduce boot time by ~85%.
        """
        if self._booted:
            return
        _log(f"Booting ({NETWORK})...")

        now = int(time.time())
        self.ws.set_credentials(self._api_key, self._api_secret)

        with ThreadPoolExecutor(max_workers=5) as executor:
            # 1. Start all REST requests in parallel
            f_prods = executor.submit(self.rest.fetch_products)
            f_ticker = executor.submit(self.rest.fetch_ticker, self._symbol)
            f_candles = executor.submit(self.rest.fetch_historical_candles, 
                                        self._symbol, now, self._resolution, 500)
            f_account = executor.submit(self.adapter.get_account_info)
            f_orders = executor.submit(self.adapter.query_open_orders, self._symbol)

            # 2. While waiting, connect WebSocket (takes ~100ms-1s)
            self.ws.connect(self._symbol, self._resolution)

            # 3. Gather Results and Hydrate State
            self._products = f_prods.result()
            self.adapter.set_products(self._products)

            ticker = f_ticker.result()
            if ticker:
                self._ticker = ticker
                p = ticker.last_price or ticker.mark_price
                if p > 0:
                    self._price = p

            candles = f_candles.result()
            if candles:
                self._candle_map = {c.time: c for c in candles}
                if self._price == 0 and candles:
                    # Sort to find latest close for initial price
                    latest = sorted(candles, key=lambda c: c.time)[-1]
                    self._price = latest.close

            # Hydrate Wallet & Positions
            info = f_account.result()
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
                    margin=p.margin, pos_side=p.pos_side,
                    side_multiplier=p.side_multiplier,
                ) for p in info.positions
            ]
            
            # Update position map
            self._pos_map = {}
            for p in self._positions:
                if p.symbol not in self._pos_map:
                    self._pos_map[p.symbol] = []
                self._pos_map[p.symbol].append(p)

            # Hydrate Orders
            raw_orders = f_orders.result()
            self._orders = [
                Order(
                    order_id=o.order_id, symbol=o.symbol,
                    side=o.side, type=o.order_type,
                    qty=float(o.qty), price=float(o.price),
                    status="New" if o.status == "Created" else o.status,
                    trigger_price=float(o.stop_price),
                ) for o in raw_orders
            ]

        self._booted = True
        _log(f"✅ Boot complete. Account: ${self._wallet.balance:,.2f} | "
             f"{len(self._positions)} positions | {len(self._orders)} orders")

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
        """Returns sorted list of candles (Public API)."""
        return sorted(self._candle_map.values(), key=lambda x: x.time)

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

    def market_buy(self, qty: float, pos_side: str = "Merged") -> OrderResult:
        return self._place("Buy", "Market", qty, pos_side=pos_side)

    def market_sell(self, qty: float, pos_side: str = "Merged") -> OrderResult:
        return self._place("Sell", "Market", qty, pos_side=pos_side)

    def limit_buy(self, qty: float, price: float, pos_side: str = "Merged") -> OrderResult:
        return self._place("Buy", "Limit", qty, price, pos_side=pos_side)

    def limit_sell(self, qty: float, price: float, pos_side: str = "Merged") -> OrderResult:
        return self._place("Sell", "Limit", qty, price, pos_side=pos_side)

    def cancel_order(self, order_id: str, pos_side: str = "Merged") -> None:
        self.adapter.cancel_order(CancelOrderRequest(
            symbol=self._symbol, order_id=order_id, pos_side=pos_side
        ))

    def cancel_orders(self, order_ids: list[str], pos_side: str = "Merged") -> None:
        """Bulk cancel specific orders."""
        self.adapter.cancel_orders(self._symbol, order_ids, pos_side=pos_side)

    def cancel_all(self, pos_side: Optional[str] = None) -> None:
        """
        Thoroughly cancel all orders for the active symbol.
        If pos_side is None, attempts to cancel for all possible modes 
        (Merged, Long, Short) and both order categories (Active, Untriggered).
        """
        sides = [pos_side] if pos_side else ["Merged", "Long", "Short"]
        for s in sides:
            try:
                # Cancel active orders (includes triggered conditional)
                self.adapter.cancel_all(self._symbol, untriggered_only=False, pos_side=s)
                # Cancel untriggered conditional orders
                self.adapter.cancel_all(self._symbol, untriggered_only=True, pos_side=s)
            except Exception:
                # Silently skip if a particular side/mode is invalid for this symbol
                pass

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
        """Returns the high-speed local L2 mirror (O(1))."""
        return self._orderbook

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
                    side_multiplier=p.side_multiplier,
                ) for p in info.positions
            ]

            # Update position map
            self._pos_map = {}
            for p in self._positions:
                if p.symbol not in self._pos_map:
                    self._pos_map[p.symbol] = []
                self._pos_map[p.symbol].append(p)

            # Auto-detect posSide logic if needed in future
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

    def _on_ws_reconnect(self):
        """Called when WS reconnects after a drop."""
        if self._booted:
            _log("WS Reconnected: Refreshing state...")
            self._hydrate_account()

    def _on_price(self, price: float, symbol: str = ""):
        """
        Optimized PnL update.
        If symbol is provided (from WebSocket), only updates that symbol.
        """
        self._price = price
        target_symbol = symbol or self._symbol
        
        for p in self._pos_map.get(target_symbol, []):
            if p.size:
                p.unrealized_pnl = (price - p.entry_price) * p.size * p.side_multiplier

    def _on_candles(self, candles: list[Candle]):
        """O(1) update via internal map."""
        for c in candles:
            self._candle_map[c.time] = c

        # Maintain memory limit (Max 2000 candles)
        if len(self._candle_map) > 2000:
            # Sort by time and keep newest 2000
            sorted_keys = sorted(self._candle_map.keys())
            for k in sorted_keys[:-2000]:
                del self._candle_map[k]

    def _on_ticker(self, ticker: TickerData):
        self._ticker = ticker

    def _on_wallet(self, wallet: Wallet):
        self._wallet = wallet

    def _on_positions(self, positions: list[Position]):
        self._positions = positions
        # Update position map
        self._pos_map = {}
        for p in self._positions:
            if p.symbol not in self._pos_map:
                self._pos_map[p.symbol] = []
            self._pos_map[p.symbol].append(p)

    def _on_orderbook(self, data: dict):
        """
        Maintains the local L2 mirror.
        Phemex sends 'snapshot' or 'incremental' updates.
        """
        is_snapshot = data.get("type") == "snapshot"
        book = data.get("orderbook_p", {})
        
        # 1. Parse Levels
        new_asks = {float(a[0]): float(a[1]) for a in book.get("asks", [])}
        new_bids = {float(b[0]): float(b[1]) for b in book.get("bids", [])}

        if is_snapshot:
            # Full replacement
            self._orderbook.asks = [OrderbookLevel(p, s) for p, s in sorted(new_asks.items()) if s > 0]
            self._orderbook.bids = [OrderbookLevel(p, s) for p, s in sorted(new_bids.items(), reverse=True) if s > 0]
        else:
            # Incremental update: Convert existing to maps for O(1) merge
            ask_map = {l.price: l.size for l in self._orderbook.asks}
            bid_map = {l.price: l.size for l in self._orderbook.bids}

            # Apply updates
            for p, s in new_asks.items():
                if s == 0: ask_map.pop(p, None)
                else: ask_map[p] = s
            
            for p, s in new_bids.items():
                if s == 0: bid_map.pop(p, None)
                else: bid_map[p] = s

            # Sync back to sorted lists
            self._orderbook.asks = [OrderbookLevel(p, s) for p, s in sorted(ask_map.items())]
            self._orderbook.bids = [OrderbookLevel(p, s) for p, s in sorted(bid_map.items(), reverse=True)]

        self._orderbook.timestamp = data.get("timestamp", 0)
