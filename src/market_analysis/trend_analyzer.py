"""
Trend Analyzer for the crypto trading bot.

This module analyzes market trends using two criteria:
1. 24-hour price change analysis
2. Order book bid/ask pressure ratio

Both criteria must agree for a directional trend determination.
"""

from decimal import Decimal
from typing import Optional

from src.storage.models import OrderBook, TrendDirection, OrderSide
from src.storage.db_manager import DatabaseManager
from src.utils.logger import get_logger
from src.utils.types import (
    DEFAULT_TREND_PRICE_CHANGE_THRESHOLD,
    DEFAULT_TREND_ORDERBOOK_PRESSURE_RATIO,
)

logger = get_logger(__name__)


class TrendAnalyzer:
    """
    Analyzes market trends using 24h price change and order book pressure.

    Determines trend direction to ensure trades only occur in the direction
    of the prevailing trend.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        price_change_threshold: Decimal = Decimal(str(DEFAULT_TREND_PRICE_CHANGE_THRESHOLD)),
        orderbook_pressure_ratio: Decimal = Decimal(str(DEFAULT_TREND_ORDERBOOK_PRESSURE_RATIO)),
    ):
        """
        Initialize the trend analyzer.

        Args:
            db_manager: Database manager instance for querying market stats
            price_change_threshold: Minimum 24h price change % for trend (default: 2.0%)
            orderbook_pressure_ratio: Bid/ask ratio threshold for trend (default: 1.2)
        """
        self.db_manager = db_manager
        self.price_change_threshold = price_change_threshold
        self.orderbook_pressure_ratio = orderbook_pressure_ratio

        # Calculate the inverse ratio for downtrend (1/1.2 ≈ 0.83)
        self.orderbook_pressure_ratio_inverse = Decimal("1") / orderbook_pressure_ratio

        logger.info(
            "trend_analyzer_initialized",
            price_change_threshold=float(price_change_threshold),
            orderbook_pressure_ratio=float(orderbook_pressure_ratio),
            orderbook_pressure_ratio_inverse=float(self.orderbook_pressure_ratio_inverse),
        )

    async def analyze_trend(
        self, symbol: str, orderbook: OrderBook
    ) -> TrendDirection:
        """
        Analyze current trend for a symbol.

        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            orderbook: Current order book snapshot

        Returns:
            TrendDirection.UP, DOWN, or SIDEWAYS
        """
        # 1. Get 24h stats from database
        stats = await self._get_market_stats(symbol)
        if not stats:
            logger.warning("no_market_stats", symbol=symbol)
            return TrendDirection.SIDEWAYS

        # 2. Analyze 24h price change
        price_trend = self._analyze_price_change(stats['price_change_24h_percent'])

        # 3. Analyze order book pressure
        orderbook_trend = self._analyze_orderbook_pressure(orderbook)

        # 4. Combine criteria (BOTH must agree)
        final_trend = self._combine_trends(price_trend, orderbook_trend)

        logger.info(
            "trend_analyzed",
            symbol=symbol,
            price_change=float(stats['price_change_24h_percent']),
            price_trend=price_trend.value,
            orderbook_trend=orderbook_trend.value,
            final_trend=final_trend.value,
        )

        return final_trend

    async def _get_market_stats(self, symbol: str):
        """
        Get market statistics for a symbol from the database.

        Args:
            symbol: Trading symbol

        Returns:
            Database record with market stats or None if not found
        """
        row = await self.db_manager.fetchrow(
            "SELECT * FROM market_stats WHERE symbol = $1",
            symbol
        )
        return row

    def _analyze_price_change(self, price_change_percent: Decimal) -> TrendDirection:
        """
        Analyze 24h price change to determine trend.

        Args:
            price_change_percent: 24-hour price change percentage

        Returns:
            TrendDirection.UP if >= threshold
            TrendDirection.DOWN if <= -threshold
            TrendDirection.SIDEWAYS otherwise
        """
        if price_change_percent >= self.price_change_threshold:
            return TrendDirection.UP
        elif price_change_percent <= -self.price_change_threshold:
            return TrendDirection.DOWN
        else:
            return TrendDirection.SIDEWAYS

    def _analyze_orderbook_pressure(self, orderbook: OrderBook) -> TrendDirection:
        """
        Analyze order book bid/ask pressure to determine trend.

        Calculates the ratio of total bid volume to total ask volume.
        A higher ratio indicates buying pressure (uptrend).
        A lower ratio indicates selling pressure (downtrend).

        Args:
            orderbook: Current order book snapshot

        Returns:
            TrendDirection.UP if ratio >= 1.2 (20% more bids than asks)
            TrendDirection.DOWN if ratio <= 0.83 (20% more asks than bids)
            TrendDirection.SIDEWAYS otherwise
        """
        # Calculate total volumes for each side
        bid_volume = orderbook.get_total_volume(OrderSide.BID)
        ask_volume = orderbook.get_total_volume(OrderSide.ASK)

        # Handle edge case: zero ask volume (avoid division by zero)
        if ask_volume == 0:
            logger.warning(
                "zero_ask_volume",
                symbol=orderbook.symbol,
                bid_volume=float(bid_volume),
            )
            return TrendDirection.SIDEWAYS

        # Calculate bid/ask ratio
        pressure_ratio = bid_volume / ask_volume

        # Determine trend based on ratio
        if pressure_ratio >= self.orderbook_pressure_ratio:
            return TrendDirection.UP
        elif pressure_ratio <= self.orderbook_pressure_ratio_inverse:
            return TrendDirection.DOWN
        else:
            return TrendDirection.SIDEWAYS

    def _combine_trends(
        self, price_trend: TrendDirection, orderbook_trend: TrendDirection
    ) -> TrendDirection:
        """
        Combine price change and order book trends.

        Returns a directional trend ONLY if both criteria agree.
        Otherwise returns SIDEWAYS.

        Args:
            price_trend: Trend from 24h price change analysis
            orderbook_trend: Trend from order book pressure analysis

        Returns:
            TrendDirection.UP only if BOTH are UP
            TrendDirection.DOWN only if BOTH are DOWN
            TrendDirection.SIDEWAYS if criteria disagree or show SIDEWAYS
        """
        # Return UP only if BOTH criteria show UP
        if price_trend == TrendDirection.UP and orderbook_trend == TrendDirection.UP:
            return TrendDirection.UP

        # Return DOWN only if BOTH criteria show DOWN
        if price_trend == TrendDirection.DOWN and orderbook_trend == TrendDirection.DOWN:
            return TrendDirection.DOWN

        # Otherwise return SIDEWAYS
        return TrendDirection.SIDEWAYS
