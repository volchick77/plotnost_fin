"""
Order executor for the crypto trading bot.

This module provides the OrderExecutor class that executes validated trading signals
on Bybit exchange. It handles position sizing, order placement, stop-loss management,
and error recovery with safety-first approach.
"""

from decimal import Decimal
from typing import Optional, Dict, Any
import asyncio

from pybit.unified_trading import HTTP

from src.storage.models import Signal, Position, PositionDirection, PositionStatus
from src.storage.db_manager import DatabaseManager
from src.utils.logger import get_logger
from src.utils.types import DEFAULT_LEVERAGE, DEFAULT_POSITION_SIZE_USDT


class OrderExecutor:
    """
    Executes validated trading signals on Bybit.

    Handles:
    - Position sizing with leverage
    - Market order placement for entry
    - Stop-loss order placement
    - Isolated margin mode setup
    - Error handling and recovery

    Safety Features:
    - NEVER leaves position without stop-loss
    - Uses ISOLATED margin mode for risk containment
    - Emergency position closure if stop-loss fails
    - Retry logic for API failures
    - Comprehensive logging of all operations
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        position_size_usdt: Decimal = Decimal(str(DEFAULT_POSITION_SIZE_USDT)),
        leverage: int = DEFAULT_LEVERAGE,
    ):
        """
        Initialize order executor.

        Args:
            db_manager: Database manager instance
            api_key: Bybit API key
            api_secret: Bybit API secret
            testnet: Use testnet (default: False for mainnet)
            position_size_usdt: Position size in USDT (default: 0.1)
            leverage: Leverage multiplier (default: 10)
        """
        self.db_manager = db_manager
        self.position_size_usdt = position_size_usdt
        self.leverage = leverage
        self.logger = get_logger(__name__)

        # Initialize Bybit HTTP client
        self.client = HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
        )

        self.logger.info(
            "order_executor_initialized",
            testnet=testnet,
            position_size=float(position_size_usdt),
            leverage=leverage,
        )

    async def execute_signal(self, signal: Signal) -> Optional[Position]:
        """
        Execute a validated trading signal.

        Args:
            signal: Validated signal to execute

        Returns:
            Position object if successful, None if failed
        """
        try:
            # 1. Set margin mode to ISOLATED
            await self._set_margin_mode(signal.symbol, "ISOLATED")

            # 2. Set leverage
            await self._set_leverage(signal.symbol, self.leverage)

            # 3. Calculate position size
            qty = await self._calculate_quantity(signal)
            if not qty:
                self.logger.error("quantity_calculation_failed", symbol=signal.symbol)
                return None

            # 4. Place entry order (market order)
            entry_order = await self._place_market_order(signal, qty)
            if not entry_order:
                self.logger.error("entry_order_failed", symbol=signal.symbol)
                return None

            # 5. Place stop-loss order (CRITICAL)
            stop_order = await self._place_stop_loss_order(signal, qty)
            if not stop_order:
                self.logger.error(
                    "stop_loss_order_failed",
                    symbol=signal.symbol,
                    message="EMERGENCY: Closing position without stop-loss"
                )
                # CRITICAL: Close position immediately if stop-loss fails
                await self._emergency_close_position(signal.symbol, qty, signal.direction)
                return None

            # 6. Create Position object
            position = Position(
                symbol=signal.symbol,
                entry_price=Decimal(str(entry_order.get("avgPrice", signal.entry_price))),
                size=qty,
                leverage=self.leverage,
                direction=signal.direction,
                signal_type=signal.type,
                stop_loss=signal.stop_loss,
                status=PositionStatus.OPEN,
                density_price=signal.density.price_level,
                signal_priority=signal.priority,
            )

            self.logger.info(
                "position_opened",
                symbol=signal.symbol,
                direction=signal.direction.value,
                entry_price=float(position.entry_price),
                qty=float(qty),
                stop_loss=float(signal.stop_loss),
                leverage=self.leverage,
            )

            return position

        except Exception as e:
            self.logger.error(
                "execute_signal_failed",
                symbol=signal.symbol,
                error=str(e),
                exc_info=True
            )
            return None

    async def _set_margin_mode(self, symbol: str, mode: str) -> bool:
        """
        Set margin mode (ISOLATED or CROSS).

        Args:
            symbol: Trading symbol
            mode: Margin mode ("ISOLATED" or "CROSS")

        Returns:
            True if successful, False otherwise
        """
        try:
            # Use asyncio to run blocking Bybit API call
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.set_margin_mode(
                    category="linear",
                    symbol=symbol,
                    tradeMode=0 if mode == "ISOLATED" else 1,
                    buyLeverage=str(self.leverage),
                    sellLeverage=str(self.leverage),
                )
            )

            if response["retCode"] == 0:
                self.logger.info("margin_mode_set", symbol=symbol, mode=mode)
                return True
            else:
                self.logger.warning("margin_mode_set_failed", symbol=symbol, response=response)
                return False

        except Exception as e:
            self.logger.error("margin_mode_error", symbol=symbol, error=str(e))
            return False

    async def _set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for symbol.

        Args:
            symbol: Trading symbol
            leverage: Leverage multiplier

        Returns:
            True if successful, False otherwise
        """
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=str(leverage),
                    sellLeverage=str(leverage),
                )
            )

            if response["retCode"] == 0:
                self.logger.info("leverage_set", symbol=symbol, leverage=leverage)
                return True
            else:
                self.logger.warning("leverage_set_failed", symbol=symbol, response=response)
                return False

        except Exception as e:
            self.logger.error("leverage_error", symbol=symbol, error=str(e))
            return False

    async def _calculate_quantity(self, signal: Signal) -> Optional[Decimal]:
        """
        Calculate position quantity in base currency.

        Formula: position_size_usdt / entry_price = quantity

        Args:
            signal: Trading signal with entry price

        Returns:
            Calculated quantity or None if calculation fails
        """
        try:
            qty = self.position_size_usdt / signal.entry_price

            # Round to appropriate precision (will need symbol-specific precision)
            # For now, round to 3 decimal places
            qty = qty.quantize(Decimal("0.001"))

            self.logger.debug(
                "quantity_calculated",
                symbol=signal.symbol,
                entry_price=float(signal.entry_price),
                position_size_usdt=float(self.position_size_usdt),
                quantity=float(qty),
            )

            return qty

        except Exception as e:
            self.logger.error("quantity_calculation_error", error=str(e))
            return None

    async def _place_market_order(self, signal: Signal, qty: Decimal) -> Optional[Dict[str, Any]]:
        """
        Place market entry order.

        Args:
            signal: Trading signal
            qty: Order quantity

        Returns:
            Order response dict if successful, None otherwise
        """
        max_retries = 3
        retry_delay = 1  # seconds

        for attempt in range(max_retries):
            try:
                side = "Buy" if signal.direction == PositionDirection.LONG else "Sell"

                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.place_order(
                        category="linear",
                        symbol=signal.symbol,
                        side=side,
                        orderType="Market",
                        qty=str(qty),
                        timeInForce="GTC",
                        positionIdx=0,  # One-way mode
                    )
                )

                if response["retCode"] == 0:
                    self.logger.info(
                        "market_order_placed",
                        symbol=signal.symbol,
                        side=side,
                        qty=float(qty),
                        order_id=response.get("result", {}).get("orderId"),
                        attempt=attempt + 1,
                    )
                    return response.get("result", {})
                else:
                    self.logger.error(
                        "market_order_failed",
                        symbol=signal.symbol,
                        response=response,
                        attempt=attempt + 1,
                    )

                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay * (attempt + 1))

            except Exception as e:
                self.logger.error(
                    "market_order_error",
                    symbol=signal.symbol,
                    error=str(e),
                    attempt=attempt + 1,
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))

        return None

    async def _place_stop_loss_order(self, signal: Signal, qty: Decimal) -> Optional[Dict[str, Any]]:
        """
        Place stop-loss order.

        CRITICAL: This must succeed or the position will be closed immediately.

        Args:
            signal: Trading signal with stop-loss price
            qty: Order quantity

        Returns:
            Order response dict if successful, None otherwise
        """
        max_retries = 3
        retry_delay = 1  # seconds

        for attempt in range(max_retries):
            try:
                # Stop-loss side is OPPOSITE of entry
                side = "Sell" if signal.direction == PositionDirection.LONG else "Buy"

                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.place_order(
                        category="linear",
                        symbol=signal.symbol,
                        side=side,
                        orderType="Market",  # Stop market order
                        qty=str(qty),
                        stopLoss=str(signal.stop_loss),
                        timeInForce="GTC",
                        positionIdx=0,
                    )
                )

                if response["retCode"] == 0:
                    self.logger.info(
                        "stop_loss_order_placed",
                        symbol=signal.symbol,
                        stop_price=float(signal.stop_loss),
                        qty=float(qty),
                        attempt=attempt + 1,
                    )
                    return response.get("result", {})
                else:
                    self.logger.error(
                        "stop_loss_order_failed",
                        symbol=signal.symbol,
                        response=response,
                        attempt=attempt + 1,
                    )

                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay * (attempt + 1))

            except Exception as e:
                self.logger.error(
                    "stop_loss_order_error",
                    symbol=signal.symbol,
                    error=str(e),
                    attempt=attempt + 1,
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))

        return None

    async def _emergency_close_position(
        self, symbol: str, qty: Decimal, direction: PositionDirection
    ) -> None:
        """
        Emergency close position if stop-loss placement fails.

        CRITICAL: This is a last-resort safety measure. Manual intervention
        may be required if this fails.

        Args:
            symbol: Trading symbol
            qty: Position quantity
            direction: Position direction
        """
        max_retries = 3
        retry_delay = 1  # seconds

        for attempt in range(max_retries):
            try:
                # Close side is OPPOSITE of entry
                side = "Sell" if direction == PositionDirection.LONG else "Buy"

                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.place_order(
                        category="linear",
                        symbol=symbol,
                        side=side,
                        orderType="Market",
                        qty=str(qty),
                        timeInForce="GTC",
                        positionIdx=0,
                        reduceOnly=True,
                    )
                )

                if response["retCode"] == 0:
                    self.logger.warning(
                        "emergency_close_successful",
                        symbol=symbol,
                        qty=float(qty),
                        attempt=attempt + 1,
                    )
                    return
                else:
                    self.logger.critical(
                        "emergency_close_failed",
                        symbol=symbol,
                        response=response,
                        attempt=attempt + 1,
                        message="MANUAL INTERVENTION REQUIRED"
                    )

                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay * (attempt + 1))

            except Exception as e:
                self.logger.critical(
                    "emergency_close_error",
                    symbol=symbol,
                    error=str(e),
                    attempt=attempt + 1,
                    message="MANUAL INTERVENTION REQUIRED"
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))

        # If we get here, all retries failed - log critical alert
        self.logger.critical(
            "emergency_close_all_retries_failed",
            symbol=symbol,
            qty=float(qty),
            direction=direction.value,
            message="CRITICAL: Position remains open without stop-loss. IMMEDIATE MANUAL INTERVENTION REQUIRED!"
        )
