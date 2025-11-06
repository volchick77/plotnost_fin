"""
Bybit WebSocket Manager for real-time order book data collection.

This module implements the WebSocket connection to Bybit's public API,
handling order book subscriptions, automatic reconnection, and data parsing.
"""

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Awaitable, Callable, Optional, Set
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed, WebSocketException

from src.storage.models import OrderBook, PriceLevel
from src.utils.logger import get_logger


logger = get_logger(__name__)


class BybitWebSocketManager:
    """
    Manages WebSocket connection to Bybit for real-time order book data.

    Features:
    - Automatic reconnection with exponential backoff
    - Dynamic symbol subscription management
    - Heartbeat/ping-pong to keep connection alive
    - Order book data parsing and validation
    - Graceful shutdown handling

    Example:
        >>> def handle_orderbook(orderbook: OrderBook):
        ...     print(f"Received {orderbook.symbol} orderbook")
        >>>
        >>> manager = BybitWebSocketManager(orderbook_callback=handle_orderbook)
        >>> manager.add_symbol("BTCUSDT")
        >>> manager.add_symbol("ETHUSDT")
        >>> await manager.start()
    """

    def __init__(
        self,
        orderbook_callback: Callable[[OrderBook], Awaitable[None]],
        orderbook_depth: int = 50,
        reconnect_delay_initial: int = 1,
        reconnect_delay_max: int = 30,
        ping_interval: int = 20,
    ):
        """
        Initialize the Bybit WebSocket Manager.

        Args:
            orderbook_callback: Async function to call when orderbook update is received
            orderbook_depth: Order book depth level (default: 50)
            reconnect_delay_initial: Initial reconnection delay in seconds (default: 1)
            reconnect_delay_max: Maximum reconnection delay in seconds (default: 30)
            ping_interval: Interval for sending ping messages in seconds (default: 20)
        """
        self.url = "wss://stream.bybit.com/v5/public/linear"
        self._symbols: Set[str] = set()
        self._orderbook_callback = orderbook_callback
        self._orderbook_depth = orderbook_depth
        self._reconnect_delay_initial = reconnect_delay_initial
        self._reconnect_delay_max = reconnect_delay_max
        self._ping_interval = ping_interval

        # State management
        self._running = False
        self._ws: Optional[WebSocketClientProtocol] = None
        self._connection_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

        logger.info(
            "websocket_manager_initialized",
            url=self.url,
            orderbook_depth=orderbook_depth,
            reconnect_delay_initial=reconnect_delay_initial,
            reconnect_delay_max=reconnect_delay_max,
        )

    async def start(self) -> None:
        """
        Start the WebSocket connection and begin listening for messages.

        This method starts the connection loop as a background task.
        """
        if self._running:
            logger.warning("websocket_already_running")
            return

        self._running = True
        self._connection_task = asyncio.create_task(self._connection_loop())
        logger.info("websocket_manager_started")

    async def stop(self) -> None:
        """
        Gracefully stop the WebSocket connection.

        Closes the connection, cancels background tasks, and cleans up resources.
        """
        if not self._running:
            logger.warning("websocket_not_running")
            return

        logger.info("websocket_manager_stopping")
        self._running = False

        # Cancel ping task
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket connection
        if self._ws and not self._ws.closed:
            await self._ws.close()

        # Cancel connection task
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass

        logger.info("websocket_manager_stopped")

    def add_symbol(self, symbol: str) -> None:
        """
        Add a symbol to track.

        If already connected, immediately subscribes to the symbol.
        Otherwise, will subscribe when connection is established.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
        """
        if symbol in self._symbols:
            logger.debug("symbol_already_tracked", symbol=symbol)
            return

        self._symbols.add(symbol)
        logger.info("symbol_added", symbol=symbol, total_symbols=len(self._symbols))

        # If already connected, subscribe immediately
        if self._ws and not self._ws.closed:
            asyncio.create_task(self._subscribe_symbol(self._ws, symbol))

    def remove_symbol(self, symbol: str) -> None:
        """
        Remove a symbol from tracking.

        If connected, unsubscribes from the symbol.

        Args:
            symbol: Trading symbol to remove
        """
        if symbol not in self._symbols:
            logger.debug("symbol_not_tracked", symbol=symbol)
            return

        self._symbols.remove(symbol)
        logger.info("symbol_removed", symbol=symbol, total_symbols=len(self._symbols))

        # If connected, unsubscribe
        if self._ws and not self._ws.closed:
            asyncio.create_task(self._unsubscribe_symbol(self._ws, symbol))

    async def _connection_loop(self) -> None:
        """
        Main connection loop with automatic reconnection.

        Implements exponential backoff retry logic:
        - Starts with initial delay (default: 1s)
        - Doubles delay on each failure (2s, 4s, 8s, ...)
        - Caps at maximum delay (default: 30s)
        - Resets to initial delay on successful connection
        """
        delay = self._reconnect_delay_initial

        while self._running:
            try:
                await self._connect_and_listen()
                # Reset delay on successful connection
                delay = self._reconnect_delay_initial
            except asyncio.CancelledError:
                logger.info("connection_loop_cancelled")
                break
            except Exception as e:
                logger.error(
                    "websocket_connection_error",
                    error=str(e),
                    error_type=type(e).__name__,
                    retry_delay=delay,
                )

                if self._running:
                    await asyncio.sleep(delay)
                    # Exponential backoff
                    delay = min(delay * 2, self._reconnect_delay_max)

    async def _connect_and_listen(self) -> None:
        """
        Connect to WebSocket and listen for messages.

        Subscribes to all tracked symbols and processes incoming messages
        until the connection is closed or an error occurs.
        """
        logger.info("connecting_to_websocket", url=self.url)

        async with websockets.connect(self.url) as ws:
            self._ws = ws
            logger.info("websocket_connected")

            # Subscribe to all symbols
            if self._symbols:
                await self._subscribe_all(ws)

            # Start ping task to keep connection alive
            self._ping_task = asyncio.create_task(self._ping_loop(ws))

            # Listen for messages
            try:
                async for message in ws:
                    await self._handle_message(message)
            except ConnectionClosed as e:
                logger.warning(
                    "websocket_connection_closed",
                    code=e.code,
                    reason=e.reason,
                )
            except WebSocketException as e:
                logger.error("websocket_exception", error=str(e))
            finally:
                # Cancel ping task when connection ends
                if self._ping_task and not self._ping_task.done():
                    self._ping_task.cancel()
                    try:
                        await self._ping_task
                    except asyncio.CancelledError:
                        pass

    async def _ping_loop(self, ws: WebSocketClientProtocol) -> None:
        """
        Send periodic ping messages to keep connection alive.

        Bybit requires heartbeat messages every 20 seconds to prevent timeout.

        Args:
            ws: WebSocket connection
        """
        try:
            while not ws.closed:
                await asyncio.sleep(self._ping_interval)
                if not ws.closed:
                    ping_msg = json.dumps({"op": "ping"})
                    await ws.send(ping_msg)
                    logger.debug("ping_sent")
        except asyncio.CancelledError:
            logger.debug("ping_loop_cancelled")
        except Exception as e:
            logger.error("ping_loop_error", error=str(e))

    async def _subscribe_all(self, ws: WebSocketClientProtocol) -> None:
        """
        Subscribe to order book updates for all tracked symbols.

        Args:
            ws: WebSocket connection
        """
        if not self._symbols:
            logger.debug("no_symbols_to_subscribe")
            return

        # Build subscription arguments
        args = [f"orderbook.{self._orderbook_depth}.{symbol}" for symbol in self._symbols]

        subscribe_msg = {
            "op": "subscribe",
            "args": args,
        }

        await ws.send(json.dumps(subscribe_msg))
        logger.info(
            "subscribed_to_symbols",
            symbols=list(self._symbols),
            depth=self._orderbook_depth,
        )

    async def _subscribe_symbol(self, ws: WebSocketClientProtocol, symbol: str) -> None:
        """
        Subscribe to order book updates for a single symbol.

        Args:
            ws: WebSocket connection
            symbol: Trading symbol to subscribe to
        """
        subscribe_msg = {
            "op": "subscribe",
            "args": [f"orderbook.{self._orderbook_depth}.{symbol}"],
        }

        await ws.send(json.dumps(subscribe_msg))
        logger.info("subscribed_to_symbol", symbol=symbol, depth=self._orderbook_depth)

    async def _unsubscribe_symbol(self, ws: WebSocketClientProtocol, symbol: str) -> None:
        """
        Unsubscribe from order book updates for a single symbol.

        Args:
            ws: WebSocket connection
            symbol: Trading symbol to unsubscribe from
        """
        unsubscribe_msg = {
            "op": "unsubscribe",
            "args": [f"orderbook.{self._orderbook_depth}.{symbol}"],
        }

        await ws.send(json.dumps(unsubscribe_msg))
        logger.info("unsubscribed_from_symbol", symbol=symbol)

    async def _handle_message(self, message: str) -> None:
        """
        Parse and handle incoming WebSocket messages.

        Processes different message types:
        - Subscription confirmations
        - Pong responses
        - Order book snapshots/updates

        Args:
            message: Raw WebSocket message (JSON string)
        """
        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if data.get("op") == "subscribe":
                if data.get("success"):
                    logger.debug("subscription_confirmed", args=data.get("args"))
                else:
                    logger.warning("subscription_failed", data=data)
                return

            # Handle pong response
            if data.get("op") == "pong":
                logger.debug("pong_received")
                return

            # Handle order book data
            topic = data.get("topic", "")
            if topic.startswith("orderbook"):
                try:
                    orderbook = self._parse_orderbook(data)
                    # Call the async callback with parsed orderbook
                    await self._orderbook_callback(orderbook)
                    logger.debug(
                        "orderbook_processed",
                        symbol=orderbook.symbol,
                        bids_count=len(orderbook.bids),
                        asks_count=len(orderbook.asks),
                    )
                except Exception as e:
                    logger.error(
                        "orderbook_parsing_error",
                        error=str(e),
                        topic=topic,
                    )
                return

            # Unknown message type
            logger.debug("unknown_message_type", data=data)

        except json.JSONDecodeError as e:
            logger.error("json_decode_error", error=str(e), message=message[:100])
        except Exception as e:
            logger.error(
                "message_handling_error",
                error=str(e),
                error_type=type(e).__name__,
            )

    def _parse_orderbook(self, data: dict) -> OrderBook:
        """
        Parse Bybit order book data into OrderBook model.

        Bybit format:
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot" or "delta",
            "ts": 1672304486868,
            "data": {
                "s": "BTCUSDT",
                "b": [["50000.00", "1.5"], ["49999.00", "2.3"]],
                "a": [["50001.00", "1.2"], ["50002.00", "3.1"]],
                "u": 123456,
                "seq": 789
            }
        }

        Args:
            data: Raw WebSocket message data

        Returns:
            Parsed OrderBook object

        Raises:
            ValueError: If data format is invalid
            KeyError: If required fields are missing
        """
        orderbook_data = data.get("data", {})

        # Extract symbol
        symbol = orderbook_data.get("s")
        if not symbol:
            raise ValueError("Missing symbol in orderbook data")

        # Parse timestamp (milliseconds to datetime)
        ts = data.get("ts", 0)
        if ts:
            timestamp = datetime.fromtimestamp(ts / 1000.0)
        else:
            timestamp = datetime.now()

        # Parse bids
        bids_raw = orderbook_data.get("b", [])
        bids = []
        for price_str, volume_str in bids_raw:
            try:
                price = Decimal(price_str)
                volume = Decimal(volume_str)
                bids.append(PriceLevel(price=price, volume=volume))
            except (ValueError, TypeError) as e:
                logger.warning(
                    "invalid_bid_level",
                    symbol=symbol,
                    price=price_str,
                    volume=volume_str,
                    error=str(e),
                )

        # Parse asks
        asks_raw = orderbook_data.get("a", [])
        asks = []
        for price_str, volume_str in asks_raw:
            try:
                price = Decimal(price_str)
                volume = Decimal(volume_str)
                asks.append(PriceLevel(price=price, volume=volume))
            except (ValueError, TypeError) as e:
                logger.warning(
                    "invalid_ask_level",
                    symbol=symbol,
                    price=price_str,
                    volume=volume_str,
                    error=str(e),
                )

        # Create OrderBook object
        orderbook = OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=timestamp,
        )

        return orderbook
