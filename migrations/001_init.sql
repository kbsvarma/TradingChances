PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  event_type TEXT NOT NULL,
  market_id TEXT,
  token_id TEXT,
  correlation_id TEXT,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY,
  venue_order_id TEXT,
  market_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  side TEXT NOT NULL,
  price REAL NOT NULL,
  size REAL NOT NULL,
  remaining_size REAL NOT NULL,
  status TEXT NOT NULL,
  created_ts REAL NOT NULL,
  last_update_ts REAL NOT NULL,
  ttl_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  market_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  intent_type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  market_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  side TEXT NOT NULL,
  price REAL NOT NULL,
  size REAL NOT NULL,
  order_id TEXT,
  client_order_id TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  key TEXT PRIMARY KEY,
  market_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  qty REAL NOT NULL,
  avg_price REAL NOT NULL,
  updated_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  equity REAL NOT NULL,
  drawdown REAL NOT NULL,
  daily_pnl REAL NOT NULL,
  hourly_pnl REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS latency_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  metric_key TEXT NOT NULL,
  p50 REAL,
  p95 REAL,
  p99 REAL,
  mean REAL
);

CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  component TEXT NOT NULL,
  error_type TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS book_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  market_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  bids_json TEXT NOT NULL,
  asks_json TEXT NOT NULL
);
