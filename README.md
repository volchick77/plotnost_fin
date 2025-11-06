# Crypto Futures Trading Bot

Automated trading bot for crypto futures based on order book density analysis (breakout and bounce strategies).

## Features

- Real-time order book analysis via WebSocket
- Density-based trading signals (breakout & bounce)
- Dynamic symbol selection (top-40 active coins)
- Isolated margin for risk management
- Multi-level safety mechanisms
- PostgreSQL + TimescaleDB for data storage

## Strategy

The bot trades futures on Bybit (Binance and BitGet planned) using two main strategies:

1. **Breakout**: Enter when price breaks through a significant density level
2. **Bounce**: Enter when price bounces off a density level

Both strategies follow the current trend and use adaptive parameters per coin.

See [docs/plans/2025-11-06-trading-strategy.md](docs/plans/2025-11-06-trading-strategy.md) for details.

## Architecture

Modular monolith built with Python 3.11+ and asyncio for concurrent processing of 40+ symbols.

See [docs/plans/2025-11-06-architecture-design.md](docs/plans/2025-11-06-architecture-design.md) for details.

## Requirements

- Python 3.11+
- PostgreSQL 15+ with TimescaleDB extension
- Minimum 2GB RAM, 10GB disk
- Stable internet connection
- Bybit API keys with trading permissions

## Installation

### 1. Clone and setup virtual environment

```bash
git clone <repository>
cd plotnost_fin
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install and setup PostgreSQL + TimescaleDB

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install postgresql-15
sudo apt-get install timescaledb-2-postgresql-15

# Enable TimescaleDB
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

**Create database:**
```bash
sudo -u postgres createdb trading_bot
sudo -u postgres psql trading_bot -c "CREATE EXTENSION timescaledb;"
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your API keys and database credentials
```

### 4. Configure bot settings

Edit `config.yaml` to adjust trading parameters, strategy settings, and safety mechanisms.

### 5. Run database migrations

```bash
alembic upgrade head
```

### 6. Run the bot

```bash
python src/main.py
```

## Configuration

### API Keys

Get your Bybit API keys from: https://www.bybit.com/app/user/api-management

Required permissions:
- Read order book data
- Place and cancel orders
- Position management

**IMPORTANT**: Use isolated margin mode and start with minimum position sizes ($0.1) for testing!

### Trading Parameters

Key settings in `config.yaml`:

- `trading.position_size_usd`: Position size in USDT (default: 0.1 for testing)
- `trading.leverage`: Leverage multiplier (default: 10x)
- `trading.margin_mode`: Must be ISOLATED
- `trading.max_concurrent_positions`: Maximum open positions
- `strategy.*`: Various strategy parameters (thresholds, percentages, etc.)

Per-coin parameters are stored in the database (`coin_parameters` table) and can be updated on-the-fly.

## Safety Features

- **Isolated Margin**: Each position is isolated to prevent cascade liquidations
- **Emergency Close**: Automatically closes all positions if connection lost > 30 seconds
- **Stop-Loss Guarantee**: Never opens a position without setting stop-loss
- **Multi-level Validation**: Validates balance, limits, and parameters before each trade
- **Comprehensive Logging**: All critical events logged with structured JSON format

## Monitoring

Logs are written to `logs/trading_bot.log` with automatic rotation.

Log levels:
- **INFO**: Normal operations (position open/close, signals)
- **WARNING**: Potential issues (reconnections, skipped signals)
- **ERROR**: Recoverable errors (API errors, retries)
- **CRITICAL**: Serious issues (emergency close, SL failures)

## Database Schema

See [docs/plans/2025-11-06-architecture-design.md](docs/plans/2025-11-06-architecture-design.md) for complete schema.

Main tables:
- `trades`: Trade history
- `coin_parameters`: Per-coin strategy parameters
- `orderbook_snapshots`: Historical order book data (TimescaleDB)
- `densities`: Detected density history (TimescaleDB)
- `market_stats`: 24h market statistics
- `system_events`: System event log

## Testing

**CRITICAL**: This bot trades with real money on mainnet!

- Start with minimum position size ($0.1)
- Use isolated margin mode
- Test with 1-2 coins first
- Monitor carefully for the first week
- Gradually scale up based on results

## Development Status

- [x] Design and architecture
- [ ] Infrastructure (Storage, Config, Logging)
- [ ] Data Collection (WebSocket, Order Book)
- [ ] Market Analysis (Density detection, Signals)
- [ ] Trading Execution
- [ ] Position Monitoring
- [ ] Integration and Testing

## Roadmap

### Phase 1: Core Functionality (Current)
- Basic trading on Bybit
- Density detection and signals
- Risk management

### Phase 2: Enhancements
- Telegram notifications
- Web dashboard
- ML-based spoof detection

### Phase 3: Expansion
- Binance and BitGet integration
- Advanced pattern analysis
- Auto-parameter optimization

## License

Private project - All rights reserved

## Disclaimer

**USE AT YOUR OWN RISK**

This trading bot involves real money and leverage. Cryptocurrency futures trading carries substantial risk of loss. Past performance does not guarantee future results. Only trade with funds you can afford to lose.

The authors are not responsible for any financial losses incurred through the use of this software.

## Support

For issues and questions, contact: andygarry707@gmail.com
