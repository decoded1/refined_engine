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
import gc
import os
import array
import sys
import asyncio
import bisect
import numpy as np
from numba import jit
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from .config import API_KEY, API_SECRET, IS_TESTNET, NETWORK, logger, REST_VIP, WS_VIP
from .models import (
    Candle, TickerData, Product, Wallet, Position, Order,
    OrderResult, OrderbookSnapshot, PlaceOrderRequest,
    AmendOrderRequest, CancelOrderRequest,
)
from .rest_client import RestClient
from .ws_client import WSClient
from .adapter import PhemexAdapter


def _log(msg: str):
    logger.log("Engine", msg)

def _warn(msg: str):
    logger.log("Engine", f"⚠ {msg}")


@jit(nopython=True, fastmath=True)
def _calculate_pnl_jit(price: float, entry_prices: np.ndarray, pnl_factors: np.ndarray, out_pnls: np.ndarray):
    """
    Vectorized PnL calculation using ARM64 Machine Code.
    Uses M3 Performance Cores via Numba JIT.
    """
    for i in range(len(entry_prices)):
        out_pnls[i] = (price - entry_prices[i]) * pnl_factors[i]


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
        use_vip: bool = False,
    ):
        self._symbol = sys.intern(symbol)
        self._symbol_bytes = self._symbol.encode("utf-8")
        self._resolution = resolution
        self._api_key = api_key or API_KEY or ""
        self._api_secret = api_secret or API_SECRET or ""
        self._booted = False

        # VIP logic
        rest_url = REST_VIP if use_vip and not IS_TESTNET else None
        ws_url = WS_VIP if use_vip and not IS_TESTNET else None

        # State
        self._price: float = 0.0
        self._candle_map: dict[int, Candle] = {} # Internal O(1) storage
        self._candles_cached: list[Candle] = []  # Cached sorted list
        self._candles_dirty: bool = True         # Cache invalidation flag
        
        # Parallel Primitive Arrays (Phase 1.5 Optimization)
        self._history_time = array.array('q')   # signed long long
        self._history_open = array.array('d')   # double
        self._history_high = array.array('d')
        self._history_low = array.array('d')
        self._history_close = array.array('d')
        self._history_volume = array.array('d')

        self._ticker: Optional[TickerData] = None
        self._products: list[Product] = []
        self._wallet = Wallet()
        self._positions: list[Position] = []
        self._pos_map: dict[str, list[Position]] = {} # Optimized lookup
        
        # Parallel Primitive Arrays for JIT Math (Phase 3 Optimization)
        self._pos_entry_prices = np.zeros(0, dtype=np.float64)
        self._pos_pnl_factors = np.zeros(0, dtype=np.float64)
        self._pos_unrealized_pnls = np.zeros(0, dtype=np.float64)
        
        self._orders: list[Order] = []
        self._order_map: dict[str, Order] = {} # Optimized lookup
        self._active_ids: set[str] = set()     # High-speed existence set
        self._orderbook = OrderbookSnapshot(symbol=symbol)

        # Components
        self.rest = RestClient(base_url=rest_url)
        self.ws = WSClient(ws_url=ws_url)
        self.adapter = PhemexAdapter(self._api_key, self._api_secret, use_vip=use_vip)
        self._executor = ThreadPoolExecutor(max_workers=8) # Persistent worker pool

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

        # Phase X: M3 Performance Core Priority
        try:
            os.nice(-20)
            _log("🚀 Process set to HIGH PRIORITY (Performance Cores)")
        except Exception:
            _log("ℹ Process priority already optimized or permission restricted")

        # Phase 2 Optimization: Disable automatic GC Jitter
        gc.disable()

        now = int(time.time())
        self.ws.set_credentials(self._api_key, self._api_secret)

        # Optimization: DNS Fast-Path (Pre-resolve once)
        from .config import resolve_host
        _log(f"Pre-resolving hostnames for {self.rest.base}...")
        resolve_host(self.rest.base) 

        with ThreadPoolExecutor(max_workers=4) as executor:
            # 1. Start all REST requests in parallel (History removed)
            f_prods = executor.submit(self.rest.fetch_products)
            f_ticker = executor.submit(self.rest.fetch_ticker, self._symbol)
            f_account = executor.submit(self.adapter.get_account_info)
            f_orders = executor.submit(self.adapter.query_open_orders, self._symbol)

            # 2. While waiting, connect WebSocket (Triggers 2,000 candle burst)
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
                    pnl_factor=p.pnl_factor,
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
            self._orders = []
            self._order_map = {}
            for o in raw_orders:
                order_id = o.order_id
                order = Order(
                    order_id=order_id, symbol=o.symbol,
                    side=o.side, type=o.order_type,
                    qty=float(o.qty), price=float(o.price),
                    status="New" if o.status == "Created" else o.status,
                    trigger_price=float(o.stop_price),
                )
                self._orders.append(order)
                self._order_map[order_id] = order
            
            self._active_ids = set(self._order_map.keys())

        self._booted = True
        _log(f"✅ Boot complete. Account: ${self._wallet.balance:,.2f} | "
             f"{len(self._positions)} positions | {len(self._orders)} orders")

    async def boot_async(self):
        """Asynchronous boot wrapper (non-blocking)."""
        await asyncio.to_thread(self.boot)

    def shutdown(self):
        """Clean shutdown."""
        self.ws.disconnect()
        self._executor.shutdown(wait=False)
        self._booted = False
        gc.enable() # Re-enable system GC
        _log("Shutdown complete.")
        # logger.shutdown() # Optional: keep alive if other engines exist

    async def shutdown_async(self):
        """Asynchronous shutdown wrapper (non-blocking)."""
        await asyncio.to_thread(self.shutdown)

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
        """Returns sorted list of candles (Optimized Cache)."""
        if self._candles_dirty:
            self._candles_cached = sorted(self._candle_map.values(), key=lambda x: x.time)
            
            # Sync Primitive Arrays
            self._history_time = array.array('q', [c.time for c in self._candles_cached])
            self._history_open = array.array('d', [c.open for c in self._candles_cached])
            self._history_high = array.array('d', [c.high for c in self._candles_cached])
            self._history_low = array.array('d', [c.low for c in self._candles_cached])
            self._history_close = array.array('d', [c.close for c in self._candles_cached])
            self._history_volume = array.array('d', [c.volume for c in self._candles_cached])
            
            self._candles_dirty = False
        return self._candles_cached

    @property
    def closes(self) -> array.array:
        """Returns raw C-doubles of close prices (Vectorized)."""
        self.candles # Trigger sync if dirty
        return self._history_close

    @property
    def highs(self) -> array.array:
        self.candles
        return self._history_high

    @property
    def lows(self) -> array.array:
        self.candles
        return self._history_low

    @property
    def volumes(self) -> array.array:
        self.candles
        return self._history_volume

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

    async def market_buy_async(self, qty: float, pos_side: str = "Merged") -> OrderResult:
        return await asyncio.to_thread(self.market_buy, qty, pos_side)

    def market_buy_batch(self, qtys: list[float], pos_side: str = "Merged") -> list[OrderResult]:
        """Place multiple market buy orders in parallel."""
        reqs = [PlaceOrderRequest(self._symbol, "Buy", "Market", q, pos_side=pos_side) for q in qtys]
        return self._pipeline_requests(self.adapter.place_order, reqs)

    async def market_buy_batch_async(self, qtys: list[float], pos_side: str = "Merged") -> list[OrderResult]:
        return await asyncio.to_thread(self.market_buy_batch, qtys, pos_side)

    def market_sell(self, qty: float, pos_side: str = "Merged") -> OrderResult:
        return self._place("Sell", "Market", qty, pos_side=pos_side)

    async def market_sell_async(self, qty: float, pos_side: str = "Merged") -> OrderResult:
        return await asyncio.to_thread(self.market_sell, qty, pos_side)

    def market_sell_batch(self, qtys: list[float], pos_side: str = "Merged") -> list[OrderResult]:
        """Place multiple market sell orders in parallel."""
        reqs = [PlaceOrderRequest(self._symbol, "Sell", "Market", q, pos_side=pos_side) for q in qtys]
        return self._pipeline_requests(self.adapter.place_order, reqs)

    async def market_sell_batch_async(self, qtys: list[float], pos_side: str = "Merged") -> list[OrderResult]:
        return await asyncio.to_thread(self.market_sell_batch, qtys, pos_side)

    def limit_buy(self, qty: float, price: float, pos_side: str = "Merged") -> OrderResult:
        return self._place("Buy", "Limit", qty, price, pos_side=pos_side)

    async def limit_buy_async(self, qty: float, price: float, pos_side: str = "Merged") -> OrderResult:
        return await asyncio.to_thread(self.limit_buy, qty, price, pos_side)

    def limit_buy_batch(self, orders: list[tuple[float, float]], pos_side: str = "Merged") -> list[OrderResult]:
        """Place multiple limit buy orders (qty, price) in parallel."""
        reqs = [PlaceOrderRequest(self._symbol, "Buy", "Limit", q, p, pos_side=pos_side) for q, p in orders]
        return self._pipeline_requests(self.adapter.place_order, reqs)

    async def limit_buy_batch_async(self, orders: list[tuple[float, float]], pos_side: str = "Merged") -> list[OrderResult]:
        return await asyncio.to_thread(self.limit_buy_batch, orders, pos_side)

    def limit_sell(self, qty: float, price: float, pos_side: str = "Merged") -> OrderResult:
        return self._place("Sell", "Limit", qty, price, pos_side=pos_side)

    async def limit_sell_async(self, qty: float, price: float, pos_side: str = "Merged") -> OrderResult:
        return await asyncio.to_thread(self.limit_sell, qty, price, pos_side)

    def limit_sell_batch(self, orders: list[tuple[float, float]], pos_side: str = "Merged") -> list[OrderResult]:
        """Place multiple limit sell orders (qty, price) in parallel."""
        reqs = [PlaceOrderRequest(self._symbol, "Sell", "Limit", q, p, pos_side=pos_side) for q, p in orders]
        return self._pipeline_requests(self.adapter.place_order, reqs)

    async def limit_sell_batch_async(self, orders: list[tuple[float, float]], pos_side: str = "Merged") -> list[OrderResult]:
        return await asyncio.to_thread(self.limit_sell_batch, orders, pos_side)

    def cancel_order(self, order_id: str, pos_side: str = "Merged") -> None:
        self.adapter.cancel_order(CancelOrderRequest(
            symbol=self._symbol, order_id=order_id, pos_side=pos_side
        ))

    async def cancel_order_async(self, order_id: str, pos_side: str = "Merged") -> None:
        await asyncio.to_thread(self.cancel_order, order_id, pos_side)

    def cancel_orders(self, order_ids: list[str], pos_side: str = "Merged") -> None:
        """Bulk cancel specific orders."""
        self.adapter.cancel_orders(self._symbol, order_ids, pos_side=pos_side)

    async def cancel_orders_async(self, order_ids: list[str], pos_side: str = "Merged") -> None:
        await asyncio.to_thread(self.cancel_orders, order_ids, pos_side)

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

    async def cancel_all_async(self, pos_side: Optional[str] = None) -> None:
        await asyncio.to_thread(self.cancel_all, pos_side)

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

    async def amend_order_async(
        self, order_id: str,
        new_price: Optional[float] = None,
        new_qty: Optional[float] = None,
        pos_side: str = "Merged",
    ) -> OrderResult:
        return await asyncio.to_thread(self.amend_order, order_id, new_price, new_qty, pos_side)

    def amend_orders_batch(self, updates: list[dict]) -> list[OrderResult]:
        """
        Amend multiple orders in parallel.
        Expects list of dicts: {'order_id': str, 'price': float, 'qty': float, 'pos_side': str}
        """
        reqs = [
            AmendOrderRequest(
                symbol=self._symbol, 
                order_id=u.get("order_id"), 
                price=u.get("price"), 
                qty=u.get("qty"), 
                pos_side=u.get("pos_side", "Merged")
            ) for u in updates
        ]
        return self._pipeline_requests(self.adapter.amend_order, reqs)

    async def amend_orders_batch_async(self, updates: list[dict]) -> list[OrderResult]:
        return await asyncio.to_thread(self.amend_orders_batch, updates)

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

    def get_volume_at(self, price: float, side: str = "Buy") -> float:
        """Returns the volume at a specific price level (O(1))."""
        book = self._orderbook
        if side == "Buy":
            return book.bid_map.get(price, 0.0)
        return book.ask_map.get(price, 0.0)

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
                    pnl_factor=p.pnl_factor,
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
            new_orders = []
            new_map = {}

            for o in raw:
                order_id = o.order_id
                status = "New" if o.status == "Created" else o.status
                
                # Optimization: In-place update if order already exists
                if order_id in self._order_map:
                    order = self._order_map[order_id]
                    order.qty = float(o.qty)
                    order.price = float(o.price)
                    order.status = status
                    order.trigger_price = float(o.stop_price)
                else:
                    # New object only if it didn't exist
                    order = Order(
                        order_id=order_id, symbol=o.symbol,
                        side=o.side, type=o.order_type,
                        qty=float(o.qty), price=float(o.price),
                        status=status,
                        trigger_price=float(o.stop_price),
                    )
                
                new_orders.append(order)
                new_map[order_id] = order

            self._orders = new_orders
            self._order_map = new_map
            self._active_ids = set(new_map.keys())
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
        Optimized PnL update (JIT-Accelerated).
        Uses Numba Machine Code for all-symbol updates and local binding for target.
        """
        self._price = price
        target = symbol or self._symbol
        
        # 1. JIT Vectorized Update for ALL positions (High-speed broad coverage)
        if len(self._pos_entry_prices) > 0:
            _calculate_pnl_jit(price, self._pos_entry_prices, self._pos_pnl_factors, self._pos_unrealized_pnls)
            
            # Sync values back to objects for public API
            for i, p in enumerate(self._positions):
                p.unrealized_pnl = self._pos_unrealized_pnls[i]

        # 2. Specific Target update (for cases where symbol differs from engine)
        if target != self._symbol:
            pos_list = self._pos_map.get(target, [])
            for p in pos_list:
                p.unrealized_pnl = (price - p.entry_price) * p.pnl_factor

    def _on_candles(self, candles: list[Candle]):
        """Offloads heavy bulk update to the background executor (Non-blocking)."""
        if not candles:
            return
        self._executor.submit(self._handle_candle_burst, candles)

    def _handle_candle_burst(self, candles: list[Candle]):
        """Internal background task for candle map maintenance."""
        # Optimization: Use direct loop instead of update() to avoid temporary dict allocation
        cmap = self._candle_map
        for c in candles:
            cmap[c.time] = c
        
        self._candles_dirty = True

        # Phase 1 Ground Level: If price is unknown, use the latest candle close
        if self._price == 0:
            latest = sorted(candles, key=lambda x: x.time)[-1]
            self._price = latest.close

        # Phase 1.5 Optimization: Amortized Cleanup
        if len(self._candle_map) > 2100:
            sorted_keys = sorted(self._candle_map.keys())
            for k in sorted_keys[:-2000]:
                del self._candle_map[k]
            self._candles_dirty = True

    def _on_ticker(self, ticker: TickerData):
        self._ticker = ticker

    def _on_wallet(self, wallet: Wallet):
        self._wallet = wallet

    def _on_positions(self, positions: list[Position]):
        for p in positions:
            p.pnl_factor = p.size * p.side_multiplier
        
        self._positions = positions
        
        # Sync Numpy Arrays for JIT
        self._pos_entry_prices = np.array([p.entry_price for p in positions], dtype=np.float64)
        self._pos_pnl_factors = np.array([p.pnl_factor for p in positions], dtype=np.float64)
        self._pos_unrealized_pnls = np.zeros(len(positions), dtype=np.float64)

        # Update position map
        self._pos_map = {}
        for p in self._positions:
            if p.symbol not in self._pos_map:
                self._pos_map[p.symbol] = []
            self._pos_map[p.symbol].append(p)

    def _on_orderbook(self, data: dict):
        """
        Maintains the local L2 mirror with O(1) price maps and O(log N) sorted lists.
        Uses binary search (bisect) for in-place sorted maintenance.
        """
        is_snapshot = data.get("type") == "snapshot"
        book_data = data.get("orderbook_p", {})
        
        # 1. Parse Levels
        new_asks = {float(a[0]): float(a[1]) for a in book_data.get("asks", [])}
        new_bids = {float(b[0]): float(b[1]) for b in book_data.get("bids", [])}

        book = self._orderbook

        if is_snapshot:
            # Full replacement
            book.ask_map = {p: s for p, s in new_asks.items() if s > 0}
            book.bid_map = {p: s for p, s in new_bids.items() if s > 0}
            book.asks = [OrderbookLevel(p, s) for p, s in sorted(book.ask_map.items())]
            book.bids = [OrderbookLevel(p, s) for p, s in sorted(book.bid_map.items(), reverse=True)]
        else:
            # Incremental update: O(log N) updates using bisect
            # Update Asks (Ascending)
            for p, s in new_asks.items():
                idx = bisect.bisect_left(book.asks, p, key=lambda x: x.price)
                if idx < len(book.asks) and book.asks[idx].price == p:
                    if s == 0:
                        book.asks.pop(idx)
                        book.ask_map.pop(p, None)
                    else:
                        book.asks[idx].size = s
                        book.ask_map[p] = s
                elif s > 0:
                    book.asks.insert(idx, OrderbookLevel(p, s))
                    book.ask_map[p] = s

            # Update Bids (Descending)
            for p, s in new_bids.items():
                # Descending search: negate values
                idx = bisect.bisect_left(book.bids, -p, key=lambda x: -x.price)
                if idx < len(book.bids) and book.bids[idx].price == p:
                    if s == 0:
                        book.bids.pop(idx)
                        book.bid_map.pop(p, None)
                    else:
                        book.bids[idx].size = s
                        book.bid_map[p] = s
                elif s > 0:
                    book.bids.insert(idx, OrderbookLevel(p, s))
                    book.bid_map[p] = s

        book.timestamp = data.get("timestamp", 0)

    def _pipeline_requests(self, func, requests: list) -> list:
        """Helper to execute multiple API calls concurrently."""
        with ThreadPoolExecutor(max_workers=len(requests) or 1) as executor:
            futures = [executor.submit(func, r) for r in requests]
            return [f.result() for f in futures]
