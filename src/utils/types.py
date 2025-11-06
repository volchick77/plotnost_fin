"""
Type definitions and constants for the crypto trading bot.

This module provides custom type aliases, constants, and shared type definitions
used throughout the codebase.
"""

from typing import Dict, List, Tuple
from decimal import Decimal

# =============================================================================
# Type Aliases
# =============================================================================

# Price and volume types
Price = float
Volume = float
PriceVolumeTuple = Tuple[Price, Volume]

# Order book types
Bids = List[PriceVolumeTuple]
Asks = List[PriceVolumeTuple]
OrderBookDict = Dict[str, List[PriceVolumeTuple]]

# Symbol types
Symbol = str
SymbolList = List[Symbol]

# Percentage type
Percent = float

# =============================================================================
# Trading Constants
# =============================================================================

# Position sizing
MIN_POSITION_SIZE_USDT = 0.1  # Minimum position size for testing
DEFAULT_POSITION_SIZE_USDT = 0.1
MAX_POSITION_SIZE_USDT = 10000.0

# Leverage
MIN_LEVERAGE = 1
MAX_LEVERAGE = 100
DEFAULT_LEVERAGE = 10
SAFE_MAX_LEVERAGE = 20  # Warning threshold

# Margin modes
MARGIN_MODE_ISOLATED = "ISOLATED"
MARGIN_MODE_CROSS = "CROSS"

# =============================================================================
# Strategy Constants
# =============================================================================

# Density detection thresholds
DEFAULT_DENSITY_THRESHOLD_ABS = 50000.0  # USDT
DEFAULT_DENSITY_THRESHOLD_RELATIVE = 3.0  # multiplier
DEFAULT_DENSITY_THRESHOLD_PERCENT = 5.0  # percent

# Cluster detection
DEFAULT_CLUSTER_RANGE_PERCENT = 0.15  # 0.1-0.2%

# Breakout strategy
DEFAULT_BREAKOUT_EROSION_PERCENT = 30.0
DEFAULT_BREAKOUT_MIN_STOP_LOSS_PERCENT = 0.1
DEFAULT_BREAKOUT_BREAKEVEN_PROFIT_PERCENT = 0.5

# Bounce strategy
DEFAULT_BOUNCE_TOUCH_TOLERANCE_PERCENT = 0.2
DEFAULT_BOUNCE_STOP_LOSS_BEHIND_DENSITY_PERCENT = 0.3
DEFAULT_BOUNCE_DENSITY_EROSION_EXIT_PERCENT = 65.0
DEFAULT_BOUNCE_DENSITY_STABLE_THRESHOLD_PERCENT = 10.0

# Take-profit
DEFAULT_TP_SLOWDOWN_MULTIPLIER = 3.0
DEFAULT_TP_LOCAL_EXTREMA_HOURS = 4

# Trend determination
DEFAULT_TREND_PRICE_CHANGE_THRESHOLD = 2.0  # percent
DEFAULT_TREND_ORDERBOOK_PRESSURE_RATIO = 1.2

# =============================================================================
# Market Analysis Constants
# =============================================================================

# Symbol selection
DEFAULT_TOP_GAINERS_COUNT = 20
DEFAULT_TOP_LOSERS_COUNT = 20
DEFAULT_MIN_24H_VOLUME = 1000000  # USDT

# Market update intervals
DEFAULT_MARKET_UPDATE_INTERVAL = 300  # seconds (5 minutes)
DEFAULT_ORDERBOOK_SNAPSHOT_INTERVAL = 300  # seconds (5 minutes)

# =============================================================================
# WebSocket Constants
# =============================================================================

# Order book depth
DEFAULT_ORDERBOOK_DEPTH = 50
MIN_ORDERBOOK_DEPTH = 10
MAX_ORDERBOOK_DEPTH = 200

# Reconnection
DEFAULT_RECONNECT_DELAY_INITIAL = 1  # seconds
DEFAULT_RECONNECT_DELAY_MAX = 30  # seconds

# =============================================================================
# Safety Constants
# =============================================================================

# Connection loss timeout
DEFAULT_CONNECTION_LOSS_TIMEOUT = 30  # seconds
MAX_CONNECTION_LOSS_TIMEOUT = 60  # seconds

# API retries
DEFAULT_MAX_API_RETRIES = 5
DEFAULT_API_RETRY_DELAY_INITIAL = 1  # seconds
DEFAULT_API_RETRY_DELAY_MAX = 10  # seconds

# Position limits
DEFAULT_MAX_CONCURRENT_POSITIONS = 10
DEFAULT_MAX_EXPOSURE_PERCENT = 5  # percent of total balance

# =============================================================================
# Database Constants
# =============================================================================

# Connection pool
DEFAULT_DB_POOL_MIN_SIZE = 5
DEFAULT_DB_POOL_MAX_SIZE = 10

# Data retention (days)
ORDERBOOK_SNAPSHOT_RETENTION_DAYS = 30
DENSITY_RETENTION_DAYS = 60

# =============================================================================
# Logging Constants
# =============================================================================

# Log levels
LOG_LEVEL_DEBUG = "DEBUG"
LOG_LEVEL_INFO = "INFO"
LOG_LEVEL_WARNING = "WARNING"
LOG_LEVEL_ERROR = "ERROR"
LOG_LEVEL_CRITICAL = "CRITICAL"

# Log file rotation
DEFAULT_LOG_MAX_SIZE_MB = 100
DEFAULT_LOG_BACKUP_COUNT = 10

# =============================================================================
# Time Constants
# =============================================================================

# Intervals in seconds
SECOND = 1
MINUTE = 60
HOUR = 3600
DAY = 86400

# Common intervals
INTERVAL_1_SECOND = 1
INTERVAL_5_SECONDS = 5
INTERVAL_30_SECONDS = 30
INTERVAL_1_MINUTE = 60
INTERVAL_5_MINUTES = 300
INTERVAL_10_MINUTES = 600
INTERVAL_1_HOUR = 3600
INTERVAL_24_HOURS = 86400

# =============================================================================
# Precision Constants
# =============================================================================

# Decimal places for display
PRICE_DECIMAL_PLACES = 8
VOLUME_DECIMAL_PLACES = 8
PERCENT_DECIMAL_PLACES = 2
USDT_DECIMAL_PLACES = 2

# =============================================================================
# System Event Types
# =============================================================================

# Event types for system_events table
EVENT_TYPE_WEBSOCKET_DISCONNECT = "websocket_disconnect"
EVENT_TYPE_WEBSOCKET_RECONNECT = "websocket_reconnect"
EVENT_TYPE_POSITION_OPENED = "position_opened"
EVENT_TYPE_POSITION_CLOSED = "position_closed"
EVENT_TYPE_POSITION_EMERGENCY_CLOSE = "position_emergency_close"
EVENT_TYPE_API_ERROR = "api_error"
EVENT_TYPE_API_RATE_LIMIT = "api_rate_limit"
EVENT_TYPE_STOP_LOSS_FAILED = "stop_loss_failed"
EVENT_TYPE_SIGNAL_GENERATED = "signal_generated"
EVENT_TYPE_SIGNAL_SKIPPED = "signal_skipped"
EVENT_TYPE_DENSITY_DETECTED = "density_detected"
EVENT_TYPE_DENSITY_DISAPPEARED = "density_disappeared"
EVENT_TYPE_BOT_STARTED = "bot_started"
EVENT_TYPE_BOT_STOPPED = "bot_stopped"
EVENT_TYPE_BOT_ERROR = "bot_error"

# Event severity levels
EVENT_SEVERITY_INFO = "info"
EVENT_SEVERITY_WARNING = "warning"
EVENT_SEVERITY_ERROR = "error"
EVENT_SEVERITY_CRITICAL = "critical"
