"""
Market analysis components for the crypto trading bot.

This package contains modules for analyzing market data and generating signals:
- density_analyzer: Tracks density erosion and detects breakout opportunities
- trend_analyzer: Determines market trend direction using price change and order book pressure
- signal_generator: Generates trading signals based on trend and density analysis
"""

from src.market_analysis.density_analyzer import DensityAnalyzer
from src.market_analysis.trend_analyzer import TrendAnalyzer
from src.market_analysis.signal_generator import SignalGenerator

__all__ = ["DensityAnalyzer", "TrendAnalyzer", "SignalGenerator"]
