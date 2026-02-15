"""
REST Client — Direct HTTP data client for the Phemex API.

Handles:
- Product listing (perpetuals)
- 24h Ticker data
- Kline/Candle data (latest + historical pagination)
- Orderbook snapshots

All public endpoints — no authentication required.
"""

from __future__ import annotations
import time
import requests
import socket
import operator
import orjson as json
from typing import Optional

from .config import REST_BASE, logger
from .models import Product, Candle, TickerData, OrderbookSnapshot, OrderbookLevel


# ── Logging ──────────────────────────────────────────────────────────────────

def _log(msg: str):
    logger.log("REST", msg)


def _warn(msg: str):
    logger.log("REST", f"⚠ {msg}")


def _err(msg: str):
    logger.log("REST", f"❌ {msg}")


# ── The Client ───────────────────────────────────────────────────────────────

class RestClient:
    """Public REST client for Phemex market data."""

    def __init__(self, base_url: Optional[str] = None):
        self.base = base_url or REST_BASE
        self.session = requests.Session()
        
        # Phase 2 Optimization: Disable Nagle's Algorithm (TCP_NODELAY)
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
        # Deep hook into urllib3 to set socket options
        adapter.poolmanager.connection_pool_kw['socket_options'] = [
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ]
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        
        self._in_flight: set[str] = set()

    # ── Products ─────────────────────────────────────────────────────────────

    def fetch_products(self) -> list[Product]:
        """Fetch all listed perpetual products (Migrated to Products-Plus)."""
        try:
            resp = self.session.get(f"{self.base}/public/products-plus", timeout=10)
            resp.raise_for_status()
            json_data = json.loads(resp.content)

            if json_data.get("code") != 0 or not json_data.get("data"):
                _err(f"Failed to fetch products: {json_data.get('msg')}")
                return []

            data = json_data["data"]
            # Phemex Plus API includes nested risk limits
            risk_limits = {}
            for entry in data.get("riskLimitsV2", []):
                symbol = entry.get("symbol")
                inner = entry.get("riskLimits", [{}])[0] # Get base tier
                risk_limits[symbol] = {
                    "limit": float(inner.get("limit", 0)),
                    "maxLeverage": 1.0 / float(inner.get("initialMarginRr", 0.01))
                }
            
            list1 = data.get("products", [])
            list2 = data.get("perpProductsV2", [])

            products = []
            for p in [*list1, *list2]:
                symbol = p["symbol"]
                if p.get("status") != "Listed":
                    continue

                # We only care about Perpetuals for this engine
                p_type = p.get("type", "")
                if p_type not in ("Perpetual", "PerpetualV2"):
                    continue

                tick_size = float(p.get("tickSize", "0.01"))
                qty_step = float(p.get("qtyStepSize", "0.001"))

                p_prec = p.get("pricePrecision")
                if p_prec is None:
                    p_prec = 0
                    ts = tick_size
                    while ts < 1:
                        ts *= 10
                        p_prec += 1

                q_prec = p.get("qtyPrecision")
                if q_prec is None:
                    q_prec = 0
                    qs = qty_step
                    while qs < 1:
                        qs *= 10
                        q_prec += 1

                # Extract Risk Limit data if available
                rl = risk_limits.get(symbol, {})
                max_lev = float(rl.get("maxLeverage", 100))
                max_pos = float(rl.get("limit", 0))

                products.append(Product(
                    symbol=symbol,
                    base_currency=p.get("baseCurrency", ""),
                    quote_currency=p.get("quoteCurrency", ""),
                    price_scale=p.get("priceScale", 4),
                    ratio_scale=p.get("ratioScale", 8),
                    value_scale=p.get("valueScale", 8),
                    tick_size=tick_size,
                    qty_step_size=qty_step,
                    price_precision=int(p_prec),
                    qty_precision=int(q_prec),
                    max_leverage=max_lev,
                    max_position_size=max_pos,
                ))

            _log(f"Loaded {len(products)} perpetual products with risk limits")
            return products

        except Exception as e:
            _err(f"Network error fetching products: {e}")
            return []

    # ── 24h Ticker ───────────────────────────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> Optional[TickerData]:
        """Fetch 24h ticker stats for a symbol."""
        try:
            resp = self.session.get(
                f"{self.base}/md/v3/ticker/24hr",
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            json_data = json.loads(resp.content)

            if json_data.get("error") or not json_data.get("result"):
                _warn(f"Ticker fetch failed: {json_data.get('error')}")
                return None

            return self._process_ticker(json_data["result"], symbol)

        except Exception as e:
            _warn(f"Error in fetch_ticker: {e}")
            return None

    # ── Candles (Latest) ─────────────────────────────────────────────────────

    def fetch_candles(
        self,
        symbol: str,
        resolution: int = 3600,
        limit: int = 100,
        end_timestamp: Optional[int] = None,
    ) -> list[Candle]:
        """Fetch latest N candles from /kline/last."""
        to = end_timestamp or int(time.time())

        try:
            url = f"{self.base}/exchange/public/md/v2/kline/last"
            resp = self.session.get(url, params={
                "symbol": symbol,
                "to": to,
                "resolution": resolution,
                "limit": limit,
            }, timeout=10)
            resp.raise_for_status()
            json_data = json.loads(resp.content)

            if json_data.get("code") != 0 or not json_data.get("data", {}).get("rows"):
                return []

            return self._parse_rows(json_data["data"]["rows"])

        except Exception as e:
            _err(f"Error fetching candles: {e}")
            return []

    # ── Candles (Historical Pagination) ──────────────────────────────────────

    def fetch_historical_candles(
        self,
        symbol: str,
        end_timestamp: int,
        resolution: int = 3600,
        limit: int = 1000,
    ) -> list[Candle]:
        """Fetch historical candles from /kline/list with from+to pagination."""
        flight_key = f"{symbol}:{resolution}:{end_timestamp}"
        if flight_key in self._in_flight:
            return []
        self._in_flight.add(flight_key)

        try:
            to = end_timestamp
            _from = to - (limit * resolution)

            url = f"{self.base}/exchange/public/md/v2/kline/list"
            resp = self.session.get(url, params={
                "symbol": symbol,
                "from": _from,
                "to": to,
                "resolution": resolution,
            }, timeout=10)
            resp.raise_for_status()
            json_data = json.loads(resp.content)

            if json_data.get("code") != 0 or not json_data.get("data", {}).get("rows"):
                if json_data.get("code") != 0:
                    _warn(f"Kline fetch: {json_data.get('msg')}")
                return []

            candles = self._parse_rows(json_data["data"]["rows"])
            _log(f"Fetched {len(candles)} historical candles for {symbol} @ {resolution}s")
            return candles

        except Exception as e:
            _err(f"Error fetching history: {e}")
            return []
        finally:
            self._in_flight.discard(flight_key)

    # ── Orderbook ────────────────────────────────────────────────────────────

    def fetch_orderbook(self, symbol: str) -> OrderbookSnapshot:
        """Fetch current orderbook snapshot."""
        try:
            resp = self.session.get(
                f"{self.base}/md/v2/orderbook",
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            json_data = json.loads(resp.content)

            if json_data.get("error"):
                raise ValueError(json_data["error"].get("message", "Unknown error"))

            result = json_data.get("result", {})
            book = result.get("orderbook_p", {})

            return OrderbookSnapshot(
                symbol=symbol,
                asks=[OrderbookLevel(price=float(a[0]), size=float(a[1])) for a in book.get("asks", [])],
                bids=[OrderbookLevel(price=float(b[0]), size=float(b[1])) for b in book.get("bids", [])],
                timestamp=result.get("timestamp", 0),
            )

        except Exception as e:
            _warn(f"Failed to fetch orderbook: {e}")
            return OrderbookSnapshot(symbol=symbol)

    # ── Internals ────────────────────────────────────────────────────────────

    @staticmethod
    def _get_val(d: dict, primary: str, *fallbacks: str) -> float:
        for key in (primary, *fallbacks):
            if key in d:
                v = d[key]
                f = float(v) if v else 0.0
                if f > 0: return f
        return 0.0

    @staticmethod
    def _get_funding(d: dict, primary: str, *fallbacks: str) -> float:
        for key in (primary, *fallbacks):
            if key in d:
                f = float(d[key]) if d[key] else 0.0
                return f / 1e8 if abs(f) > 1 else f
        return 0.0

    def _process_ticker(self, d: dict, symbol: str) -> TickerData:
        """Parse ticker response (handles Rp/Rv/Ep field name variants)."""
        gv = self._get_val
        gf = self._get_funding

        return TickerData(
            symbol=symbol,
            last_price=gv(d, "lastRp", "last", "lastPrice", "closeRp"),
            mark_price=gv(d, "markRp", "markPrice", "markPriceRp"),
            index_price=gv(d, "indexRp", "indexPrice", "indexLastPriceRp"),
            high_24h=gv(d, "highRp", "high", "highPriceRp"),
            low_24h=gv(d, "lowRp", "low", "lowPriceRp"),
            volume_24h=gv(d, "volumeRq", "volume", "volume24h", "turnoverRv"),
            open_interest=gv(d, "openInterestRv", "openInterest"),
            funding_rate=gf(d, "fundingRateRr", "fundingRate"),
            pred_funding_rate=gf(d, "predFundingRateRr", "predFundingRate"),
            bid=gv(d, "bidRp", "bid"),
            ask=gv(d, "askRp", "ask"),
        )

    # Optimized item extractor for candle rows
    _candle_getter = operator.itemgetter(0, 3, 4, 5, 6, 7)

    def _parse_rows(self, rows: list) -> list[Candle]:
        """Parse kline row arrays into Candle objects (Optimized Batch Parsing)."""
        if not rows: return []
        
        get = self._candle_getter
        
        # Optimization: Detect scale factor once per batch
        first_ts = int(rows[0][0])
        is_ms = first_ts > 2_000_000_000
        
        if is_ms:
            return [
                Candle(
                    int(data[0]) // 1000,
                    float(data[1]), float(data[2]), float(data[3]), float(data[4]), 
                    float(data[5])
                ) for data in (get(r) for r in rows)
            ]
        else:
            return [
                Candle(
                    int(data[0]),
                    float(data[1]), float(data[2]), float(data[3]), float(data[4]), 
                    float(data[5])
                ) for data in (get(r) for r in rows)
            ]

    @staticmethod
    def format_resolution(seconds: int) -> str:
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 86400:
            return f"{seconds // 3600}h"
        return f"{seconds // 86400}d"
