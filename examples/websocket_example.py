"""
Example usage of the Bybit WebSocket Manager.

This script demonstrates how to use the BybitWebSocketManager to subscribe
to real-time order book data for multiple symbols.

Run with: python examples/websocket_example.py
"""

import asyncio
from src.data_collection.bybit_websocket import BybitWebSocketManager
from src.storage.models import OrderBook
from src.utils.logger import setup_logger, get_logger


# Setup logging
setup_logger(log_level="INFO", console_output=True)
logger = get_logger(__name__)


def handle_orderbook_update(orderbook: OrderBook):
    """
    Callback function for order book updates.

    Args:
        orderbook: OrderBook object with latest data
    """
    mid_price = orderbook.get_mid_price()

    logger.info(
        "orderbook_update",
        symbol=orderbook.symbol,
        timestamp=orderbook.timestamp.isoformat(),
        bids_count=len(orderbook.bids),
        asks_count=len(orderbook.asks),
        mid_price=str(mid_price) if mid_price else None,
        best_bid=str(orderbook.bids[0].price) if orderbook.bids else None,
        best_ask=str(orderbook.asks[0].price) if orderbook.asks else None,
    )


async def main():
    """Main function to run the WebSocket manager example."""

    logger.info("starting_websocket_example")

    # Create WebSocket manager
    manager = BybitWebSocketManager(
        orderbook_callback=handle_orderbook_update,
        orderbook_depth=50,
        reconnect_delay_initial=1,
        reconnect_delay_max=30,
    )

    # Add symbols to track
    manager.add_symbol("BTCUSDT")
    manager.add_symbol("ETHUSDT")
    manager.add_symbol("SOLUSDT")

    # Start the WebSocket connection
    await manager.start()

    try:
        # Run for 60 seconds
        logger.info("running_for_60_seconds")
        await asyncio.sleep(60)

        # Demonstrate dynamic symbol management
        logger.info("removing_SOLUSDT")
        manager.remove_symbol("SOLUSDT")

        logger.info("adding_BNBUSDT")
        manager.add_symbol("BNBUSDT")

        # Run for another 30 seconds
        await asyncio.sleep(30)

    except KeyboardInterrupt:
        logger.info("keyboard_interrupt_received")
    finally:
        # Graceful shutdown
        logger.info("stopping_websocket_manager")
        await manager.stop()
        logger.info("websocket_example_finished")


if __name__ == "__main__":
    asyncio.run(main())
