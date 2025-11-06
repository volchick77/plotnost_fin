"""
Signal Generator for the crypto trading bot.

This module generates trading signals based on trend and density analysis,
combining breakout and bounce strategies to identify trading opportunities
that align with the current market trend.
"""

from decimal import Decimal
from typing import List, Optional

from src.storage.models import (
    Signal, SignalType, TrendDirection, OrderSide, PositionDirection,
    OrderBook, Density, CoinParameters
)
from src.market_analysis.trend_analyzer import TrendAnalyzer
from src.market_analysis.density_analyzer import DensityAnalyzer
from src.data_collection.orderbook_manager import OrderBookManager
from src.storage.db_manager import DatabaseManager
from src.utils.logger import get_logger


logger = get_logger(__name__)


def is_price_near_level_decimal(
    price: Decimal,
    level: Decimal,
    tolerance_percent: Decimal
) -> bool:
    """
    Check if price is within tolerance of a level (Decimal version).

    Args:
        price: Current price
        level: Target price level
        tolerance_percent: Tolerance as percentage (e.g., 0.2 for 0.2%)

    Returns:
        True if price is within tolerance of level
    """
    if level == 0:
        return False

    diff_percent = abs((price - level) / level) * Decimal("100")
    return diff_percent <= tolerance_percent


class SignalGenerator:
    """
    Generates trading signals based on trend and density analysis.

    Combines breakout and bounce strategies to identify trading opportunities
    that align with the current market trend.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        orderbook_manager: OrderBookManager,
        trend_analyzer: TrendAnalyzer,
        density_analyzer: DensityAnalyzer,
    ):
        """
        Initialize the Signal Generator.

        Args:
            db_manager: Database manager for accessing coin parameters
            orderbook_manager: Manager for orderbook and density data
            trend_analyzer: Analyzer for determining market trends
            density_analyzer: Analyzer for density breakouts and erosion
        """
        self.db_manager = db_manager
        self.orderbook_manager = orderbook_manager
        self.trend_analyzer = trend_analyzer
        self.density_analyzer = density_analyzer
        self.logger = get_logger(__name__)

        self.logger.info("signal_generator_initialized")

    async def generate_signals(self, symbol: str) -> List[Signal]:
        """
        Generate trading signals for a symbol.

        This method:
        1. Gets current orderbook and parameters
        2. Analyzes trend direction
        3. Generates signals based on preferred strategy
        4. Returns list of signals (can be empty)

        Args:
            symbol: Trading symbol to analyze

        Returns:
            List of signals (can be empty)
        """
        try:
            # Get current data
            orderbook = self.orderbook_manager.get_current_orderbook(symbol)
            params = self.db_manager.coin_params_cache.get_sync(symbol)

            if not orderbook:
                self.logger.debug(
                    "no_orderbook_available",
                    symbol=symbol,
                    message="Cannot generate signals without orderbook"
                )
                return []

            if not params:
                self.logger.warning(
                    "no_coin_parameters",
                    symbol=symbol,
                    message="Cannot generate signals without parameters"
                )
                return []

            if not params.enabled:
                self.logger.debug(
                    "symbol_disabled",
                    symbol=symbol,
                    message="Trading disabled for this symbol"
                )
                return []

            # Analyze trend
            trend = await self.trend_analyzer.analyze_trend(symbol, orderbook)

            if trend == TrendDirection.SIDEWAYS:
                # Don't trade in neutral/sideways markets
                self.logger.debug(
                    "sideways_market",
                    symbol=symbol,
                    message="No signals in sideways market"
                )
                return []

            signals = []

            # Generate signals based on preferred strategy
            if params.preferred_strategy in ["breakout", "both"]:
                breakout_signal = await self._check_breakout_strategy(
                    symbol, orderbook, params, trend
                )
                if breakout_signal:
                    signals.append(breakout_signal)

            if params.preferred_strategy in ["bounce", "both"]:
                bounce_signal = await self._check_bounce_strategy(
                    symbol, orderbook, params, trend
                )
                if bounce_signal:
                    signals.append(bounce_signal)

            if signals:
                self.logger.info(
                    "signals_generated",
                    symbol=symbol,
                    count=len(signals),
                    trend=trend.value,
                    strategy=params.preferred_strategy
                )

            return signals

        except Exception as e:
            self.logger.error(
                "signal_generation_failed",
                symbol=symbol,
                error=str(e),
                exc_info=True
            )
            return []

    async def _check_breakout_strategy(
        self,
        symbol: str,
        orderbook: OrderBook,
        params: CoinParameters,
        trend: TrendDirection,
    ) -> Optional[Signal]:
        """
        Check for breakout signals.

        Breakout strategy logic:
        - LONG: ASK density broken (resistance breakout) + uptrend
        - SHORT: BID density broken (support breakout) + downtrend

        Args:
            symbol: Trading symbol
            orderbook: Current orderbook
            params: Coin parameters
            trend: Current trend direction

        Returns:
            Breakout signal or None
        """
        try:
            # Get broken densities based on trend
            if trend == TrendDirection.UP:
                # Look for broken ASK density (resistance breakout)
                density = self.density_analyzer.get_strongest_broken_density(
                    symbol, OrderSide.ASK
                )
                if density and density.erosion_percent() >= params.breakout_erosion_percent:
                    return self._create_breakout_signal(
                        symbol, density, PositionDirection.LONG, orderbook, params
                    )

            elif trend == TrendDirection.DOWN:
                # Look for broken BID density (support breakout)
                density = self.density_analyzer.get_strongest_broken_density(
                    symbol, OrderSide.BID
                )
                if density and density.erosion_percent() >= params.breakout_erosion_percent:
                    return self._create_breakout_signal(
                        symbol, density, PositionDirection.SHORT, orderbook, params
                    )

            return None

        except Exception as e:
            self.logger.error(
                "breakout_strategy_check_failed",
                symbol=symbol,
                error=str(e),
                exc_info=True
            )
            return None

    def _create_breakout_signal(
        self,
        symbol: str,
        density: Density,
        direction: PositionDirection,
        orderbook: OrderBook,
        params: CoinParameters,
    ) -> Signal:
        """
        Create a breakout signal.

        Breakout signal logic:
        - Entry: Current market price
        - Stop-loss: Behind broken density level
        - Priority: HIGH for clusters, MEDIUM otherwise

        Args:
            symbol: Trading symbol
            density: Broken density that triggered signal
            direction: LONG or SHORT
            orderbook: Current orderbook
            params: Coin parameters

        Returns:
            Breakout signal
        """
        current_price = orderbook.get_mid_price()
        if not current_price:
            raise ValueError("Cannot get mid price from orderbook")

        # Calculate stop-loss behind broken density
        if direction == PositionDirection.LONG:
            # Stop below broken resistance
            stop_loss = density.price_level * (
                Decimal("1") - params.breakout_min_stop_loss_percent / Decimal("100")
            )
        else:
            # Stop above broken support
            stop_loss = density.price_level * (
                Decimal("1") + params.breakout_min_stop_loss_percent / Decimal("100")
            )

        # Priority based on cluster status
        priority = Decimal("2.0") if density.is_cluster else Decimal("1.0")

        signal = Signal(
            symbol=symbol,
            type=SignalType.BREAKOUT,
            direction=direction,
            entry_price=current_price,
            stop_loss=stop_loss,
            density=density,
            priority=priority,
        )

        self.logger.info(
            "breakout_signal_generated",
            symbol=symbol,
            direction=direction.value,
            entry=float(current_price),
            stop_loss=float(stop_loss),
            density_level=float(density.price_level),
            density_erosion=float(density.erosion_percent()),
            is_cluster=density.is_cluster,
            priority="HIGH" if density.is_cluster else "MEDIUM",
        )

        return signal

    async def _check_bounce_strategy(
        self,
        symbol: str,
        orderbook: OrderBook,
        params: CoinParameters,
        trend: TrendDirection,
    ) -> Optional[Signal]:
        """
        Check for bounce signals.

        Bounce strategy logic:
        - LONG: Price touching BID density (support) + uptrend + stable density
        - SHORT: Price touching ASK density (resistance) + downtrend + stable density

        Args:
            symbol: Trading symbol
            orderbook: Current orderbook
            params: Coin parameters
            trend: Current trend direction

        Returns:
            Bounce signal or None
        """
        try:
            current_price = orderbook.get_mid_price()
            if not current_price:
                self.logger.debug(
                    "no_mid_price",
                    symbol=symbol,
                    message="Cannot check bounce without mid price"
                )
                return None

            densities = self.orderbook_manager.get_current_densities(symbol)

            if trend == TrendDirection.UP:
                # Look for price near BID density (support bounce)
                for density in densities:
                    if density.side != OrderSide.BID:
                        continue

                    # Check if price is touching density
                    if not is_price_near_level_decimal(
                        current_price,
                        density.price_level,
                        params.bounce_touch_tolerance_percent
                    ):
                        continue

                    # Check if density is stable (low erosion)
                    if density.erosion_percent() >= params.bounce_density_stable_percent:
                        continue

                    # Generate LONG bounce signal
                    return self._create_bounce_signal(
                        symbol, density, PositionDirection.LONG, params
                    )

            elif trend == TrendDirection.DOWN:
                # Look for price near ASK density (resistance bounce)
                for density in densities:
                    if density.side != OrderSide.ASK:
                        continue

                    if not is_price_near_level_decimal(
                        current_price,
                        density.price_level,
                        params.bounce_touch_tolerance_percent
                    ):
                        continue

                    if density.erosion_percent() >= params.bounce_density_stable_percent:
                        continue

                    # Generate SHORT bounce signal
                    return self._create_bounce_signal(
                        symbol, density, PositionDirection.SHORT, params
                    )

            return None

        except Exception as e:
            self.logger.error(
                "bounce_strategy_check_failed",
                symbol=symbol,
                error=str(e),
                exc_info=True
            )
            return None

    def _create_bounce_signal(
        self,
        symbol: str,
        density: Density,
        direction: PositionDirection,
        params: CoinParameters,
    ) -> Signal:
        """
        Create a bounce signal.

        Bounce signal logic:
        - Entry: At density price level
        - Stop-loss: Behind density
        - Priority: MEDIUM (always)

        Args:
            symbol: Trading symbol
            density: Density that triggered signal
            direction: LONG or SHORT
            params: Coin parameters

        Returns:
            Bounce signal
        """
        entry_price = density.price_level

        # Calculate stop-loss behind density
        if direction == PositionDirection.LONG:
            # Stop below support
            stop_loss = density.price_level * (
                Decimal("1") - params.bounce_stop_loss_behind_density_percent / Decimal("100")
            )
        else:
            # Stop above resistance
            stop_loss = density.price_level * (
                Decimal("1") + params.bounce_stop_loss_behind_density_percent / Decimal("100")
            )

        signal = Signal(
            symbol=symbol,
            type=SignalType.BOUNCE,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            density=density,
            priority=Decimal("1.0"),  # MEDIUM priority
        )

        self.logger.info(
            "bounce_signal_generated",
            symbol=symbol,
            direction=direction.value,
            entry=float(entry_price),
            stop_loss=float(stop_loss),
            density_level=float(density.price_level),
            density_volume_percent=float(density.volume_percent) if density.volume_percent else None,
            is_cluster=density.is_cluster,
            priority="MEDIUM",
        )

        return signal
