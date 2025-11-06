"""
Density Analyzer for the crypto trading bot.

This module analyzes density erosion to detect breakout opportunities.
It tracks volume changes in density levels and identifies when densities
are being eroded (broken through) by market pressure.
"""

from decimal import Decimal
from typing import List, Optional

from src.storage.models import Density, OrderSide, TrendDirection
from src.data_collection.orderbook_manager import OrderBookManager
from src.utils.logger import get_logger


class DensityAnalyzer:
    """
    Analyzes density erosion to detect breakout opportunities.
    
    Tracks volume changes in density levels and identifies when densities
    are being eroded (broken through) by market pressure.
    """
    
    def __init__(
        self,
        orderbook_manager: OrderBookManager,
        erosion_threshold_percent: Decimal = Decimal("30.0"),
    ):
        """
        Initialize density analyzer.
        
        Args:
            orderbook_manager: OrderBook manager instance
            erosion_threshold_percent: Erosion % to consider density broken (default: 30%)
        """
        self.orderbook_manager = orderbook_manager
        self.erosion_threshold_percent = erosion_threshold_percent
        self.logger = get_logger(__name__)
        
        self.logger.info(
            "density_analyzer_initialized",
            erosion_threshold_percent=float(erosion_threshold_percent),
        )
        
    def get_broken_densities(self, symbol: str) -> List[Density]:
        """
        Get list of densities that have been broken (eroded).
        
        Args:
            symbol: Trading symbol to analyze
            
        Returns:
            List of densities with erosion >= threshold
        """
        # Get current densities from orderbook manager
        current_densities = self.orderbook_manager.get_current_densities(symbol)
        
        if not current_densities:
            self.logger.debug(
                "no_current_densities",
                symbol=symbol,
            )
            return []
        
        broken_densities = []
        
        for density in current_densities:
            erosion_percent = density.erosion_percent()
            
            # Check if erosion exceeds threshold
            if erosion_percent >= self.erosion_threshold_percent:
                self.logger.info(
                    "density_broken",
                    symbol=symbol,
                    price_level=float(density.price_level),
                    side=density.side.value,
                    initial_volume=float(density.initial_volume),
                    current_volume=float(density.volume),
                    erosion_percent=float(erosion_percent),
                    is_cluster=density.is_cluster,
                    relative_strength=float(density.relative_strength) if density.relative_strength else None,
                )
                broken_densities.append(density)
        
        if broken_densities:
            self.logger.info(
                "broken_densities_found",
                symbol=symbol,
                count=len(broken_densities),
            )
        
        return broken_densities
        
    def get_strongest_broken_density(
        self, symbol: str, side: OrderSide
    ) -> Optional[Density]:
        """
        Get the strongest (most eroded) broken density on a specific side.
        
        Args:
            symbol: Trading symbol
            side: BID or ASK side
            
        Returns:
            Density with highest erosion, or None if no broken densities
        """
        broken = self.get_broken_densities(symbol)
        
        # Filter by side
        side_broken = [d for d in broken if d.side == side]
        
        if not side_broken:
            self.logger.debug(
                "no_broken_densities_on_side",
                symbol=symbol,
                side=side.value,
            )
            return None
        
        # Sort by erosion percent (highest first)
        side_broken.sort(key=lambda d: d.erosion_percent(), reverse=True)
        
        strongest = side_broken[0]
        
        self.logger.info(
            "strongest_broken_density",
            symbol=symbol,
            side=side.value,
            price_level=float(strongest.price_level),
            erosion_percent=float(strongest.erosion_percent()),
            is_cluster=strongest.is_cluster,
            relative_strength=float(strongest.relative_strength) if strongest.relative_strength else None,
        )
        
        return strongest
        
    def has_cluster_breakout(self, density: Density) -> bool:
        """
        Check if the broken density is part of a cluster.
        
        Cluster breakouts are considered stronger signals.
        
        Args:
            density: Density to check
            
        Returns:
            True if density is part of a cluster
        """
        is_cluster = density.is_cluster
        
        self.logger.debug(
            "cluster_breakout_check",
            price_level=float(density.price_level),
            side=density.side.value,
            is_cluster=is_cluster,
        )
        
        return is_cluster
        
    def calculate_breakout_strength(self, density: Density) -> Decimal:
        """
        Calculate breakout strength score.
        
        Higher score = stronger breakout signal
        
        Factors:
        - Erosion percent (higher = stronger)
        - Cluster membership (clusters = stronger)
        - Relative strength (higher initial density = stronger)
        
        Args:
            density: Density to analyze
            
        Returns:
            Breakout strength score (0-100)
        """
        score = Decimal("0")
        
        # Base score from erosion (0-50 points)
        erosion = density.erosion_percent()
        score += min(erosion / Decimal("2"), Decimal("50"))
        
        # Cluster bonus (20 points)
        if density.is_cluster:
            score += Decimal("20")
        
        # Relative strength bonus (0-30 points)
        # Higher initial density relative to average = stronger
        if density.relative_strength:
            if density.relative_strength > Decimal("5"):
                score += Decimal("30")
            elif density.relative_strength > Decimal("3"):
                score += Decimal("20")
            elif density.relative_strength > Decimal("2"):
                score += Decimal("10")
        
        # Cap at 100
        final_score = min(score, Decimal("100"))
        
        self.logger.debug(
            "breakout_strength_calculated",
            price_level=float(density.price_level),
            side=density.side.value,
            erosion=float(erosion),
            is_cluster=density.is_cluster,
            relative_strength=float(density.relative_strength) if density.relative_strength else None,
            score=float(final_score),
        )
        
        return final_score
        
    def get_breakout_direction(self, density: Density) -> str:
        """
        Determine the breakout direction based on which side was broken.
        
        If BID density breaks (price moved down through it): potential SHORT
        If ASK density breaks (price moved up through it): potential LONG
        
        Args:
            density: The broken density
            
        Returns:
            "LONG" or "SHORT" indicating the breakout direction
        """
        if density.side == OrderSide.BID:
            direction = "SHORT"
        else:  # OrderSide.ASK
            direction = "LONG"
        
        self.logger.debug(
            "breakout_direction_determined",
            price_level=float(density.price_level),
            side=density.side.value,
            direction=direction,
        )
        
        return direction
        
    def analyze_broken_densities(self, symbol: str) -> dict:
        """
        Perform comprehensive analysis of all broken densities for a symbol.
        
        Returns a summary with:
        - All broken densities
        - Strongest broken density per side
        - Breakout strength scores
        - Recommended direction
        
        Args:
            symbol: Trading symbol to analyze
            
        Returns:
            Dictionary containing analysis results
        """
        broken = self.get_broken_densities(symbol)
        
        if not broken:
            self.logger.debug(
                "no_broken_densities_for_analysis",
                symbol=symbol,
            )
            return {
                "symbol": symbol,
                "broken_densities": [],
                "strongest_bid": None,
                "strongest_ask": None,
                "recommended_direction": None,
                "max_strength_score": Decimal("0"),
            }
        
        # Get strongest on each side
        strongest_bid = self.get_strongest_broken_density(symbol, OrderSide.BID)
        strongest_ask = self.get_strongest_broken_density(symbol, OrderSide.ASK)
        
        # Calculate strength scores for all broken densities
        density_scores = [
            {
                "density": d,
                "strength_score": self.calculate_breakout_strength(d),
                "direction": self.get_breakout_direction(d),
            }
            for d in broken
        ]
        
        # Find the strongest overall
        if density_scores:
            strongest_overall = max(density_scores, key=lambda x: x["strength_score"])
            recommended_direction = strongest_overall["direction"]
            max_strength = strongest_overall["strength_score"]
        else:
            recommended_direction = None
            max_strength = Decimal("0")
        
        analysis = {
            "symbol": symbol,
            "broken_densities": broken,
            "density_scores": density_scores,
            "strongest_bid": strongest_bid,
            "strongest_ask": strongest_ask,
            "recommended_direction": recommended_direction,
            "max_strength_score": max_strength,
        }
        
        self.logger.info(
            "broken_densities_analysis_complete",
            symbol=symbol,
            broken_count=len(broken),
            bid_broken=len([d for d in broken if d.side == OrderSide.BID]),
            ask_broken=len([d for d in broken if d.side == OrderSide.ASK]),
            recommended_direction=recommended_direction,
            max_strength_score=float(max_strength),
        )
        
        return analysis
        
    def update_erosion_threshold(self, new_threshold: Decimal) -> None:
        """
        Update the erosion threshold percentage.
        
        Args:
            new_threshold: New erosion threshold percentage
        """
        old_threshold = self.erosion_threshold_percent
        self.erosion_threshold_percent = new_threshold
        
        self.logger.info(
            "erosion_threshold_updated",
            old_threshold=float(old_threshold),
            new_threshold=float(new_threshold),
        )
