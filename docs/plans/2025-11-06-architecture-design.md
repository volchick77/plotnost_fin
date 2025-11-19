# Архитектура и дизайн торгового бота

> **Note**: This document was created during initial design. Some implementation details may differ from the final code. For latest implementation, refer to the source code.

**Дата:** 2025-11-06
**Версия:** 1.0
**Проект:** Автоматизированный торговый бот для фьючерсов

---

## Обзор системы

Торговый бот реализован как **модульный монолит на Python** с использованием асинхронного программирования (asyncio). Все компоненты работают в едином процессе с одним event loop, обеспечивая высокую производительность и простоту разработки.

### Технологический стек

**Язык и runtime:**
- Python 3.11+
- asyncio для конкурентности
- Type hints для статической типизации

**Библиотеки для работы с биржей:**
- `pybit` - официальная Python библиотека для Bybit API
- `websockets` - для WebSocket соединений
- `aiohttp` - для асинхронных HTTP запросов

**База данных:**
- PostgreSQL 15+
- TimescaleDB расширение (для временных рядов)
- `asyncpg` - асинхронный драйвер PostgreSQL

**Конфигурация и логирование:**
- `PyYAML` - парсинг config.yaml
- `python-dotenv` - загрузка переменных окружения
- `structlog` - структурированное логирование

---

## Архитектура системы

### Общая схема

```
┌─────────────────────────────────────────────────────────────┐
│                         Main Process                        │
│                      (Python asyncio)                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐      ┌──────────────┐                   │
│  │   Config     │      │   Database   │                   │
│  │   Loader     │      │   Manager    │                   │
│  └──────────────┘      └──────────────┘                   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐ │
│  │            Data Collector Module                      │ │
│  │  - WebSocket connections (40+ symbols)               │ │
│  │  - Order book state management                       │ │
│  │  - Market statistics updates                         │ │
│  └───────────────────┬──────────────────────────────────┘ │
│                      │ asyncio.Queue                       │
│                      ▼                                      │
│  ┌──────────────────────────────────────────────────────┐ │
│  │            Market Analyzer Module                     │ │
│  │  - Dynamic symbol selection (top-40)                 │ │
│  │  - Density detection in order book                   │ │
│  │  - Trend determination (24h + order book)            │ │
│  │  - Local max/min tracking                            │ │
│  └───────────────────┬──────────────────────────────────┘ │
│                      │ asyncio.Queue                       │
│                      ▼                                      │
│  ┌──────────────────────────────────────────────────────┐ │
│  │            Signal Generator Module                    │ │
│  │  - Breakout signal detection                         │ │
│  │  - Bounce signal detection                           │ │
│  │  - Signal prioritization                             │ │
│  └───────────────────┬──────────────────────────────────┘ │
│                      │ asyncio.Queue                       │
│                      ▼                                      │
│  ┌──────────────────────────────────────────────────────┐ │
│  │            Order Executor Module                      │ │
│  │  - Position opening (validation, API calls)          │ │
│  │  - Stop-loss placement                               │ │
│  │  - Emergency position closing                        │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐ │
│  │            Position Monitor Module                    │ │
│  │  - Active position tracking                          │ │
│  │  - Stop-loss to breakeven management                 │ │
│  │  - Dynamic take-profit detection                     │ │
│  │  - Density erosion monitoring (for bounce)           │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Модульная структура

Приложение организовано по принципу **separation of concerns** - каждый модуль отвечает за свою область:

1. **Data Collector** - сбор рыночных данных
2. **Market Analyzer** - анализ данных и выявление паттернов
3. **Signal Generator** - генерация торговых сигналов
4. **Order Executor** - исполнение торговых операций
5. **Position Monitor** - управление открытыми позициями

---

## Компоненты системы

### 1. Data Collector (Сборщик данных)

**Ответственность:**
- Управление WebSocket подключениями к Bybit
- Поддержание актуального состояния order book в памяти
- Периодический запрос статистики монет

**Ключевые классы:**

```python
class BybitWebSocketManager:
    """Управляет WebSocket подключениями к Bybit"""

    async def connect_orderbook(self, symbol: str) -> None:
        """Подключение к orderbook stream для монеты"""

    async def disconnect_orderbook(self, symbol: str) -> None:
        """Отключение от orderbook stream"""

    async def handle_orderbook_update(self, data: dict) -> None:
        """Обработка обновления orderbook"""

    async def reconnect_with_backoff(self, symbol: str) -> None:
        """Переподключение с exponential backoff"""

class OrderBookManager:
    """Управляет состоянием order book для всех монет"""

    def __init__(self):
        self.orderbooks: dict[str, OrderBook] = {}
        self.update_queue: asyncio.Queue = asyncio.Queue()

    def update_orderbook(self, symbol: str, bids: list, asks: list) -> None:
        """Обновление orderbook для монеты"""

    def get_orderbook(self, symbol: str) -> OrderBook:
        """Получение текущего состояния orderbook"""

    async def snapshot_to_db(self) -> None:
        """Периодическое сохранение snapshot в БД"""

class MarketStatsFetcher:
    """Получает статистику монет для динамического выбора"""

    async def fetch_24h_stats(self) -> list[dict]:
        """Запрос 24h статистики всех монет"""

    async def update_top_symbols(self) -> tuple[list[str], list[str]]:
        """Обновление списка топ-20 растущих и падающих"""
```

**WebSocket стратегия:**

- Отдельный WebSocket stream для каждой активной монеты
- Глубина orderbook: 50 уровней (баланс между детальностью и нагрузкой)
- При обновлении → event в `asyncio.Queue` для Market Analyzer
- Automatic reconnection с exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s

**Обработка обрыва соединения:**

```python
async def maintain_connection(self, symbol: str):
    """Бесконечный цикл поддержания соединения"""
    retry_delay = 1
    max_delay = 30

    while symbol in self.active_symbols:
        try:
            await self.connect_orderbook(symbol)
            retry_delay = 1  # Reset on successful connection
        except Exception as e:
            logger.error(f"Connection lost for {symbol}: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)

            # Emergency check
            if await self.has_open_positions() and retry_delay >= 30:
                await self.emergency_close_all_positions()
```

### 2. Market Analyzer (Анализатор рынка)

**Ответственность:**
- Выбор активных монет (топ-40)
- Обнаружение плотностей в order book
- Определение направления тренда
- Отслеживание локальных максимумов/минимумов

**Ключевые классы:**

```python
class DynamicSymbolSelector:
    """Выбирает топ-40 монет для торговли"""

    async def update_active_symbols(self) -> None:
        """Обновление списка каждые 5-10 минут"""

    def rank_symbols(self, stats: list[dict]) -> dict:
        """Ранжирование по объему и изменению цены"""

    def get_top_gainers(self, n: int = 20) -> list[str]:
        """Топ-N растущих монет"""

    def get_top_losers(self, n: int = 20) -> list[str]:
        """Топ-N падающих монет"""

class DensityAnalyzer:
    """Обнаруживает плотности в order book"""

    def detect_densities(self, orderbook: OrderBook,
                        params: CoinParameters) -> list[Density]:
        """Определение плотностей по критериям"""

    def check_absolute_volume(self, level: PriceLevel,
                             threshold: float) -> bool:
        """Проверка абсолютного объема"""

    def check_relative_volume(self, level: PriceLevel,
                             neighbors: list[PriceLevel],
                             multiplier: float) -> bool:
        """Проверка относительного объема"""

    def check_percent_of_total(self, level: PriceLevel,
                              total_volume: float,
                              threshold: float) -> bool:
        """Проверка процента от общего объема"""

    def detect_clusters(self, levels: list[PriceLevel],
                       price_range_percent: float) -> list[Cluster]:
        """Обнаружение кластеров ордеров"""

class TrendAnalyzer:
    """Определяет направление тренда"""

    def determine_trend(self, symbol: str,
                       orderbook: OrderBook,
                       stats_24h: dict) -> Trend:
        """Определение тренда по двум факторам"""

    def analyze_price_change_24h(self, stats: dict) -> TrendDirection:
        """Анализ изменения цены за 24h"""

    def analyze_orderbook_pressure(self, orderbook: OrderBook) -> TrendDirection:
        """Анализ давления в orderbook"""

class LocalExtremaTracker:
    """Отслеживает локальные максимумы/минимумы"""

    def update(self, symbol: str, price: float, timestamp: datetime) -> None:
        """Обновление при новой цене"""

    def get_local_max(self, symbol: str, hours: int = 4) -> float:
        """Локальный максимум за N часов"""

    def get_local_min(self, symbol: str, hours: int = 4) -> float:
        """Локальный минимум за N часов"""

    def is_new_territory(self, symbol: str, price: float,
                        direction: str) -> bool:
        """Проверка движения в новую территорию"""
```

**Алгоритм обнаружения плотности:**

```python
def detect_densities(self, orderbook: OrderBook, params: CoinParameters) -> list[Density]:
    densities = []

    # Проверяем bid и ask отдельно
    for side in ['bid', 'ask']:
        levels = orderbook.bids if side == 'bid' else orderbook.asks
        total_volume = sum(level.volume for level in levels)

        # Сначала находим кластеры
        clusters = self.detect_clusters(levels, params.cluster_range_percent)

        # Проверяем каждый уровень и кластер
        for item in levels + clusters:
            # Критерий 1: Абсолютный объем
            if not self.check_absolute_volume(item, params.density_threshold_abs):
                continue

            # Критерий 2: Относительный объем
            neighbors = self.get_neighbors(item, levels, n=5)
            if not self.check_relative_volume(item, neighbors,
                                            params.density_threshold_relative):
                continue

            # Критерий 3: Процент от общего
            if not self.check_percent_of_total(item, total_volume,
                                              params.density_threshold_percent):
                continue

            # Все критерии пройдены → это плотность
            densities.append(Density(
                price_level=item.price,
                volume=item.volume,
                side=side,
                timestamp=datetime.now()
            ))

    return densities
```

### 3. Signal Generator (Генератор сигналов)

**Ответственность:**
- Генерация сигналов на пробой
- Генерация сигналов на отскок
- Приоритизация сигналов

**Ключевые классы:**

```python
class SignalGenerator:
    """Генерирует торговые сигналы"""

    def __init__(self):
        self.signal_queue: asyncio.Queue = asyncio.Queue()

    async def process_market_updates(self) -> None:
        """Бесконечный цикл обработки обновлений"""

    def check_breakout_signal(self, symbol: str,
                             density: Density,
                             trend: Trend,
                             orderbook: OrderBook) -> Optional[Signal]:
        """Проверка условий для сигнала пробоя"""

    def check_bounce_signal(self, symbol: str,
                           density: Density,
                           trend: Trend,
                           orderbook: OrderBook) -> Optional[Signal]:
        """Проверка условий для сигнала отскока"""

    def prioritize_signal(self, signal: Signal,
                         local_extrema: LocalExtremaTracker) -> float:
        """Приоритет сигнала (выше = лучше)"""

class BreakoutDetector:
    """Детектор сигналов пробоя"""

    def __init__(self):
        self.density_tracker: dict[str, list[Density]] = {}

    def track_density_erosion(self, symbol: str, density: Density,
                             current_volume: float) -> float:
        """Отслеживание разъедания плотности"""

    def is_breakout_conditions_met(self, symbol: str,
                                   density: Density,
                                   trend: Trend,
                                   erosion_percent: float) -> bool:
        """Проверка всех условий пробоя"""

class BounceDetector:
    """Детектор сигналов отскока"""

    def is_price_touching_density(self, price: float,
                                  density_level: float,
                                  tolerance_percent: float = 0.2) -> bool:
        """Проверка касания цены к плотности"""

    def is_density_stable(self, density: Density,
                         history: list[float]) -> bool:
        """Проверка стабильности плотности"""

    def is_trading_activity_low(self, orderbook: OrderBook,
                                density: Density) -> bool:
        """Проверка низкой торговой активности"""
```

**Логика генерации сигнала пробоя:**

```python
def check_breakout_signal(self, symbol: str, density: Density,
                         trend: Trend, orderbook: OrderBook) -> Optional[Signal]:
    # Условие 1: Тренд совпадает с направлением пробоя
    if density.side == 'ask' and trend.direction != TrendDirection.UP:
        return None
    if density.side == 'bid' and trend.direction != TrendDirection.DOWN:
        return None

    # Условие 2: Плотность разъедена на 30%
    current_volume = orderbook.get_volume_at_level(density.price_level, density.side)
    erosion = (density.initial_volume - current_volume) / density.initial_volume * 100

    if erosion < 30:  # Настраиваемый параметр
        return None

    # Условие 3: Цена пробила уровень
    current_price = orderbook.get_mid_price()
    if density.side == 'ask' and current_price <= density.price_level:
        return None
    if density.side == 'bid' and current_price >= density.price_level:
        return None

    # Бонус: Проверка новой территории (повышает приоритет)
    is_new_territory = self.local_extrema_tracker.is_new_territory(
        symbol, current_price,
        'up' if density.side == 'ask' else 'down'
    )

    return Signal(
        type=SignalType.BREAKOUT,
        symbol=symbol,
        direction='LONG' if density.side == 'ask' else 'SHORT',
        entry_price=current_price,
        density=density,
        priority=2.0 if is_new_territory else 1.0,
        timestamp=datetime.now()
    )
```

### 4. Order Executor (Исполнитель ордеров)

**Ответственность:**
- Валидация перед открытием позиции
- Выставление рыночных ордеров
- Установка stop-loss
- Экстренное закрытие позиций

**Ключевые классы:**

```python
class OrderExecutor:
    """Исполняет торговые операции"""

    def __init__(self, bybit_client):
        self.client = bybit_client
        self.active_positions: dict[str, Position] = {}

    async def execute_signal(self, signal: Signal) -> Optional[Position]:
        """Исполнение сигнала"""

    async def validate_before_entry(self, signal: Signal) -> bool:
        """Валидация перед входом"""

    async def open_position(self, signal: Signal) -> Position:
        """Открытие позиции"""

    async def set_stop_loss(self, position: Position, price: float) -> bool:
        """Установка stop-loss"""

    async def close_position(self, position: Position, reason: str) -> TradeResult:
        """Закрытие позиции"""

    async def emergency_close_all(self) -> None:
        """Экстренное закрытие всех позиций"""

class PositionValidator:
    """Валидация параметров позиции"""

    def check_balance_sufficient(self, required_margin: float) -> bool:
        """Проверка достаточности баланса"""

    def check_min_position_size(self, symbol: str, size: float) -> bool:
        """Проверка минимального размера для монеты"""

    def check_isolated_margin_available(self, symbol: str) -> bool:
        """Проверка возможности изолированной маржи"""

    def check_max_positions_limit(self, current_count: int, max_limit: int) -> bool:
        """Проверка лимита одновременных позиций"""
```

**Процесс открытия позиции:**

```python
async def execute_signal(self, signal: Signal) -> Optional[Position]:
    # Шаг 1: Валидация
    if not await self.validate_before_entry(signal):
        logger.warning(f"Signal validation failed: {signal}")
        return None

    # Шаг 2: Проверка лимитов
    if len(self.active_positions) >= self.config.max_concurrent_positions:
        logger.info(f"Max positions limit reached, skipping signal")
        return None

    try:
        # Шаг 3: Открытие позиции
        position = await self.open_position(signal)

        # Шаг 4: Установка stop-loss (КРИТИЧНО!)
        stop_loss_price = self.calculate_stop_loss(signal)
        success = await self.set_stop_loss(position, stop_loss_price)

        if not success:
            # Если не смогли выставить SL → закрываем позицию
            logger.critical(f"Failed to set stop-loss, closing position {position.id}")
            await self.close_position(position, reason="stop_loss_failed")
            return None

        # Шаг 5: Регистрация позиции
        self.active_positions[position.id] = position
        await self.save_to_db(position)

        logger.info(f"Position opened: {position}")
        return position

    except Exception as e:
        logger.error(f"Failed to execute signal: {e}")
        return None
```

**Экстренное закрытие (при потере связи > 30 сек):**

```python
async def emergency_close_all(self) -> None:
    logger.critical("EMERGENCY: Closing all positions due to connection loss")

    tasks = []
    for position in self.active_positions.values():
        task = self.close_position(position, reason="emergency_connection_loss")
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            logger.critical(f"Failed emergency close: {result}")

    self.active_positions.clear()
```

### 5. Position Monitor (Монитор позиций)

**Ответственность:**
- Отслеживание открытых позиций
- Перевод stop-loss в безубыток
- Обнаружение условий для take-profit
- Мониторинг разъедания плотности (для bounce)

**Ключевые классы:**

```python
class PositionMonitor:
    """Мониторит открытые позиции"""

    def __init__(self, executor: OrderExecutor):
        self.executor = executor
        self.monitors: dict[str, asyncio.Task] = {}

    async def start_monitoring(self, position: Position) -> None:
        """Запуск мониторинга позиции"""

    async def monitor_position(self, position: Position) -> None:
        """Основной цикл мониторинга"""

    async def check_breakeven_move(self, position: Position) -> bool:
        """Проверка условий для перевода в безубыток"""

    async def check_take_profit_conditions(self, position: Position) -> bool:
        """Проверка условий для закрытия с прибылью"""

    async def check_density_erosion(self, position: Position) -> bool:
        """Проверка разъедания плотности (для bounce)"""

class TakeProfitAnalyzer:
    """Анализирует условия для фиксации прибыли"""

    def is_movement_slowing(self, price_history: list[float]) -> bool:
        """Замедление движения"""

    def is_counter_density_detected(self, orderbook: OrderBook,
                                   direction: str) -> bool:
        """Обнаружена встречная плотность"""

    def is_aggressive_counter_orders(self, orderbook: OrderBook,
                                    direction: str) -> bool:
        """Агрессивные встречные ордера"""

    def is_returning_to_known_levels(self, current_price: float,
                                    local_extrema: LocalExtremaTracker,
                                    direction: str) -> bool:
        """Возврат на проторенные уровни"""
```

**Цикл мониторинга позиции:**

```python
async def monitor_position(self, position: Position) -> None:
    logger.info(f"Started monitoring position {position.id}")

    breakeven_moved = False

    while position.id in self.executor.active_positions:
        try:
            # Получаем текущую цену
            current_price = await self.get_current_price(position.symbol)
            current_profit_percent = position.calculate_profit_percent(current_price)

            # Проверка 1: Перевод в безубыток (если еще не сделали)
            if not breakeven_moved and current_profit_percent >= 0.5:
                await self.move_stop_to_breakeven(position)
                breakeven_moved = True
                logger.info(f"Stop-loss moved to breakeven for {position.id}")

            # Проверка 2: Разъедание плотности (только для bounce)
            if position.signal_type == SignalType.BOUNCE:
                if await self.check_density_erosion(position):
                    logger.info(f"Density eroded 60-70%, closing {position.id}")
                    await self.executor.close_position(position,
                                                      reason="density_erosion")
                    break

            # Проверка 3: Условия для take-profit
            if await self.check_take_profit_conditions(position):
                logger.info(f"Take-profit conditions met for {position.id}")
                await self.executor.close_position(position,
                                                  reason="take_profit")
                break

            # Ждем перед следующей проверкой
            await asyncio.sleep(1)  # Проверяем каждую секунду

        except Exception as e:
            logger.error(f"Error monitoring position {position.id}: {e}")
            await asyncio.sleep(5)
```

---

## База данных

### Схема PostgreSQL + TimescaleDB

**1. trades (История сделок)**

```sql
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(20) NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    entry_price DECIMAL(20, 8) NOT NULL,
    exit_price DECIMAL(20, 8),
    position_size DECIMAL(20, 8) NOT NULL,
    leverage INTEGER NOT NULL,
    signal_type VARCHAR(20) NOT NULL,  -- 'breakout' | 'bounce'
    profit_loss DECIMAL(20, 8),
    profit_loss_percent DECIMAL(10, 4),
    stop_loss_price DECIMAL(20, 8) NOT NULL,
    stop_loss_triggered BOOLEAN DEFAULT FALSE,
    exit_reason VARCHAR(50),  -- 'take_profit', 'stop_loss', 'emergency', etc.
    parameters_snapshot JSONB,  -- Параметры на момент входа
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_entry_time ON trades(entry_time);
CREATE INDEX idx_trades_signal_type ON trades(signal_type);
```

**2. coin_parameters (Параметры для каждой монеты)**

```sql
CREATE TABLE coin_parameters (
    symbol VARCHAR(20) PRIMARY KEY,

    -- Пороги для определения плотности
    density_threshold_abs DECIMAL(20, 2) DEFAULT 50000,
    density_threshold_relative DECIMAL(5, 2) DEFAULT 3.0,
    density_threshold_percent DECIMAL(5, 2) DEFAULT 5.0,

    -- Параметры стратегии
    breakout_eaten_percent DECIMAL(5, 2) DEFAULT 30.0,
    bounce_density_stable_percent DECIMAL(5, 2) DEFAULT 10.0,

    -- Предпочтения
    preferred_strategy VARCHAR(20) DEFAULT 'both',  -- 'breakout' | 'bounce' | 'both'

    -- Управление
    enabled BOOLEAN DEFAULT TRUE,

    -- Метаданные
    updated_at TIMESTAMP DEFAULT NOW(),
    notes TEXT
);
```

**3. orderbook_snapshots (TimescaleDB hypertable)**

```sql
CREATE TABLE orderbook_snapshots (
    time TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    bids JSONB NOT NULL,  -- [{price: "50000", volume: "1.5"}, ...]
    asks JSONB NOT NULL,
    total_bid_volume DECIMAL(20, 8),
    total_ask_volume DECIMAL(20, 8),
    mid_price DECIMAL(20, 8)
);

-- Преобразуем в TimescaleDB hypertable
SELECT create_hypertable('orderbook_snapshots', 'time');

-- Индексы
CREATE INDEX idx_obs_symbol_time ON orderbook_snapshots(symbol, time DESC);

-- Retention policy: храним 30 дней
SELECT add_retention_policy('orderbook_snapshots', INTERVAL '30 days');
```

**4. densities (TimescaleDB hypertable)**

```sql
CREATE TABLE densities (
    time TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    price_level DECIMAL(20, 8) NOT NULL,
    side VARCHAR(4) NOT NULL,  -- 'bid' | 'ask'
    volume DECIMAL(20, 8) NOT NULL,
    volume_percent DECIMAL(5, 2),  -- % от общего объема стакана
    relative_strength DECIMAL(5, 2),  -- во сколько раз больше среднего
    is_cluster BOOLEAN DEFAULT FALSE,
    appeared_at TIMESTAMP,
    disappeared_at TIMESTAMP
);

SELECT create_hypertable('densities', 'time');

CREATE INDEX idx_densities_symbol_time ON densities(symbol, time DESC);
CREATE INDEX idx_densities_price ON densities(price_level);

SELECT add_retention_policy('densities', INTERVAL '60 days');
```

**5. system_events (Логи системы)**

```sql
CREATE TABLE system_events (
    id SERIAL PRIMARY KEY,
    time TIMESTAMP NOT NULL DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,  -- 'info' | 'warning' | 'error' | 'critical'
    symbol VARCHAR(20),
    details JSONB,
    message TEXT
);

CREATE INDEX idx_events_time ON system_events(time DESC);
CREATE INDEX idx_events_severity ON system_events(severity);
CREATE INDEX idx_events_type ON system_events(event_type);
```

**6. market_stats (Статистика монет)**

```sql
CREATE TABLE market_stats (
    symbol VARCHAR(20) PRIMARY KEY,
    volume_24h DECIMAL(20, 2),
    price_change_24h_percent DECIMAL(10, 4),
    current_price DECIMAL(20, 8),
    is_active BOOLEAN DEFAULT FALSE,  -- входит ли в топ-40
    rank INTEGER,  -- позиция в рейтинге
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_market_stats_active ON market_stats(is_active, rank);
```

### Стратегия работы с БД

**In-memory данные (быстрый доступ):**
- Текущее состояние orderbook для всех активных монет
- Параметры монет (coin_parameters) - кэшируются при старте
- Открытые позиции
- Последние обнаруженные плотности

**Периодическая запись:**
- Orderbook snapshots: каждые 5-10 минут (фоновая задача)
- Densities: при появлении/изменении/исчезновении
- Market stats: каждые 5-10 минут

**Немедленная запись:**
- Trades: при открытии и закрытии позиции
- System events: при критических событиях

**Миграции:**
- Используем `alembic` для управления версиями схемы БД

---

## Конфигурация

### Структура config.yaml

```yaml
# Биржа
exchange:
  name: "bybit"
  testnet: false
  api_key_env: "BYBIT_API_KEY"
  api_secret_env: "BYBIT_API_SECRET"

# WebSocket
websocket:
  reconnect_delay_initial: 1
  reconnect_delay_max: 30
  orderbook_depth: 50
  snapshot_interval: 300

# Рыночная аналитика
market:
  update_interval: 300
  top_gainers_count: 20
  top_losers_count: 20
  min_24h_volume: 1000000

# Торговля
trading:
  position_size_usd: 0.1
  leverage: 10
  margin_mode: "ISOLATED"
  max_concurrent_positions: 10
  max_exposure_percent: 5

# Параметры стратегии по умолчанию
strategy:
  # Пробой
  breakout_erosion_percent: 30.0
  breakout_min_stop_loss_percent: 0.1
  breakout_breakeven_profit_percent: 0.5

  # Отскок
  bounce_touch_tolerance_percent: 0.2
  bounce_stop_loss_behind_density_percent: 0.3
  bounce_density_erosion_exit_percent: 65.0

  # Take-profit
  tp_slowdown_multiplier: 3.0
  tp_local_extrema_hours: 4

# Безопасность
safety:
  connection_loss_timeout: 30
  emergency_close_all: true
  require_stop_loss: true
  max_api_retries: 5

# База данных
database:
  host: "localhost"
  port: 5432
  name: "trading_bot"
  user_env: "DB_USER"
  password_env: "DB_PASSWORD"
  pool_size: 10

# Логирование
logging:
  level: "INFO"
  file: "logs/trading_bot.log"
  max_size_mb: 100
  backup_count: 10
  format: "json"  # structured logging
```

### .env файл (НЕ коммитить в git!)

```env
BYBIT_API_KEY=your_api_key_here
BYBIT_API_SECRET=your_api_secret_here
DB_USER=trading_bot
DB_PASSWORD=secure_password_here
```

---

## Структура проекта

```
plotnost_fin/
├── .env                          # API ключи (не в git)
├── .gitignore
├── config.yaml                   # Конфигурация
├── requirements.txt              # Python зависимости
├── README.md
│
├── docs/
│   └── plans/
│       ├── 2025-11-06-trading-strategy.md
│       └── 2025-11-06-architecture-design.md
│
├── src/
│   ├── __init__.py
│   ├── main.py                   # Точка входа
│   ├── config.py                 # Загрузка конфигурации
│   │
│   ├── data_collection/
│   │   ├── __init__.py
│   │   ├── bybit_websocket.py    # WebSocket менеджер
│   │   ├── orderbook_manager.py  # Управление состоянием orderbook
│   │   └── market_stats_fetcher.py       # Получение статистики
│   │
│   ├── analyzers/
│   │   ├── __init__.py
│   │   ├── symbol_selector.py    # Динамический выбор монет
│   │   ├── density_analyzer.py   # Обнаружение плотностей
│   │   ├── trend_analyzer.py     # Определение тренда
│   │   └── signal_generator.py   # Генерация сигналов
│   │
│   ├── executors/
│   │   ├── __init__.py
│   │   ├── order_executor.py     # Исполнение ордеров
│   │   └── position_validator.py # Валидация позиций
│   │
│   ├── monitors/
│   │   ├── __init__.py
│   │   ├── position_monitor.py   # Мониторинг позиций
│   │   └── take_profit.py        # Логика take-profit
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db_manager.py         # Асинхронный менеджер БД
│   │   ├── models.py             # Pydantic модели
│   │   └── migrations/           # Alembic миграции
│   │       └── versions/
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py             # Настройка логирования
│       ├── helpers.py            # Вспомогательные функции
│       └── types.py              # Типы и enums
│
├── logs/                         # Логи (не в git)
│   └── trading_bot.log
│
└── tests/                        # Тесты
    ├── __init__.py
    ├── test_analyzers/
    ├── test_executors/
    └── test_monitors/
```

---

## Главный файл main.py

```python
import asyncio
import signal
from typing import Optional

from src.config import load_config
from src.utils.logger import setup_logger
from src.storage.db_manager import DatabaseManager
from src.data_collectors.bybit_collector import BybitWebSocketManager
from src.data_collectors.orderbook_manager import OrderBookManager
from src.data_collectors.market_stats import MarketStatsFetcher
from src.analyzers.symbol_selector import DynamicSymbolSelector
from src.analyzers.density_analyzer import DensityAnalyzer
from src.analyzers.signal_generator import SignalGenerator
from src.executors.order_executor import OrderExecutor
from src.monitors.position_monitor import PositionMonitor

logger = setup_logger()


class TradingBot:
    """Главный класс торгового бота"""

    def __init__(self):
        self.config = load_config()
        self.db: Optional[DatabaseManager] = None
        self.ws_manager: Optional[BybitWebSocketManager] = None
        self.orderbook_manager: Optional[OrderBookManager] = None
        self.running = False

    async def start(self):
        """Запуск всех компонентов бота"""
        logger.info("Starting trading bot...")

        # Инициализация БД
        self.db = DatabaseManager(self.config.database)
        await self.db.connect()

        # Инициализация компонентов
        self.orderbook_manager = OrderBookManager(self.db)
        self.ws_manager = BybitWebSocketManager(
            self.config.exchange,
            self.orderbook_manager
        )

        market_stats = MarketStatsFetcher(self.config.exchange)
        symbol_selector = DynamicSymbolSelector(market_stats, self.db)
        density_analyzer = DensityAnalyzer(self.db)
        signal_generator = SignalGenerator(density_analyzer, self.db)
        order_executor = OrderExecutor(self.config.exchange, self.db)
        position_monitor = PositionMonitor(order_executor, self.orderbook_manager)

        # Запуск всех асинхронных задач
        self.running = True

        tasks = [
            # Обновление списка активных монет
            symbol_selector.run(),

            # Управление WebSocket подключениями
            self.ws_manager.maintain_connections(symbol_selector),

            # Периодическое сохранение snapshots
            self.orderbook_manager.periodic_snapshot_save(),

            # Генерация сигналов
            signal_generator.run(self.orderbook_manager),

            # Исполнение сигналов
            order_executor.run(signal_generator.signal_queue),

            # Мониторинг позиций
            position_monitor.run(),
        ]

        # Запускаем все задачи параллельно
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self):
        """Остановка бота"""
        logger.info("Stopping trading bot...")
        self.running = False

        # Закрытие WebSocket соединений
        if self.ws_manager:
            await self.ws_manager.close_all()

        # Закрытие БД
        if self.db:
            await self.db.disconnect()

        logger.info("Trading bot stopped")


async def main():
    bot = TradingBot()

    # Обработка сигналов для graceful shutdown
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Безопасность и обработка ошибок

### Многоуровневая защита

**Уровень 1: Валидация перед действием**
- Проверка баланса, лимитов, параметров перед каждым ордером
- Если валидация не прошла → логируем и пропускаем

**Уровень 2: Retry mechanism**
- API запросы с повторными попытками (exponential backoff)
- Максимум 5 попыток для некритичных операций
- Немедленные повторы для критичных (закрытие позиций)

**Уровень 3: Emergency shutdown**
- При потере связи > 30 сек + открытые позиции → закрытие всех позиций
- При критической ошибке API → остановка торговли, логирование

**Уровень 4: Логирование**
- Все критические события → CRITICAL level
- Все ошибки API → ERROR level
- Переподключения → WARNING level
- Обычная работа → INFO level

### Обработка WebSocket обрывов

```python
async def maintain_connection_with_safety(self, symbol: str):
    retry_delay = 1
    connection_lost_time = None

    while symbol in self.active_symbols:
        try:
            await self.connect_orderbook(symbol)
            retry_delay = 1
            connection_lost_time = None

        except Exception as e:
            if connection_lost_time is None:
                connection_lost_time = datetime.now()

            time_lost = (datetime.now() - connection_lost_time).total_seconds()

            # Проверка критического таймаута
            if time_lost >= 30 and await self.has_open_positions():
                logger.critical(f"Connection lost for {time_lost}s, emergency close")
                await self.emergency_close_all_positions()

            logger.error(f"Connection lost for {symbol}: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
```

---

## Развертывание

### Требования системы

- **OS:** Linux (Ubuntu 22.04+ рекомендуется)
- **Python:** 3.11+
- **PostgreSQL:** 15+ с TimescaleDB
- **RAM:** минимум 2GB, рекомендуется 4GB
- **Диск:** 10GB для БД и логов
- **Сеть:** Стабильное соединение, низкая latency к Bybit API

### Установка

**1. Клонирование и настройка окружения:**

```bash
git clone <repository>
cd plotnost_fin
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**2. Установка PostgreSQL и TimescaleDB:**

```bash
# Ubuntu
sudo apt-get install postgresql-15
sudo apt-get install timescaledb-2-postgresql-15

# Включение TimescaleDB
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

**3. Создание базы данных:**

```bash
sudo -u postgres createdb trading_bot
sudo -u postgres psql trading_bot -c "CREATE EXTENSION timescaledb;"
```

**4. Настройка конфигурации:**

```bash
cp .env.example .env
# Отредактировать .env с вашими ключами

cp config.example.yaml config.yaml
# Настроить параметры при необходимости
```

**5. Применение миграций:**

```bash
alembic upgrade head
```

**6. Запуск:**

```bash
python src/main.py
```

### Systemd сервис (для продакшна)

```ini
[Unit]
Description=Trading Bot
After=network.target postgresql.service

[Service]
Type=simple
User=trading
WorkingDirectory=/home/trading/plotnost_fin
Environment="PATH=/home/trading/plotnost_fin/venv/bin"
ExecStart=/home/trading/plotnost_fin/venv/bin/python src/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Мониторинг и логирование

### Структура логов

**Формат:** JSON для легкого парсинга

```json
{
  "timestamp": "2025-11-06T14:30:00Z",
  "level": "INFO",
  "module": "order_executor",
  "event": "position_opened",
  "symbol": "BTCUSDT",
  "position_id": "abc-123",
  "entry_price": 50000.50,
  "size": 0.1
}
```

**Уровни логирования:**

- **DEBUG:** Детальная информация для отладки (выключено в продакшне)
- **INFO:** Нормальная работа (вход/выход, обновления)
- **WARNING:** Потенциальные проблемы (переподключения, пропущенные сигналы)
- **ERROR:** Ошибки, которые не останавливают работу (API ошибки)
- **CRITICAL:** Критичные события (экстренное закрытие, невозможность SL)

### Метрики для отслеживания

**Real-time метрики (в логах):**
- Количество открытых позиций
- Количество WebSocket подключений
- Количество обработанных сигналов/час
- Латентность API запросов

**Бизнес метрики (из БД):**
- Win rate за день/неделю/месяц
- Profit factor
- Средняя прибыль/убыток
- Maximum drawdown

---

## Тестирование

### Уровни тестов

**1. Unit тесты:**
- Тестирование отдельных функций (density detection, signal generation)
- Использование mock данных

**2. Integration тесты:**
- Тестирование взаимодействия компонентов
- Использование тестовой БД

**3. Тестирование на продакшне:**
- Минимальные суммы ($0.1)
- Изолированная маржа
- Детальное логирование

### Пример unit теста

```python
import pytest
from src.analyzers.density_analyzer import DensityAnalyzer
from src.storage.models import OrderBook, PriceLevel, CoinParameters

def test_density_detection():
    analyzer = DensityAnalyzer(mock_db)

    # Создаем mock orderbook
    orderbook = OrderBook(
        symbol="BTCUSDT",
        bids=[
            PriceLevel(price=50000, volume=100000),  # Плотность
            PriceLevel(price=49990, volume=20000),
            PriceLevel(price=49980, volume=15000),
        ],
        asks=[...]
    )

    params = CoinParameters(
        density_threshold_abs=50000,
        density_threshold_relative=3.0,
        density_threshold_percent=5.0
    )

    densities = analyzer.detect_densities(orderbook, params)

    assert len(densities) == 1
    assert densities[0].price_level == 50000
    assert densities[0].side == 'bid'
```

---

## Будущие улучшения

### Фаза 1: Базовая функциональность (текущая)
- ✅ Модульная архитектура
- ✅ WebSocket для real-time данных
- ✅ Обнаружение плотностей
- ✅ Стратегии пробоя и отскока
- ✅ Базовый риск-менеджмент

### Фаза 2: Улучшения после тестирования
- Telegram бот для уведомлений
- Web dashboard (React + FastAPI backend)
- ML для детекции спуфинга
- Расширенная аналитика паттернов

### Фаза 3: Масштабирование
- Поддержка Binance и BitGet
- Микросервисная архитектура (если нужно)
- Распределенная обработка данных
- Auto-tuning параметров с ML

---

**Документ создан:** 2025-11-06
**Автор:** Claude Code
**Статус:** Готов к реализации
