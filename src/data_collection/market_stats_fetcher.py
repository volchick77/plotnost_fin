"""
Market statistics fetcher for the trading bot.

This module fetches 24h market statistics from Bybit API and maintains
a list of top-40 most active symbols (top-20 gainers and top-20 losers).
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional

import aiohttp

from src.storage.db_manager import DatabaseManager
from src.storage.models import MarketStats
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MarketStatsFetcher:
    """
    Fetches and maintains market statistics for trading pairs.

    Periodically fetches 24h statistics from Bybit API and updates database
    with top-20 gainers and top-20 losers by volume.
    """

    BYBIT_API_URL = "https://api.bybit.com/v5/market/tickers"

    def __init__(
        self,
        db_manager: DatabaseManager,
        update_interval: int = 300,
        top_count: int = 20,
        min_volume_24h: float = 1_000_000,
    ):
        """
        Initialize market stats fetcher.

        Args:
            db_manager: Database manager instance
            update_interval: Update interval in seconds (default: 300 = 5 minutes)
            top_count: Number of gainers/losers to select (default: 20)
            min_volume_24h: Minimum 24h volume in USDT (default: 1,000,000)
        """
        self.db_manager = db_manager
        self.update_interval = update_interval
        self.top_count = top_count
        self.min_volume_24h = Decimal(str(min_volume_24h))

        self._update_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False

        logger.info(
            "market_stats_fetcher_initialized",
            update_interval=update_interval,
            top_count=top_count,
            min_volume_24h=min_volume_24h,
        )

    async def start(self) -> None:
        """Start the periodic market stats update task."""
        if self._running:
            logger.warning("market_stats_fetcher_already_running")
            return

        self._running = True

        # Create HTTP session
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(limit=10),
        )

        # Block until initial data is loaded
        logger.info("fetching_initial_market_stats")
        await self.update_market_stats()
        logger.info("initial_market_stats_loaded")

        # Then start background loop for periodic updates
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("market_stats_fetcher_started")

    async def stop(self) -> None:
        """Stop the update task and cleanup resources."""
        if not self._running:
            return

        self._running = False

        # Cancel update task
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        # Close HTTP session
        if self._session:
            await self._session.close()
            self._session = None

        logger.info("market_stats_fetcher_stopped")

    async def fetch_24h_stats(self) -> list[MarketStats]:
        """
        Fetch 24h market statistics from Bybit API.

        Returns:
            List of MarketStats objects for USDT perpetual futures

        Raises:
            Exception: If all retry attempts fail
        """
        if not self._session:
            raise RuntimeError("Session not initialized. Call start() first.")

        url = f"{self.BYBIT_API_URL}?category=linear"
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                async with self._session.get(url) as response:
                    # Handle rate limiting
                    if response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        logger.warning(
                            "api_rate_limited",
                            retry_after=retry_after,
                            attempt=attempt + 1,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    data = await response.json()

                    # Parse response
                    if data.get("retCode") != 0:
                        error_msg = data.get("retMsg", "Unknown error")
                        logger.error(
                            "api_error_response",
                            ret_code=data.get("retCode"),
                            message=error_msg,
                        )
                        raise Exception(f"API error: {error_msg}")

                    result = data.get("result", {})
                    ticker_list = result.get("list", [])

                    # Parse and filter tickers
                    stats_list = []
                    now = datetime.now()

                    for ticker in ticker_list:
                        symbol = ticker.get("symbol", "")

                        # Filter: only USDT perpetual futures
                        if not symbol.endswith("USDT"):
                            continue

                        # Skip options and other derivatives (they have dates or strikes in symbol)
                        # Perpetual symbols are like: BTCUSDT, ETHUSDT (no date suffix)
                        # Options would be like: BTC-31DEC23-40000-C
                        if '-' in symbol or any(char.isdigit() for char in symbol[:-4]):
                            # Skip if it has dates, dashes, or digits in non-USDT part
                            continue

                        try:
                            volume_24h = Decimal(ticker.get("volume24h", "0"))
                            price_change_24h_percent = Decimal(
                                ticker.get("price24hPcnt", "0")
                            ) * Decimal("100")  # Convert to percentage
                            current_price = Decimal(ticker.get("lastPrice", "0"))

                            # Apply minimum volume filter
                            if volume_24h < self.min_volume_24h:
                                continue

                            stats = MarketStats(
                                symbol=symbol,
                                volume_24h=volume_24h,
                                price_change_24h_percent=price_change_24h_percent,
                                current_price=current_price,
                                is_active=False,  # Will be set during selection
                                rank=None,  # Will be set during selection
                                updated_at=now,
                            )
                            stats_list.append(stats)

                        except (ValueError, TypeError) as e:
                            logger.warning(
                                "failed_to_parse_ticker",
                                symbol=symbol,
                                error=str(e),
                            )
                            continue

                    logger.info(
                        "fetched_market_stats",
                        total_tickers=len(ticker_list),
                        filtered_stats=len(stats_list),
                    )

                    return stats_list

            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(
                    "api_request_failed",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    error=str(e),
                )

                if attempt < max_retries - 1:
                    # Exponential backoff
                    backoff_time = 2 ** attempt
                    await asyncio.sleep(backoff_time)
                    continue

            except Exception as e:
                logger.error("unexpected_error_fetching_stats", error=str(e))
                raise

        # All retries failed
        raise Exception(f"Failed to fetch market stats after {max_retries} attempts: {last_error}")

    async def update_market_stats(self) -> None:
        """
        Update market statistics in database.

        1. Fetches 24h stats from Bybit
        2. Selects top-20 gainers and top-20 losers by volume
        3. Marks selected symbols as is_active=True, others as is_active=False
        4. Updates database with all stats
        """
        try:
            logger.info("updating_market_stats")

            # Fetch fresh data from API
            all_stats = await self.fetch_24h_stats()

            if not all_stats:
                logger.warning("no_market_stats_fetched")
                return

            # Separate gainers and losers
            gainers = [
                stats for stats in all_stats
                if stats.price_change_24h_percent > Decimal("0")
            ]
            losers = [
                stats for stats in all_stats
                if stats.price_change_24h_percent < Decimal("0")
            ]

            # Sort by volume (descending)
            gainers.sort(key=lambda x: x.volume_24h, reverse=True)
            losers.sort(key=lambda x: x.volume_24h, reverse=True)

            # Select top N gainers and losers
            selected_gainers = gainers[: self.top_count]
            selected_losers = losers[: self.top_count]

            # Mark selected symbols as active and assign ranks
            active_symbols = set()
            rank = 1

            for stats in selected_gainers:
                stats.is_active = True
                stats.rank = rank
                active_symbols.add(stats.symbol)
                rank += 1

            for stats in selected_losers:
                stats.is_active = True
                stats.rank = rank
                active_symbols.add(stats.symbol)
                rank += 1

            # Mark non-selected symbols as inactive
            for stats in all_stats:
                if stats.symbol not in active_symbols:
                    stats.is_active = False
                    stats.rank = None

            # Update database for all symbols
            for stats in all_stats:
                await self.db_manager.upsert_market_stats(stats)

            logger.info(
                "market_stats_updated",
                total_symbols=len(all_stats),
                active_symbols=len(active_symbols),
                gainers=len(selected_gainers),
                losers=len(selected_losers),
            )

        except Exception as e:
            logger.error("failed_to_update_market_stats", error=str(e))
            # Don't re-raise - we want the loop to continue

    async def _update_loop(self) -> None:
        """
        Background task that periodically updates market statistics.

        Runs update_market_stats() every update_interval seconds.
        """
        logger.info("market_stats_update_loop_started", interval=self.update_interval)

        # Run initial update immediately
        try:
            await self.update_market_stats()
        except Exception as e:
            logger.error("initial_update_failed", error=str(e))

        # Continue with periodic updates
        while self._running:
            try:
                await asyncio.sleep(self.update_interval)
                if self._running:  # Check again after sleep
                    await self.update_market_stats()

            except asyncio.CancelledError:
                logger.info("market_stats_update_loop_cancelled")
                break

            except Exception as e:
                logger.error("update_loop_error", error=str(e))
                # Continue running despite errors

        logger.info("market_stats_update_loop_stopped")
