"""
Adapter — The Execution Bridge.

Implements the IExchange contract using direct HTTPS calls to Phemex.
Handles HMAC signing, rate limit tracking, and response normalization.
CRITICAL: Handles float truncation based on Product specs to prevent rejection.
"""

from __future__ import annotations
import uuid
import time
import math
import os
import requests
from typing import Optional
from urllib.parse import urlencode

from .config import REST_BASE, IS_TESTNET, sign_hmac, API_KEY_BYTES, API_SECRET_BYTES, sign_hmac_bytes
from .models import (
    PlaceOrderRequest,
    AmendOrderRequest,
    CancelOrderRequest,
    OrderResult,
    AccountInfo,
    Balance,
    PositionInfo,
    OrderbookSnapshot,
    OrderbookLevel,
    Product
)


# ── Logging ──────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(f"  [ADAPTER] {msg}")


def _warn(msg: str):
    print(f"  [ADAPTER] ⚠ {msg}")


# ── Raw Order Type (Phemex-native format) ────────────────────────────────────

class RawOpenOrder(dict):
    """Dict subclass for Phemex raw order data with convenient accessors."""

    @property
    def order_id(self) -> str:
        return str(self.get("orderID") or self.get("orderId") or "")

    @property
    def cl_ord_id(self) -> str:
        return str(self.get("clOrdID") or self.get("clOrdId") or "")

    @property
    def symbol(self) -> str:
        return str(self.get("symbol") or "")

    @property
    def side(self) -> str:
        return str(self.get("side") or "")

    @property
    def price(self) -> str:
        return str(self.get("priceRp") or self.get("priceEp") or "0")

    @property
    def qty(self) -> str:
        return str(self.get("orderQtyRq") or self.get("orderQty") or "0")

    @property
    def order_type(self) -> str:
        return str(self.get("ordType") or self.get("orderType") or "")

    @property
    def status(self) -> str:
        return str(self.get("ordStatus") or "")

    @property
    def stop_price(self) -> str:
        return str(self.get("stopPxRp") or "0")


# ── The Adapter ──────────────────────────────────────────────────────────────

class PhemexAdapter:
    """
    Execution bridge: translates Stratos-standard requests into
    Phemex API calls with HMAC signing.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool = IS_TESTNET,
        base_url: Optional[str] = None,
    ):
        self.name = "PhemexAdapter"
        self.is_simulated = is_testnet

        self._api_key = api_key
        self._api_secret = api_secret
        self._api_key_bytes = API_KEY_BYTES if api_key == os.getenv("PHEMEX_TESTNET_KEY", os.getenv("PHEMEX_MAINNET_KEY")) else api_key.encode("utf-8")
        self._api_secret_bytes = API_SECRET_BYTES if api_secret == os.getenv("PHEMEX_TESTNET_SECRET", os.getenv("PHEMEX_MAINNET_SECRET")) else api_secret.encode("utf-8")
        self._is_testnet = is_testnet
        self._base = base_url or REST_BASE
        self._rate_limit_used = 0.0  # 0-100%
        self.session = requests.Session()

        # Cache for product precision specs (Symbol -> Product)
        self._products: dict[str, Product] = {}
        self._qty_multipliers: dict[str, float] = {}
        self._price_multipliers: dict[str, float] = {}

    def set_products(self, products: list[Product]):
        """Populate local cache of product specs for precision formatting."""
        self._products = {p.symbol: p for p in products}
        self._qty_multipliers = {p.symbol: float(10**p.qty_precision) for p in products}
        self._price_multipliers = {p.symbol: float(10**p.price_precision) for p in products}

    # ── Formatting Helpers ───────────────────────────────────────────────────

    def _fmt_qty(self, symbol: str, qty: float) -> str:
        """Format quantity: Floor to step size via multiplier."""
        m = self._qty_multipliers.get(symbol)
        p = self._products.get(symbol)
        if m is None or p is None:
            return str(qty)

        # Using multiplier avoids float division overhead
        truncated = math.floor(qty * m) / m
        return f"{truncated:.{p.qty_precision}f}"

    def _fmt_price(self, symbol: str, price: float) -> str:
        """Format price: Round to tick size via multiplier."""
        m = self._price_multipliers.get(symbol)
        p = self._products.get(symbol)
        if m is None or p is None:
            return str(price)

        # Round via multiplier for speed and precision
        rounded = round(price * m) / m
        return f"{rounded:.{p.price_precision}f}"

    # ── IExchange: Execution ─────────────────────────────────────────────────

    def place_order(self, req: PlaceOrderRequest) -> OrderResult:
        """Place a new order on Phemex."""
        endpoint = "/g-orders/create"

        payload = {
            "symbol": req.symbol,
            "clOrdID": req.cl_ord_id or str(uuid.uuid4()),
            "side": req.side,
            "orderQtyRq": self._fmt_qty(req.symbol, req.qty),
            "ordType": req.type,
            "timeInForce": req.time_in_force,
            "reduceOnly": str(req.reduce_only).lower(),
            "posSide": req.pos_side,
        }

        if req.price is not None:
            payload["priceRp"] = self._fmt_price(req.symbol, req.price)

        if req.stop_loss is not None:
            payload["stopLossRp"] = self._fmt_price(req.symbol, req.stop_loss)

        if req.take_profit is not None:
            payload["takeProfitRp"] = self._fmt_price(req.symbol, req.take_profit)

        if req.trigger_price is not None:
            payload["stopPxRp"] = self._fmt_price(req.symbol, req.trigger_price)

        if req.trigger_type:
            payload["triggerType"] = req.trigger_type

        if req.close_on_trigger:
            payload["closeOnTrigger"] = True

        if req.tp_limit_price is not None:
            payload["tpPxRp"] = self._fmt_price(req.symbol, req.tp_limit_price)

        if req.sl_limit_price is not None:
            payload["slPxRp"] = self._fmt_price(req.symbol, req.sl_limit_price)

        if req.tp_trigger:
            payload["tpTrigger"] = req.tp_trigger

        if req.sl_trigger:
            payload["slTrigger"] = req.sl_trigger

        if req.peg_offset_value is not None:
            # Offset usually follows price precision
            payload["pegOffsetValueRp"] = self._fmt_price(req.symbol, req.peg_offset_value)

        if req.peg_price_type:
            payload["pegPriceType"] = req.peg_price_type

        if req.stp_instruction:
            payload["stpInstruction"] = req.stp_instruction

        if req.text:
            payload["text"] = req.text

        res = self._request("PUT", endpoint, payload)
        data = res.get("data", {})

        status = data.get("ordStatus", "")
        if status == "Created":
            status = "New"

        return OrderResult(
            order_id=data.get("orderID", ""),
            cl_ord_id=data.get("clOrdID", ""),
            status=status,
            avg_price=float(data.get("avgPriceRp", "0")),
            cum_qty=float(data.get("cumQtyRq", "0")),
        )

    def amend_order(self, req: AmendOrderRequest) -> OrderResult:
        """Amend an existing order (change price/qty)."""
        endpoint = "/g-orders/replace"

        payload = {
            "symbol": req.symbol,
            "posSide": req.pos_side,
        }

        if req.order_id:
            payload["orderID"] = req.order_id
        if req.cl_ord_id:
            payload["origClOrdID"] = req.cl_ord_id

        if req.price is not None:
            payload["priceRp"] = self._fmt_price(req.symbol, req.price)
        if req.qty is not None:
            payload["orderQtyRq"] = self._fmt_qty(req.symbol, req.qty)
        if req.trigger_price is not None:
            payload["stopPxRp"] = self._fmt_price(req.symbol, req.trigger_price)
        if req.take_profit is not None:
            payload["takeProfitRp"] = self._fmt_price(req.symbol, req.take_profit)
        if req.stop_loss is not None:
            payload["stopLossRp"] = self._fmt_price(req.symbol, req.stop_loss)

        if req.peg_offset_value is not None:
            payload["pegOffsetValueRp"] = self._fmt_price(req.symbol, req.peg_offset_value)
        if req.peg_price_type:
            payload["pegPriceType"] = req.peg_price_type
        if req.trigger_type:
            payload["triggerType"] = req.trigger_type

        res = self._request("PUT", endpoint, payload)
        data = res.get("data", {})

        return OrderResult(
            order_id=data.get("orderID", ""),
            cl_ord_id=data.get("clOrdID", ""),
            status=data.get("ordStatus", ""),
            avg_price=float(data.get("avgPriceRp", "0")),
            cum_qty=float(data.get("cumQtyRq", "0")),
        )

    def cancel_order(self, req: CancelOrderRequest) -> None:
        """Cancel a single order by orderID or clOrdID."""
        endpoint = "/g-orders/cancel"
        payload = {
            "symbol": req.symbol,
            "posSide": req.pos_side,
        }
        if req.order_id:
            payload["orderID"] = req.order_id
        if req.cl_ord_id:
            payload["clOrdID"] = req.cl_ord_id
        self._request("DELETE", endpoint, payload)

    def cancel_all(self, symbol: str, untriggered_only: bool = False, pos_side: str = "Merged") -> None:
        """
        Cancel all open orders for a symbol.
        Note: Phemex docs don't officially list posSide for /g-orders/all,
        but we pass it to support Hedge Mode disambiguation if supported.
        """
        endpoint = "/g-orders/all"
        payload = {
            "symbol": symbol,
            "untriggered": str(untriggered_only).lower(),
            "posSide": pos_side,
        }
        self._request("DELETE", endpoint, payload)

    def cancel_orders(self, symbol: str, order_ids: list[str], pos_side: str = "Merged") -> None:
        """Bulk cancel specific orders."""
        if not order_ids:
            return

        endpoint = "/g-orders"
        # Join IDs with comma. requests/urlencode will encode this as %2C
        # but _request will handle unescaping for signature.
        payload = {
            "symbol": symbol,
            "orderID": ",".join(order_ids),
            "posSide": pos_side,
        }
        self._request("DELETE", endpoint, payload)

    def query_orders(self, symbol: str, order_ids: list[str]) -> list[RawOpenOrder]:
        """Query specific orders by ID (Batch)."""
        if not order_ids:
            return []

        endpoint = "/api-data/g-futures/orders/by-order-id"
        payload = {
            "symbol": symbol,
            "orderID": ",".join(order_ids),
        }
        res = self._request("GET", endpoint, payload)
        rows = res.get("data", {}).get("rows", [])
        return [RawOpenOrder(o) for o in rows]

    # ── IExchange: Data ──────────────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        """Fetch account balance and positions."""
        endpoint = "/g-accounts/accountPositions"
        res = self._request("GET", endpoint, {"currency": "USDT"})
        data = res.get("data", {})

        account = data.get("account", {})
        positions_raw = data.get("positions", [])

        balance_total = float(account.get("accountBalanceRv", "0"))
        balance_used = float(account.get("totalUsedBalanceRv", "0"))

        positions = []
        for p in positions_raw:
            size = float(p.get("size", "0"))
            if abs(size) == 0:
                continue

            side = p.get("side", "Buy")
            pos_side = p.get("posSide", "Merged")
            # Long if posSide is Long, OR Merged and side is Buy
            is_long = (pos_side == "Long") or (pos_side == "Merged" and side == "Buy")
            multiplier = 1.0 if is_long else -1.0

            positions.append(PositionInfo(
                symbol=p.get("symbol", ""),
                side=side,
                size=size,
                entry_price=float(p.get("avgEntryPriceRp", "0")),
                unrealized_pnl=float(p.get("unrealisedPnlRv", "0")),
                leverage=float(p.get("leverageRr", p.get("leverageEr", "0"))),
                liquidation_price=float(p.get("liquidationPriceRp", "0")),
                margin=float(p.get("usedBalanceRv", "0")),
                pos_side=pos_side,
                side_multiplier=multiplier,
            ))

        return AccountInfo(
            balance=Balance(
                total=balance_total,
                used=balance_used,
                available=balance_total - balance_used,
            ),
            positions=positions,
        )

    def get_rate_limit_usage(self) -> float:
        """Return current rate limit estimate (0-100%)."""
        return self._rate_limit_used

    # ── Extended Methods ─────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[PositionInfo]:
        """Get a specific position by symbol."""
        account = self.get_account_info()
        for p in account.positions:
            if p.symbol == symbol and abs(p.size) > 0:
                return p
        return None

    def query_open_orders(self, symbol: str) -> list[RawOpenOrder]:
        """Query all open orders for a symbol (raw Phemex format)."""
        endpoint = "/g-orders/activeList"
        res = self._request("GET", endpoint, {"symbol": symbol})
        rows = res.get("data", {}).get("rows", [])
        return [RawOpenOrder(o) for o in rows]

    def query_closed_orders(self, symbol: str, limit: int = 20) -> list[RawOpenOrder]:
        """Query recent closed/filled orders."""
        endpoint = "/exchange/order/v2/orderList"
        res = self._request("GET", endpoint, {
            "symbol": symbol,
            "ordStatus": "Filled,Canceled",
            "limit": limit,
        })
        rows = res.get("data", {}).get("rows", [])
        return [RawOpenOrder(o) for o in rows]

    def query_order_history(
        self, symbol: str, limit: int = 20, offset: int = 0,
        start: Optional[int] = None, end: Optional[int] = None,
    ) -> list[dict]:
        """Query order history with time range support (ms timestamps)."""
        params: dict = {"symbol": symbol, "currency": "USDT", "offset": offset, "limit": limit}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        res = self._request("GET", "/api-data/g-futures/orders", params)
        return res.get("data", {}).get("rows", [])

    def query_trades_history(
        self, symbol: str, limit: int = 20, offset: int = 0,
        start: Optional[int] = None, end: Optional[int] = None,
    ) -> list[dict]:
        """Query fill/execution history (ms timestamps, max 90 days)."""
        params: dict = {"symbol": symbol, "currency": "USDT", "offset": offset, "limit": limit}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        res = self._request("GET", "/api-data/g-futures/trades", params)
        return res.get("data", {}).get("rows", [])

    def query_funding_fees(self, symbol: str, limit: int = 20, offset: int = 0) -> list[dict]:
        """Query funding fee payment history."""
        res = self._request("GET", "/api-data/g-futures/funding-fees", {
            "symbol": symbol, "offset": offset, "limit": limit,
        })
        return res.get("data", {}).get("rows", [])

    def query_closed_positions(self, symbol: str, limit: int = 20, offset: int = 0) -> list[dict]:
        """Query closed position history (PnL, ROI, fees)."""
        res = self._request("GET", "/api-data/g-futures/closedPosition", {
            "symbol": symbol, "currency": "USDT", "offset": offset, "limit": limit,
        })
        return res.get("data", {}).get("rows", [])

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage for a symbol."""
        endpoint = "/g-positions/leverage"
        self._request("PUT", endpoint, {
            "symbol": symbol,
            "leverageRr": str(leverage),
        })

    def switch_position_mode(self, symbol: str, mode: str) -> None:
        """Switch between OneWay and Hedged position mode for a symbol."""
        if mode not in ("OneWay", "Hedged"):
            raise ValueError(f"Invalid position mode: {mode}. Must be OneWay or Hedged")
        self._request("PUT", "/g-positions/switch-pos-mode-sync", {
            "symbol": symbol,
            "targetPosMode": mode,
        })

    def assign_position_balance(self, symbol: str, pos_side: str, balance: float) -> None:
        """Adjust margin for an isolated-mode position."""
        self._request("POST", "/g-positions/assign", {
            "symbol": symbol,
            "posSide": pos_side,
            "posBalanceRv": str(balance),
        })

    def query_orderbook(self, symbol: str) -> OrderbookSnapshot:
        """Fetch orderbook via direct (unsigned) endpoint."""
        try:
            resp = requests.get(
                f"{self._base}/md/v2/orderbook",
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            json_data = resp.json()

            if json_data.get("error"):
                raise ValueError(json_data["error"].get("message", ""))

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

    # ── Internal: Signed Request ─────────────────────────────────────────────

    def _request(self, method: str, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make an authenticated request with HMAC-SHA256 signing."""
        params = params or {}
        expiry = int(time.time()) + 60

        # Phemex G-API uses query string for all methods
        query_string = urlencode(params)
        path = endpoint + (f"?{query_string}" if query_string else "")
        full_url = f"{self._base}{path}"

        # Signature: HMAC(endpoint + queryString + expiry)
        query_string_sig = query_string.replace("%2C", ",")
        sign_string = f"{endpoint}{query_string_sig}{expiry}"
        signature = sign_hmac_bytes(self._api_secret_bytes, sign_string.encode("utf-8"))

        headers = {
            "x-phemex-access-token": self._api_key,
            "x-phemex-request-expiry": str(expiry),
            "x-phemex-request-signature": signature,
        }

        # Rate Limit Safety
        if self._rate_limit_used > 95:
            time.sleep(1.0)

        resp = self.session.request(method, full_url, headers=headers, timeout=10)
        json_data = resp.json()

        # Rate limit tracking
        remaining = resp.headers.get("x-ratelimit-remaining-contract")
        if remaining:
            rem = int(remaining)
            limit = int(resp.headers.get("x-ratelimit-limit-contract", 500))
            if rem < 50:
                _warn(f"Low Rate Limit: {remaining}")

            # Update usage % (invert remaining)
            self._rate_limit_used = max(0, 100 - (rem / limit) * 100)

        # Handle list responses (e.g. from api-data endpoints)
        if isinstance(json_data, list):
            return {"data": {"rows": json_data}}

        # Error handling for object responses
        code = json_data.get("code", json_data.get("error", {}).get("code"))
        if code is not None and code != 0:
            msg = json_data.get("msg", json_data.get("error", {}).get("message", ""))
            raise RuntimeError(f"API Error {code}: {msg}")

        return json_data
