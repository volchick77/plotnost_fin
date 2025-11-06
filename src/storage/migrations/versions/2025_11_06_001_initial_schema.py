"""Initial database schema with TimescaleDB support.

Creates all tables for the trading bot:
- trades: Trading history with full P&L tracking
- coin_parameters: Per-symbol configuration
- orderbook_snapshots: TimescaleDB hypertable for order book data
- densities: TimescaleDB hypertable for density tracking
- system_events: System event logging
- market_stats: Market statistics and active symbols

Includes:
- TimescaleDB hypertables for time-series data
- Retention policies (30 days for snapshots, 60 for densities)
- All required indexes
- Default coin_parameters for top-10 coins

Revision ID: 001
Revises:
Create Date: 2025-11-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables and TimescaleDB configuration."""

    # ==================== 1. trades table ====================
    op.create_table(
        'trades',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('entry_time', sa.TIMESTAMP, nullable=False),
        sa.Column('exit_time', sa.TIMESTAMP, nullable=False),
        sa.Column('entry_price', sa.DECIMAL(20, 8), nullable=False),
        sa.Column('exit_price', sa.DECIMAL(20, 8), nullable=False),
        sa.Column('position_size', sa.DECIMAL(20, 8), nullable=False),
        sa.Column('leverage', sa.Integer, nullable=False),
        sa.Column('signal_type', sa.String(20), nullable=False),
        sa.Column('profit_loss', sa.DECIMAL(20, 8), nullable=True),
        sa.Column('profit_loss_percent', sa.DECIMAL(10, 4), nullable=True),
        sa.Column('stop_loss_price', sa.DECIMAL(20, 8), nullable=False),
        sa.Column('stop_loss_triggered', sa.Boolean, default=False),
        sa.Column('exit_reason', sa.String(50), nullable=True),
        sa.Column('parameters_snapshot', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.TIMESTAMP, server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP, server_default=sa.text('NOW()'), nullable=False),
    )

    # Indexes for trades
    op.create_index('idx_trades_symbol', 'trades', ['symbol'])
    op.create_index('idx_trades_entry_time', 'trades', ['entry_time'])
    op.create_index('idx_trades_signal_type', 'trades', ['signal_type'])
    op.create_index('idx_trades_exit_time', 'trades', ['exit_time'])

    # ==================== 2. coin_parameters table ====================
    op.create_table(
        'coin_parameters',
        sa.Column('symbol', sa.String(20), primary_key=True),

        # Density thresholds
        sa.Column('density_threshold_abs', sa.DECIMAL(20, 2), default=50000, nullable=False),
        sa.Column('density_threshold_relative', sa.DECIMAL(5, 2), default=3.0, nullable=False),
        sa.Column('density_threshold_percent', sa.DECIMAL(5, 2), default=5.0, nullable=False),

        # Cluster detection
        sa.Column('cluster_range_percent', sa.DECIMAL(5, 2), default=0.5, nullable=False),

        # Breakout parameters
        sa.Column('breakout_erosion_percent', sa.DECIMAL(5, 2), default=30.0, nullable=False),
        sa.Column('breakout_min_stop_loss_percent', sa.DECIMAL(5, 2), default=0.1, nullable=False),
        sa.Column('breakout_breakeven_profit_percent', sa.DECIMAL(5, 2), default=0.5, nullable=False),

        # Bounce parameters
        sa.Column('bounce_touch_tolerance_percent', sa.DECIMAL(5, 2), default=0.2, nullable=False),
        sa.Column('bounce_density_stable_percent', sa.DECIMAL(5, 2), default=10.0, nullable=False),
        sa.Column('bounce_stop_loss_behind_density_percent', sa.DECIMAL(5, 2), default=0.3, nullable=False),
        sa.Column('bounce_density_erosion_exit_percent', sa.DECIMAL(5, 2), default=65.0, nullable=False),

        # Take-profit parameters
        sa.Column('tp_slowdown_multiplier', sa.DECIMAL(5, 2), default=3.0, nullable=False),
        sa.Column('tp_local_extrema_hours', sa.Integer, default=4, nullable=False),

        # Strategy preference
        sa.Column('preferred_strategy', sa.String(20), default='both', nullable=False),

        # Control
        sa.Column('enabled', sa.Boolean, default=True, nullable=False),

        # Metadata
        sa.Column('updated_at', sa.TIMESTAMP, server_default=sa.text('NOW()'), nullable=False),
        sa.Column('notes', sa.Text, nullable=True),
    )

    # ==================== 3. orderbook_snapshots table (TimescaleDB) ====================
    op.create_table(
        'orderbook_snapshots',
        sa.Column('time', sa.TIMESTAMP, nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('bids', postgresql.JSONB, nullable=False),
        sa.Column('asks', postgresql.JSONB, nullable=False),
        sa.Column('total_bid_volume', sa.DECIMAL(20, 8), nullable=True),
        sa.Column('total_ask_volume', sa.DECIMAL(20, 8), nullable=True),
        sa.Column('mid_price', sa.DECIMAL(20, 8), nullable=True),
    )

    # Convert to TimescaleDB hypertable
    op.execute(
        "SELECT create_hypertable('orderbook_snapshots', 'time', if_not_exists => TRUE);"
    )

    # Index for orderbook_snapshots
    op.create_index(
        'idx_obs_symbol_time',
        'orderbook_snapshots',
        ['symbol', sa.text('time DESC')]
    )

    # Retention policy: 30 days
    op.execute(
        "SELECT add_retention_policy('orderbook_snapshots', INTERVAL '30 days', if_not_exists => TRUE);"
    )

    # ==================== 4. densities table (TimescaleDB) ====================
    op.create_table(
        'densities',
        sa.Column('time', sa.TIMESTAMP, nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('price_level', sa.DECIMAL(20, 8), nullable=False),
        sa.Column('side', sa.String(4), nullable=False),
        sa.Column('volume', sa.DECIMAL(20, 8), nullable=False),
        sa.Column('volume_percent', sa.DECIMAL(5, 2), nullable=True),
        sa.Column('relative_strength', sa.DECIMAL(5, 2), nullable=True),
        sa.Column('is_cluster', sa.Boolean, default=False, nullable=False),
        sa.Column('appeared_at', sa.TIMESTAMP, nullable=True),
        sa.Column('disappeared_at', sa.TIMESTAMP, nullable=True),
    )

    # Convert to TimescaleDB hypertable
    op.execute(
        "SELECT create_hypertable('densities', 'time', if_not_exists => TRUE);"
    )

    # Indexes for densities
    op.create_index(
        'idx_densities_symbol_time',
        'densities',
        ['symbol', sa.text('time DESC')]
    )
    op.create_index('idx_densities_price', 'densities', ['price_level'])
    op.create_index('idx_densities_side', 'densities', ['side'])

    # Retention policy: 60 days
    op.execute(
        "SELECT add_retention_policy('densities', INTERVAL '60 days', if_not_exists => TRUE);"
    )

    # ==================== 5. system_events table ====================
    op.create_table(
        'system_events',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('time', sa.TIMESTAMP, server_default=sa.text('NOW()'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=True),
        sa.Column('details', postgresql.JSONB, nullable=True),
        sa.Column('message', sa.Text, nullable=True),
    )

    # Indexes for system_events
    op.create_index('idx_events_time', 'system_events', [sa.text('time DESC')])
    op.create_index('idx_events_severity', 'system_events', ['severity'])
    op.create_index('idx_events_type', 'system_events', ['event_type'])

    # ==================== 6. market_stats table ====================
    op.create_table(
        'market_stats',
        sa.Column('symbol', sa.String(20), primary_key=True),
        sa.Column('volume_24h', sa.DECIMAL(20, 2), nullable=True),
        sa.Column('price_change_24h_percent', sa.DECIMAL(10, 4), nullable=True),
        sa.Column('current_price', sa.DECIMAL(20, 8), nullable=True),
        sa.Column('is_active', sa.Boolean, default=False, nullable=False),
        sa.Column('rank', sa.Integer, nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP, server_default=sa.text('NOW()'), nullable=False),
    )

    # Index for market_stats
    op.create_index('idx_market_stats_active', 'market_stats', ['is_active', 'rank'])

    # ==================== Insert default coin parameters ====================
    # Top 10 coins with default parameters
    top_coins = [
        ('BTCUSDT', 100000, 'Bitcoin - flagship cryptocurrency'),
        ('ETHUSDT', 50000, 'Ethereum - smart contract platform'),
        ('SOLUSDT', 30000, 'Solana - high-performance blockchain'),
        ('BNBUSDT', 25000, 'Binance Coin - exchange token'),
        ('XRPUSDT', 20000, 'Ripple - payment protocol'),
        ('ADAUSDT', 15000, 'Cardano - proof-of-stake blockchain'),
        ('DOGEUSDT', 15000, 'Dogecoin - meme cryptocurrency'),
        ('MATICUSDT', 12000, 'Polygon - Ethereum scaling solution'),
        ('DOTUSDT', 10000, 'Polkadot - multi-chain protocol'),
        ('UNIUSDT', 10000, 'Uniswap - decentralized exchange token'),
    ]

    for symbol, threshold, notes in top_coins:
        op.execute(f"""
            INSERT INTO coin_parameters (
                symbol, density_threshold_abs, density_threshold_relative,
                density_threshold_percent, cluster_range_percent,
                breakout_erosion_percent, breakout_min_stop_loss_percent,
                breakout_breakeven_profit_percent, bounce_touch_tolerance_percent,
                bounce_density_stable_percent, bounce_stop_loss_behind_density_percent,
                bounce_density_erosion_exit_percent, tp_slowdown_multiplier,
                tp_local_extrema_hours, preferred_strategy, enabled, notes
            ) VALUES (
                '{symbol}', {threshold}, 3.0, 5.0, 0.5,
                30.0, 0.1, 0.5, 0.2,
                10.0, 0.3, 65.0, 3.0,
                4, 'both', TRUE, '{notes}'
            )
        """)


def downgrade() -> None:
    """Drop all tables."""
    # Drop tables in reverse order to handle dependencies
    op.drop_table('market_stats')
    op.drop_table('system_events')
    op.drop_table('densities')
    op.drop_table('orderbook_snapshots')
    op.drop_table('coin_parameters')
    op.drop_table('trades')
