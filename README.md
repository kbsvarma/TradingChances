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
- Realized fill deltas feed hourly/daily loss breakers.
- FLATTENING invariant: no new quoting/arb orders are emitted.
- User WS watchdog pauses/flat-flags if private stream goes silent.
- Edge decay guard auto-disables markets with poor realized-vs-predicted edge.
- Adaptive slippage buffer expands from recent live fill slippage.

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
- while `FLATTENING`, decision cycle skips strategy intents and only flatten workflow is allowed

## Market Label Validation

`config.yaml`:

- `markets.allow_nonstandard_yes_no_labels: false` (default, strict)
- if `true`, registry also accepts nonstandard binary labels like `true/false` or `y/n`

## User WS Watchdog

Config (`config.yaml`):

- `safety.user_ws_timeout_sec: 15`

Behavior:

- each private user event (ack/fill/cancel/reject) refreshes watchdog heartbeat
- if no user events for timeout window while running:
  - transition `RUNNING -> FLATTENING -> SAFE`
  - execute configured flatten mode
  - emit error alert log/event

To effectively disable for debugging, set a very large timeout value.

## Edge Decay Protection

Config (`config.yaml`):

- `safety.edge_decay_min_ratio: 0.5`
- `safety.edge_decay_min_trades: 15`
- `safety.edge_decay_window_size: 30`

Metric:

- quality ratio = `avg_realized_edge / avg_predicted_edge`
- if ratio below threshold after minimum trade samples, only that market is auto-disabled

## Adaptive Slippage Buffer

Config (`config.yaml`):

- `safety.slippage_multiplier: 1.5`
- `safety.slippage_window_size: 50`

Behavior:

- live slippage = `abs(fill_price - expected_price_at_intent)`
- rolling p95 slippage per market feeds dynamic buffer
- effective buffer used by strategy is:
  - `max(failure_buffer, rolling_p95 * slippage_multiplier)`
  - never below configured baseline `failure_buffer`

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
5. Verify hourly/daily loss breakers are configured and monitoring realized fill losses.
6. Run `pytest -q` on the deployment artifact.
7. Manually verify `pause`, `resume`, and `flatten` behavior before enabling live.
8. Verify User WS watchdog triggers SAFE mode when private stream is intentionally paused.
9. Start with conservative edge/slippage settings, then tighten only after stable live samples.
