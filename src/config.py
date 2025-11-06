"""
Configuration loader for the crypto trading bot.

This module provides configuration loading from YAML files with environment variable
overrides, validation using Pydantic, and comprehensive error handling.
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class ExchangeConfig(BaseModel):
    """Exchange configuration settings."""

    name: str = Field(description="Exchange name (e.g., bybit)")
    api_key_env: str = Field(description="Environment variable name for API key")
    api_secret_env: str = Field(description="Environment variable name for API secret")
    api_key: Optional[str] = Field(default=None, description="API key loaded from environment")
    api_secret: Optional[str] = Field(default=None, description="API secret loaded from environment")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate exchange name."""
        allowed_exchanges = ["bybit"]
        if v.lower() not in allowed_exchanges:
            raise ValueError(f"Exchange must be one of {allowed_exchanges}, got '{v}'")
        return v.lower()


class WebSocketConfig(BaseModel):
    """WebSocket connection configuration."""

    orderbook_depth: int = Field(gt=0, le=200, description="Order book depth to fetch")
    reconnect_delay_initial: int = Field(gt=0, description="Initial reconnect delay in seconds")
    reconnect_delay_max: int = Field(gt=0, description="Maximum reconnect delay in seconds")
    snapshot_interval: int = Field(gt=0, description="Snapshot interval in seconds")

    @field_validator("reconnect_delay_max")
    @classmethod
    def validate_reconnect_delays(cls, v: int, info) -> int:
        """Ensure max delay is greater than or equal to initial delay."""
        if "orderbook_depth" in info.data:
            # Access other fields through info.data
            initial = info.data.get("reconnect_delay_initial")
            if initial and v < initial:
                raise ValueError(
                    f"reconnect_delay_max ({v}) must be >= reconnect_delay_initial ({initial})"
                )
        return v


class MarketConfig(BaseModel):
    """Market analysis configuration."""

    update_interval: int = Field(gt=0, description="Update interval in seconds")
    top_gainers_count: int = Field(gt=0, le=100, description="Number of top gainers to track")
    top_losers_count: int = Field(gt=0, le=100, description="Number of top losers to track")
    min_24h_volume: float = Field(gt=0, description="Minimum 24h volume in USDT")


class TradingConfig(BaseModel):
    """Trading parameters configuration."""

    position_size_usd: float = Field(gt=0, description="Position size in USD")
    leverage: int = Field(gt=0, le=100, description="Leverage multiplier")
    margin_mode: str = Field(description="Margin mode (ISOLATED or CROSS)")
    max_concurrent_positions: int = Field(gt=0, description="Maximum concurrent positions")
    max_exposure_percent: float = Field(gt=0, le=100, description="Maximum exposure as percent of balance")

    @field_validator("margin_mode")
    @classmethod
    def validate_margin_mode(cls, v: str) -> str:
        """Validate margin mode."""
        allowed_modes = ["ISOLATED", "CROSS"]
        v_upper = v.upper()
        if v_upper not in allowed_modes:
            raise ValueError(f"margin_mode must be one of {allowed_modes}, got '{v}'")
        return v_upper

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: int) -> int:
        """Validate leverage is reasonable."""
        if v > 50:
            raise ValueError(
                f"Leverage {v} is very high (>50x). Please use caution or reduce leverage."
            )
        return v


class StrategyConfig(BaseModel):
    """Strategy parameters configuration."""

    # Density detection thresholds
    density_threshold_abs: float = Field(gt=0, description="Absolute density threshold in USDT")
    density_threshold_relative: float = Field(gt=0, description="Relative density threshold multiplier")
    density_threshold_percent: float = Field(gt=0, le=100, description="Density threshold as percent")

    # Cluster detection
    cluster_range_percent: float = Field(gt=0, le=100, description="Price range for clustering")

    # Breakout parameters
    breakout_erosion_percent: float = Field(gt=0, le=100, description="Breakout erosion percent")
    breakout_min_stop_loss_percent: float = Field(gt=0, description="Minimum stop loss percent")
    breakout_breakeven_profit_percent: float = Field(gt=0, description="Breakeven profit percent")

    # Bounce parameters
    bounce_touch_tolerance_percent: float = Field(gt=0, description="Bounce touch tolerance")
    bounce_stop_loss_behind_density_percent: float = Field(gt=0, description="Stop loss behind density")
    bounce_density_erosion_exit_percent: float = Field(gt=0, le=100, description="Density erosion exit")
    bounce_density_stable_threshold_percent: float = Field(gt=0, le=100, description="Density stable threshold")

    # Take-profit parameters
    tp_slowdown_multiplier: float = Field(gt=0, description="Take profit slowdown multiplier")
    tp_local_extrema_hours: int = Field(gt=0, description="Local extrema hours")

    # Trend determination
    trend_price_change_threshold: float = Field(gt=0, description="Price change threshold for trend")
    trend_orderbook_pressure_ratio: float = Field(gt=0, description="Orderbook pressure ratio")


class SafetyConfig(BaseModel):
    """Safety mechanisms configuration."""

    connection_loss_timeout: int = Field(gt=0, description="Connection loss timeout in seconds")
    emergency_close_all: bool = Field(description="Emergency close all positions on connection loss")
    require_stop_loss: bool = Field(description="Require stop loss for all positions")
    max_api_retries: int = Field(gt=0, description="Maximum API retry attempts")
    api_retry_delay_initial: int = Field(gt=0, description="Initial API retry delay in seconds")
    api_retry_delay_max: int = Field(gt=0, description="Maximum API retry delay in seconds")

    @field_validator("api_retry_delay_max")
    @classmethod
    def validate_retry_delays(cls, v: int, info) -> int:
        """Ensure max delay is greater than or equal to initial delay."""
        if "api_retry_delay_initial" in info.data:
            initial = info.data.get("api_retry_delay_initial")
            if initial and v < initial:
                raise ValueError(
                    f"api_retry_delay_max ({v}) must be >= api_retry_delay_initial ({initial})"
                )
        return v


class DatabaseConfig(BaseModel):
    """Database connection configuration."""

    host: str = Field(description="Database host")
    port: int = Field(gt=0, le=65535, description="Database port")
    name: str = Field(description="Database name")
    user_env: str = Field(description="Environment variable name for database user")
    password_env: str = Field(description="Environment variable name for database password")
    pool_min_size: int = Field(gt=0, description="Minimum connection pool size")
    pool_max_size: int = Field(gt=0, description="Maximum connection pool size")
    user: Optional[str] = Field(default=None, description="Database user loaded from environment")
    password: Optional[str] = Field(default=None, description="Database password loaded from environment")

    @field_validator("pool_max_size")
    @classmethod
    def validate_pool_sizes(cls, v: int, info) -> int:
        """Ensure max pool size is greater than or equal to min pool size."""
        if "pool_min_size" in info.data:
            min_size = info.data.get("pool_min_size")
            if min_size and v < min_size:
                raise ValueError(
                    f"pool_max_size ({v}) must be >= pool_min_size ({min_size})"
                )
        return v


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(description="Logging level")
    file: str = Field(description="Log file path")
    max_size_mb: int = Field(gt=0, description="Maximum log file size in MB")
    backup_count: int = Field(ge=0, description="Number of backup log files")
    format: str = Field(description="Log format (json or text)")
    console_output: bool = Field(description="Enable console output")

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        """Validate logging level."""
        allowed_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in allowed_levels:
            raise ValueError(f"Logging level must be one of {allowed_levels}, got '{v}'")
        return v_upper

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        """Validate log format."""
        allowed_formats = ["json", "text"]
        v_lower = v.lower()
        if v_lower not in allowed_formats:
            raise ValueError(f"Log format must be one of {allowed_formats}, got '{v}'")
        return v_lower


class Config(BaseModel):
    """Main configuration object containing all settings."""

    exchange: ExchangeConfig
    websocket: WebSocketConfig
    market: MarketConfig
    trading: TradingConfig
    strategy: StrategyConfig
    safety: SafetyConfig
    database: DatabaseConfig
    logging: LoggingConfig

    @model_validator(mode="after")
    def load_secrets_from_env(self) -> "Config":
        """
        Load sensitive data from environment variables after initial validation.

        Raises:
            ValueError: If required environment variables are missing.
        """
        # Load exchange API credentials
        api_key = os.getenv(self.exchange.api_key_env)
        api_secret = os.getenv(self.exchange.api_secret_env)

        if not api_key:
            raise ValueError(
                f"Missing required environment variable: {self.exchange.api_key_env}"
            )
        if not api_secret:
            raise ValueError(
                f"Missing required environment variable: {self.exchange.api_secret_env}"
            )

        self.exchange.api_key = api_key
        self.exchange.api_secret = api_secret

        # Load database credentials
        db_user = os.getenv(self.database.user_env)
        db_password = os.getenv(self.database.password_env)

        if not db_user:
            raise ValueError(
                f"Missing required environment variable: {self.database.user_env}"
            )
        if not db_password:
            raise ValueError(
                f"Missing required environment variable: {self.database.password_env}"
            )

        self.database.user = db_user
        self.database.password = db_password

        return self


def load_config(config_path: str = "./config.yaml", env_file: str = ".env") -> Config:
    """
    Load configuration from YAML file with environment variable overrides.

    This function:
    1. Loads environment variables from .env file (if it exists)
    2. Reads the YAML configuration file
    3. Validates all configuration parameters using Pydantic
    4. Loads secrets from environment variables
    5. Returns a validated Config object

    Args:
        config_path: Path to the YAML configuration file. Defaults to "./config.yaml"
        env_file: Path to the .env file. Defaults to ".env"

    Returns:
        Config: Validated configuration object with all settings and secrets loaded

    Raises:
        FileNotFoundError: If config.yaml is not found
        ValueError: If configuration validation fails or required env vars are missing
        yaml.YAMLError: If YAML parsing fails

    Example:
        >>> config = load_config()
        >>> print(config.trading.position_size_usd)
        0.1
        >>> print(config.exchange.api_key)
        'your_api_key'
    """
    # Load environment variables from .env file (if it exists)
    env_path = Path(env_file)
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # Try to load from current directory if specified path doesn't exist
        load_dotenv()

    # Check if config file exists
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Please create a config.yaml file in the project root directory.\n"
            f"You can use the template from the documentation."
        )

    # Load YAML configuration
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML configuration: {e}") from e

    if not config_data:
        raise ValueError(
            f"Configuration file is empty: {config_path}\n"
            f"Please add configuration settings to the file."
        )

    # Apply environment variable overrides for optional settings
    # Format: SECTION_KEY=value (e.g., TRADING_POSITION_SIZE=0.2)
    _apply_env_overrides(config_data)

    # Validate and create Config object
    try:
        config = Config(**config_data)
    except Exception as e:
        raise ValueError(f"Configuration validation failed: {e}") from e

    return config


def _apply_env_overrides(config_data: dict) -> None:
    """
    Apply environment variable overrides to configuration data.

    Environment variables should follow the pattern: SECTION_KEY=value
    Example: TRADING_POSITION_SIZE=0.2, LOGGING_LEVEL=DEBUG

    This allows runtime overrides without modifying config.yaml.

    Args:
        config_data: Dictionary of configuration data to modify in-place
    """
    env_prefix_map = {
        "EXCHANGE": "exchange",
        "WEBSOCKET": "websocket",
        "MARKET": "market",
        "TRADING": "trading",
        "STRATEGY": "strategy",
        "SAFETY": "safety",
        "DATABASE": "database",
        "LOGGING": "logging",
    }

    for env_key, env_value in os.environ.items():
        # Check if it matches our pattern
        for prefix, section in env_prefix_map.items():
            if env_key.startswith(f"{prefix}_"):
                # Extract the config key
                config_key = env_key[len(prefix) + 1 :].lower()

                # Ensure the section exists
                if section not in config_data:
                    continue

                # Try to convert the value to the appropriate type
                if config_key in config_data[section]:
                    original_value = config_data[section][config_key]
                    try:
                        # Convert to the same type as the original value
                        if isinstance(original_value, bool):
                            # Handle boolean conversion
                            config_data[section][config_key] = env_value.lower() in (
                                "true",
                                "1",
                                "yes",
                                "on",
                            )
                        elif isinstance(original_value, int):
                            config_data[section][config_key] = int(env_value)
                        elif isinstance(original_value, float):
                            config_data[section][config_key] = float(env_value)
                        else:
                            config_data[section][config_key] = env_value
                    except (ValueError, TypeError):
                        # If conversion fails, use string value
                        config_data[section][config_key] = env_value
