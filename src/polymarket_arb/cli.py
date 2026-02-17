from __future__ import annotations

import argparse
import asyncio
import json

from polymarket_arb.backtest import Backtester
from polymarket_arb.config import load_config
from polymarket_arb.engine import TradingEngine
from polymarket_arb.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket CLOB arb bot MVP")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--mode", choices=["live", "backtest"], default=None)
    return parser.parse_args()


async def amain() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    if args.mode:
        cfg.runtime.mode = args.mode
    setup_logging(cfg.runtime.log_level)

    if cfg.runtime.mode == "backtest":
        report = await Backtester(cfg).run()
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        engine = TradingEngine(cfg)
        await engine.start()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
