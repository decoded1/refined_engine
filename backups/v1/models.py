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

@dataclass
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


@dataclass
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


@dataclass
class CancelOrderRequest:
    symbol: str
    order_id: Optional[str] = None
    cl_ord_id: Optional[str] = None
    pos_side: PositionSide = "Merged"


# ── Response Objects (The "Result") ──────────────────────────────────────────

@dataclass
class OrderResult:
    order_id: str
    cl_ord_id: str
    status: OrderStatus
    avg_price: float = 0.0
    cum_qty: float = 0.0


@dataclass
class Balance:
    total: float = 0.0       # Equity
    available: float = 0.0   # Free Margin
    used: float = 0.0        # Used Margin


@dataclass
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


@dataclass
class AccountInfo:
    balance: Balance = field(default_factory=Balance)
    positions: list[PositionInfo] = field(default_factory=list)


@dataclass
class Wallet:
    currency: str = "USDT"
    balance: float = 0.0
    available: float = 0.0
    used: float = 0.0


# ── Market Data ──────────────────────────────────────────────────────────────

@dataclass
class Candle:
    time: int = 0       # Unix timestamp (seconds)
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0


@dataclass
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


@dataclass
class Product:
    symbol: str = ""
    base_currency: str = ""
    quote_currency: str = ""
    price_scale: int = 4
    ratio_scale: int = 8
    value_scale: int = 8


@dataclass
class OrderbookLevel:
    price: float = 0.0
    size: float = 0.0


@dataclass
class OrderbookSnapshot:
    symbol: str = ""
    asks: list[OrderbookLevel] = field(default_factory=list)
    bids: list[OrderbookLevel] = field(default_factory=list)
    timestamp: int = 0


@dataclass
class Order:
    order_id: str = ""
    symbol: str = ""
    side: Side = "Buy"
    type: OrderType = "Limit"
    qty: float = 0.0
    price: float = 0.0
    status: OrderStatus = "New"
    trigger_price: float = 0.0


@dataclass
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
