# Future Improvements for Trading Bot

This document outlines enhancements that would improve the trading bot's performance but are not critical for initial production deployment.

---

## 1. Public Trade Stream for Accurate Aggressive Order Detection

**Current Implementation:** Uses orderbook bid/ask volume imbalance as a proxy for aggressive counter orders.

**Limitation:** Orderbook volume shows limit orders, not actual executed trades (market orders).

**Improvement:**
- Subscribe to Bybit `publicTrade.{symbol}` WebSocket stream
- Track executed trade volume in 5-10 second windows
- Detect sudden spikes in opposite-direction trades
- More accurate signal for reversal detection

**Implementation Effort:** Medium
- New component: `TradeHistoryManager`
- Modify: `BybitWebSocketManager` to add trade stream
- Modify: `PositionMonitor._check_aggressive_counter_orders()` to use trade data

**Priority:** Medium

---

## 2. LocalExtremaTracker for Precise Return-to-Range Detection

**Current Implementation:** Compares current price with `position.density_price` (the breakout level).

**Limitation:** Doesn't track true local max/min over 1-4 hour windows as specified in strategy.

**Improvement:**
- Implement `LocalExtremaTracker` class (see architecture doc lines 254-268)
- Track local maximum and minimum prices over configurable time windows
- Detect peak/valley using simple peak detection algorithm
- More accurate determination of "new territory" vs "known range"

**Implementation Effort:** Medium
- New component: `src/market_analysis/local_extrema_tracker.py`
- Integration with `OrderBookManager` or as standalone tracker
- Update `PositionMonitor._check_return_to_known_levels()`

**Priority:** Medium

---

## 3. Machine Learning for Spoofing Detection

**Current Implementation:** None - relies on density stability criteria.

**Improvement:**
- Collect features: density lifetime, volume changes, price action
- Train classifier to detect spoofing patterns
- Filter out likely-spoofing densities before signal generation
- Reduce false signals significantly

**Implementation Effort:** High
- Data collection pipeline
- ML model training infrastructure
- Feature engineering
- Model serving in production

**Priority:** Low (requires significant data collection first)

---

## 4. Multi-Symbol Correlation Analysis

**Current Implementation:** Each symbol traded independently.

**Improvement:**
- Track correlations between symbols (e.g., BTC/ETH)
- Adjust position sizing based on correlation
- Avoid over-exposure to correlated assets
- Portfolio-level risk management

**Implementation Effort:** High
- Correlation calculation engine
- Portfolio optimizer
- Risk model updates

**Priority:** Low (more relevant at higher capital scale)

---

## 5. Dynamic Parameter Optimization

**Current Implementation:** Static parameters in `coin_parameters` table.

**Improvement:**
- Collect performance metrics per symbol per strategy
- Periodically optimize parameters based on historical performance
- A/B testing framework for parameter changes
- Adaptive strategy that learns over time

**Implementation Effort:** High
- Analytics pipeline
- Optimization algorithms
- Backtesting framework
- Gradual rollout system

**Priority:** Medium (after 2-4 weeks of data collection)

---

## Implementation Roadmap

**Phase 1 (Current):** Production-ready baseline
- All critical TODOs resolved
- Real API integration
- 4-condition take-profit (hybrid approach)

**Phase 2 (1-2 months):**
- Public trade stream (#1)
- LocalExtremaTracker (#2)
- Performance monitoring dashboard

**Phase 3 (3-6 months):**
- Dynamic parameter optimization (#5)
- Advanced spoofing detection (#3)

**Phase 4 (6+ months):**
- Multi-symbol correlation (#4)
- ML-based enhancements
