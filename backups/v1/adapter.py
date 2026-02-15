"""
Adapter — The Execution Bridge.

Implements the IExchange contract using direct HTTPS calls to Phemex.
Handles HMAC signing, rate limit tracking, and response normalization.

This is the layer that translates between Stratos-standard types
and Phemex-specific API formats.
"""

from __future__ import annotations
import uuid
import time
import requests
from typing import Optional
from urllib.parse import urlencode

from .config import REST_BASE, IS_TESTNET, sign_hmac
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
        self._is_testnet = is_testnet
        self._base = base_url or REST_BASE
        self._rate_limit_used = 0  # 0-100%

    # ── IExchange: Execution ─────────────────────────────────────────────────

    def place_order(self, req: PlaceOrderRequest) -> OrderResult:
        """Place a new order on Phemex."""
        endpoint = "/g-orders/create"

        payload = {
            "symbol": req.symbol,
            "clOrdID": req.cl_ord_id or str(uuid.uuid4()),
            "side": req.side,
            "orderQtyRq": str(req.qty),
            "ordType": req.type,
            "timeInForce": req.time_in_force,
            "reduceOnly": str(req.reduce_only).lower(),
            "posSide": req.pos_side,
        }

        if req.price is not None:
            payload["priceRp"] = str(req.price)

        if req.stop_loss is not None:
            payload["stopLossRp"] = str(req.stop_loss)

        if req.take_profit is not None:
            payload["takeProfitRp"] = str(req.take_profit)

        if req.trigger_price is not None:
            payload["stopPxRp"] = str(req.trigger_price)

        if req.trigger_type:
            payload["triggerType"] = req.trigger_type

        if req.close_on_trigger:
            payload["closeOnTrigger"] = True

        if req.tp_limit_price is not None:
            payload["tpPxRp"] = str(req.tp_limit_price)

        if req.sl_limit_price is not None:
            payload["slPxRp"] = str(req.sl_limit_price)

        if req.tp_trigger:
            payload["tpTrigger"] = req.tp_trigger

        if req.sl_trigger:
            payload["slTrigger"] = req.sl_trigger

        if req.peg_offset_value is not None:
            payload["pegOffsetValueRp"] = str(req.peg_offset_value)

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
            payload["priceRp"] = str(req.price)
        if req.qty is not None:
            payload["orderQtyRq"] = str(req.qty)
        if req.trigger_price is not None:
            payload["stopPxRp"] = str(req.trigger_price)
        if req.take_profit is not None:
            payload["takeProfitRp"] = str(req.take_profit)
        if req.stop_loss is not None:
            payload["stopLossRp"] = str(req.stop_loss)
        if req.peg_offset_value is not None:
            payload["pegOffsetValueRp"] = str(req.peg_offset_value)
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

    def cancel_all(self, symbol: str, untriggered_only: bool = False) -> None:
        """Cancel all open orders for a symbol."""
        endpoint = "/g-orders/all"
        payload = {
            "symbol": symbol,
            "untriggered": str(untriggered_only).lower(),
        }
        self._request("DELETE", endpoint, payload)

    def cancel_orders(self, symbol: str, order_ids: list[str], pos_side: str = "Merged") -> None:
        """
        Bulk cancel specific orders.
        Optimization: Uses single request to cancel multiple IDs.
        """
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
            positions.append(PositionInfo(
                symbol=p.get("symbol", ""),
                side=p.get("side", "Buy"),
                size=float(p.get("size", "0")),
                entry_price=float(p.get("avgEntryPriceRp", "0")),
                unrealized_pnl=float(p.get("unrealisedPnlRv", "0")),
                leverage=float(p.get("leverageRr", p.get("leverageEr", "0"))),
                liquidation_price=float(p.get("liquidationPriceRp", "0")),
                margin=float(p.get("usedBalanceRv", "0")),
                pos_side=p.get("posSide", "Merged"),
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
        # Optimization: Phemex requires unencoded commas in signature for lists
        # even though they are encoded in the URL.
        query_string_sig = query_string.replace("%2C", ",")
        
        sign_string = f"{endpoint}{query_string_sig}{expiry}"
        signature = sign_hmac(self._api_secret, sign_string)

        headers = {
            "x-phemex-access-token": self._api_key,
            "x-phemex-request-expiry": str(expiry),
            "x-phemex-request-signature": signature,
        }

        resp = requests.request(method, full_url, headers=headers, timeout=10)
        json_data = resp.json()

        # Rate limit tracking
        remaining = resp.headers.get("x-ratelimit-remaining-contract")
        if remaining:
            rem = int(remaining)
            if rem < 50:
                _warn(f"Low Rate Limit: {remaining}")
            self._rate_limit_used = max(0, 100 - (rem / 500) * 100)

        # Handle list responses (e.g. from api-data endpoints)
        if isinstance(json_data, list):
            return {"data": {"rows": json_data}}

        # Error handling for object responses
        code = json_data.get("code", json_data.get("error", {}).get("code"))
        if code is not None and code != 0:
            msg = json_data.get("msg", json_data.get("error", {}).get("message", ""))
            raise RuntimeError(f"API Error {code}: {msg}")

        return json_data
