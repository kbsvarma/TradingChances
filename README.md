# Polymarket CLOB Arbitrage Bot (MVP v1)

Deterministic asyncio bot scaffold for Polymarket CLOB inefficiency capture:

`executable_edge = 1 - (best_yes + best_no) - fee_rate - slippage(size) - failure_buffer`

Trade only when `executable_edge > min_edge_threshold`.

## Safety Defaults

- `DRY_RUN=true` by default.
- `START_PAUSED=true` by default.
- Explicit `resume` command required to place live orders.
- Kill switch and loss limits enabled in both `live` and `backtest` mode.

## Features in v1 scaffold

- Single-writer asyncio trading engine.
- Public (`market`) and private (`user`) websocket clients.
- Sequence-independent resync flow stub (REST snapshot + local rehydrate).
- Deterministic strategy and pluggable slippage model.
- Risk manager with circuit breakers and kill-switch state machine.
- Order manager with lifecycle state machine, dedupe, TTL cancel, and cancel-churn governor.
- Adaptive rate limiting for `/order` and `/order` cancel limits.
- SQLite WAL persistence with async write buffer.
- Periodic snapshots for positions, PnL, latency metrics, and sparse book states.
- Backtest mode replaying recorded events.
- Structured JSON logs.
- CommandBus abstraction + CLI control API.

## Repository Layout

- `config.yaml`: strategy/risk/order/rate-limit thresholds.
- `.env.example`: required environment variables.
- `migrations/001_init.sql`: SQLite schema migration.
- `src/polymarket_arb/`: engine modules.
- `tests/`: unit tests for `RiskManager` and `OrderManager`.

## Setup

1. Create environment and install:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

2. Configure environment:

```bash
cp .env.example .env
# edit .env keys
```

3. Review and adjust `config.yaml` thresholds.

## Required Environment Variables

- `CLOB_WS_URL`
- `CLOB_REST_URL`
- `CHAIN_ID`
- `SIGNATURE_TYPE`
- `PRIVATE_KEY`
- `CLOB_API_KEY`
- `CLOB_API_SECRET`
- `CLOB_API_PASSPHRASE`
- `MARKETS` (comma-separated)
- `DRY_RUN`
- `START_PAUSED`
- `BOT_MODE`
- `DB_PATH`

## Run

### Live Mode (safe default)

```bash
python -m polymarket_arb --config config.yaml --mode live
```

CLI commands:

- `pause`
- `resume`
- `flatten`
- `reload`
- `set min_edge_threshold=0.01 failure_buffer=0.002`
- `backtest`
- `markets on market_1,market_2`
- `markets off market_2`
- `stop`

### Backtest Mode

```bash
python -m polymarket_arb --config config.yaml --mode backtest
```

Backtest replays recorded `events` from SQLite and reports metrics JSON.

## Operational Runbook (AWS us-east VPS)

1. Start dry-run and paused.
2. Verify WS connectivity and event ingestion in logs.
3. Verify DB writes (`events`, `order_intents`, `orders`).
4. `resume` for controlled trading window.
5. Watch reject/latency/drawdown; pause on anomaly.
6. Use `flatten` before maintenance.

## Notes on Polymarket Integration

- Intended place/cancel path uses `py-clob-client` for EIP-712 order signing.
- If `py-clob-client` import/init fails, adapter forces `dry_run` and logs error.
- Gasless builder-relayer flow is intentionally not in v1 critical path.

## Testing

```bash
pytest -q
```

Covers:

- `RiskManager` limits and kill-switch behavior.
- `OrderManager` dedupe, churn governor, TTL cancel.

## Next Hardening Steps

- Replace REST snapshot stub endpoints with exact Polymarket endpoint payload contracts.
- Add explicit user-channel auth signing handshake details.
- Add richer simulator (recorded fills + queue position model).
- Add Telegram adapter implementing `CommandAPI` protocol.
