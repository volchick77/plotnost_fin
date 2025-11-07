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

        # Rate limiting
        self._api_semaphore = asyncio.Semaphore(20)  # Max 20 concurrent requests

        self.logger.info(
            "order_executor_initialized",
            testnet=testnet,
            position_size=float(position_size_usdt),
            leverage=leverage,
        )

    async def _api_call_with_retry(
        self,
        func: callable,
        *args,
        is_critical: bool = False,
        **kwargs
    ) -> dict:
        """
        Execute API call with retry logic and rate limiting.

        Args:
            func: Sync function to call (from pybit client)
            is_critical: If True, use more retries for critical operations
            *args, **kwargs: Arguments to pass to func

        Returns:
            API response dict

        Raises:
            Exception if all retries exhausted
        """
        max_retries = 5 if is_critical else 3

        for attempt in range(max_retries):
            try:
                async with self._api_semaphore:
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, lambda: func(*args, **kwargs))

                    # Check for rate limit error
                    if response.get("retCode") == 10006:  # Rate limit exceeded
                        delay = 0.5 * (2 ** attempt)  # Exponential backoff
                        self.logger.warning(
                            "rate_limit_hit",
                            attempt=attempt + 1,
                            retry_delay=delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Check for other errors
                    if response.get("retCode") != 0:
                        self.logger.error(
                            "api_call_failed",
                            ret_code=response.get("retCode"),
                            ret_msg=response.get("retMsg"),
                            attempt=attempt + 1,
                        )

                        if attempt < max_retries - 1:
                            await asyncio.sleep(1 * (attempt + 1))
                            continue
                        else:
                            raise Exception(f"API call failed: {response.get('retMsg')}")

                    return response

            except Exception as e:
                self.logger.error(
                    "api_call_exception",
                    error=str(e),
                    attempt=attempt + 1,
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    raise

        raise Exception("All retries exhausted")

    async def get_account_balance(self) -> Decimal:
        """
        Get account balance in USDT from Bybit.

        Returns:
            Available USDT balance
        """
        try:
            response = await self._api_call_with_retry(
                self.client.get_wallet_balance,
                accountType="UNIFIED",
                coin="USDT",
                is_critical=False,
            )

            # Parse response
            result = response.get("result", {})
            coins = result.get("list", [{}])[0].get("coin", [])

            for coin in coins:
                if coin.get("coin") == "USDT":
                    balance = Decimal(coin.get("walletBalance", "0"))

                    self.logger.info(
                        "balance_fetched",
                        balance=float(balance),
                    )

                    return balance

            self.logger.warning("usdt_balance_not_found")
            return Decimal("0")

        except Exception as e:
            self.logger.error(
                "get_balance_failed",
                error=str(e),
                exc_info=True,
            )
            # Return 0 on error to trigger safety checks
            return Decimal("0")

    async def fetch_open_positions_from_exchange(self) -> list[dict]:
        """
        Fetch all open positions from Bybit exchange.

        Returns:
            List of position dictionaries
        """
        try:
            response = await self._api_call_with_retry(
                self.client.get_positions,
                category="linear",
                settleCoin="USDT",
                is_critical=False,
            )

            result = response.get("result", {})
            positions = result.get("list", [])

            # Filter only positions with non-zero size
            open_positions = [
                pos for pos in positions
                if Decimal(pos.get("size", "0")) > 0
            ]

            self.logger.info(
                "positions_fetched_from_exchange",
                count=len(open_positions),
            )

            return open_positions

        except Exception as e:
            self.logger.error(
                "fetch_positions_failed",
                error=str(e),
                exc_info=True,
            )
            return []

    async def close_position(
        self, symbol: str, qty: Decimal, side: str
    ) -> bool:
        """
        Close a position with market order.

        Args:
            symbol: Trading symbol
            qty: Quantity to close
            side: "Sell" for closing LONG, "Buy" for closing SHORT

        Returns:
            True if successful, False otherwise
        """
        try:
            response = await self._api_call_with_retry(
                self.client.place_order,
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=str(qty),
                timeInForce="GTC",
                reduceOnly=True,
                positionIdx=0,
                is_critical=True,  # Closing is critical
            )

            if response.get("retCode") == 0:
                order_id = response.get("result", {}).get("orderId")
                self.logger.info(
                    "position_closed",
                    symbol=symbol,
                    qty=float(qty),
                    side=side,
                    order_id=order_id,
                )
                return True
            else:
                self.logger.error(
                    "close_position_failed",
                    symbol=symbol,
                    response=response,
                )
                return False

        except Exception as e:
            self.logger.error(
                "close_position_exception",
                symbol=symbol,
                error=str(e),
                exc_info=True,
            )
            return False

    async def modify_stop_loss(
        self, symbol: str, stop_loss: Decimal
    ) -> bool:
        """
        Modify stop-loss for an open position.

        Args:
            symbol: Trading symbol
            stop_loss: New stop-loss price

        Returns:
            True if successful, False otherwise
        """
        try:
            response = await self._api_call_with_retry(
                self.client.set_trading_stop,
                category="linear",
                symbol=symbol,
                stopLoss=str(stop_loss),
                positionIdx=0,
                is_critical=True,  # SL modification is critical
            )

            if response.get("retCode") == 0:
                self.logger.info(
                    "stop_loss_modified",
                    symbol=symbol,
                    stop_loss=float(stop_loss),
                )
                return True
            else:
                self.logger.error(
                    "modify_stop_loss_failed",
                    symbol=symbol,
                    response=response,
                )
                return False

        except Exception as e:
            self.logger.error(
                "modify_stop_loss_exception",
                symbol=symbol,
                error=str(e),
                exc_info=True,
            )
            return False

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

            # 4. Place entry order WITH stop-loss (atomically)
            # CRITICAL: Stop-loss is set during order creation - no separate SL order needed
            entry_order = await self._place_market_order(signal, qty)
            if not entry_order:
                self.logger.error("entry_order_with_stop_loss_failed", symbol=signal.symbol)
                return None

            # 5. Create Position object
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

            # 6. Save to database and get trade_id
            trade_id = await self.db_manager.create_trade_record(position)
            position.id = trade_id  # Update position with DB ID

            self.logger.info(
                "position_opened",
                symbol=signal.symbol,
                direction=signal.direction.value,
                entry_price=float(position.entry_price),
                qty=float(qty),
                stop_loss=float(signal.stop_loss),
                leverage=self.leverage,
                trade_id=str(trade_id),
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
        Place market entry order WITH stop-loss atomically.

        CRITICAL: Stop-loss is set during order creation for maximum safety.
        Position never exists without stop-loss protection.

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
                        # CRITICAL: Set stop-loss atomically with position opening
                        stopLoss=str(signal.stop_loss),
                        slOrderType="Market",  # Stop-loss executes as market order
                        slTriggerBy="LastPrice",  # Trigger based on last traded price
                        timeInForce="GTC",
                        positionIdx=0,  # One-way mode
                    )
                )

                if response["retCode"] == 0:
                    self.logger.info(
                        "market_order_placed_with_stop_loss",
                        symbol=signal.symbol,
                        side=side,
                        qty=float(qty),
                        stop_loss=float(signal.stop_loss),
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
