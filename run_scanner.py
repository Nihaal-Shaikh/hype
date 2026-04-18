"""CLI entry point for the multi-coin scanner trading loop.

Scans a curated universe of markets each tick. Single-slot: holds at most
one position at a time. If holding, watches for SELL on that coin. If flat,
takes the first BUY signal across the universe.

Usage:
    python run_scanner.py [--fast 9] [--slow 21] [--interval 1h]
                          [--interval-seconds 300] [--lookback-hours 48]
                          [--live]

Dry-run by default. Same safety model as run_live.py.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import timezone

from execution import ExecutionConfig, set_leverage_if_needed, validate_trade
from hype_bot import (
    ACTIVE_DEXES,
    AssetClass,
    Info,
    TradableMarket,
    fetch_candles,
    get_tradable_market,
    load_main_address,
    make_exchange,
    make_info,
)
from live_state import (
    SessionState,
    check_candle_freshness,
    compute_order_params,
    is_duplicate_signal,
)
from scanner import ScanResult, scan_universe
from scanner_io import build_state, write_scanner_state
from strategy import Signal, Strategy
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig


# Curated universe — diverse asset classes, liquid markets only.
CURATED_UNIVERSE: list[tuple[str, str]] = [
    ("", "BTC"),
    ("", "ETH"),
    ("", "SOL"),
    ("", "HYPE"),
    ("xyz", "xyz:CL"),
    ("xyz", "xyz:GOLD"),
    ("xyz", "xyz:TSLA"),
    ("xyz", "xyz:NVDA"),
    ("xyz", "xyz:SP500"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    logging.Formatter.converter = time.gmtime
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                          datefmt="%Y-%m-%dT%H:%M:%SZ")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
    return logging.getLogger(__name__)


def build_universe(
    info: Info,
    symbols: list[tuple[str, str]] | None = None,
    logger: logging.Logger | None = None,
) -> list[TradableMarket]:
    """Build TradableMarket list from (dex, symbol) pairs.

    Skips any symbol that raises on get_tradable_market (e.g., stale meta).
    """
    pairs = symbols if symbols is not None else CURATED_UNIVERSE
    markets: list[TradableMarket] = []
    for dex, sym in pairs:
        try:
            markets.append(get_tradable_market(info, dex, sym))
        except Exception as exc:
            if logger is not None:
                logger.warning("Skipping %s (%s): %s", sym, dex or "core", exc)
    return markets


def _pick_buy(results: list[ScanResult]) -> ScanResult | None:
    """Return first BUY result, or None. First-signal-wins tie-breaker."""
    for r in results:
        if r.signal == Signal.BUY:
            return r
    return None


def _find_sell_for(results: list[ScanResult], coin: str) -> ScanResult | None:
    """Return the SELL result for the given coin, or None."""
    for r in results:
        if r.market.symbol == coin and r.signal == Signal.SELL:
            return r
    return None


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------

def _execute_buy(
    info: Info,
    exchange,
    result: ScanResult,
    state: SessionState,
    config: ExecutionConfig,
    is_live: bool,
    logger: logging.Logger,
) -> None:
    """Open a long position based on a BUY ScanResult."""
    market = result.market
    mid = market.current_mid
    if mid is None:
        logger.info("No mid price for %s — skipping BUY", market.symbol)
        return

    if is_live:
        try:
            user_state = info.user_state(load_main_address())
            capital = float(user_state.get("withdrawable", 50.0))
        except Exception:
            capital = 50.0
    else:
        capital = 50.0

    is_valid, reason = validate_trade(Signal.BUY, market, capital, config)
    if not is_valid:
        logger.info("Validation failed for %s: %s", market.symbol, reason)
        return

    notional, size = compute_order_params(capital, mid, market.size_decimals, config)

    if not is_live:
        logger.info(
            "DRY-RUN: Would BUY %.5f %s @ ~$%.4f, notional=$%.2f (class=%s)",
            size, market.symbol, mid, notional, market.asset_class.value,
        )
        state.coin = market.symbol
        state.open_position(market.symbol, mid, size, notional)
        return

    if not state.leverage_set:
        set_leverage_if_needed(exchange, market.symbol, config.max_leverage, config.is_cross)
        state.leverage_set = True

    res = exchange.market_open(market.symbol, is_buy=True, sz=size, slippage=0.05)
    logger.info("market_open result: %s", res)
    state.coin = market.symbol
    user_state = info.user_state(load_main_address())
    state.sync_from_exchange(user_state, market.symbol)


def _execute_sell(
    info: Info,
    exchange,
    result: ScanResult,
    state: SessionState,
    is_live: bool,
    logger: logging.Logger,
) -> None:
    """Close the held position based on a SELL ScanResult."""
    market = result.market
    mid = market.current_mid
    if mid is None:
        logger.info("No mid price for %s — skipping SELL", market.symbol)
        return

    if not is_live:
        if state.position is not None:
            proceeds = state.position.size * mid
            pnl = proceeds - state.position.notional
            logger.info(
                "DRY-RUN: Would SELL %s @ ~$%.4f, est. PnL=$%+.4f",
                market.symbol, mid, pnl,
            )
            state.close_position(mid, proceeds)
        return

    res = exchange.market_close(market.symbol)
    logger.info("market_close result: %s", res)
    if state.position is not None:
        proceeds = state.position.size * mid
        state.close_position(mid, proceeds)
    user_state = info.user_state(load_main_address())
    state.sync_from_exchange(user_state, market.symbol)


def _run_one_tick(
    info: Info,
    exchange,
    strategy: Strategy,
    state: SessionState,
    config: ExecutionConfig,
    markets: list[TradableMarket],
    interval: str,
    lookback_hours: int,
    is_live: bool,
    logger: logging.Logger,
) -> list[ScanResult]:
    """Execute one scanner tick. Returns the scan results for state snapshotting."""
    try:
        results = scan_universe(info, strategy, markets, interval, lookback_hours)

        # Log every signal for observability
        for r in results:
            logger.info(
                "Signal %s on %s (class=%s)",
                r.signal.value.upper(), r.market.symbol, r.market.asset_class.value,
            )

        if state.is_holding:
            held = state.position.coin if state.position else state.coin
            sell = _find_sell_for(results, held)
            if sell is not None:
                _execute_sell(info, exchange, sell, state, is_live, logger)
            else:
                logger.info("Holding %s — no SELL signal this tick", held)
            return results

        buy = _pick_buy(results)
        if buy is None:
            logger.info("No BUY signals across %d markets", len(results))
            return results

        _execute_buy(info, exchange, buy, state, config, is_live, logger)
        return results

    except Exception as exc:
        logger.error("Unhandled exception in tick: %s", exc, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-coin EMA crossover scanner loop for Hyperliquid.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--live", action="store_true", default=False,
                        help="Enable real orders (default: dry-run)")
    parser.add_argument("--fast", type=int, default=9)
    parser.add_argument("--slow", type=int, default=21)
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--lookback-hours", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = _setup_logging()

    mode = "LIVE" if args.live else "DRY-RUN"
    divider = "=" * 60
    logger.info(divider)
    logger.info("SCANNER LOOP — %s", mode)
    logger.info("Strategy: EMA %d/%d | Interval: %s | Tick: %ds",
                args.fast, args.slow, args.interval, args.interval_seconds)
    logger.info(divider)
    if args.live:
        logger.warning("LIVE MODE — real orders will be placed!")

    info = make_info(ACTIVE_DEXES)
    exchange = make_exchange(ACTIVE_DEXES) if args.live else None
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=args.fast, slow_period=args.slow))
    state = SessionState(coin="")
    config = ExecutionConfig()

    markets = build_universe(info, logger=logger)
    logger.info("Universe: %d markets loaded", len(markets))
    for m in markets:
        status = "OPEN" if m.open_now else "CLOSED"
        logger.info("  %s (%s) — %s", m.symbol, m.asset_class.value, status)

    if args.live:
        user_state = info.user_state(load_main_address())
        # Check for any open position; if one exists, adopt it
        for p in user_state.get("assetPositions", []):
            pos = p.get("position", {})
            if abs(float(pos.get("szi", "0"))) > 0:
                state.coin = pos["coin"]
                state.sync_from_exchange(user_state, pos["coin"])
                logger.warning("Adopted existing position: %s", state.summary())
                break

    try:
        while True:
            results = _run_one_tick(
                info=info,
                exchange=exchange,
                strategy=strategy,
                state=state,
                config=config,
                markets=markets,
                interval=args.interval,
                lookback_hours=args.lookback_hours,
                is_live=args.live,
                logger=logger,
            )
            try:
                snap = build_state(
                    mode=mode,
                    strategy_name=strategy.describe(),
                    session=state,
                    last_results=results,
                    universe_size=len(markets),
                )
                write_scanner_state(snap)
            except Exception as exc:
                logger.warning("Failed to write scanner state file: %s", exc)
            logger.info("Sleeping %ds... | %s", args.interval_seconds, state.summary())
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        logger.info("Ctrl+C — stopping scanner")
        logger.info("Final: %s", state.summary())
        if state.is_holding:
            logger.warning("Position still open: %s", state.position)
        return 0


if __name__ == "__main__":
    sys.exit(main())
