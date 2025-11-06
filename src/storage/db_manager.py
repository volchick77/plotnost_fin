"""
Database manager for the trading bot using asyncpg.

This module provides:
- DatabaseManager: Main async database connection and operations
- CoinParametersCache: In-memory cache for coin parameters
- CRUD operations for all database tables
- Connection pooling and error handling
"""

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import asyncpg
from asyncpg import Pool, Record

from src.utils.logger import get_logger

from .models import (
    CoinParameters,
    Density,
    ExitReason,
    MarketStats,
    OrderBook,
    OrderSide,
    Position,
    PositionStatus,
    SystemEvent,
    Trade,
)


class CoinParametersCache:
    """In-memory cache for coin parameters with periodic refresh."""

    def __init__(self, db_manager: "DatabaseManager", refresh_interval: int = 300):
        """
        Initialize cache.

        Args:
            db_manager: Database manager instance
            refresh_interval: How often to refresh cache (seconds)
        """
        self.db_manager = db_manager
        self.refresh_interval = refresh_interval
        self._cache: dict[str, CoinParameters] = {}
        self._lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the cache refresh task."""
        await self.refresh()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Stop the cache refresh task."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    async def _refresh_loop(self) -> None:
        """Background task to refresh cache periodically."""
        while True:
            try:
                await asyncio.sleep(self.refresh_interval)
                await self.refresh()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log error but don't crash the loop
                print(f"Error refreshing coin parameters cache: {e}")

    async def refresh(self) -> None:
        """Refresh all parameters from database."""
        async with self._lock:
            params_list = await self.db_manager.get_all_coin_parameters()
            self._cache = {params.symbol: params for params in params_list}

    async def get(self, symbol: str) -> Optional[CoinParameters]:
        """
        Get parameters for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            CoinParameters if found, None otherwise
        """
        async with self._lock:
            return self._cache.get(symbol)

    async def set(self, params: CoinParameters) -> None:
        """
        Update parameters for a symbol (also updates DB).

        Args:
            params: New coin parameters
        """
        async with self._lock:
            await self.db_manager.upsert_coin_parameters(params)
            self._cache[params.symbol] = params

    def get_sync(self, symbol: str) -> Optional[CoinParameters]:
        """
        Synchronously get parameters (no lock - use carefully).

        Args:
            symbol: Trading symbol

        Returns:
            CoinParameters if found, None otherwise
        """
        return self._cache.get(symbol)


class DatabaseManager:
    """
    Async database manager using asyncpg.

    Provides connection pooling and CRUD operations for all tables.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "trading_bot",
        user: str = "trading_bot",
        password: str = "",
        pool_size: int = 10,
    ):
        """
        Initialize database manager.

        Args:
            host: Database host
            port: Database port
            database: Database name
            user: Database user
            password: Database password
            pool_size: Connection pool size
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.pool_size = pool_size
        self.pool: Optional[Pool] = None
        self.coin_params_cache: Optional[CoinParametersCache] = None
        self.logger = get_logger(__name__)

    async def connect(self) -> None:
        """Establish connection pool to database."""
        if self.pool is not None:
            return

        self.pool = await asyncpg.create_pool(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            min_size=2,
            max_size=self.pool_size,
            command_timeout=60,
        )

        # Initialize and start coin parameters cache
        self.coin_params_cache = CoinParametersCache(self, refresh_interval=300)
        await self.coin_params_cache.start()

    async def disconnect(self) -> None:
        """Close connection pool."""
        if self.coin_params_cache:
            await self.coin_params_cache.stop()

        if self.pool:
            await self.pool.close()
            self.pool = None

    async def execute(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        retry_count: int = 3,
    ) -> str:
        """
        Execute a query with retry logic.

        Args:
            query: SQL query
            *args: Query parameters
            timeout: Query timeout in seconds
            retry_count: Number of retries on failure

        Returns:
            Status string from database

        Raises:
            Exception: If all retries fail
        """
        if not self.pool:
            raise RuntimeError("Database not connected")

        last_error = None
        for attempt in range(retry_count):
            try:
                async with self.pool.acquire() as conn:
                    return await conn.execute(query, *args, timeout=timeout)
            except Exception as e:
                last_error = e
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

        raise last_error  # type: ignore

    async def fetch(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        retry_count: int = 3,
    ) -> list[Record]:
        """
        Fetch multiple rows with retry logic.

        Args:
            query: SQL query
            *args: Query parameters
            timeout: Query timeout in seconds
            retry_count: Number of retries on failure

        Returns:
            List of database records

        Raises:
            Exception: If all retries fail
        """
        if not self.pool:
            raise RuntimeError("Database not connected")

        last_error = None
        for attempt in range(retry_count):
            try:
                async with self.pool.acquire() as conn:
                    return await conn.fetch(query, *args, timeout=timeout)
            except Exception as e:
                last_error = e
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

        raise last_error  # type: ignore

    async def fetchrow(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        retry_count: int = 3,
    ) -> Optional[Record]:
        """
        Fetch a single row with retry logic.

        Args:
            query: SQL query
            *args: Query parameters
            timeout: Query timeout in seconds
            retry_count: Number of retries on failure

        Returns:
            Database record or None

        Raises:
            Exception: If all retries fail
        """
        if not self.pool:
            raise RuntimeError("Database not connected")

        last_error = None
        for attempt in range(retry_count):
            try:
                async with self.pool.acquire() as conn:
                    return await conn.fetchrow(query, *args, timeout=timeout)
            except Exception as e:
                last_error = e
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

        raise last_error  # type: ignore

    async def fetchval(
        self,
        query: str,
        *args: Any,
        column: int = 0,
        timeout: Optional[float] = None,
        retry_count: int = 3,
    ) -> Any:
        """
        Fetch a single value with retry logic.

        Args:
            query: SQL query
            *args: Query parameters
            column: Column index to return
            timeout: Query timeout in seconds
            retry_count: Number of retries on failure

        Returns:
            Single value from database

        Raises:
            Exception: If all retries fail
        """
        if not self.pool:
            raise RuntimeError("Database not connected")

        last_error = None
        for attempt in range(retry_count):
            try:
                async with self.pool.acquire() as conn:
                    return await conn.fetchval(query, *args, column=column, timeout=timeout)
            except Exception as e:
                last_error = e
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

        raise last_error  # type: ignore

    # ==================== Coin Parameters ====================

    async def get_coin_parameters(self, symbol: str) -> Optional[CoinParameters]:
        """
        Get parameters for a specific symbol.

        Args:
            symbol: Trading symbol

        Returns:
            CoinParameters if found, None otherwise
        """
        row = await self.fetchrow(
            "SELECT * FROM coin_parameters WHERE symbol = $1",
            symbol,
        )

        if not row:
            return None

        return CoinParameters(
            symbol=row["symbol"],
            density_threshold_abs=row["density_threshold_abs"],
            density_threshold_relative=row["density_threshold_relative"],
            density_threshold_percent=row["density_threshold_percent"],
            cluster_range_percent=row["cluster_range_percent"],
            breakout_erosion_percent=row["breakout_erosion_percent"],
            breakout_min_stop_loss_percent=row["breakout_min_stop_loss_percent"],
            breakout_breakeven_profit_percent=row["breakout_breakeven_profit_percent"],
            bounce_touch_tolerance_percent=row["bounce_touch_tolerance_percent"],
            bounce_density_stable_percent=row["bounce_density_stable_percent"],
            bounce_stop_loss_behind_density_percent=row["bounce_stop_loss_behind_density_percent"],
            bounce_density_erosion_exit_percent=row["bounce_density_erosion_exit_percent"],
            tp_slowdown_multiplier=row["tp_slowdown_multiplier"],
            tp_local_extrema_hours=row["tp_local_extrema_hours"],
            preferred_strategy=row["preferred_strategy"],
            enabled=row["enabled"],
            updated_at=row["updated_at"],
            notes=row.get("notes"),
        )

    async def get_all_coin_parameters(self) -> list[CoinParameters]:
        """
        Get parameters for all symbols.

        Returns:
            List of all coin parameters
        """
        rows = await self.fetch("SELECT * FROM coin_parameters ORDER BY symbol")
        return [
            CoinParameters(
                symbol=row["symbol"],
                density_threshold_abs=row["density_threshold_abs"],
                density_threshold_relative=row["density_threshold_relative"],
                density_threshold_percent=row["density_threshold_percent"],
                cluster_range_percent=row["cluster_range_percent"],
                breakout_erosion_percent=row["breakout_erosion_percent"],
                breakout_min_stop_loss_percent=row["breakout_min_stop_loss_percent"],
                breakout_breakeven_profit_percent=row["breakout_breakeven_profit_percent"],
                bounce_touch_tolerance_percent=row["bounce_touch_tolerance_percent"],
                bounce_density_stable_percent=row["bounce_density_stable_percent"],
                bounce_stop_loss_behind_density_percent=row["bounce_stop_loss_behind_density_percent"],
                bounce_density_erosion_exit_percent=row["bounce_density_erosion_exit_percent"],
                tp_slowdown_multiplier=row["tp_slowdown_multiplier"],
                tp_local_extrema_hours=row["tp_local_extrema_hours"],
                preferred_strategy=row["preferred_strategy"],
                enabled=row["enabled"],
                updated_at=row["updated_at"],
                notes=row.get("notes"),
            )
            for row in rows
        ]

    async def upsert_coin_parameters(self, params: CoinParameters) -> None:
        """
        Insert or update coin parameters.

        Args:
            params: Coin parameters to save
        """
        await self.execute(
            """
            INSERT INTO coin_parameters (
                symbol, density_threshold_abs, density_threshold_relative,
                density_threshold_percent, cluster_range_percent,
                breakout_erosion_percent, breakout_min_stop_loss_percent,
                breakout_breakeven_profit_percent, bounce_touch_tolerance_percent,
                bounce_density_stable_percent, bounce_stop_loss_behind_density_percent,
                bounce_density_erosion_exit_percent, tp_slowdown_multiplier,
                tp_local_extrema_hours, preferred_strategy, enabled, notes, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
            ON CONFLICT (symbol) DO UPDATE SET
                density_threshold_abs = EXCLUDED.density_threshold_abs,
                density_threshold_relative = EXCLUDED.density_threshold_relative,
                density_threshold_percent = EXCLUDED.density_threshold_percent,
                cluster_range_percent = EXCLUDED.cluster_range_percent,
                breakout_erosion_percent = EXCLUDED.breakout_erosion_percent,
                breakout_min_stop_loss_percent = EXCLUDED.breakout_min_stop_loss_percent,
                breakout_breakeven_profit_percent = EXCLUDED.breakout_breakeven_profit_percent,
                bounce_touch_tolerance_percent = EXCLUDED.bounce_touch_tolerance_percent,
                bounce_density_stable_percent = EXCLUDED.bounce_density_stable_percent,
                bounce_stop_loss_behind_density_percent = EXCLUDED.bounce_stop_loss_behind_density_percent,
                bounce_density_erosion_exit_percent = EXCLUDED.bounce_density_erosion_exit_percent,
                tp_slowdown_multiplier = EXCLUDED.tp_slowdown_multiplier,
                tp_local_extrema_hours = EXCLUDED.tp_local_extrema_hours,
                preferred_strategy = EXCLUDED.preferred_strategy,
                enabled = EXCLUDED.enabled,
                notes = EXCLUDED.notes,
                updated_at = EXCLUDED.updated_at
            """,
            params.symbol,
            params.density_threshold_abs,
            params.density_threshold_relative,
            params.density_threshold_percent,
            params.cluster_range_percent,
            params.breakout_erosion_percent,
            params.breakout_min_stop_loss_percent,
            params.breakout_breakeven_profit_percent,
            params.bounce_touch_tolerance_percent,
            params.bounce_density_stable_percent,
            params.bounce_stop_loss_behind_density_percent,
            params.bounce_density_erosion_exit_percent,
            params.tp_slowdown_multiplier,
            params.tp_local_extrema_hours,
            params.preferred_strategy,
            params.enabled,
            params.notes,
            params.updated_at,
        )

    # ==================== Trades ====================

    async def save_trade(self, trade: Trade) -> None:
        """
        Save a trade record.

        Args:
            trade: Trade to save
        """
        await self.execute(
            """
            INSERT INTO trades (
                id, symbol, entry_time, exit_time, entry_price, exit_price,
                position_size, leverage, signal_type, direction, profit_loss, profit_loss_percent,
                stop_loss_price, stop_loss_triggered, exit_reason, parameters_snapshot,
                created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
            ON CONFLICT (id) DO UPDATE SET
                exit_time = EXCLUDED.exit_time,
                exit_price = EXCLUDED.exit_price,
                profit_loss = EXCLUDED.profit_loss,
                profit_loss_percent = EXCLUDED.profit_loss_percent,
                stop_loss_triggered = EXCLUDED.stop_loss_triggered,
                exit_reason = EXCLUDED.exit_reason,
                updated_at = EXCLUDED.updated_at
            """,
            trade.id,
            trade.symbol,
            trade.entry_time,
            trade.exit_time,
            trade.entry_price,
            trade.exit_price,
            trade.position_size,
            trade.leverage,
            trade.signal_type.value,
            trade.direction.value,
            trade.profit_loss,
            trade.profit_loss_percent,
            trade.stop_loss_price,
            trade.stop_loss_triggered,
            trade.exit_reason.value,
            json.dumps(trade.parameters_snapshot),
            trade.created_at,
            trade.updated_at,
        )

    async def get_trades_by_symbol(
        self, symbol: str, limit: int = 100
    ) -> list[Trade]:
        """
        Get recent trades for a symbol.

        Args:
            symbol: Trading symbol
            limit: Maximum number of trades to return

        Returns:
            List of trades, most recent first
        """
        rows = await self.fetch(
            """
            SELECT * FROM trades
            WHERE symbol = $1
            ORDER BY exit_time DESC
            LIMIT $2
            """,
            symbol,
            limit,
        )

        return [self._row_to_trade(row) for row in rows]

    async def get_all_trades(
        self, limit: int = 100, offset: int = 0
    ) -> list[Trade]:
        """
        Get all trades with pagination.

        Args:
            limit: Maximum number of trades to return
            offset: Number of trades to skip

        Returns:
            List of trades, most recent first
        """
        rows = await self.fetch(
            """
            SELECT * FROM trades
            ORDER BY exit_time DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

        return [self._row_to_trade(row) for row in rows]

    def _row_to_trade(self, row: Record) -> Trade:
        """Convert database row to Trade object."""
        from .models import ExitReason, PositionDirection, SignalType

        return Trade(
            id=row["id"],
            symbol=row["symbol"],
            entry_time=row["entry_time"],
            exit_time=row["exit_time"],
            entry_price=row["entry_price"],
            exit_price=row["exit_price"],
            position_size=row["position_size"],
            leverage=row["leverage"],
            direction=PositionDirection(row["direction"]),
            signal_type=SignalType(row["signal_type"]),
            profit_loss=row["profit_loss"],
            profit_loss_percent=row["profit_loss_percent"],
            stop_loss_price=row["stop_loss_price"],
            stop_loss_triggered=row["stop_loss_triggered"],
            exit_reason=ExitReason(row["exit_reason"]),
            parameters_snapshot=json.loads(row["parameters_snapshot"]) if isinstance(row["parameters_snapshot"], str) else row["parameters_snapshot"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def create_trade_record(self, position: Position) -> UUID:
        """
        Create a trade record for an open position.

        Args:
            position: Position object to create record for

        Returns:
            UUID of created trade record from database
        """
        query = """
            INSERT INTO trades (
                symbol, entry_time, entry_price, position_size, leverage,
                direction, signal_type, stop_loss_price, status, breakeven_moved
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
        """

        trade_id = await self.fetchval(
            query,
            position.symbol,
            position.entry_time,
            position.entry_price,
            position.size,
            position.leverage,
            position.direction.value,
            position.signal_type.value,
            position.stop_loss,
            PositionStatus.OPEN.value,
            False,  # breakeven_moved
            timeout=5.0,
        )

        self.logger.info(
            "trade_record_created",
            trade_id=str(trade_id),
            symbol=position.symbol,
        )

        return trade_id

    async def update_trade_stop_loss(
        self, trade_id: UUID, new_sl: Decimal, breakeven: bool
    ) -> None:
        """
        Update stop-loss for a trade record.

        Args:
            trade_id: UUID of trade to update
            new_sl: New stop-loss price
            breakeven: Whether this is a breakeven move
        """
        query = """
            UPDATE trades
            SET stop_loss_price = $1, breakeven_moved = $2, updated_at = NOW()
            WHERE id = $3
        """

        await self.execute(
            query,
            new_sl,
            breakeven,
            trade_id,
            timeout=5.0,
        )

        self.logger.info(
            "trade_stop_loss_updated",
            trade_id=str(trade_id),
            new_sl=float(new_sl),
            breakeven=breakeven,
        )

    async def close_trade_record(
        self,
        trade_id: UUID,
        exit_price: Decimal,
        exit_time: datetime,
        pnl: Decimal,
        reason: ExitReason,
    ) -> None:
        """
        Close a trade record with exit information.

        Args:
            trade_id: UUID of trade to close
            exit_price: Price at which position was closed
            exit_time: Timestamp of closure
            pnl: Realized profit/loss
            reason: Reason for exit
        """
        query = """
            UPDATE trades
            SET exit_time = $1, exit_price = $2, profit_loss = $3,
                profit_loss_percent = (($2 - entry_price) / entry_price * 100),
                exit_reason = $4, status = $5, updated_at = NOW()
            WHERE id = $6
        """

        await self.execute(
            query,
            exit_time,
            exit_price,
            pnl,
            reason.value,
            PositionStatus.CLOSED.value,
            trade_id,
            timeout=5.0,
        )

        self.logger.info(
            "trade_record_closed",
            trade_id=str(trade_id),
            exit_price=float(exit_price),
            pnl=float(pnl),
            reason=reason.value,
        )

    async def get_open_trades(self) -> list[dict]:
        """
        Get all open trade records from database.

        Returns:
            List of dictionaries with trade data
        """
        query = """
            SELECT * FROM trades
            WHERE status = $1
            ORDER BY entry_time DESC
        """

        rows = await self.fetch(
            query,
            PositionStatus.OPEN.value,
            timeout=10.0,
        )

        trades = [dict(row) for row in rows]

        self.logger.info("open_trades_fetched", count=len(trades))

        return trades

    # ==================== Order Book Snapshots ====================

    async def save_orderbook_snapshot(self, orderbook: OrderBook) -> None:
        """
        Save an order book snapshot.

        Args:
            orderbook: Order book to save
        """
        bids_json = json.dumps([
            {"price": str(level.price), "volume": str(level.volume)}
            for level in orderbook.bids
        ])
        asks_json = json.dumps([
            {"price": str(level.price), "volume": str(level.volume)}
            for level in orderbook.asks
        ])

        total_bid_volume = orderbook.get_total_volume(OrderSide.BID)
        total_ask_volume = orderbook.get_total_volume(OrderSide.ASK)
        mid_price = orderbook.get_mid_price()

        await self.execute(
            """
            INSERT INTO orderbook_snapshots (
                time, symbol, bids, asks, total_bid_volume, total_ask_volume, mid_price
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            orderbook.timestamp,
            orderbook.symbol,
            bids_json,
            asks_json,
            total_bid_volume,
            total_ask_volume,
            mid_price,
        )

    # ==================== Densities ====================

    async def save_density(self, density: Density) -> None:
        """
        Save a density record.

        Args:
            density: Density to save
        """
        await self.execute(
            """
            INSERT INTO densities (
                time, symbol, price_level, side, volume, volume_percent,
                relative_strength, is_cluster, appeared_at, disappeared_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            density.appeared_at,
            density.symbol,
            density.price_level,
            density.side.value,
            density.volume,
            density.volume_percent,
            density.relative_strength,
            density.is_cluster,
            density.appeared_at,
            density.disappeared_at,
        )

    async def update_density_disappeared(
        self, symbol: str, price_level: Decimal, side: str, disappeared_at: datetime
    ) -> None:
        """
        Mark a density as disappeared.

        Args:
            symbol: Trading symbol
            price_level: Price level of density
            side: Bid or ask
            disappeared_at: When it disappeared
        """
        await self.execute(
            """
            UPDATE densities
            SET disappeared_at = $4
            WHERE symbol = $1 AND price_level = $2 AND side = $3
                AND disappeared_at IS NULL
            """,
            symbol,
            price_level,
            side,
            disappeared_at,
        )

    # ==================== Market Stats ====================

    async def upsert_market_stats(self, stats: MarketStats) -> None:
        """
        Insert or update market statistics.

        Args:
            stats: Market statistics to save
        """
        await self.execute(
            """
            INSERT INTO market_stats (
                symbol, volume_24h, price_change_24h_percent, current_price,
                is_active, rank, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (symbol) DO UPDATE SET
                volume_24h = EXCLUDED.volume_24h,
                price_change_24h_percent = EXCLUDED.price_change_24h_percent,
                current_price = EXCLUDED.current_price,
                is_active = EXCLUDED.is_active,
                rank = EXCLUDED.rank,
                updated_at = EXCLUDED.updated_at
            """,
            stats.symbol,
            stats.volume_24h,
            stats.price_change_24h_percent,
            stats.current_price,
            stats.is_active,
            stats.rank,
            stats.updated_at,
        )

    async def get_active_symbols(self) -> list[str]:
        """
        Get list of currently active symbols.

        Returns:
            List of active symbol names
        """
        rows = await self.fetch(
            """
            SELECT symbol FROM market_stats
            WHERE is_active = TRUE
            ORDER BY rank
            """
        )
        return [row["symbol"] for row in rows]

    # ==================== System Events ====================

    async def log_event(self, event: SystemEvent) -> None:
        """
        Log a system event.

        Args:
            event: System event to log
        """
        details_json = json.dumps(event.details) if event.details else None

        await self.execute(
            """
            INSERT INTO system_events (
                time, event_type, severity, symbol, details, message
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            event.timestamp,
            event.event_type,
            event.severity,
            event.symbol,
            details_json,
            event.message,
        )

    async def get_recent_events(
        self, limit: int = 100, severity: Optional[str] = None
    ) -> list[SystemEvent]:
        """
        Get recent system events.

        Args:
            limit: Maximum number of events to return
            severity: Filter by severity level

        Returns:
            List of system events, most recent first
        """
        if severity:
            rows = await self.fetch(
                """
                SELECT * FROM system_events
                WHERE severity = $1
                ORDER BY time DESC
                LIMIT $2
                """,
                severity,
                limit,
            )
        else:
            rows = await self.fetch(
                """
                SELECT * FROM system_events
                ORDER BY time DESC
                LIMIT $1
                """,
                limit,
            )

        return [
            SystemEvent(
                event_type=row["event_type"],
                severity=row["severity"],
                symbol=row["symbol"],
                message=row["message"],
                details=json.loads(row["details"]) if row["details"] else None,
                timestamp=row["time"],
            )
            for row in rows
        ]
