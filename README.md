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
- Backtest accounting with `cash + unrealized` equity model.

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

- `final_equity`, `max_drawdown`, `win_rate`, `trade_count`
- `cash`, `realized_pnl`, `unrealized_pnl`
- equity curve
- fill/cancel/reject ratios
- partial fill frequency
- edge predicted vs realized

Backtest accounting model:

- fills update `cash` (including fees)
- open positions are marked to market into `unrealized_pnl`
- `equity = cash + unrealized_pnl`

## Flatten Mode

Set in `config.yaml`:

- `trading_safety.flatten_mode: cancel_only` (default)
- `trading_safety.flatten_mode: cancel_and_unwind`

`cancel_only`: cancels open orders only.

`cancel_and_unwind`: cancels open orders then attempts inventory unwind subject to slippage/rate limits.

Kill-switch behavior uses flatten mode too:

- circuit breaker trigger -> `FLATTENING` -> run configured flatten path -> `SAFE`

## Dry Run

Keep in `.env`:

```bash
DRY_RUN=true
START_PAUSED=true
```

This runs live market/user WS + full decision path without real order placement.

## Tests

```bash
pytest -q
```

## Safety Checklist Before Live Trading

1. Keep `DRY_RUN=true` and `START_PAUSED=true` for first startup.
2. Verify enabled markets have valid binary yes/no mapping.
3. Verify websocket connection and periodic resync logs.
4. Confirm log redaction is active (no API secrets/private key in logs).
5. Run `pytest -q` on the deployment artifact.
6. Manually verify `pause`, `resume`, and `flatten` behavior before enabling live.
