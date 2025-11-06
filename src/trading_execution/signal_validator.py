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
        max_exposure_percent: Decimal = Decimal(str(DEFAULT_MAX_EXPOSURE_PERCENT)),
        signal_max_age_seconds: int = 60,
    ):
        """
        Initialize the Signal Validator.

        Args:
            db_manager: Database manager for querying symbols and positions
            orderbook_manager: OrderBook manager for market data
            max_concurrent_positions: Maximum number of concurrent open positions (default: 10)
            max_exposure_percent: Maximum exposure per position as % of balance (default: 5%)
            signal_max_age_seconds: Maximum signal age before rejection (default: 60s)
        """
        self.db_manager = db_manager
        self.orderbook_manager = orderbook_manager
        self.max_concurrent_positions = max_concurrent_positions
        self.max_exposure_percent = max_exposure_percent
        self.signal_max_age_seconds = signal_max_age_seconds
        self.logger = get_logger(__name__)

        self.logger.info(
            "signal_validator_initialized",
            max_concurrent_positions=max_concurrent_positions,
            max_exposure_percent=float(max_exposure_percent),
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

        # 10. Validate balance and exposure
        # This is a simplified check - actual implementation needs position size calculation
        # For now, just check minimum balance
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
        Get number of currently open positions.

        This will query the positions table once it's created in Batch 5.
        For now, returns 0 as a placeholder.

        Returns:
            Count of open positions
        """
        try:
            # TODO: This will be implemented when positions table is created in Batch 5
            # row = await self.db_manager.fetchrow(
            #     "SELECT COUNT(*) as count FROM positions WHERE status = $1",
            #     PositionStatus.OPEN.value
            # )
            # return row["count"] if row else 0

            # Placeholder: return 0 until positions table exists
            return 0
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

        This will query the positions table once it's created in Batch 5.
        For now, returns False as a placeholder.

        Args:
            symbol: Trading symbol to check

        Returns:
            True if open position exists for symbol, False otherwise
        """
        try:
            # TODO: This will be implemented when positions table is created in Batch 5
            # row = await self.db_manager.fetchrow(
            #     "SELECT COUNT(*) as count FROM positions WHERE symbol = $1 AND status = $2",
            #     symbol,
            #     PositionStatus.OPEN.value
            # )
            # return (row["count"] if row else 0) > 0

            # Placeholder: return False until positions table exists
            return False
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
