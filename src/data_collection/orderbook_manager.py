"""
OrderBook Manager for the crypto trading bot.

This module manages order book data, including:
- Periodic snapshots to database (every 5 minutes by default)
- In-memory current order book for each symbol
- Density detection using 3-criteria analysis
- Cluster detection for grouped density levels
- Density lifecycle tracking (appearance/disappearance)
"""

import asyncio
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from src.storage.db_manager import DatabaseManager
from src.storage.models import CoinParameters, Density, OrderBook, OrderSide, PriceLevel
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderBookManager:
    """
    Manages order book data and density detection for trading symbols.

    Responsibilities:
    - Store periodic orderbook snapshots to database
    - Maintain in-memory current orderbook for each symbol
    - Detect significant order book densities using 3 criteria
    - Detect clusters of nearby densities
    - Track density lifecycle (appearance/disappearance)
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        snapshot_interval: int = 300,
    ):
        """
        Initialize the OrderBook Manager.

        Args:
            db_manager: Database manager for persistence
            snapshot_interval: Seconds between snapshots (default 300 = 5 minutes)
        """
        self.db_manager = db_manager
        self.snapshot_interval = snapshot_interval

        # In-memory storage
        self._current_orderbooks: dict[str, OrderBook] = {}
        self._tracked_densities: dict[str, list[Density]] = {}

        # Price and volume history for take-profit analysis
        self.price_history: dict[str, deque] = {}
        self.volume_history: dict[str, deque] = {}
        self.history_max_points = 60  # ~30 seconds at 2 updates/sec

        # Background tasks
        self._snapshot_task: Optional[asyncio.Task] = None
        self._running = False

        logger.info(
            "orderbook_manager_initialized",
            snapshot_interval=snapshot_interval,
        )

    async def start(self) -> None:
        """Start the periodic snapshot task."""
        if self._running:
            logger.warning("orderbook_manager_already_running")
            return

        self._running = True
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())
        logger.info("orderbook_manager_started")

    async def stop(self) -> None:
        """Stop the snapshot task."""
        if not self._running:
            return

        self._running = False

        if self._snapshot_task:
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass

        logger.info("orderbook_manager_stopped")

    async def update_orderbook(self, orderbook: OrderBook) -> None:
        """
        Update in-memory orderbook and detect new/disappeared densities.

        This is the main entry point for orderbook updates. It:
        1. Updates the in-memory orderbook
        2. Detects current densities
        3. Tracks density lifecycle changes

        Args:
            orderbook: New orderbook state
        """
        symbol = orderbook.symbol
        self._current_orderbooks[symbol] = orderbook

        # Get coin parameters for this symbol
        params = self.db_manager.coin_params_cache.get_sync(symbol)
        if not params:
            logger.warning(
                "no_coin_parameters",
                symbol=symbol,
                message="Cannot detect densities without parameters"
            )
            return

        # Detect current densities and track lifecycle
        try:
            await self._process_densities(orderbook, params)
        except Exception as e:
            logger.error(
                "density_detection_failed",
                symbol=symbol,
                error=str(e),
                exc_info=True
            )

        # Track price history
        mid_price = orderbook.get_mid_price()
        if mid_price:
            if symbol not in self.price_history:
                self.price_history[symbol] = deque(maxlen=self.history_max_points)

            self.price_history[symbol].append((datetime.now(), mid_price))

        # Track volume history
        if symbol not in self.volume_history:
            self.volume_history[symbol] = deque(maxlen=self.history_max_points)

        bid_volume = orderbook.get_total_volume(OrderSide.BID)
        ask_volume = orderbook.get_total_volume(OrderSide.ASK)
        self.volume_history[symbol].append((datetime.now(), bid_volume, ask_volume))

    async def _process_densities(
        self, orderbook: OrderBook, params: CoinParameters
    ) -> None:
        """
        Process density detection and tracking for an orderbook.

        Args:
            orderbook: Order book to process
            params: Coin parameters for density detection
        """
        symbol = orderbook.symbol

        # Detect densities
        densities = await self.detect_densities(orderbook, params)

        # Detect clusters
        densities = self.detect_clusters(densities, params.cluster_range_percent)

        # Track lifecycle changes
        await self._track_density_lifecycle(symbol, densities)

    async def detect_densities(
        self, orderbook: OrderBook, params: CoinParameters
    ) -> list[Density]:
        """
        Detect significant densities in the orderbook using 3 criteria.

        All three criteria must pass for a level to be considered a density:
        1. Absolute: volume >= threshold_abs
        2. Relative: volume >= avg_volume * threshold_relative
        3. Percentage: volume >= total_side_volume * threshold_percent / 100

        Args:
            orderbook: Order book to analyze
            params: Coin parameters with density thresholds

        Returns:
            List of detected densities (not yet marked as clusters)
        """
        densities: list[Density] = []

        # Process bid side
        if orderbook.bids:
            bid_densities = await self._detect_densities_for_side(
                symbol=orderbook.symbol,
                levels=orderbook.bids,
                side=OrderSide.BID,
                params=params,
            )
            densities.extend(bid_densities)

        # Process ask side
        if orderbook.asks:
            ask_densities = await self._detect_densities_for_side(
                symbol=orderbook.symbol,
                levels=orderbook.asks,
                side=OrderSide.ASK,
                params=params,
            )
            densities.extend(ask_densities)

        logger.debug(
            "densities_detected",
            symbol=orderbook.symbol,
            total_count=len(densities),
            bid_count=len([d for d in densities if d.side == OrderSide.BID]),
            ask_count=len([d for d in densities if d.side == OrderSide.ASK]),
        )

        return densities

    async def _detect_densities_for_side(
        self,
        symbol: str,
        levels: list[PriceLevel],
        side: OrderSide,
        params: CoinParameters,
    ) -> list[Density]:
        """
        Detect densities on one side of the orderbook.

        Args:
            symbol: Trading symbol
            levels: Price levels to analyze
            side: Bid or ask side
            params: Coin parameters

        Returns:
            List of densities found on this side
        """
        if not levels:
            return []

        densities: list[Density] = []

        # Calculate total volume and average volume
        total_volume = sum(level.volume for level in levels)
        avg_volume = total_volume / Decimal(len(levels))

        # Check each level against all 3 criteria
        for level in levels:
            volume = level.volume
            price = level.price

            # Criterion 1: Absolute threshold (in USDT value)
            # Convert volume to USDT by multiplying by price
            volume_usd = volume * price
            meets_absolute = volume_usd >= params.density_threshold_abs

            # Criterion 2: Relative threshold (vs average)
            meets_relative = volume >= (avg_volume * params.density_threshold_relative)

            # Criterion 3: Percentage threshold (of total side volume)
            meets_percentage = volume >= (
                total_volume * params.density_threshold_percent / Decimal("100")
            )

            # All 3 criteria must pass
            if meets_absolute and meets_relative and meets_percentage:
                # Calculate metrics
                volume_percent = (volume / total_volume * Decimal("100")) if total_volume > 0 else Decimal("0")
                relative_strength = (volume / avg_volume) if avg_volume > 0 else Decimal("0")

                density = Density(
                    symbol=symbol,
                    price_level=price,
                    volume=volume,
                    initial_volume=volume,  # This will be the initial volume for new densities
                    side=side,
                    volume_percent=volume_percent,
                    relative_strength=relative_strength,
                    is_cluster=False,  # Will be updated by cluster detection
                    appeared_at=datetime.now(),
                )
                densities.append(density)

        return densities

    def detect_clusters(
        self, densities: list[Density], cluster_range_percent: Decimal
    ) -> list[Density]:
        """
        Detect clusters of nearby density levels.

        A cluster is 3 or more density levels within cluster_range_percent of each other.
        Densities in clusters are marked with is_cluster=True.

        Args:
            densities: List of densities to analyze
            cluster_range_percent: Price range for clustering (e.g., 0.5 = 0.5%)

        Returns:
            Same list of densities with is_cluster flag updated
        """
        if len(densities) < 3:
            return densities

        # Group by side
        bid_densities = [d for d in densities if d.side == OrderSide.BID]
        ask_densities = [d for d in densities if d.side == OrderSide.ASK]

        # Process each side separately
        self._mark_clusters_for_side(bid_densities, cluster_range_percent)
        self._mark_clusters_for_side(ask_densities, cluster_range_percent)

        return densities

    def _mark_clusters_for_side(
        self, densities: list[Density], cluster_range_percent: Decimal
    ) -> None:
        """
        Mark clusters on one side of the orderbook.

        Args:
            densities: Densities from one side (modified in place)
            cluster_range_percent: Price range for clustering
        """
        if len(densities) < 3:
            return

        # Sort by price
        sorted_densities = sorted(densities, key=lambda d: d.price_level)

        # Find clusters using sliding window approach
        for i in range(len(sorted_densities)):
            # Look ahead to find all densities within range
            cluster_members = [sorted_densities[i]]

            for j in range(i + 1, len(sorted_densities)):
                price_diff_percent = abs(
                    (sorted_densities[j].price_level - sorted_densities[i].price_level)
                    / sorted_densities[i].price_level * Decimal("100")
                )

                if price_diff_percent <= cluster_range_percent:
                    cluster_members.append(sorted_densities[j])
                else:
                    break  # Prices are sorted, so no point checking further

            # If we found 3+ members, mark them as cluster
            if len(cluster_members) >= 3:
                for member in cluster_members:
                    member.is_cluster = True

    async def _track_density_lifecycle(
        self, symbol: str, current_densities: list[Density]
    ) -> None:
        """
        Track density lifecycle by comparing current with previously tracked densities.

        This method:
        1. Identifies disappeared densities (in tracked but not in current)
        2. Updates database with disappeared_at timestamp
        3. Identifies new densities (in current but not in tracked)
        4. Saves new densities to database
        5. Updates tracked densities

        Args:
            symbol: Trading symbol
            current_densities: Currently detected densities
        """
        # Get previously tracked densities for this symbol
        previous_densities = self._tracked_densities.get(symbol, [])

        # Create lookup keys for efficient comparison
        # Key format: "price_side" (e.g., "50000.00_bid")
        def density_key(d: Density) -> str:
            return f"{d.price_level}_{d.side.value}"

        current_keys = {density_key(d): d for d in current_densities}
        previous_keys = {density_key(d): d for d in previous_densities}

        # Find disappeared densities
        disappeared_keys = set(previous_keys.keys()) - set(current_keys.keys())
        for key in disappeared_keys:
            disappeared_density = previous_keys[key]
            disappeared_at = datetime.now()

            # Update database
            try:
                await self.db_manager.update_density_disappeared(
                    symbol=symbol,
                    price_level=disappeared_density.price_level,
                    side=disappeared_density.side.value,
                    disappeared_at=disappeared_at,
                )
                logger.info(
                    "density_disappeared",
                    symbol=symbol,
                    price_level=float(disappeared_density.price_level),
                    side=disappeared_density.side.value,
                )
            except Exception as e:
                logger.error(
                    "failed_to_mark_density_disappeared",
                    symbol=symbol,
                    price_level=float(disappeared_density.price_level),
                    error=str(e),
                )

        # Find new densities
        new_keys = set(current_keys.keys()) - set(previous_keys.keys())
        for key in new_keys:
            new_density = current_keys[key]

            # Save to database
            try:
                await self.db_manager.save_density(new_density)
                logger.info(
                    "new_density_detected",
                    symbol=symbol,
                    price_level=float(new_density.price_level),
                    side=new_density.side.value,
                    volume=float(new_density.volume),
                    volume_percent=float(new_density.volume_percent) if new_density.volume_percent else None,
                    relative_strength=float(new_density.relative_strength) if new_density.relative_strength else None,
                    is_cluster=new_density.is_cluster,
                )
            except Exception as e:
                logger.error(
                    "failed_to_save_density",
                    symbol=symbol,
                    price_level=float(new_density.price_level),
                    error=str(e),
                )

        # Update densities that still exist (to track volume changes)
        # For densities that appear in both lists, update the volume
        for key in set(current_keys.keys()) & set(previous_keys.keys()):
            current = current_keys[key]
            previous = previous_keys[key]

            # Keep the initial_volume from the previous tracking
            current.initial_volume = previous.initial_volume
            current.appeared_at = previous.appeared_at

        # Update tracked densities
        self._tracked_densities[symbol] = current_densities

    async def _snapshot_loop(self) -> None:
        """
        Background task that periodically saves orderbook snapshots.

        Runs every snapshot_interval seconds and saves all current orderbooks.
        """
        logger.info("snapshot_loop_started", interval_seconds=self.snapshot_interval)

        while self._running:
            try:
                await asyncio.sleep(self.snapshot_interval)

                if not self._running:
                    break

                # Save all current orderbooks
                snapshot_count = 0
                for symbol, orderbook in self._current_orderbooks.items():
                    try:
                        await self.db_manager.save_orderbook_snapshot(orderbook)
                        snapshot_count += 1
                    except Exception as e:
                        logger.error(
                            "snapshot_save_failed",
                            symbol=symbol,
                            error=str(e),
                        )

                logger.info(
                    "snapshots_saved",
                    count=snapshot_count,
                    symbols=list(self._current_orderbooks.keys()),
                )

            except asyncio.CancelledError:
                logger.info("snapshot_loop_cancelled")
                break
            except Exception as e:
                logger.error(
                    "snapshot_loop_error",
                    error=str(e),
                    exc_info=True,
                )

    def get_current_orderbook(self, symbol: str) -> Optional[OrderBook]:
        """
        Get the current in-memory orderbook for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current orderbook or None if not available
        """
        return self._current_orderbooks.get(symbol)

    def get_current_densities(self, symbol: str) -> list[Density]:
        """
        Get currently tracked densities for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List of current densities (empty list if none)
        """
        return self._tracked_densities.get(symbol, [])

    def get_price_history(
        self, symbol: str, seconds: int
    ) -> list[tuple[datetime, Decimal]]:
        """
        Get price history for a symbol.

        Args:
            symbol: Trading symbol
            seconds: Number of seconds of history to return

        Returns:
            List of (timestamp, price) tuples
        """
        if symbol not in self.price_history:
            return []

        cutoff_time = datetime.now() - timedelta(seconds=seconds)

        history = [
            (ts, price)
            for ts, price in self.price_history[symbol]
            if ts >= cutoff_time
        ]

        return history

    def get_volume_history(
        self, symbol: str, seconds: int
    ) -> list[tuple[datetime, Decimal, Decimal]]:
        """
        Get volume history for a symbol.

        Args:
            symbol: Trading symbol
            seconds: Number of seconds of history to return

        Returns:
            List of (timestamp, bid_volume, ask_volume) tuples
        """
        if symbol not in self.volume_history:
            return []

        cutoff_time = datetime.now() - timedelta(seconds=seconds)

        history = [
            (ts, bid_vol, ask_vol)
            for ts, bid_vol, ask_vol in self.volume_history[symbol]
            if ts >= cutoff_time
        ]

        return history
