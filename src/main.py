"""
Main entry point for the crypto trading bot.

This module provides the TradingBot orchestrator that coordinates all components
and manages the trading lifecycle.
"""

import asyncio
import signal
import sys
from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.config import load_config, Config
from src.storage.db_manager import DatabaseManager
from src.storage.models import Position, PositionDirection, PositionStatus, SignalType, ExitReason
from src.data_collection.market_stats_fetcher import MarketStatsFetcher
from src.data_collection.orderbook_manager import OrderBookManager
from src.data_collection.bybit_websocket import BybitWebSocketManager
from src.market_analysis.trend_analyzer import TrendAnalyzer
from src.market_analysis.density_analyzer import DensityAnalyzer
from src.market_analysis.signal_generator import SignalGenerator
from src.trading_execution.signal_validator import SignalValidator
from src.trading_execution.order_executor import OrderExecutor
from src.position_management.position_monitor import PositionMonitor
from src.position_management.safety_monitor import SafetyMonitor
from src.utils.logger import setup_logger, get_logger


class TradingBot:
    """
    Main trading bot orchestrator.

    Coordinates all components and manages the trading lifecycle.
    """

    def __init__(self, config: Config):
        """
        Initialize trading bot with configuration.

        Args:
            config: Configuration object loaded from YAML
        """
        self.config = config
        self.logger = get_logger(__name__)
        self._running = False
        self._tasks = []

        # Components (initialized in start())
        self.db_manager: Optional[DatabaseManager] = None
        self.market_stats_fetcher: Optional[MarketStatsFetcher] = None
        self.orderbook_manager: Optional[OrderBookManager] = None
        self.websocket_manager: Optional[BybitWebSocketManager] = None
        self.trend_analyzer: Optional[TrendAnalyzer] = None
        self.density_analyzer: Optional[DensityAnalyzer] = None
        self.signal_generator: Optional[SignalGenerator] = None
        self.signal_validator: Optional[SignalValidator] = None
        self.order_executor: Optional[OrderExecutor] = None
        self.position_monitor: Optional[PositionMonitor] = None
        self.safety_monitor: Optional[SafetyMonitor] = None

    async def start(self):
        """Initialize and start all components."""
        self.logger.info("trading_bot_starting")

        try:
            # 1. Initialize database
            self.logger.info("initializing_database")
            self.db_manager = DatabaseManager(
                host=self.config.database.host,
                port=self.config.database.port,
                database=self.config.database.name,
                user=self.config.database.user,
                password=self.config.database.password,
                pool_size=self.config.database.pool_max_size,
            )
            await self.db_manager.connect()
            self.logger.info("database_connected")

            # 2. Initialize data collection
            self.logger.info("initializing_data_collection")
            self.market_stats_fetcher = MarketStatsFetcher(
                db_manager=self.db_manager,
                update_interval=self.config.market.update_interval,
                top_count=self.config.market.top_gainers_count,
                min_volume_24h=self.config.market.min_24h_volume,
            )
            await self.market_stats_fetcher.start()

            self.orderbook_manager = OrderBookManager(
                db_manager=self.db_manager,
                snapshot_interval=self.config.websocket.snapshot_interval,
            )
            await self.orderbook_manager.start()

            self.websocket_manager = BybitWebSocketManager(
                orderbook_callback=self.orderbook_manager.update_orderbook,
                orderbook_depth=self.config.websocket.orderbook_depth,
            )

            # 3. Initialize analysis
            self.logger.info("initializing_analysis_components")
            self.trend_analyzer = TrendAnalyzer(db_manager=self.db_manager)
            self.density_analyzer = DensityAnalyzer(orderbook_manager=self.orderbook_manager)
            self.signal_generator = SignalGenerator(
                db_manager=self.db_manager,
                orderbook_manager=self.orderbook_manager,
                trend_analyzer=self.trend_analyzer,
                density_analyzer=self.density_analyzer,
            )

            # 4. Initialize execution
            self.logger.info("initializing_execution_components")
            self.signal_validator = SignalValidator(
                db_manager=self.db_manager,
                orderbook_manager=self.orderbook_manager,
            )

            self.order_executor = OrderExecutor(
                db_manager=self.db_manager,
                api_key=self.config.exchange.api_key,
                api_secret=self.config.exchange.api_secret,
                testnet=False,  # Using mainnet by default
                position_size_usdt=Decimal(str(self.config.trading.position_size_usd)),
                leverage=self.config.trading.leverage,
            )

            # 5. Initialize position management
            self.logger.info("initializing_position_management")
            self.position_monitor = PositionMonitor(
                db_manager=self.db_manager,
                orderbook_manager=self.orderbook_manager,
                check_interval_seconds=5,
            )

            self.safety_monitor = SafetyMonitor(
                db_manager=self.db_manager,
                order_executor=self.order_executor,
                initial_balance=Decimal("0"),  # Will be set on first check
                max_loss_percent=Decimal("10"),  # Emergency shutdown at 10% loss
            )

            # 6. Subscribe to active symbols
            self.logger.info("subscribing_to_active_symbols")
            await self._subscribe_to_symbols()

            # 7. Start WebSocket
            self.logger.info("starting_websocket")
            await self.websocket_manager.start()

            # 7.5. Sync positions from exchange (after WebSocket for orderbook data)
            self.logger.info("syncing_positions_from_exchange")
            await asyncio.sleep(2)  # Wait for initial orderbook data to arrive
            await self._sync_positions_on_startup()

            # 8. Start background tasks
            self.logger.info("starting_background_tasks")
            self._running = True
            self._tasks = [
                asyncio.create_task(self._signal_generation_loop()),
                asyncio.create_task(self._position_monitoring_loop()),
                asyncio.create_task(self._safety_monitoring_loop()),
            ]

            self.logger.info("trading_bot_started")

        except Exception as e:
            self.logger.error("trading_bot_startup_failed", error=str(e), exc_info=True)
            await self.stop()
            raise

    async def stop(self):
        """Stop all components gracefully."""
        self.logger.info("trading_bot_stopping")
        self._running = False

        # Cancel tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Stop components
        if self.websocket_manager:
            try:
                await self.websocket_manager.stop()
            except Exception as e:
                self.logger.error("websocket_stop_error", error=str(e))

        if self.orderbook_manager:
            try:
                await self.orderbook_manager.stop()
            except Exception as e:
                self.logger.error("orderbook_manager_stop_error", error=str(e))

        if self.market_stats_fetcher:
            try:
                await self.market_stats_fetcher.stop()
            except Exception as e:
                self.logger.error("market_stats_fetcher_stop_error", error=str(e))

        if self.db_manager:
            try:
                await self.db_manager.disconnect()
            except Exception as e:
                self.logger.error("database_disconnect_error", error=str(e))

        self.logger.info("trading_bot_stopped")

    async def _subscribe_to_symbols(self):
        """Subscribe to active symbols from market stats."""
        try:
            symbols = await self.db_manager.get_active_symbols()

            # Limit to 10 symbols for testing
            symbols_to_subscribe = symbols[:10]

            for symbol in symbols_to_subscribe:
                self.websocket_manager.add_symbol(symbol)
                self.logger.info("subscribed_to_symbol", symbol=symbol)

            self.logger.info(
                "subscription_complete",
                total_symbols=len(symbols),
                subscribed=len(symbols_to_subscribe),
            )

        except Exception as e:
            self.logger.error("symbol_subscription_error", error=str(e), exc_info=True)

    async def _sync_positions_on_startup(self) -> None:
        """
        Synchronize positions with exchange on startup.

        Restores monitoring for positions that were open when bot stopped.
        """
        try:
            self.logger.info("syncing_positions_on_startup")

            # 1. Get positions from exchange
            exchange_positions = await self.order_executor.fetch_open_positions_from_exchange()

            # 2. Get trade records from database
            db_trades = await self.db_manager.get_open_trades()

            # Create mapping of symbol -> trade data
            trade_map = {trade['symbol']: trade for trade in db_trades}

            # 3. Match and restore position monitoring
            restored_count = 0
            for pos_data in exchange_positions:
                symbol = pos_data.get('symbol')

                # Find corresponding trade in DB
                trade = trade_map.get(symbol)
                if not trade:
                    self.logger.warning(
                        "exchange_position_without_db_record",
                        symbol=symbol,
                        message="Position exists on exchange but not in DB. Manual intervention may be required."
                    )
                    continue

                # Reconstruct Position object from DB and exchange data
                position = Position(
                    id=trade['id'],
                    symbol=symbol,
                    entry_price=Decimal(str(trade['entry_price'])),
                    entry_time=trade['entry_time'],
                    size=Decimal(str(pos_data.get('size', '0'))),
                    leverage=trade['leverage'],
                    direction=PositionDirection(trade['direction']),
                    signal_type=SignalType(trade['signal_type']),
                    stop_loss=Decimal(str(trade['stop_loss_price'])),
                    status=PositionStatus.OPEN,
                    density_price=Decimal(str(pos_data.get('avgPrice', trade['entry_price']))),
                    signal_priority=Decimal("1.0"),
                    breakeven_moved=trade.get('breakeven_moved', False),
                )

                # Start monitoring
                await self.position_monitor.start_monitoring(position)
                restored_count += 1

                self.logger.info(
                    "position_restored",
                    symbol=symbol,
                    position_id=str(position.id),
                    entry_price=float(position.entry_price),
                )

            self.logger.info(
                "position_sync_complete",
                exchange_positions=len(exchange_positions),
                db_trades=len(db_trades),
                restored=restored_count,
            )

        except Exception as e:
            self.logger.error(
                "position_sync_error",
                error=str(e),
                exc_info=True,
            )

    async def _signal_generation_loop(self):
        """Generate and process trading signals."""
        self.logger.info("signal_generation_loop_started")

        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                # Check if trading is enabled
                if self.safety_monitor and not self.safety_monitor.is_trading_enabled():
                    self.logger.warning("signal_generation_skipped_trading_disabled")
                    continue

                symbols = await self.db_manager.get_active_symbols()

                for symbol in symbols[:10]:  # Limit to 10
                    try:
                        # Generate signals
                        signals = await self.signal_generator.generate_signals(symbol)

                        for signal in signals:
                            # CHANGED: Get real balance from exchange
                            balance = await self.order_executor.get_account_balance()
                            is_valid, reason = await self.signal_validator.validate_signal(signal, balance)

                            if not is_valid:
                                self.logger.info("signal_rejected", symbol=symbol, reason=reason)
                                continue

                            # Execute signal
                            position = await self.order_executor.execute_signal(signal)

                            if position:
                                await self.position_monitor.start_monitoring(position)
                                self.logger.info(
                                    "position_opened_from_signal",
                                    symbol=symbol,
                                    position_id=str(position.id),
                                    direction=position.direction.value,
                                    entry_price=float(position.entry_price),
                                )

                    except Exception as e:
                        self.logger.error(
                            "signal_processing_error",
                            symbol=symbol,
                            error=str(e),
                            exc_info=True,
                        )

            except asyncio.CancelledError:
                self.logger.info("signal_generation_loop_cancelled")
                break
            except Exception as e:
                self.logger.error("signal_generation_error", error=str(e), exc_info=True)

        self.logger.info("signal_generation_loop_stopped")

    async def _position_monitoring_loop(self):
        """Monitor open positions."""
        self.logger.info("position_monitoring_loop_started")

        while self._running:
            try:
                await asyncio.sleep(1)  # CHANGED: Check every 1 second for real-time TP

                # Get all monitored positions
                monitored_positions = self.position_monitor.get_monitored_positions()

                for position in monitored_positions:
                    try:
                        # Check if breakeven move is needed
                        if not position.breakeven_moved:
                            orderbook = self.orderbook_manager.get_current_orderbook(position.symbol)
                            if orderbook:
                                current_price = orderbook.get_mid_price()
                                if current_price:
                                    profit_percent = position.calculate_profit_percent(current_price)
                                    params = self.db_manager.coin_params_cache.get_sync(position.symbol)

                                    if params:
                                        # Check breakeven condition based on strategy
                                        should_move = False
                                        if position.signal_type == SignalType.BREAKOUT:
                                            if profit_percent >= params.breakout_breakeven_profit_percent:
                                                should_move = True
                                        elif position.signal_type == SignalType.BOUNCE:
                                            # For bounce, check density erosion
                                            densities = self.orderbook_manager.get_current_densities(position.symbol)
                                            for d in densities:
                                                if d.price_level == position.density_price:
                                                    if d.erosion_percent() >= params.bounce_density_erosion_exit_percent:
                                                        should_move = True
                                                    break

                                        if should_move:
                                            # Move SL to breakeven on exchange
                                            success = await self.order_executor.modify_stop_loss(
                                                position.symbol,
                                                position.entry_price
                                            )

                                            if success:
                                                # Update in memory
                                                position.stop_loss = position.entry_price
                                                position.breakeven_moved = True

                                                # Update in database
                                                await self.db_manager.update_trade_stop_loss(
                                                    position.id,
                                                    position.entry_price,
                                                    breakeven=True
                                                )

                                                self.logger.info(
                                                    "stop_loss_moved_to_breakeven",
                                                    symbol=position.symbol,
                                                    position_id=str(position.id),
                                                    entry_price=float(position.entry_price),
                                                )

                    except Exception as e:
                        self.logger.error(
                            "breakeven_check_error",
                            symbol=position.symbol,
                            error=str(e),
                            exc_info=True,
                        )

                # Now check for positions to close
                positions_to_close = await self.position_monitor.check_positions()

                for position in positions_to_close:
                    try:
                        # Get current price for PnL calculation
                        orderbook = self.orderbook_manager.get_current_orderbook(position.symbol)
                        current_price = orderbook.get_mid_price() if orderbook else position.entry_price

                        # CHANGED: Actually close position via API
                        close_side = "Sell" if position.direction == PositionDirection.LONG else "Buy"
                        success = await self.order_executor.close_position(
                            position.symbol,
                            position.size,
                            close_side
                        )

                        if success:
                            # Calculate PnL
                            pnl = position.calculate_pnl(current_price)

                            # Update database
                            await self.db_manager.close_trade_record(
                                position.id,
                                exit_price=current_price,
                                exit_time=datetime.now(),
                                pnl=pnl,
                                reason=position.exit_reason or ExitReason.TAKE_PROFIT,
                            )

                            # Stop monitoring
                            await self.position_monitor.stop_monitoring(position.symbol)

                            self.logger.info(
                                "position_closed",
                                symbol=position.symbol,
                                position_id=str(position.id),
                                reason=position.exit_reason.value if position.exit_reason else "unknown",
                                pnl=float(pnl),
                                exit_price=float(current_price),
                            )
                        else:
                            self.logger.error(
                                "position_close_failed",
                                symbol=position.symbol,
                                position_id=str(position.id),
                                message="Will retry on next check",
                            )

                    except Exception as e:
                        self.logger.error(
                            "position_close_error",
                            symbol=position.symbol,
                            position_id=str(position.id) if hasattr(position, 'id') else 'unknown',
                            error=str(e),
                            exc_info=True,
                        )

            except asyncio.CancelledError:
                self.logger.info("position_monitoring_loop_cancelled")
                break
            except Exception as e:
                self.logger.error("position_monitoring_error", error=str(e), exc_info=True)

        self.logger.info("position_monitoring_loop_stopped")

    async def _safety_monitoring_loop(self):
        """Monitor safety conditions."""
        self.logger.info("safety_monitoring_loop_started")

        while self._running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                # CHANGED: Get real balance
                balance = await self.order_executor.get_account_balance()

                # Pass balance to safety monitor
                # Note: safety_monitor.check_safety_conditions needs update to accept balance
                safety_ok = await self.safety_monitor.check_safety_conditions()

                if not safety_ok:
                    self.logger.warning("safety_check_failed")

                    # If emergency shutdown is triggered, stop the bot
                    if self.safety_monitor.is_emergency_shutdown():
                        self.logger.critical("emergency_shutdown_detected_stopping_bot")
                        await self.stop()
                        break

            except asyncio.CancelledError:
                self.logger.info("safety_monitoring_loop_cancelled")
                break
            except Exception as e:
                self.logger.error("safety_monitoring_error", error=str(e), exc_info=True)

        self.logger.info("safety_monitoring_loop_stopped")


async def main():
    """Main entry point for the trading bot."""
    # Setup logging first
    setup_logger(log_level="INFO", log_file="logs/trading_bot.log", console_output=True)
    logger = get_logger(__name__)

    logger.info("starting_crypto_trading_bot")

    try:
        # Load config
        logger.info("loading_configuration")
        config = load_config("config.yaml", ".env")
        logger.info("configuration_loaded")

        # Create bot
        bot = TradingBot(config)

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()

        def signal_handler(sig):
            logger.info("shutdown_signal_received", signal=sig.name)
            asyncio.create_task(bot.stop())

        # Register signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

        # Start bot
        await bot.start()

        # Keep running until stopped
        logger.info("bot_running_press_ctrl_c_to_stop")
        while bot._running:
            await asyncio.sleep(1)

    except Exception as e:
        logger.critical("bot_startup_failed", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        if 'bot' in locals():
            await bot.stop()
        logger.info("bot_shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
