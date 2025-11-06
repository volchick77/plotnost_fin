"""
Pydantic models for the trading bot storage layer.

This module defines all data models used throughout the application:
- Order book structures (OrderBook, PriceLevel, Cluster)
- Market analysis entities (Density, Trend, Signal)
- Trading entities (Position, Trade)
- Configuration (CoinParameters)
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


# ==================== Enums ====================


class SignalType(str, Enum):
    """Type of trading signal."""

    BREAKOUT = "breakout"
    BOUNCE = "bounce"


class TrendDirection(str, Enum):
    """Market trend direction."""

    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"


class PositionStatus(str, Enum):
    """Status of a trading position."""

    OPEN = "open"
    CLOSED = "closed"


class ExitReason(str, Enum):
    """Reason for exiting a position."""

    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    EMERGENCY = "emergency"
    DENSITY_EROSION = "density_erosion"

    # Advanced take-profit reasons
    MOMENTUM_SLOWDOWN = "momentum_slowdown"
    COUNTER_DENSITY = "counter_density"
    AGGRESSIVE_REVERSAL = "aggressive_reversal"
    RETURN_TO_RANGE = "return_to_range"

    STOP_LOSS_FAILED = "stop_loss_failed"
    MANUAL = "manual"
    TIMEOUT = "timeout"


class OrderSide(str, Enum):
    """Side of order book or order."""

    BID = "bid"
    ASK = "ask"


class PositionDirection(str, Enum):
    """Direction of a trading position."""

    LONG = "LONG"
    SHORT = "SHORT"


# ==================== Order Book Models ====================


class PriceLevel(BaseModel):
    """A single price level in the order book."""

    price: Decimal = Field(..., description="Price at this level")
    volume: Decimal = Field(..., description="Volume (in base currency) at this level")

    @field_validator("price", "volume")
    @classmethod
    def validate_positive(cls, v: Decimal) -> Decimal:
        """Ensure price and volume are positive."""
        if v <= 0:
            raise ValueError("Price and volume must be positive")
        return v

    class Config:
        json_encoders = {
            Decimal: str
        }


class Cluster(BaseModel):
    """A cluster of nearby price levels forming a larger density."""

    price_start: Decimal = Field(..., description="Starting price of cluster")
    price_end: Decimal = Field(..., description="Ending price of cluster")
    total_volume: Decimal = Field(..., description="Total volume across all levels")
    level_count: int = Field(..., description="Number of price levels in cluster")
    average_price: Decimal = Field(..., description="Volume-weighted average price")
    side: OrderSide = Field(..., description="Bid or ask side")

    class Config:
        json_encoders = {
            Decimal: str
        }


class OrderBook(BaseModel):
    """Complete order book state for a symbol."""

    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")
    bids: list[PriceLevel] = Field(default_factory=list, description="Bid price levels")
    asks: list[PriceLevel] = Field(default_factory=list, description="Ask price levels")
    timestamp: datetime = Field(default_factory=datetime.now, description="When snapshot was taken")

    def get_mid_price(self) -> Optional[Decimal]:
        """Calculate mid price between best bid and ask."""
        if not self.bids or not self.asks:
            return None
        best_bid = max(self.bids, key=lambda x: x.price)
        best_ask = min(self.asks, key=lambda x: x.price)
        return (best_bid.price + best_ask.price) / 2

    def get_total_volume(self, side: OrderSide) -> Decimal:
        """Get total volume for a side of the order book."""
        levels = self.bids if side == OrderSide.BID else self.asks
        return sum(level.volume for level in levels)

    def get_volume_at_level(self, price: Decimal, side: OrderSide) -> Decimal:
        """Get volume at a specific price level."""
        levels = self.bids if side == OrderSide.BID else self.asks
        for level in levels:
            if abs(level.price - price) < Decimal("0.00000001"):  # Float comparison tolerance
                return level.volume
        return Decimal("0")

    class Config:
        json_encoders = {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


# ==================== Market Analysis Models ====================


class Density(BaseModel):
    """A detected density (large volume concentration) in the order book."""

    symbol: str = Field(..., description="Trading symbol")
    price_level: Decimal = Field(..., description="Price where density is located")
    volume: Decimal = Field(..., description="Current volume at this level")
    initial_volume: Decimal = Field(..., description="Initial volume when first detected")
    side: OrderSide = Field(..., description="Bid or ask side")
    volume_percent: Optional[Decimal] = Field(None, description="Percentage of total order book volume")
    relative_strength: Optional[Decimal] = Field(None, description="Multiplier vs average level volume")
    is_cluster: bool = Field(default=False, description="Whether this is a cluster of levels")
    appeared_at: datetime = Field(default_factory=datetime.now, description="When density first appeared")
    disappeared_at: Optional[datetime] = Field(None, description="When density disappeared")

    def erosion_percent(self) -> Decimal:
        """Calculate percentage of density that has been eroded."""
        if self.initial_volume <= 0:
            return Decimal("0")
        return ((self.initial_volume - self.volume) / self.initial_volume) * 100

    class Config:
        json_encoders = {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


class Trend(BaseModel):
    """Market trend information for a symbol."""

    symbol: str = Field(..., description="Trading symbol")
    direction: TrendDirection = Field(..., description="Overall trend direction")
    strength: Decimal = Field(..., description="Strength of trend (0-100)")
    price_change_24h_percent: Decimal = Field(..., description="24h price change percentage")
    orderbook_bid_ask_ratio: Decimal = Field(..., description="Ratio of bid to ask volume")
    timestamp: datetime = Field(default_factory=datetime.now, description="When trend was determined")

    class Config:
        json_encoders = {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


class Signal(BaseModel):
    """A trading signal generated by the system."""

    id: UUID = Field(default_factory=uuid4, description="Unique signal ID")
    type: SignalType = Field(..., description="Type of signal (breakout or bounce)")
    symbol: str = Field(..., description="Trading symbol")
    direction: PositionDirection = Field(..., description="Long or short")
    entry_price: Decimal = Field(..., description="Suggested entry price")
    stop_loss: Decimal = Field(..., description="Stop loss price for risk management")
    density: Density = Field(..., description="The density that triggered this signal")
    priority: Decimal = Field(default=Decimal("1.0"), description="Signal priority (higher is better)")
    timestamp: datetime = Field(default_factory=datetime.now, description="When signal was generated")
    processed: bool = Field(default=False, description="Whether signal has been processed")

    class Config:
        json_encoders = {
            UUID: str,
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


# ==================== Trading Models ====================


class Position(BaseModel):
    """An open or closed trading position."""

    id: UUID = Field(default_factory=uuid4, description="Unique position ID")
    symbol: str = Field(..., description="Trading symbol")
    entry_price: Decimal = Field(..., description="Price at which position was opened")
    exit_price: Optional[Decimal] = Field(None, description="Price at which position was closed")
    size: Decimal = Field(..., description="Position size in base currency")
    leverage: int = Field(..., description="Leverage multiplier")
    direction: PositionDirection = Field(..., description="Long or short")
    signal_type: SignalType = Field(..., description="Which signal type triggered this")
    stop_loss: Decimal = Field(..., description="Stop loss price")
    status: PositionStatus = Field(default=PositionStatus.OPEN, description="Position status")
    entry_time: datetime = Field(default_factory=datetime.now, description="When position was opened")
    exit_time: Optional[datetime] = Field(None, description="When position was closed")
    exit_reason: Optional[ExitReason] = Field(None, description="Why position was closed")
    breakeven_moved: bool = Field(default=False, description="Whether stop loss moved to breakeven")

    # Associated data
    density_price: Decimal = Field(..., description="Price of the density that triggered this")
    signal_priority: Decimal = Field(..., description="Priority of the signal")

    def calculate_profit_percent(self, current_price: Decimal) -> Decimal:
        """Calculate current profit percentage."""
        price_diff = current_price - self.entry_price
        if self.direction == PositionDirection.SHORT:
            price_diff = -price_diff

        return (price_diff / self.entry_price) * 100 * self.leverage

    def calculate_profit_loss(self) -> Optional[Decimal]:
        """Calculate realized profit/loss (only for closed positions)."""
        if self.status != PositionStatus.CLOSED or self.exit_price is None:
            return None

        price_diff = self.exit_price - self.entry_price
        if self.direction == PositionDirection.SHORT:
            price_diff = -price_diff

        return (price_diff / self.entry_price) * 100 * self.leverage

    class Config:
        json_encoders = {
            UUID: str,
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


class Trade(BaseModel):
    """A completed trade (closed position) with full history."""

    id: UUID = Field(..., description="Trade ID (same as position ID)")
    symbol: str = Field(..., description="Trading symbol")
    entry_time: datetime = Field(..., description="When position was opened")
    exit_time: datetime = Field(..., description="When position was closed")
    entry_price: Decimal = Field(..., description="Entry price")
    exit_price: Decimal = Field(..., description="Exit price")
    position_size: Decimal = Field(..., description="Position size")
    leverage: int = Field(..., description="Leverage used")
    direction: PositionDirection = Field(..., description="Long or short")
    signal_type: SignalType = Field(..., description="Signal type that triggered entry")
    profit_loss: Decimal = Field(..., description="Profit/loss amount")
    profit_loss_percent: Decimal = Field(..., description="Profit/loss percentage")
    stop_loss_price: Decimal = Field(..., description="Stop loss price")
    stop_loss_triggered: bool = Field(..., description="Whether exit was due to stop loss")
    exit_reason: ExitReason = Field(..., description="Reason for exit")
    parameters_snapshot: dict = Field(..., description="Trading parameters at time of entry")
    created_at: datetime = Field(default_factory=datetime.now, description="Record creation time")
    updated_at: datetime = Field(default_factory=datetime.now, description="Record update time")

    @classmethod
    def from_position(cls, position: Position, parameters_snapshot: dict) -> "Trade":
        """Create a Trade record from a closed Position."""
        if position.status != PositionStatus.CLOSED:
            raise ValueError("Cannot create Trade from open Position")
        if position.exit_price is None or position.exit_time is None:
            raise ValueError("Position missing exit data")

        pnl_percent = position.calculate_profit_loss()
        if pnl_percent is None:
            raise ValueError("Cannot calculate P&L for position")

        # Calculate absolute P&L (simplified - would need actual position value)
        pnl_amount = (position.exit_price - position.entry_price) * position.size
        if position.direction == PositionDirection.SHORT:
            pnl_amount = -pnl_amount

        return cls(
            id=position.id,
            symbol=position.symbol,
            entry_time=position.entry_time,
            exit_time=position.exit_time,
            entry_price=position.entry_price,
            exit_price=position.exit_price,
            position_size=position.size,
            leverage=position.leverage,
            direction=position.direction,
            signal_type=position.signal_type,
            profit_loss=pnl_amount,
            profit_loss_percent=pnl_percent,
            stop_loss_price=position.stop_loss,
            stop_loss_triggered=(position.exit_reason == ExitReason.STOP_LOSS),
            exit_reason=position.exit_reason or ExitReason.MANUAL,
            parameters_snapshot=parameters_snapshot,
        )

    class Config:
        json_encoders = {
            UUID: str,
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


# ==================== Configuration Models ====================


class CoinParameters(BaseModel):
    """Trading parameters specific to each coin/symbol."""

    symbol: str = Field(..., description="Trading symbol")

    # Density detection thresholds
    density_threshold_abs: Decimal = Field(
        default=Decimal("50000"),
        description="Absolute volume threshold for density detection"
    )
    density_threshold_relative: Decimal = Field(
        default=Decimal("3.0"),
        description="Relative volume multiplier (vs neighbors)"
    )
    density_threshold_percent: Decimal = Field(
        default=Decimal("5.0"),
        description="Minimum percentage of total order book volume"
    )

    # Cluster detection
    cluster_range_percent: Decimal = Field(
        default=Decimal("0.5"),
        description="Price range for grouping levels into clusters (%)"
    )

    # Breakout strategy parameters
    breakout_erosion_percent: Decimal = Field(
        default=Decimal("30.0"),
        description="Density erosion threshold for breakout signal (%)"
    )
    breakout_min_stop_loss_percent: Decimal = Field(
        default=Decimal("0.1"),
        description="Minimum stop loss distance from entry (%)"
    )
    breakout_breakeven_profit_percent: Decimal = Field(
        default=Decimal("0.5"),
        description="Profit threshold to move stop to breakeven (%)"
    )

    # Bounce strategy parameters
    bounce_touch_tolerance_percent: Decimal = Field(
        default=Decimal("0.2"),
        description="Price tolerance for 'touching' density (%)"
    )
    bounce_density_stable_percent: Decimal = Field(
        default=Decimal("10.0"),
        description="Max volume change for density to be 'stable' (%)"
    )
    bounce_stop_loss_behind_density_percent: Decimal = Field(
        default=Decimal("0.3"),
        description="Stop loss distance behind density (%)"
    )
    bounce_density_erosion_exit_percent: Decimal = Field(
        default=Decimal("65.0"),
        description="Density erosion threshold to exit bounce position (%)"
    )

    # Take profit parameters
    tp_slowdown_multiplier: Decimal = Field(
        default=Decimal("3.0"),
        description="Movement slowdown multiplier for TP detection"
    )
    tp_local_extrema_hours: int = Field(
        default=4,
        description="Hours to look back for local extrema"
    )

    # Strategy preferences
    preferred_strategy: str = Field(
        default="both",
        description="Preferred strategy: 'breakout', 'bounce', or 'both'"
    )

    # Control
    enabled: bool = Field(default=True, description="Whether trading is enabled for this symbol")

    # Metadata
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update time")
    notes: Optional[str] = Field(None, description="Notes about parameters for this coin")

    @field_validator("preferred_strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        """Ensure strategy is valid."""
        if v not in ["breakout", "bounce", "both"]:
            raise ValueError("Strategy must be 'breakout', 'bounce', or 'both'")
        return v

    class Config:
        json_encoders = {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


# ==================== Market Statistics ====================


class MarketStats(BaseModel):
    """Market statistics for a symbol."""

    symbol: str = Field(..., description="Trading symbol")
    volume_24h: Decimal = Field(..., description="24-hour trading volume")
    price_change_24h_percent: Decimal = Field(..., description="24-hour price change percentage")
    current_price: Decimal = Field(..., description="Current market price")
    is_active: bool = Field(default=False, description="Whether symbol is in top-40 active list")
    rank: Optional[int] = Field(None, description="Ranking position")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update time")

    class Config:
        json_encoders = {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }


# ==================== System Events ====================


class SystemEvent(BaseModel):
    """System event for logging and monitoring."""

    event_type: str = Field(..., description="Type of event")
    severity: str = Field(..., description="Severity level: info, warning, error, critical")
    symbol: Optional[str] = Field(None, description="Related symbol if applicable")
    message: str = Field(..., description="Event message")
    details: Optional[dict] = Field(None, description="Additional event details")
    timestamp: datetime = Field(default_factory=datetime.now, description="When event occurred")

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        """Ensure severity is valid."""
        if v not in ["info", "warning", "error", "critical"]:
            raise ValueError("Severity must be 'info', 'warning', 'error', or 'critical'")
        return v

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
