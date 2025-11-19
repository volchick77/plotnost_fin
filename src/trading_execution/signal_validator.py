"""
Signal Validator for the crypto trading bot.

This module validates trading signals before execution to ensure safety:
- Symbol validation (enabled and active)
- Position limits (maximum concurrent positions, no duplicates)
- Balance validation (sufficient balance, minimum position size)
- Risk management (exposure limits, stop-loss validation, entry price checks)
- Signal quality (freshness, not already processed, density still exists)
"""

from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, Tuple

from src.storage.models import Signal, Position, PositionStatus
from src.storage.db_manager import DatabaseManager
from src.data_collection.orderbook_manager import OrderBookManager
from src.utils.logger import get_logger
from src.utils.types import (
    DEFAULT_MAX_CONCURRENT_POSITIONS,
    DEFAULT_MAX_EXPOSURE_PERCENT,
    MIN_POSITION_SIZE_USDT,
)


class SignalValidator:
    """
    Validates trading signals before execution.

    Ensures signals meet safety requirements:
    - Symbol is enabled and active
    - Position limits not exceeded
    - Sufficient balance available
    - Risk parameters within bounds
    - Signal is fresh and not processed
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        orderbook_manager: OrderBookManager,
        max_concurrent_positions: int = DEFAULT_MAX_CONCURRENT_POSITIONS,
        max_exposure_percent: Decimal = Decimal("80"),
        max_volume_impact_percent: Decimal = Decimal("1"),
        position_size_usdt: Decimal = Decimal("0.1"),
        leverage: int = 10,
        signal_max_age_seconds: int = 60,
    ):
        """
        Initialize the Signal Validator.

        Args:
            db_manager: Database manager for querying symbols and positions
            orderbook_manager: OrderBook manager for market data
            max_concurrent_positions: Maximum number of concurrent open positions (default: 10)
            max_exposure_percent: Maximum total exposure as % of balance (default: 80%)
            max_volume_impact_percent: Maximum order value as % of 24h volume (default: 1%)
            position_size_usdt: Position size in USDT (default: 0.1 for testing)
            leverage: Trading leverage (default: 10)
            signal_max_age_seconds: Maximum signal age before rejection (default: 60s)
        """
        self.db_manager = db_manager
        self.orderbook_manager = orderbook_manager
        self.max_concurrent_positions = max_concurrent_positions
        self.max_exposure_percent = max_exposure_percent
        self.max_volume_impact_percent = max_volume_impact_percent
        self.position_size_usdt = position_size_usdt
        self.leverage = leverage
        self.signal_max_age_seconds = signal_max_age_seconds
        self.logger = get_logger(__name__)

        self.logger.info(
            "signal_validator_initialized",
            max_concurrent_positions=max_concurrent_positions,
            max_exposure_percent=float(max_exposure_percent),
            max_volume_impact_percent=float(max_volume_impact_percent),
            position_size_usdt=float(position_size_usdt),
            leverage=leverage,
            signal_max_age_seconds=signal_max_age_seconds,
        )

    async def validate_signal(
        self, signal: Signal, account_balance: Decimal
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a signal for execution.

        Performs comprehensive validation checks to ensure the signal is safe to execute:
        1. Symbol is enabled in CoinParameters
        2. Symbol is active in market_stats
        3. Signal is fresh (< max_age_seconds old)
        4. Signal has not been processed already
        5. Maximum concurrent positions not exceeded
        6. No existing position on this symbol
        7. Stop-loss is reasonable distance from entry
        8. Entry price is close to current market price
        9. Density still exists in orderbook
        10. Account has sufficient balance

        Args:
            signal: Signal to validate
            account_balance: Current account balance in USDT

        Returns:
            Tuple of (is_valid, rejection_reason):
            - (True, None) if signal is valid and ready for execution
            - (False, "reason") if signal is rejected with explanation
        """
        # 1. Check if symbol is enabled
        params = self.db_manager.coin_params_cache.get_sync(signal.symbol)
        if not params or not params.enabled:
            reason = f"Symbol {signal.symbol} is not enabled"
            self.logger.warning(
                "signal_rejected_symbol_not_enabled",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                reason=reason,
            )
            return False, reason

        # 2. Check if symbol is active
        is_active = await self._check_symbol_active(signal.symbol)
        if not is_active:
            reason = f"Symbol {signal.symbol} is not in active list"
            self.logger.warning(
                "signal_rejected_symbol_not_active",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                reason=reason,
            )
            return False, reason

        # 3. Check signal freshness
        age_seconds = (datetime.now() - signal.timestamp).total_seconds()
        if age_seconds > self.signal_max_age_seconds:
            reason = f"Signal too old ({age_seconds:.1f}s > {self.signal_max_age_seconds}s)"
            self.logger.warning(
                "signal_rejected_too_old",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                age_seconds=age_seconds,
                max_age_seconds=self.signal_max_age_seconds,
                reason=reason,
            )
            return False, reason

        # 4. Check if already processed
        if signal.processed:
            reason = "Signal already processed"
            self.logger.warning(
                "signal_rejected_already_processed",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                reason=reason,
            )
            return False, reason

        # 5. Check position limits
        open_positions = await self._get_open_positions_count()
        if open_positions >= self.max_concurrent_positions:
            reason = f"Maximum positions reached ({open_positions}/{self.max_concurrent_positions})"
            self.logger.warning(
                "signal_rejected_max_positions",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                open_positions=open_positions,
                max_positions=self.max_concurrent_positions,
                reason=reason,
            )
            return False, reason

        # 6. Check for duplicate position on same symbol
        has_position = await self._has_open_position(signal.symbol)
        if has_position:
            reason = f"Already have open position on {signal.symbol}"
            self.logger.warning(
                "signal_rejected_duplicate_position",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                reason=reason,
            )
            return False, reason

        # 7. Validate stop-loss distance
        stop_distance_percent = abs(
            (signal.entry_price - signal.stop_loss) / signal.entry_price * Decimal("100")
        )
        if stop_distance_percent < Decimal("0.05"):
            reason = f"Stop-loss too close ({stop_distance_percent:.3f}% < 0.05%)"
            self.logger.warning(
                "signal_rejected_stop_loss_too_close",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                stop_distance_percent=float(stop_distance_percent),
                entry_price=float(signal.entry_price),
                stop_loss=float(signal.stop_loss),
                reason=reason,
            )
            return False, reason

        # 8. Check entry price is close to current market price
        current_price = self._get_current_market_price(signal.symbol)
        if current_price:
            price_diff_percent = abs(
                (signal.entry_price - current_price) / current_price * Decimal("100")
            )
            if price_diff_percent > Decimal("1.0"):
                reason = f"Entry price too far from market ({price_diff_percent:.2f}% > 1.0%)"
                self.logger.warning(
                    "signal_rejected_entry_price_mismatch",
                    symbol=signal.symbol,
                    signal_id=str(signal.id),
                    entry_price=float(signal.entry_price),
                    current_price=float(current_price),
                    price_diff_percent=float(price_diff_percent),
                    reason=reason,
                )
                return False, reason
        else:
            # If we can't get current price, log warning but don't reject
            self.logger.warning(
                "signal_validation_no_current_price",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                message="Cannot validate entry price - no current orderbook",
            )

        # 9. Check density still exists
        densities = self.orderbook_manager.get_current_densities(signal.symbol)
        density_exists = any(
            abs(d.price_level - signal.density.price_level) < Decimal("0.00000001")
            and d.side == signal.density.side
            for d in densities
        )
        if not density_exists:
            reason = "Density no longer exists in orderbook"
            self.logger.warning(
                "signal_rejected_density_disappeared",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                density_price=float(signal.density.price_level),
                density_side=signal.density.side.value,
                current_densities_count=len(densities),
                reason=reason,
            )
            return False, reason

        # 10. Validate total exposure (sum of all positions < 80% of capital)
        exposure_valid, exposure_reason = await self._validate_total_exposure(
            signal, account_balance
        )
        if not exposure_valid:
            self.logger.warning(
                "signal_rejected_exposure_exceeded",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                reason=exposure_reason,
            )
            return False, exposure_reason

        # 11. Validate market impact (order size * leverage < 1% of 24h volume)
        impact_valid, impact_reason = await self._validate_market_impact(signal)
        if not impact_valid:
            self.logger.warning(
                "signal_rejected_market_impact",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                reason=impact_reason,
            )
            return False, impact_reason

        # 12. Validate minimum balance
        if account_balance < Decimal(str(MIN_POSITION_SIZE_USDT)):
            reason = f"Insufficient balance ({account_balance} < {MIN_POSITION_SIZE_USDT})"
            self.logger.warning(
                "signal_rejected_insufficient_balance",
                symbol=signal.symbol,
                signal_id=str(signal.id),
                account_balance=float(account_balance),
                min_balance=MIN_POSITION_SIZE_USDT,
                reason=reason,
            )
            return False, reason

        # All checks passed
        self.logger.info(
            "signal_validated",
            symbol=signal.symbol,
            signal_id=str(signal.id),
            signal_type=signal.type.value,
            direction=signal.direction.value,
            entry=float(signal.entry_price),
            stop_loss=float(signal.stop_loss),
            priority=float(signal.priority),
            age_seconds=age_seconds,
            account_balance=float(account_balance),
        )

        return True, None

    async def _check_symbol_active(self, symbol: str) -> bool:
        """
        Check if symbol is in active list (market_stats.is_active = True).

        Args:
            symbol: Trading symbol to check

        Returns:
            True if symbol is active, False otherwise
        """
        try:
            row = await self.db_manager.fetchrow(
                "SELECT is_active FROM market_stats WHERE symbol = $1",
                symbol
            )
            return row["is_active"] if row else False
        except Exception as e:
            self.logger.error(
                "failed_to_check_symbol_active",
                symbol=symbol,
                error=str(e),
            )
            # On error, return False to be safe
            return False

    async def _get_open_positions_count(self) -> int:
        """
        Get number of currently open positions from trades table.

        Returns:
            Count of open positions
        """
        try:
            row = await self.db_manager.fetchrow(
                "SELECT COUNT(*) as count FROM trades WHERE status = $1",
                'open'
            )
            return row["count"] if row else 0
        except Exception as e:
            self.logger.error(
                "failed_to_get_open_positions_count",
                error=str(e),
            )
            # On error, return high number to prevent opening positions
            return self.max_concurrent_positions

    async def _has_open_position(self, symbol: str) -> bool:
        """
        Check if there's an open position for this symbol.

        Args:
            symbol: Trading symbol to check

        Returns:
            True if open position exists for symbol, False otherwise
        """
        try:
            row = await self.db_manager.fetchrow(
                "SELECT COUNT(*) as count FROM trades WHERE symbol = $1 AND status = $2",
                symbol,
                'open'
            )
            return (row["count"] if row else 0) > 0
        except Exception as e:
            self.logger.error(
                "failed_to_check_open_position",
                symbol=symbol,
                error=str(e),
            )
            # On error, return True to prevent opening duplicate positions
            return True

    def _get_current_market_price(self, symbol: str) -> Optional[Decimal]:
        """
        Get current market price from orderbook (mid-price between best bid and ask).

        Args:
            symbol: Trading symbol

        Returns:
            Current mid-price or None if orderbook not available
        """
        try:
            orderbook = self.orderbook_manager.get_current_orderbook(symbol)
            if orderbook:
                return orderbook.get_mid_price()
            return None
        except Exception as e:
            self.logger.error(
                "failed_to_get_current_market_price",
                symbol=symbol,
                error=str(e),
            )
            return None

    async def _validate_total_exposure(
        self, signal: Signal, account_balance: Decimal
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that total exposure doesn't exceed max_exposure_percent of capital.

        Calculates:
        - Sum of all open positions' value
        - Adds new position value
        - Checks against balance * max_exposure_percent

        Args:
            signal: New signal to validate
            account_balance: Current account balance

        Returns:
            Tuple of (is_valid, reason)
        """
        try:
            # Get all open positions from trades table
            open_trades = await self.db_manager.get_open_trades()

            # Calculate current total exposure
            current_exposure = Decimal("0")
            for trade in open_trades:
                # Exposure = position_size * entry_price
                position_value = Decimal(str(trade['position_size'])) * Decimal(str(trade['entry_price']))
                current_exposure += position_value

            # Calculate new position value
            new_position_value = self.position_size_usdt

            # Total exposure with new position
            total_exposure = current_exposure + new_position_value

            # Maximum allowed exposure
            max_allowed = account_balance * (self.max_exposure_percent / Decimal("100"))

            self.logger.debug(
                "exposure_validation",
                symbol=signal.symbol,
                current_exposure=float(current_exposure),
                new_position_value=float(new_position_value),
                total_exposure=float(total_exposure),
                max_allowed=float(max_allowed),
                account_balance=float(account_balance),
                max_exposure_percent=float(self.max_exposure_percent),
            )

            if total_exposure > max_allowed:
                reason = (
                    f"Total exposure {total_exposure:.2f} USDT would exceed "
                    f"{self.max_exposure_percent}% of balance ({max_allowed:.2f} USDT)"
                )
                return False, reason

            return True, None

        except Exception as e:
            self.logger.error(
                "exposure_validation_error",
                symbol=signal.symbol,
                error=str(e),
                exc_info=True,
            )
            # On error, reject to be safe
            return False, f"Exposure validation error: {str(e)}"

    async def _validate_market_impact(
        self, signal: Signal
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that order value * leverage doesn't exceed max % of 24h volume.

        This prevents placing orders that could significantly move the market.

        Args:
            signal: Signal to validate

        Returns:
            Tuple of (is_valid, reason)
        """
        try:
            # Get 24h volume for symbol from market_stats
            row = await self.db_manager.fetchrow(
                "SELECT volume_24h FROM market_stats WHERE symbol = $1",
                signal.symbol
            )

            if not row or not row.get('volume_24h'):
                # If no volume data, allow with warning
                self.logger.warning(
                    "market_impact_no_volume_data",
                    symbol=signal.symbol,
                    message="No 24h volume data available, skipping market impact check"
                )
                return True, None

            volume_24h = Decimal(str(row['volume_24h']))

            # Calculate order value with leverage
            order_value = self.position_size_usdt * self.leverage

            # Maximum allowed order value
            max_allowed = volume_24h * (self.max_volume_impact_percent / Decimal("100"))

            self.logger.debug(
                "market_impact_validation",
                symbol=signal.symbol,
                position_size=float(self.position_size_usdt),
                leverage=self.leverage,
                order_value=float(order_value),
                volume_24h=float(volume_24h),
                max_allowed=float(max_allowed),
                max_volume_impact_percent=float(self.max_volume_impact_percent),
            )

            if order_value > max_allowed:
                impact_percent = (order_value / volume_24h) * Decimal("100")
                reason = (
                    f"Order value {order_value:.2f} USDT (with {self.leverage}x leverage) "
                    f"is {impact_percent:.4f}% of 24h volume ({volume_24h:.0f} USDT), "
                    f"exceeds max {self.max_volume_impact_percent}%"
                )
                return False, reason

            return True, None

        except Exception as e:
            self.logger.error(
                "market_impact_validation_error",
                symbol=signal.symbol,
                error=str(e),
                exc_info=True,
            )
            # On error, allow but log warning
            return True, None
