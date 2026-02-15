"""
Models — Data classes mirroring the IExchange universal contract.

The Universal Contract:
Defines the standard language that the engine uses to talk to ANY exchange.
Phemex calls it `orderQtyRq`. Binance calls it `quantity`.
We just call it `qty`. The Adapter does the translation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional


# ── Standard Enums ───────────────────────────────────────────────────────────

Side = Literal["Buy", "Sell"]
OrderType = Literal["Limit", "Market", "Stop", "StopLimit", "MarketIfTouched", "LimitIfTouched", "Bracket"]
TimeInForce = Literal["GoodTillCancel", "ImmediateOrCancel", "FillOrKill", "PostOnly"]
PositionSide = Literal["Merged", "Long", "Short"]
TriggerType = Literal["ByMarkPrice", "ByLastPrice"]
OrderStatus = Literal["New", "Filled", "PartiallyFilled", "Canceled", "Rejected"]


# ── Request Objects (The "Intent") ───────────────────────────────────────────

@dataclass(slots=True)
class PlaceOrderRequest:
    symbol: str
    side: Side
    type: OrderType
    qty: float
    price: Optional[float] = None          # Required for Limit

    # Risk Management
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    tp_limit_price: Optional[float] = None   # Limit price for TP (tpPxRp)
    sl_limit_price: Optional[float] = None   # Limit price for SL (slPxRp)
    tp_trigger: Optional[str] = None         # TP trigger source (ByMarkPrice, ByLastPrice, etc.)
    sl_trigger: Optional[str] = None         # SL trigger source
    reduce_only: bool = False
    close_on_trigger: bool = False

    # Advanced
    time_in_force: TimeInForce = "GoodTillCancel"
    trigger_price: Optional[float] = None    # For Stop/Conditional orders
    trigger_type: Optional[TriggerType] = None
    peg_offset_value: Optional[float] = None # Trailing offset from current price
    peg_price_type: Optional[str] = None     # TrailingStopPeg, TrailingTakeProfitPeg, etc.
    stp_instruction: Optional[str] = None    # Self-trade prevention: CancelMaker, CancelTaker, CancelBoth

    # Tracking
    cl_ord_id: Optional[str] = None          # Custom ID for tracking
    pos_side: PositionSide = "Merged"        # One-Way Mode default
    text: Optional[str] = None               # Order comment (e.g. strategy tag)


@dataclass(slots=True)
class AmendOrderRequest:
    symbol: str
    order_id: Optional[str] = None           # Exchange ID
    cl_ord_id: Optional[str] = None          # Custom ID

    # Fields to update (None = no change)
    price: Optional[float] = None
    qty: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    trigger_price: Optional[float] = None
    peg_offset_value: Optional[float] = None # Trailing offset
    peg_price_type: Optional[str] = None     # Trailing type
    trigger_type: Optional[str] = None       # Trigger source
    pos_side: PositionSide = "Merged"


@dataclass(slots=True)
class CancelOrderRequest:
    symbol: str
    order_id: Optional[str] = None
    cl_ord_id: Optional[str] = None
    pos_side: PositionSide = "Merged"


# ── Response Objects (The "Result") ──────────────────────────────────────────

@dataclass(slots=True)
class OrderResult:
    order_id: str
    cl_ord_id: str
    status: OrderStatus
    avg_price: float = 0.0
    cum_qty: float = 0.0


@dataclass(slots=True)
class Balance:
    total: float = 0.0       # Equity
    available: float = 0.0   # Free Margin
    used: float = 0.0        # Used Margin


@dataclass(slots=True)
class PositionInfo:
    symbol: str = ""
    side: Side = "Buy"
    size: float = 0.0
    entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: float = 1.0
    liquidation_price: float = 0.0
    margin: float = 0.0
    pos_side: PositionSide = "Merged"
    side_multiplier: float = 1.0
    pnl_factor: float = 0.0  # size * side_multiplier


@dataclass(slots=True)
class AccountInfo:
    balance: Balance = field(default_factory=Balance)
    positions: list[PositionInfo] = field(default_factory=list)


@dataclass(slots=True)
class Wallet:
    currency: str = "USDT"
    balance: float = 0.0
    available: float = 0.0
    used: float = 0.0


# ── Market Data ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Candle:
    time: int = 0       # Unix timestamp (seconds)
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0


@dataclass(slots=True)
class TickerData:
    symbol: str = ""
    last_price: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    volume_24h: float = 0.0
    open_interest: float = 0.0
    funding_rate: float = 0.0
    pred_funding_rate: float = 0.0
    bid: float = 0.0
    ask: float = 0.0


@dataclass(slots=True)
class Product:
    symbol: str = ""
    base_currency: str = ""
    quote_currency: str = ""

    # Scaling factors (Legacy V1)
    price_scale: int = 4
    ratio_scale: int = 8
    value_scale: int = 8

    # Precision fields (V2 / Standard)
    tick_size: float = 0.01       # Min price increment (e.g. 0.1 for BTC)
    qty_step_size: float = 0.001  # Min qty increment (e.g. 0.001 for BTC)
    price_precision: int = 2      # Decimals for price (e.g. 1 for BTC)
    qty_precision: int = 3        # Decimals for qty (e.g. 3 for BTC)
    max_leverage: float = 100.0   # Risk Limit metadata
    max_position_size: float = 0.0


@dataclass(slots=True)
class OrderbookLevel:
    price: float = 0.0
    size: float = 0.0


@dataclass(slots=True)
class OrderbookSnapshot:
    symbol: str = ""
    timestamp: int = 0
    ask_map: dict[float, float] = field(default_factory=dict) # price -> size
    bid_map: dict[float, float] = field(default_factory=dict) # price -> size
    _asks_cache: list[OrderbookLevel] = field(default_factory=list)
    _bids_cache: list[OrderbookLevel] = field(default_factory=list)
    _dirty: bool = field(default=True)

    @property
    def asks(self) -> list[OrderbookLevel]:
        if self._dirty:
            self._sync()
        return self._asks_cache

    @property
    def bids(self) -> list[OrderbookLevel]:
        if self._dirty:
            self._sync()
        return self._bids_cache

    def _sync(self):
        self._asks_cache = [OrderbookLevel(p, s) for p, s in sorted(self.ask_map.items())]
        self._bids_cache = [OrderbookLevel(p, s) for p, s in sorted(self.bid_map.items(), reverse=True)]
        self._dirty = False


@dataclass(slots=True)
class Order:
    order_id: str = ""
    symbol: str = ""
    side: Side = "Buy"
    type: OrderType = "Limit"
    qty: float = 0.0
    price: float = 0.0
    status: OrderStatus = "New"
    trigger_price: float = 0.0


@dataclass(slots=True)
class Position:
    symbol: str = ""
    side: Side = "Buy"
    size: float = 0.0
    value: float = 0.0
    entry_price: float = 0.0
    mark_price: float = 0.0
    liquidation_price: float = 0.0
    leverage: float = 1.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    margin: float = 0.0
    pos_side: PositionSide = "Merged"
    side_multiplier: float = 1.0
    pnl_factor: float = 0.0  # size * side_multiplier
