from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(slots=True)
class RuntimeConfig:
    mode: str
    dry_run: bool
    start_paused: bool
    log_level: str
    event_queue_maxsize: int
    event_queue_high_watermark: int


@dataclass(slots=True)
class RiskConfig:
    max_position_per_market: float
    max_total_exposure: float
    max_hourly_loss: float
    max_daily_loss: float
    max_open_orders_per_market: int
    p95_latency_ms_limit: int
    reject_rate_limit: float
    drawdown_limit: float
    ws_health_timeout_sec: int
    picked_off_spike_count: int
    picked_off_window_sec: int
    picked_off_freshness_ms: int


@dataclass(slots=True)
class OrderConfig:
    default_ttl_ms: int
    min_order_lifetime_ms: int
    max_cancels_per_sec_per_market: int
    intent_price_epsilon: float
    intent_size_epsilon: float


@dataclass(slots=True)
class ThresholdConfig:
    min_edge_threshold: float
    failure_buffer: float
    default_fee_rate: float
    max_slippage_bps: float


@dataclass(slots=True)
class PersistenceConfig:
    db_path: str
    flush_interval_sec: int
    buffer_maxsize: int
    buffer_high_watermark: int
    flush_timeout_sec: int


@dataclass(slots=True)
class TradingSafetyConfig:
    flatten_mode: str


@dataclass(slots=True)
class MarketValidationConfig:
    allow_nonstandard_yes_no_labels: bool


@dataclass(slots=True)
class SnapshotConfig:
    require_nonempty_active_book: bool
    max_level_size: float | None


@dataclass(slots=True)
class SafetyConfig:
    user_ws_timeout_sec: int
    edge_decay_min_ratio: float
    edge_decay_min_trades: int
    edge_decay_window_size: int
    slippage_multiplier: float
    slippage_window_size: int


@dataclass(slots=True)
class BotConfig:
    runtime: RuntimeConfig
    markets: list[str]
    thresholds: ThresholdConfig
    risk: RiskConfig
    order: OrderConfig
    persistence: PersistenceConfig
    trading_safety: TradingSafetyConfig
    market_validation: MarketValidationConfig
    snapshot: SnapshotConfig
    safety: SafetyConfig
    gamma_url: str
    raw: dict[str, Any]


def _deep_get(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        cur = cur[part]
    return cur


def load_config(config_path: str = "config.yaml") -> BotConfig:
    load_dotenv()
    data = yaml.safe_load(Path(config_path).read_text())

    markets_env = os.getenv("MARKETS")
    markets = [m.strip() for m in markets_env.split(",")] if markets_env else data["markets"]["enabled"]

    runtime = RuntimeConfig(
        mode=os.getenv("BOT_MODE", str(_deep_get(data, "runtime.mode"))),
        dry_run=os.getenv("DRY_RUN", str(_deep_get(data, "runtime.dry_run"))).lower() == "true",
        start_paused=os.getenv("START_PAUSED", str(_deep_get(data, "runtime.start_paused"))).lower() == "true",
        log_level=os.getenv("LOG_LEVEL", str(_deep_get(data, "runtime.log_level"))),
        event_queue_maxsize=int(_deep_get(data, "runtime.event_queue_maxsize")),
        event_queue_high_watermark=int(_deep_get(data, "runtime.event_queue_high_watermark")),
    )
    risk = RiskConfig(**data["risk"])
    order = OrderConfig(**data["order"])
    thresholds = ThresholdConfig(**data["thresholds"])
    persistence = PersistenceConfig(
        db_path=os.getenv("DB_PATH", str(_deep_get(data, "persistence.db_path"))),
        flush_interval_sec=int(_deep_get(data, "persistence.flush_interval_sec")),
        buffer_maxsize=int(_deep_get(data, "persistence.buffer_maxsize")),
        buffer_high_watermark=int(_deep_get(data, "persistence.buffer_high_watermark")),
        flush_timeout_sec=int(_deep_get(data, "persistence.flush_timeout_sec")),
    )
    trading_safety = TradingSafetyConfig(flatten_mode=str(_deep_get(data, "trading_safety.flatten_mode")))
    market_validation = MarketValidationConfig(
        allow_nonstandard_yes_no_labels=bool(_deep_get(data, "markets.allow_nonstandard_yes_no_labels"))
    )
    raw_max_level_size = _deep_get(data, "snapshot.max_level_size")
    snapshot = SnapshotConfig(
        require_nonempty_active_book=bool(_deep_get(data, "snapshot.require_nonempty_active_book")),
        max_level_size=None if raw_max_level_size is None else float(raw_max_level_size),
    )
    safety = SafetyConfig(
        user_ws_timeout_sec=int(_deep_get(data, "safety.user_ws_timeout_sec")),
        edge_decay_min_ratio=float(_deep_get(data, "safety.edge_decay_min_ratio")),
        edge_decay_min_trades=int(_deep_get(data, "safety.edge_decay_min_trades")),
        edge_decay_window_size=int(_deep_get(data, "safety.edge_decay_window_size")),
        slippage_multiplier=float(_deep_get(data, "safety.slippage_multiplier")),
        slippage_window_size=int(_deep_get(data, "safety.slippage_window_size")),
    )

    return BotConfig(
        runtime=runtime,
        markets=markets,
        thresholds=thresholds,
        risk=risk,
        order=order,
        persistence=persistence,
        trading_safety=trading_safety,
        market_validation=market_validation,
        snapshot=snapshot,
        safety=safety,
        gamma_url=os.getenv("GAMMA_API_URL", str(_deep_get(data, "gamma.gamma_api_url"))),
        raw=data,
    )


def required_env() -> dict[str, str]:
    return {
        "CLOB_WS_URL": os.getenv("CLOB_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/"),
        "CLOB_REST_URL": os.getenv("CLOB_REST_URL", "https://clob.polymarket.com"),
        "CHAIN_ID": os.getenv("CHAIN_ID", "137"),
        "SIGNATURE_TYPE": os.getenv("SIGNATURE_TYPE", "EOA"),
        "PRIVATE_KEY": os.getenv("PRIVATE_KEY", ""),
        "CLOB_API_KEY": os.getenv("CLOB_API_KEY", ""),
        "CLOB_API_SECRET": os.getenv("CLOB_API_SECRET", ""),
        "CLOB_API_PASSPHRASE": os.getenv("CLOB_API_PASSPHRASE", ""),
    }
