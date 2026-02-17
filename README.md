# Polymarket CLOB Arbitrage Bot (MVP v1.3+ hardening)

Deterministic asyncio bot scaffold for Polymarket CLOB inefficiency capture:

`executable_edge = 1 - (best_yes + best_no) - fee_rate - slippage(size) - failure_buffer`

## Safety Defaults

- `DRY_RUN=true` default.
- `START_PAUSED=true` default.
- No LLM calls in critical trading path.
- Explicit `resume` required.
- Kill-switch circuit breakers always active.

## What Is Hardened

- Explicit YES/NO token mapping via `MarketRegistry` (no token sorting assumptions).
- User WS auth payload centralized in `wss_auth.py`.
- Snapshot resync via REST `GET /book?token_id=...`.
- Non-blocking order execution path: sync CLOB calls wrapped in `asyncio.to_thread` with semaphore.
- Quantized semantic dedupe in `OrderManager` (ticks/size units).
- Backpressure handling for event queue and DB queue.
- Log redaction for API keys, passphrases, secrets, private keys.
- Backtest updates positions and produces non-flat PnL when fills occur.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

## Required Env Vars

- `CLOB_WS_URL`
- `CLOB_REST_URL`
- `GAMMA_API_URL`
- `CHAIN_ID`
- `SIGNATURE_TYPE`
- `PRIVATE_KEY`
- `CLOB_API_KEY`
- `CLOB_API_SECRET`
- `CLOB_API_PASSPHRASE`
- `MARKETS`
- `DRY_RUN`
- `START_PAUSED`
- `BOT_MODE`
- `DB_PATH`

## Run

### Live

```bash
python -m polymarket_arb --config config.yaml --mode live
```

CLI commands:

- `pause`
- `resume`
- `flatten` (mode controlled by config)
- `reload`
- `set min_edge_threshold=0.01 failure_buffer=0.002`
- `backtest`
- `markets on market_1,market_2`
- `markets off market_2`
- `stop`

### Backtest

```bash
python -m polymarket_arb --config config.yaml --mode backtest
```

Backtest output includes:

- equity curve
- fill/cancel/reject ratios
- partial fill frequency
- edge predicted vs realized

## Flatten Mode

Set in `config.yaml`:

- `trading_safety.flatten_mode: cancel_only` (default)
- `trading_safety.flatten_mode: cancel_and_unwind`

`cancel_only`: cancels open orders only.

`cancel_and_unwind`: cancels open orders then attempts inventory unwind subject to slippage/rate limits.

## Dry Run

Keep in `.env`:

```bash
DRY_RUN=true
START_PAUSED=true
```

This runs live market/user WS + full decision path without real order placement.

## Tests

```bash
PYTHONPATH=src pytest -q
```
