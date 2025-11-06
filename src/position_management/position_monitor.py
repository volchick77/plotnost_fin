"""
Position Monitor for the crypto trading bot.

This module manages the lifecycle of open positions, including:
- Moving stop-loss to breakeven when conditions are met
- Detecting take-profit conditions (slowdown, extrema)
- Monitoring bounce density erosion
- Updating position status in database
- Determining when to close positions
"""

from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Optional

from src.storage.models import (
    Position,
    PositionStatus,
    SignalType,
    PositionDirection,
    ExitReason,
)
from src.storage.db_manager import DatabaseManager
from src.data_collection.orderbook_manager import OrderBookManager
from src.utils.logger import get_logger


class PositionMonitor:
    """
    Monitors and manages open positions.

    Responsibilities:
    - Move stop-loss to breakeven when profitable
    - Detect take-profit conditions (slowdown, extrema)
    - Monitor bounce density erosion
    - Update position status in database
    - Close positions when exit conditions met
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        orderbook_manager: OrderBookManager,
        check_interval_seconds: int = 5,
    ):
        """
        Initialize the Position Monitor.

        Args:
            db_manager: Database manager for persistence
            orderbook_manager: OrderBook manager for market data
            check_interval_seconds: How often to check positions (default: 5 seconds)
        """
        self.db_manager = db_manager
        self.orderbook_manager = orderbook_manager
        self.check_interval_seconds = check_interval_seconds
        self.logger = get_logger(__name__)

        # Track positions to avoid duplicate processing
        self._monitored_positions: dict[str, Position] = {}

        self.logger.info(
            "position_monitor_initialized",
            check_interval=check_interval_seconds,
        )

    async def start_monitoring(self, position: Position) -> None:
        """
        Start monitoring a position.

        Args:
            position: Position to monitor

        Raises:
            ValueError: If already monitoring a different position for this symbol
        """
        symbol = position.symbol

        # Check if already monitoring a position for this symbol
        if symbol in self._monitored_positions:
            existing = self._monitored_positions[symbol]
            if existing.id != position.id:
                raise ValueError(
                    f"Cannot monitor position {position.id} for {symbol}: "
                    f"already monitoring position {existing.id}"
                )
            # Already monitoring this exact position
            self.logger.debug("position_already_monitored", symbol=symbol, position_id=str(position.id))
            return

        self._monitored_positions[symbol] = position

        self.logger.info(
            "position_monitoring_started",
            symbol=symbol,
            position_id=str(position.id),
            direction=position.direction.value,
            signal_type=position.signal_type.value,
            entry=float(position.entry_price),
            stop_loss=float(position.stop_loss),
            size=float(position.size),
            leverage=position.leverage,
        )

    async def stop_monitoring(self, symbol: str) -> None:
        """
        Stop monitoring a position.

        Args:
            symbol: Symbol to stop monitoring
        """
        if symbol in self._monitored_positions:
            position = self._monitored_positions[symbol]
            del self._monitored_positions[symbol]

            self.logger.info(
                "position_monitoring_stopped",
                symbol=symbol,
                position_id=str(position.id),
            )
        else:
            self.logger.warning(
                "position_not_monitored",
                symbol=symbol,
            )

    async def check_positions(self) -> List[Position]:
        """
        Check all monitored positions and update them.

        This is the main monitoring loop that:
        1. Gets current market price
        2. Checks for breakeven stop-loss movement
        3. Checks for exit conditions (TP, density erosion)
        4. Returns positions that should be closed

        Returns:
            List of positions that should be closed
        """
        positions_to_close = []

        for symbol, position in list(self._monitored_positions.items()):
            # Skip if position is not open
            if position.status != PositionStatus.OPEN:
                self.logger.warning(
                    "position_not_open",
                    symbol=symbol,
                    position_id=str(position.id),
                    status=position.status.value,
                )
                continue

            try:
                # Get current market price
                orderbook = self.orderbook_manager.get_current_orderbook(symbol)
                if not orderbook:
                    self.logger.debug(
                        "orderbook_not_available",
                        symbol=symbol,
                    )
                    continue

                current_price = orderbook.get_mid_price()
                if not current_price:
                    self.logger.warning(
                        "mid_price_not_available",
                        symbol=symbol,
                    )
                    continue

                # Get coin parameters
                params = self.db_manager.coin_params_cache.get_sync(symbol)
                if not params:
                    self.logger.warning(
                        "coin_parameters_not_available",
                        symbol=symbol,
                    )
                    continue

                # Check for breakeven stop-loss move
                if not position.breakeven_moved:
                    should_move = await self._check_breakeven_conditions(
                        position, current_price, params
                    )
                    if should_move:
                        await self._move_to_breakeven(position)

                # Check for exit conditions
                exit_reason = await self._check_exit_conditions(
                    position, current_price, params
                )

                if exit_reason:
                    position.exit_reason = exit_reason
                    position.status = PositionStatus.CLOSING
                    positions_to_close.append(position)

                    self.logger.info(
                        "position_marked_for_closure",
                        symbol=symbol,
                        position_id=str(position.id),
                        exit_reason=exit_reason.value,
                        current_price=float(current_price),
                    )

            except Exception as e:
                self.logger.error(
                    "position_check_error",
                    symbol=symbol,
                    position_id=str(position.id),
                    error=str(e),
                    exc_info=True,
                )

        return positions_to_close

    async def _check_breakeven_conditions(
        self, position: Position, current_price: Decimal, params
    ) -> bool:
        """
        Check if position should move stop-loss to breakeven.

        For BREAKOUT strategy:
            - Move when profit >= breakeven_profit_percent (default: 0.5%)

        For BOUNCE strategy:
            - Move when density erodes >= density_erosion_exit_percent (default: 65%)

        Args:
            position: Position to check
            current_price: Current market price
            params: Coin parameters with thresholds

        Returns:
            True if should move to breakeven
        """
        try:
            if position.signal_type == SignalType.BREAKOUT:
                # Breakout: Move when profit >= breakeven_profit_percent
                profit_percent = position.calculate_profit_percent(current_price)
                threshold = params.breakout_breakeven_profit_percent

                if profit_percent >= threshold:
                    self.logger.info(
                        "breakeven_condition_met_breakout",
                        symbol=position.symbol,
                        position_id=str(position.id),
                        profit_percent=float(profit_percent),
                        threshold=float(threshold),
                        current_price=float(current_price),
                        entry_price=float(position.entry_price),
                    )
                    return True

            elif position.signal_type == SignalType.BOUNCE:
                # Bounce: Move when density erodes >= erosion_exit_percent
                densities = self.orderbook_manager.get_current_densities(position.symbol)

                # Find the density that triggered this position
                target_density = None
                for d in densities:
                    if (d.price_level == position.density_price and
                        self._is_same_side(d.side.value, position.direction)):
                        target_density = d
                        break

                if target_density:
                    erosion = target_density.erosion_percent()
                    threshold = params.bounce_density_erosion_exit_percent

                    if erosion >= threshold:
                        self.logger.info(
                            "breakeven_condition_met_bounce",
                            symbol=position.symbol,
                            position_id=str(position.id),
                            density_price=float(target_density.price_level),
                            density_erosion=float(erosion),
                            threshold=float(threshold),
                            initial_volume=float(target_density.initial_volume),
                            current_volume=float(target_density.volume),
                        )
                        return True
                else:
                    self.logger.debug(
                        "target_density_not_found",
                        symbol=position.symbol,
                        position_id=str(position.id),
                        density_price=float(position.density_price),
                        direction=position.direction.value,
                    )

        except Exception as e:
            self.logger.error(
                "breakeven_check_error",
                symbol=position.symbol,
                position_id=str(position.id),
                error=str(e),
                exc_info=True,
            )

        return False

    def _is_same_side(self, density_side: str, position_direction: PositionDirection) -> bool:
        """
        Check if density side matches position direction.

        LONG positions use BID density (support below price)
        SHORT positions use ASK density (resistance above price)

        Args:
            density_side: Side of density ("BID" or "ASK")
            position_direction: Direction of position (LONG or SHORT)

        Returns:
            True if density side matches position direction
        """
        if position_direction == PositionDirection.LONG:
            return density_side == "BID"  # LONG positions use BID density (support)
        else:
            return density_side == "ASK"  # SHORT positions use ASK density (resistance)

    async def _move_to_breakeven(self, position: Position) -> None:
        """
        Move stop-loss to breakeven (entry price).

        This protects profits by ensuring the position won't result in a loss.
        Only moves once per position (tracked with breakeven_moved flag).

        Args:
            position: Position to update
        """
        old_stop = position.stop_loss
        position.stop_loss = position.entry_price
        position.breakeven_moved = True

        # TODO: Update stop-loss on exchange via API
        # For now, just update in memory and log
        # In production, this would call:
        # await self.exchange_api.modify_stop_loss(position.id, position.entry_price)

        self.logger.info(
            "stop_loss_moved_to_breakeven",
            symbol=position.symbol,
            position_id=str(position.id),
            old_stop_loss=float(old_stop),
            new_stop_loss=float(position.entry_price),
            entry_price=float(position.entry_price),
        )

    async def _check_exit_conditions(
        self, position: Position, current_price: Decimal, params
    ) -> Optional[ExitReason]:
        """
        Check if position should be closed.

        Exit conditions:
        1. Stop-loss hit (checked by exchange, not here)
        2. Bounce density eroded too much (>= 65%)
        3. Take-profit target reached (simplified: 2% profit for now)

        Args:
            position: Position to check
            current_price: Current market price
            params: Coin parameters

        Returns:
            ExitReason if should close, None otherwise
        """
        try:
            # Check for bounce density erosion exit
            if position.signal_type == SignalType.BOUNCE:
                densities = self.orderbook_manager.get_current_densities(position.symbol)

                target_density = None
                for d in densities:
                    if (d.price_level == position.density_price and
                        self._is_same_side(d.side.value, position.direction)):
                        target_density = d
                        break

                if target_density:
                    erosion = target_density.erosion_percent()
                    threshold = params.bounce_density_erosion_exit_percent

                    if erosion >= threshold:
                        self.logger.info(
                            "bounce_density_eroded",
                            symbol=position.symbol,
                            position_id=str(position.id),
                            density_price=float(target_density.price_level),
                            erosion=float(erosion),
                            threshold=float(threshold),
                            initial_volume=float(target_density.initial_volume),
                            current_volume=float(target_density.volume),
                        )
                        return ExitReason.DENSITY_EROSION
                else:
                    # If density completely disappeared, also exit
                    self.logger.warning(
                        "bounce_density_disappeared",
                        symbol=position.symbol,
                        position_id=str(position.id),
                        density_price=float(position.density_price),
                    )
                    return ExitReason.DENSITY_EROSION

            # Check for take-profit via profit threshold
            # TODO: Implement proper momentum slowdown detection
            # For now, using a simple profit percentage threshold
            profit_percent = position.calculate_profit_percent(current_price)

            # Simple take-profit at 2% profit for now
            # In production, this would be more sophisticated:
            # - Detect momentum slowdown (velocity decrease by slowdown_multiplier)
            # - Detect local extrema (price reversal in last N hours)
            tp_threshold = Decimal("2.0")

            if profit_percent >= tp_threshold:
                self.logger.info(
                    "take_profit_target_reached",
                    symbol=position.symbol,
                    position_id=str(position.id),
                    profit_percent=float(profit_percent),
                    threshold=float(tp_threshold),
                    current_price=float(current_price),
                    entry_price=float(position.entry_price),
                )
                return ExitReason.TAKE_PROFIT

        except Exception as e:
            self.logger.error(
                "exit_condition_check_error",
                symbol=position.symbol,
                position_id=str(position.id),
                error=str(e),
                exc_info=True,
            )

        return None

    def get_monitored_positions(self) -> List[Position]:
        """
        Get list of all currently monitored positions.

        Returns:
            List of monitored positions
        """
        return list(self._monitored_positions.values())

    def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get the monitored position for a specific symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Position if being monitored, None otherwise
        """
        return self._monitored_positions.get(symbol)

    def is_monitoring(self, symbol: str) -> bool:
        """
        Check if currently monitoring a position for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            True if monitoring a position for this symbol
        """
        return symbol in self._monitored_positions
