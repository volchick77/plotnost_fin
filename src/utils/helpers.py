"""
Utility helper functions for the crypto trading bot.

This module provides common utility functions for calculations, formatting,
and data processing used throughout the bot.
"""

from decimal import Decimal
from typing import List, Optional


def calculate_profit_percent(
    entry_price: float,
    exit_price: float,
    direction: str
) -> float:
    """
    Calculate profit/loss percentage for a trade.

    Args:
        entry_price: Entry price of the position
        exit_price: Exit price of the position
        direction: 'LONG' or 'SHORT'

    Returns:
        Profit percentage (positive for profit, negative for loss)

    Example:
        >>> calculate_profit_percent(100.0, 105.0, 'LONG')
        5.0
        >>> calculate_profit_percent(100.0, 95.0, 'SHORT')
        5.0
    """
    if entry_price == 0:
        raise ValueError("Entry price cannot be zero")

    direction = direction.upper()

    if direction == 'LONG':
        return ((exit_price - entry_price) / entry_price) * 100
    elif direction == 'SHORT':
        return ((entry_price - exit_price) / entry_price) * 100
    else:
        raise ValueError(f"Invalid direction: {direction}. Must be 'LONG' or 'SHORT'")


def calculate_required_margin(
    position_size: float,
    leverage: int
) -> float:
    """
    Calculate required margin for a position.

    Args:
        position_size: Position size in USDT
        leverage: Leverage multiplier

    Returns:
        Required margin in USDT

    Example:
        >>> calculate_required_margin(100.0, 10)
        10.0
    """
    if leverage <= 0:
        raise ValueError("Leverage must be greater than 0")

    return position_size / leverage


def is_price_near_level(
    price: float,
    level: float,
    tolerance_percent: float = 0.2
) -> bool:
    """
    Check if price is within tolerance of a level.

    Args:
        price: Current price
        level: Target price level
        tolerance_percent: Tolerance as percentage (default 0.2%)

    Returns:
        True if price is within tolerance of level

    Example:
        >>> is_price_near_level(100.0, 100.1, 0.2)
        True
        >>> is_price_near_level(100.0, 101.0, 0.2)
        False
    """
    if level == 0:
        return False

    diff_percent = abs((price - level) / level) * 100
    return diff_percent <= tolerance_percent


def calculate_average_volume(
    volumes: List[float],
    n: Optional[int] = None
) -> float:
    """
    Calculate average volume from a list.

    Args:
        volumes: List of volume values
        n: Number of values to average (if None, uses all)

    Returns:
        Average volume

    Example:
        >>> calculate_average_volume([10.0, 20.0, 30.0])
        20.0
        >>> calculate_average_volume([10.0, 20.0, 30.0, 40.0], n=2)
        15.0
    """
    if not volumes:
        return 0.0

    if n is not None:
        volumes = volumes[:n]

    return sum(volumes) / len(volumes)


def format_usdt(amount: float, decimals: int = 2) -> str:
    """
    Format USDT amount for display.

    Args:
        amount: Amount in USDT
        decimals: Number of decimal places

    Returns:
        Formatted string with $ prefix

    Example:
        >>> format_usdt(1234.56)
        '$1,234.56'
        >>> format_usdt(0.123456, decimals=4)
        '$0.1235'
    """
    return f"${amount:,.{decimals}f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """
    Format percentage for display.

    Args:
        value: Percentage value
        decimals: Number of decimal places

    Returns:
        Formatted string with % suffix

    Example:
        >>> format_percent(5.123)
        '5.12%'
        >>> format_percent(-2.5)
        '-2.50%'
    """
    return f"{value:.{decimals}f}%"


def decimal_to_float(value: Decimal) -> float:
    """
    Convert Decimal to float safely.

    Args:
        value: Decimal value

    Returns:
        Float value
    """
    return float(value)


def float_to_decimal(value: float) -> Decimal:
    """
    Convert float to Decimal safely.

    Args:
        value: Float value

    Returns:
        Decimal value
    """
    return Decimal(str(value))


def clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Clamp a value between min and max.

    Args:
        value: Value to clamp
        min_val: Minimum value
        max_val: Maximum value

    Returns:
        Clamped value

    Example:
        >>> clamp(5, 0, 10)
        5
        >>> clamp(-5, 0, 10)
        0
        >>> clamp(15, 0, 10)
        10
    """
    return max(min_val, min(max_val, value))
