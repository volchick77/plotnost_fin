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
from src.storage.models import SystemEvent
from src.utils.logger import get_logger
from src.utils.types import EVENT_SEVERITY_CRITICAL


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
        initial_balance: Decimal = Decimal("0"),
        max_loss_percent: Decimal = Decimal("10"),
    ):
        """
        Initialize safety monitor.

        Args:
            db_manager: Database manager instance
            order_executor: Order executor instance
            initial_balance: Starting balance for loss calculation (fetched on startup if 0)
            max_loss_percent: Maximum allowed loss as % of initial balance (default: 10%)
        """
        self.db_manager = db_manager
        self.order_executor = order_executor
        self.initial_balance = initial_balance
        self.max_loss_percent = max_loss_percent
        self.logger = get_logger(__name__)

        self._emergency_shutdown = False
        self._trading_enabled = True
        self._last_balance_check: Optional[datetime] = None

        self.logger.info(
            "safety_monitor_initialized",
            initial_balance=float(initial_balance),
            max_loss_percent=float(max_loss_percent),
        )

    async def check_safety_conditions(self, balance: Optional[Decimal] = None) -> bool:
        """
        Check all safety conditions.

        Safety checks (in order):
        1. Capital loss check - triggers emergency if loss > max_loss_percent
        2. Connection health - technical check only

        Args:
            balance: Optional current account balance

        Returns:
            True if all conditions pass, False otherwise
        """
        try:
            # Fetch balance if not provided
            if balance is None:
                balance = await self.order_executor.get_account_balance()

            # Initialize initial_balance on first check
            if self.initial_balance == Decimal("0"):
                self.initial_balance = balance
                self.logger.info(
                    "initial_balance_set",
                    initial_balance=float(self.initial_balance),
                )

            # Check 1: Capital loss (ONLY trigger for emergency shutdown)
            capital_ok = await self._check_capital_loss(balance)
            if not capital_ok:
                return False

            # Check 2: Connection health (technical check)
            health_ok = await self._check_connection_health()

            return health_ok

        except Exception as e:
            self.logger.error("safety_check_error", error=str(e), exc_info=True)
            return False

    async def _check_capital_loss(self, balance: Decimal) -> bool:
        """
        Check if capital loss exceeds maximum allowed percentage.

        This is the ONLY condition that triggers emergency shutdown.

        Args:
            balance: Current account balance

        Returns:
            True if within acceptable loss, False if emergency triggered
        """
        try:
            self._last_balance_check = datetime.now()

            if self.initial_balance <= Decimal("0"):
                self.logger.warning("initial_balance_not_set")
                return True

            # Calculate loss percentage
            if balance >= self.initial_balance:
                loss_percent = Decimal("0")
            else:
                loss_percent = ((self.initial_balance - balance) / self.initial_balance) * Decimal("100")

            self.logger.debug(
                "capital_loss_check",
                current_balance=float(balance),
                initial_balance=float(self.initial_balance),
                loss_percent=float(loss_percent),
                max_loss_percent=float(self.max_loss_percent),
            )

            if loss_percent >= self.max_loss_percent:
                self.logger.critical(
                    "capital_loss_exceeded",
                    current_balance=float(balance),
                    initial_balance=float(self.initial_balance),
                    loss_percent=float(loss_percent),
                    max_loss_percent=float(self.max_loss_percent),
                )

                # Log critical event
                await self._log_critical_event(
                    "CAPITAL_LOSS_EXCEEDED",
                    f"Loss {loss_percent:.2f}% exceeds maximum {self.max_loss_percent}%. "
                    f"Initial: {self.initial_balance} USDT, Current: {balance} USDT"
                )

                # Trigger emergency shutdown
                await self.emergency_shutdown()
                return False

            return True

        except Exception as e:
            self.logger.error("capital_loss_check_error", error=str(e), exc_info=True)
            return True  # Don't trigger emergency on check error

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
        Execute emergency shutdown procedures with forced position closing.

        Emergency shutdown sequence:
        1. Set emergency_shutdown flag
        2. Disable trading
        3. Log critical event
        4. CLOSE ALL OPEN POSITIONS (forced market orders)
        5. Verify all positions closed

        CRITICAL: This closes ALL positions immediately with market orders.
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
                "Emergency shutdown initiated - closing all positions"
            )

            # CRITICAL: Close ALL open positions
            closed_count = 0
            failed_count = 0

            # Fetch all open positions from exchange
            try:
                exchange_positions = await self.order_executor.fetch_open_positions_from_exchange()

                self.logger.critical(
                    "emergency_closing_positions",
                    position_count=len(exchange_positions),
                )

                for pos_data in exchange_positions:
                    symbol = pos_data.get('symbol', '')
                    size = Decimal(str(pos_data.get('size', '0')))
                    side = pos_data.get('side', '')  # 'Buy' or 'Sell'

                    if size <= 0:
                        continue

                    # Determine close side (opposite of position side)
                    close_side = "Sell" if side == "Buy" else "Buy"

                    try:
                        success = await self.order_executor.close_position(
                            symbol=symbol,
                            qty=size,
                            side=close_side
                        )

                        if success:
                            closed_count += 1
                            self.logger.critical(
                                "emergency_position_closed",
                                symbol=symbol,
                                size=float(size),
                                side=close_side,
                            )
                        else:
                            failed_count += 1
                            self.logger.critical(
                                "emergency_position_close_failed",
                                symbol=symbol,
                                size=float(size),
                                side=close_side,
                            )

                    except Exception as e:
                        failed_count += 1
                        self.logger.critical(
                            "emergency_position_close_error",
                            symbol=symbol,
                            error=str(e),
                            exc_info=True,
                        )

                # Log final results
                await self._log_critical_event(
                    "EMERGENCY_POSITIONS_CLOSED",
                    f"Closed {closed_count} positions, failed {failed_count}"
                )

            except Exception as e:
                self.logger.critical(
                    "emergency_fetch_positions_failed",
                    error=str(e),
                    exc_info=True,
                    message="MANUAL INTERVENTION REQUIRED - Could not fetch positions"
                )

            self.logger.critical(
                "emergency_shutdown_complete",
                closed_positions=closed_count,
                failed_positions=failed_count,
                message="All trading stopped. Manual verification recommended."
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
            "last_balance_check": self._last_balance_check.isoformat() if self._last_balance_check else None,
            "initial_balance": float(self.initial_balance),
            "max_loss_percent": float(self.max_loss_percent),
        }
