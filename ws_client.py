"""
WebSocket Client — Live data streaming from Phemex.
Thread-safe: runs the WS event loop in a background thread.
"""

from __future__ import annotations
import json
import time
import threading
import queue
from typing import Optional, Callable

try:
    import websocket
    HAS_WS = True
except ImportError:
    HAS_WS = False

from .config import WS_URL, sign_hmac
from .models import Candle, TickerData, Wallet, Position


def _log(msg: str):
    print(f"  [WS] {msg}")

def _warn(msg: str):
    print(f"  [WS] ⚠ {msg}")


class WSClient:
    """WebSocket client for live Phemex data with auto-reconnect."""

    def __init__(self, ws_url: Optional[str] = None):
        if not HAS_WS:
            raise ImportError("pip install websocket-client")
        self._ws_url = ws_url or WS_URL
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._api_key: Optional[str] = None
        self._api_secret: Optional[str] = None
        self._connected = False
        self._explicitly_closed = False
        self._reconnect_attempts = 0
        self._max_reconnects = 10
        self._current_symbol = "BTCUSDT"
        self._current_resolution = 60
        self._ticker_fields: list[str] = []
        self._ticker_index_map: dict[str, int] = {}

        # Async Dispatch Queue
        self._queue: queue.Queue = queue.Queue()
        self._processor_thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        self.on_price_update: Optional[Callable[[float], None]] = None
        self.on_candle_update: Optional[Callable[[list[Candle]], None]] = None
        self.on_ticker_update: Optional[Callable[[TickerData], None]] = None
        self.on_wallet_update: Optional[Callable[[Wallet], None]] = None
        self.on_positions_update: Optional[Callable[[list[Position]], None]] = None
        self.on_tick: Optional[Callable[[float, str, int], None]] = None
        self.on_trade: Optional[Callable[[list[dict]], None]] = None
        self.on_orderbook: Optional[Callable[[dict], None]] = None

        # Dispatch Table for O(1) message routing
        self._dispatch_keys = {
            "kline_p": self._handle_kline,
            "kline": self._handle_kline,
            "tick_p": self._handle_tick,
            "trades_p": self._handle_trades,
            "orderbook_p": self._handle_orderbook,
            "accounts_p": self._handle_aop,
            "positions_p": self._handle_aop,
        }
        self._dispatch_methods = {
            "perp_market24h_pack_p.update": self._handle_ticker,
        }

    @property
    def connected(self) -> bool:
        return self._connected

    def set_credentials(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret

    def connect(self, symbol: str = "BTCUSDT", resolution: int = 60):
        self._current_symbol = symbol
        self._current_resolution = resolution
        self._explicitly_closed = False
        if self._connected:
            return
        _log(f"Connecting to {self._ws_url}...")
        self._ws = websocket.WebSocketApp(
            self._ws_url,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=self._ws.run_forever, 
            kwargs={"ping_interval": 30, "ping_timeout": 10}, 
            daemon=True
        )
        self._thread.start()

        # Start background processor thread
        self._processor_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._processor_thread.start()

        for _ in range(100):
            if self._connected:
                return
            time.sleep(0.1)
        if not self._connected:
            _warn("Connection timed out")

    def disconnect(self):
        self._explicitly_closed = True
        
        # 1. Close WebSocket Connection
        if self._ws:
            self._ws.close()
        
        # 2. Join WS Thread (give it 2s to finish teardown)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._ws = None
        self._thread = None
        self._connected = False

        # 3. Shutdown Processor Thread
        if self._queue:
            self._queue.put(None)
            if self._processor_thread and self._processor_thread.is_alive():
                self._processor_thread.join(timeout=2.0)
        self._processor_thread = None

    def update_subscription(self, symbol: str, resolution: int = 60):
        self._current_symbol = symbol
        self._current_resolution = resolution
        self._subscribe_market(symbol, resolution)

    # ── WS Callbacks ─────────────────────────────────────────────────────────

    def _on_open(self, ws):
        _log("✅ Connected")
        self._connected = True
        self._reconnect_attempts = 0
        if self.on_connected:
            self.on_connected()
        if self._api_key and self._api_secret:
            self._authenticate()
        self._subscribe_market(self._current_symbol, self._current_resolution)

    def _on_message(self, ws, data: str):
        """Put raw data into the queue for async processing."""
        if not self._explicitly_closed:
            self._queue.put(data)

    def _process_queue(self):
        """Background thread loop for processing messages and triggering callbacks."""
        while True:
            data = self._queue.get()
            if data is None: # Shutdown sentinel
                break

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                self._queue.task_done()
                continue

            if msg.get("error"):
                _warn(f"API Error {msg['error'].get('code')}: {msg['error'].get('message')}")
                self._queue.task_done()
                continue

            # 1. Dispatch by Method
            method = msg.get("method")
            if method in self._dispatch_methods:
                try:
                    self._dispatch_methods[method](msg)
                except Exception as e:
                    _warn(f"Handler error (method={method}): {e}")
                self._queue.task_done()
                continue

            # 2. Dispatch by Data Key
            handled = False
            for key, handler in self._dispatch_keys.items():
                if key in msg:
                    try:
                        handler(msg if key in ("trades_p", "orderbook_p", "accounts_p", "positions_p") else msg[key])
                    except Exception as e:
                        _warn(f"Handler error (key={key}): {e}")
                    handled = True
                    break
            
            if not handled:
                # Potential unhandled AOP or other message
                pass

            self._queue.task_done()

    def _on_error(self, ws, error):
        _warn(f"Error: {error}")

    def _on_close(self, ws, code, msg):
        _log(f"Closed (code={code})")
        self._connected = False
        if self.on_disconnected:
            self.on_disconnected()
        if not self._explicitly_closed:
            self._schedule_reconnect()

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _handle_kline(self, kline):
        rows = kline if isinstance(kline, list) else kline.get("rows", [kline])
        candles = self._parse_rows(rows)
        if candles and self.on_candle_update:
            self.on_candle_update(candles)
        if candles and self.on_price_update:
            self.on_price_update(candles[-1].close)

    def _handle_ticker(self, payload: dict):
        fields = payload.get("fields", [])
        data = payload.get("data", [])
        if not fields or not data:
            return

        # Optimization: Cache index mapping only if fields change
        if fields != self._ticker_fields:
            self._ticker_fields = fields
            self._ticker_index_map = {f: i for i, f in enumerate(fields)}

        si = self._ticker_index_map.get("symbol", -1)
        if si == -1:
            return

        row = next((r for r in data if r[si] == self._current_symbol), None)
        if not row:
            return

        def gv(k):
            idx = self._ticker_index_map.get(k, -1)
            return float(row[idx]) if idx != -1 and row[idx] else 0.0
        ticker = TickerData(
            symbol=self._current_symbol,
            last_price=gv("lastRp"), mark_price=gv("markRp"),
            index_price=gv("indexRp"), high_24h=gv("highRp"),
            low_24h=gv("lowRp"), volume_24h=gv("volumeRq"),
            open_interest=gv("openInterestRv"),
            funding_rate=gv("fundingRateRr"),
            pred_funding_rate=gv("predFundingRateRr"),
            bid=gv("bidRp"), ask=gv("askRp"),
        )
        if self.on_ticker_update:
            self.on_ticker_update(ticker)
        price = ticker.last_price or ticker.mark_price
        if price > 0 and self.on_price_update:
            self.on_price_update(price)

    def _handle_tick(self, tick: dict):
        price = float(tick.get("last", 0))
        symbol = tick.get("symbol", "")
        timestamp = tick.get("timestamp", 0)
        if self.on_tick:
            self.on_tick(price, symbol, timestamp)
        if price > 0 and self.on_price_update:
            self.on_price_update(price)

    def _handle_trades(self, msg: dict):
        trades_raw = msg.get("trades_p", [])
        trades = []
        for row in trades_raw:
            ts = int(row[0])
            if ts > 2_000_000_000_000_000:
                ts = ts // 1_000_000_000  # ns → s
            elif ts > 2_000_000_000_000:
                ts = ts // 1_000  # ms → s
            trades.append({"time": ts, "side": row[1], "price": float(row[2]), "qty": float(row[3])})
        if trades and self.on_trade:
            self.on_trade(trades)
        if trades and self.on_price_update:
            self.on_price_update(trades[-1]["price"])

    def _handle_orderbook(self, msg: dict):
        book = msg.get("orderbook_p", {})
        parsed = {
            "asks": [[float(a[0]), float(a[1])] for a in book.get("asks", [])],
            "bids": [[float(b[0]), float(b[1])] for b in book.get("bids", [])],
            "type": msg.get("type", ""),
            "sequence": msg.get("sequence", 0),
        }
        if self.on_orderbook:
            self.on_orderbook(parsed)

    def _handle_aop(self, msg: dict):
        if msg.get("positions_p"):
            positions = []
            for p in msg["positions_p"]:
                positions.append(Position(
                    symbol=p.get("symbol", ""),
                    side=p.get("side", "Buy"),
                    size=float(p.get("size", "0")),
                    entry_price=float(p.get("avgEntryPriceRp", "0")),
                    mark_price=float(p.get("markPriceRp", "0")),
                    liquidation_price=float(p.get("liquidationPriceRp", "0")),
                    leverage=abs(float(p.get("leverageRr", "0"))),
                    unrealized_pnl=float(p.get("unrealisedPnlRv", "0")),
                    margin=float(p.get("usedBalanceRv", "0")),
                    pos_side=p.get("posSide", "Merged"),
                ))
            if self.on_positions_update:
                self.on_positions_update(positions)
        if msg.get("accounts_p") and msg["accounts_p"]:
            acc = msg["accounts_p"][0]
            bal = float(acc.get("accountBalanceRv", "0"))
            used = float(acc.get("totalUsedBalanceRv", "0"))
            if self.on_wallet_update:
                self.on_wallet_update(Wallet("USDT", bal, bal - used, used))

    # ── Auth & Subscriptions ─────────────────────────────────────────────────

    def _authenticate(self):
        expiry = int(time.time()) + 60
        sig = sign_hmac(self._api_secret, f"{self._api_key}{expiry}")
        self._send({"id": 99, "method": "user.auth", "params": ["API", self._api_key, sig, expiry]})
        _log("🔐 Auth sent")

    def subscribe_tick(self, symbol: str):
        """Subscribe to price tick events (index price, low latency)."""
        if not self._connected:
            return
        self._send({"id": 104, "method": "tick_p.subscribe", "params": [f".{symbol}"]})

    def subscribe_trades(self, symbol: str):
        """Subscribe to live trade stream (last-trade price, side, qty)."""
        if not self._connected:
            return
        self._send({"id": 105, "method": "trade_p.subscribe", "params": [symbol]})

    def subscribe_orderbook(self, symbol: str):
        """Subscribe to incremental orderbook updates."""
        if not self._connected:
            return
        self._send({"id": 106, "method": "orderbook_p.subscribe", "params": [symbol]})

    def _subscribe_market(self, symbol: str, resolution: int = 60):
        if not self._connected:
            return
        self._send({"id": 101, "method": "kline_p.subscribe", "params": [symbol, resolution]})
        self._send({"id": 102, "method": "perp_market24h_pack_p.subscribe", "params": []})
        self._send({"id": 106, "method": "orderbook_p.subscribe", "params": [symbol]})
        if self._api_key:
            self._send({"id": 103, "method": "aop_p.subscribe", "params": []})
        _log(f"📊 Subscribed: {symbol} @ {resolution // 60}m + Orderbook")

    def _send(self, payload: dict):
        if self._ws:
            try:
                self._ws.send(json.dumps(payload))
            except Exception as e:
                _warn(f"Send failed: {e}")

    # ── Reconnect ────────────────────────────────────────────────────────────

    def _schedule_reconnect(self):
        if self._reconnect_attempts >= self._max_reconnects:
            _warn("Max reconnects reached")
            return
        delay = min(1.0 * (2 ** self._reconnect_attempts), 30.0)
        self._reconnect_attempts += 1
        _log(f"Reconnecting in {delay:.0f}s (#{self._reconnect_attempts})...")
        t = threading.Timer(delay, lambda: self.connect(self._current_symbol, self._current_resolution))
        t.daemon = True
        t.start()

    @staticmethod
    def _parse_rows(rows: list) -> list[Candle]:
        candles = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            t = int(row[0])
            if t > 2_000_000_000:
                t //= 1000
            candles.append(Candle(t, float(row[3]), float(row[4]), float(row[5]), float(row[6]),
                                  float(row[7]) if len(row) > 7 else 0.0))
        return candles
