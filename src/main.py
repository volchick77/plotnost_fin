"""
Main entry point for the crypto trading bot.

This module provides the TradingBot orchestrator that coordinates all components
and manages the trading lifecycle.
"""

import asyncio
import signal
import sys
from decimal import Decimal
from typing import Optional

from src.config import load_config, Config
from src.storage.db_manager import DatabaseManager
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
                min_balance_usdt=Decimal("10"),
                max_total_exposure_percent=Decimal(str(self.config.trading.max_exposure_percent)),
                max_position_exposure_percent=Decimal(str(self.config.trading.max_exposure_percent)),
            )

            # 6. Subscribe to active symbols
            self.logger.info("subscribing_to_active_symbols")
            await self._subscribe_to_symbols()

            # 7. Start WebSocket
            self.logger.info("starting_websocket")
            await self.websocket_manager.start()

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
                            # Validate signal
                            # TODO: Get real balance from exchange API
                            balance = Decimal("100")
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
                await asyncio.sleep(5)  # Check every 5 seconds

                positions_to_close = await self.position_monitor.check_positions()

                for position in positions_to_close:
                    try:
                        # TODO: Close position via order_executor
                        # await self.order_executor.close_position(position)

                        await self.position_monitor.stop_monitoring(position.symbol)
                        self.logger.info(
                            "position_closed",
                            symbol=position.symbol,
                            position_id=str(position.id),
                            reason=position.exit_reason,
                            pnl=float(position.realized_pnl) if position.realized_pnl else None,
                        )

                    except Exception as e:
                        self.logger.error(
                            "position_close_error",
                            symbol=position.symbol,
                            position_id=str(position.id),
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
