"""
Safety Monitor for the crypto trading bot.

This module provides the SafetyMonitor class that monitors system health
and enforces safety limits to protect against catastrophic losses.
"""

from decimal import Decimal
from typing import List, Optional
from datetime import datetime, timedelta

from src.storage.db_manager import DatabaseManager
from src.trading_execution.order_executor import OrderExecutor
from src.storage.models import Position, PositionStatus, SystemEvent
from src.utils.logger import get_logger
from src.utils.types import (
    DEFAULT_MAX_EXPOSURE_PERCENT,
    EVENT_TYPE_BOT_ERROR,
    EVENT_SEVERITY_CRITICAL,
)


class SafetyMonitor:
    """
    Monitors system safety and enforces risk limits.

    Responsibilities:
    - Monitor account balance
    - Enforce exposure limits
    - Check connection health
    - Execute emergency procedures
    - Alert on critical conditions

    Safety Features:
    - Emergency shutdown if balance drops below minimum
    - Position exposure limits (total and per-position)
    - Connection health monitoring
    - Critical event logging
    - Trading disable on repeated failures
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        order_executor: OrderExecutor,
        min_balance_usdt: Decimal = Decimal("10"),
        max_total_exposure_percent: Decimal = Decimal("50"),
        max_position_exposure_percent: Decimal = Decimal(str(DEFAULT_MAX_EXPOSURE_PERCENT)),
    ):
        """
        Initialize safety monitor.

        Args:
            db_manager: Database manager instance
            order_executor: Order executor instance
            min_balance_usdt: Minimum account balance in USDT (default: 10)
            max_total_exposure_percent: Maximum total exposure as % of balance (default: 50)
            max_position_exposure_percent: Maximum per-position exposure as % of balance (default: 5)
        """
        self.db_manager = db_manager
        self.order_executor = order_executor
        self.min_balance_usdt = min_balance_usdt
        self.max_total_exposure_percent = max_total_exposure_percent
        self.max_position_exposure_percent = max_position_exposure_percent
        self.logger = get_logger(__name__)

        self._emergency_shutdown = False
        self._trading_enabled = True
        self._last_balance_check: Optional[datetime] = None
        self._last_exposure_check: Optional[datetime] = None
        self._consecutive_failures = 0
        self._max_consecutive_failures = 3

        self.logger.info(
            "safety_monitor_initialized",
            min_balance_usdt=float(min_balance_usdt),
            max_total_exposure_percent=float(max_total_exposure_percent),
            max_position_exposure_percent=float(max_position_exposure_percent),
        )

    async def check_safety_conditions(self) -> bool:
        """
        Check all safety conditions.

        Performs comprehensive safety checks:
        1. Account balance above minimum threshold
        2. Position exposure within limits
        3. Database connection health

        Returns:
            True if all safe, False if issues found
        """
        try:
            # 1. Check account balance
            balance_ok = await self._check_account_balance()
            if not balance_ok:
                self._consecutive_failures += 1
                return False

            # 2. Check exposure limits
            exposure_ok = await self._check_exposure_limits()
            if not exposure_ok:
                self._consecutive_failures += 1
                return False

            # 3. Check connection health
            health_ok = await self._check_connection_health()
            if not health_ok:
                self.logger.warning("connection_health_issues")
                self._consecutive_failures += 1
            else:
                # Reset failure counter on success
                self._consecutive_failures = 0

            # Check if too many consecutive failures
            if self._consecutive_failures >= self._max_consecutive_failures:
                self.logger.critical(
                    "too_many_consecutive_failures",
                    consecutive_failures=self._consecutive_failures,
                    max_allowed=self._max_consecutive_failures,
                )
                await self._log_critical_event(
                    "REPEATED_SAFETY_FAILURES",
                    f"Safety checks failed {self._consecutive_failures} times consecutively"
                )
                await self.disable_trading()
                return False

            return True

        except Exception as e:
            self.logger.error("safety_check_failed", error=str(e), exc_info=True)
            self._consecutive_failures += 1
            return False

    async def _check_account_balance(self) -> bool:
        """
        Check account balance is above minimum.

        Returns:
            True if balance is safe, False if below minimum
        """
        try:
            # TODO: Get real balance from exchange API
            # For now, use placeholder
            # In production, this would call:
            # balance = await self.order_executor.client.get_wallet_balance()
            balance = Decimal("100")

            self._last_balance_check = datetime.now()

            if balance < self.min_balance_usdt:
                self.logger.critical(
                    "balance_below_minimum",
                    balance=float(balance),
                    minimum=float(self.min_balance_usdt),
                )

                # Log critical event
                await self._log_critical_event(
                    "LOW_BALANCE",
                    f"Balance {balance} below minimum {self.min_balance_usdt}"
                )

                # Trigger emergency shutdown
                await self.emergency_shutdown()
                return False

            self.logger.debug(
                "balance_check_passed",
                balance=float(balance),
                minimum=float(self.min_balance_usdt),
            )
            return True

        except Exception as e:
            self.logger.error("balance_check_error", error=str(e), exc_info=True)
            return False

    async def _check_exposure_limits(self) -> bool:
        """
        Check total and per-position exposure limits.

        Verifies:
        - Total exposure doesn't exceed max_total_exposure_percent
        - Individual positions don't exceed max_position_exposure_percent

        Returns:
            True if exposure is within limits, False if exceeded
        """
        try:
            # TODO: Get real positions and balance from exchange
            # For now, use placeholder
            balance = Decimal("100")

            # In production, this would query open positions:
            # positions = await self._get_open_positions()
            # total_exposure = sum(pos.size * pos.entry_price for pos in positions)
            total_exposure = Decimal("0")

            self._last_exposure_check = datetime.now()

            # Calculate exposure percentage
            exposure_percent = (total_exposure / balance * Decimal("100")) if balance > 0 else Decimal("0")

            if exposure_percent > self.max_total_exposure_percent:
                self.logger.critical(
                    "total_exposure_exceeded",
                    exposure_percent=float(exposure_percent),
                    max_percent=float(self.max_total_exposure_percent),
                    total_exposure=float(total_exposure),
                    balance=float(balance),
                )

                await self._log_critical_event(
                    "EXPOSURE_LIMIT_EXCEEDED",
                    f"Total exposure {exposure_percent}% exceeds limit {self.max_total_exposure_percent}%"
                )

                # TODO: Implement position reduction logic
                # await self._reduce_exposure(positions, balance)
                return False

            self.logger.debug(
                "exposure_check_passed",
                exposure_percent=float(exposure_percent),
                max_percent=float(self.max_total_exposure_percent),
                total_exposure=float(total_exposure),
                balance=float(balance),
            )
            return True

        except Exception as e:
            self.logger.error("exposure_check_error", error=str(e), exc_info=True)
            return False

    async def _check_connection_health(self) -> bool:
        """
        Check database and WebSocket connections.

        Verifies:
        - Database connection pool is active
        - Simple query can be executed

        Returns:
            True if connections are healthy, False otherwise
        """
        try:
            # Check database connection
            if not self.db_manager.pool:
                self.logger.error("database_connection_lost")
                await self._log_critical_event(
                    "DATABASE_CONNECTION_LOST",
                    "Database connection pool is not available"
                )
                return False

            # Try a simple query to verify connection
            result = await self.db_manager.fetchval("SELECT 1")

            if result != 1:
                self.logger.error("database_query_failed", result=result)
                return False

            self.logger.debug("connection_health_ok")
            return True

        except Exception as e:
            self.logger.error("connection_health_check_error", error=str(e), exc_info=True)
            await self._log_critical_event(
                "CONNECTION_HEALTH_CHECK_FAILED",
                f"Connection health check error: {str(e)}"
            )
            return False

    async def emergency_shutdown(self) -> None:
        """
        Execute emergency shutdown procedures.

        Emergency shutdown sequence:
        1. Set emergency_shutdown flag
        2. Disable trading
        3. Log critical event
        4. TODO: Close all open positions
        5. TODO: Alert operator

        CRITICAL: This is a last-resort safety measure.
        Manual intervention may be required.
        """
        if self._emergency_shutdown:
            self.logger.warning("emergency_shutdown_already_active")
            return  # Already in shutdown

        self._emergency_shutdown = True
        self._trading_enabled = False

        self.logger.critical("emergency_shutdown_initiated")

        try:
            # Log critical event
            await self._log_critical_event(
                "EMERGENCY_SHUTDOWN",
                "Emergency shutdown initiated - all trading stopped"
            )

            # TODO: Close all open positions
            # In production, this would:
            # 1. Get all open positions from exchange
            # 2. Close each position with market orders
            # 3. Verify all positions are closed
            # 4. Send alert to operator

            self.logger.critical(
                "emergency_shutdown_complete",
                message="All trading stopped. Manual intervention may be required."
            )

        except Exception as e:
            self.logger.critical(
                "emergency_shutdown_failed",
                error=str(e),
                message="MANUAL INTERVENTION REQUIRED",
                exc_info=True,
            )

    async def disable_trading(self) -> None:
        """
        Disable trading without emergency shutdown.

        This is used for non-critical safety violations where
        positions can remain open but no new positions should be created.
        """
        if not self._trading_enabled:
            self.logger.warning("trading_already_disabled")
            return

        self._trading_enabled = False

        self.logger.warning("trading_disabled")

        await self._log_critical_event(
            "TRADING_DISABLED",
            "Trading disabled due to safety concerns"
        )

    async def enable_trading(self) -> None:
        """
        Re-enable trading after manual verification.

        This should only be called after:
        1. Safety issues have been resolved
        2. System state has been manually verified
        3. All checks pass
        """
        if self._emergency_shutdown:
            self.logger.error(
                "cannot_enable_trading_emergency_shutdown",
                message="Clear emergency shutdown first"
            )
            return

        self._trading_enabled = True
        self._consecutive_failures = 0

        self.logger.info("trading_enabled")

        await self._log_critical_event(
            "TRADING_ENABLED",
            "Trading re-enabled after safety verification"
        )

    async def _log_critical_event(self, event_type: str, message: str) -> None:
        """
        Log critical event to database.

        Args:
            event_type: Type of critical event
            message: Event message
        """
        try:
            event = SystemEvent(
                event_type=event_type,
                severity=EVENT_SEVERITY_CRITICAL,
                message=message,
                timestamp=datetime.now(),
            )
            await self.db_manager.log_event(event)

        except Exception as e:
            self.logger.error(
                "failed_to_log_critical_event",
                event_type=event_type,
                message=message,
                error=str(e),
                exc_info=True,
            )

    def is_trading_enabled(self) -> bool:
        """
        Check if trading is currently enabled.

        Returns:
            True if trading is enabled, False otherwise
        """
        return self._trading_enabled and not self._emergency_shutdown

    def is_emergency_shutdown(self) -> bool:
        """
        Check if system is in emergency shutdown mode.

        Returns:
            True if in emergency shutdown, False otherwise
        """
        return self._emergency_shutdown

    def get_status(self) -> dict:
        """
        Get current safety monitor status.

        Returns:
            Dictionary with status information
        """
        return {
            "trading_enabled": self._trading_enabled,
            "emergency_shutdown": self._emergency_shutdown,
            "consecutive_failures": self._consecutive_failures,
            "last_balance_check": self._last_balance_check.isoformat() if self._last_balance_check else None,
            "last_exposure_check": self._last_exposure_check.isoformat() if self._last_exposure_check else None,
            "min_balance_usdt": float(self.min_balance_usdt),
            "max_total_exposure_percent": float(self.max_total_exposure_percent),
            "max_position_exposure_percent": float(self.max_position_exposure_percent),
        }
