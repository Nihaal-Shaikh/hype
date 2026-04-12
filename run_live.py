"""CLI entry point for the live trading loop.

Usage:
    python run_live.py [--coin BTC] [--interval 1h] [--fast 9] [--slow 21]
                       [--interval-seconds 3600] [--lookback-hours 48]
                       [--live]

Without --live the loop runs in dry-run mode: all logic executes normally
but no orders are placed.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import timezone

import pandas as pd

from execution import ExecutionConfig, set_leverage_if_needed, validate_trade
from hype_bot import (
    ACTIVE_DEXES,
    TradableMarket,
    AssetClass,
    fetch_candles,
    get_tradable_market,
    is_open_now,
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
from strategy import Signal, Strategy
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _interval_to_seconds(interval: str) -> int:
    """Convert a candle interval string to seconds.

    Supports: '1m', '5m', '15m', '30m', '1h', '4h', '8h', '12h', '1d'.
    Raises ValueError for unrecognised formats.
    """
    interval = interval.strip().lower()
    if interval.endswith("m"):
        return int(interval[:-1]) * 60
    if interval.endswith("h"):
        return int(interval[:-1]) * 3600
    if interval.endswith("d"):
        return int(interval[:-1]) * 86400
    raise ValueError(f"Unrecognised interval format: {interval!r}")


def _setup_logging() -> logging.Logger:
    """Configure root logger with UTC timestamps, return module logger."""
    logging.Formatter.converter = time.gmtime  # UTC for all formatters
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                          datefmt="%Y-%m-%dT%H:%M:%SZ")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)
    else:
        # Replace existing handlers to avoid duplicate output in tests
        root.handlers = [handler]
    return logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core tick
# ---------------------------------------------------------------------------

def _run_one_tick(
    info,
    exchange,
    strategy: Strategy,
    state: SessionState,
    config: ExecutionConfig,
    coin: str,
    interval: str,
    interval_seconds: int,
    lookback_hours: int,
    is_live: bool,
    logger: logging.Logger,
) -> None:
    """Execute one tick of the trading loop.

    Designed to be called repeatedly (e.g. every interval_seconds seconds).
    All exceptions are caught and logged so the outer loop never crashes.
    """
    try:
        # 1. Fetch candles
        candles = fetch_candles(info, coin, interval, lookback_hours)

        # 2. Check freshness (use candle interval, not tick interval)
        candle_interval_seconds = _interval_to_seconds(interval)
        is_fresh, freshness_reason = check_candle_freshness(candles, candle_interval_seconds)
        if not is_fresh:
            logger.warning("Candle freshness check failed: %s", freshness_reason)
            return

        # 3. Sufficient candle count?
        try:
            min_bars = strategy.config.slow_period
        except AttributeError:
            min_bars = 21
        if len(candles) < min_bars:
            logger.info(
                "Insufficient candles: have %d, need %d — skipping tick",
                len(candles), min_bars,
            )
            return

        # 4. Evaluate strategy
        signal: Signal = strategy.evaluate(candles)

        # 5. Duplicate-signal guard
        last_row = candles.iloc[-1]
        candle_time = last_row["time"]
        if hasattr(candle_time, "to_pydatetime"):
            candle_time = candle_time.to_pydatetime()
        if candle_time.tzinfo is None:
            candle_time = candle_time.replace(tzinfo=timezone.utc)

        if is_duplicate_signal(signal, candle_time, state.last_signal_candle_time):
            logger.info(
                "Duplicate signal %s on candle %s — skipping",
                signal.value, candle_time.isoformat(),
            )
            return

        # 6. HOLD → nothing to do
        if signal == Signal.HOLD:
            logger.info("Signal: HOLD — no action")
            return

        # 7. BUY
        if signal == Signal.BUY:
            if state.is_holding:
                logger.info("Already holding %s, skipping BUY", coin)
                return

            mid = float(last_row["close"])

            # Build TradableMarket for validation
            dex = ""  # default to core dex for BTC and other core-dex coins
            try:
                market = get_tradable_market(info, dex, coin)
            except Exception as exc:
                logger.warning(
                    "get_tradable_market failed (%s) — using fallback TradableMarket", exc
                )
                market = TradableMarket(
                    dex=dex,
                    symbol=coin,
                    asset_class=AssetClass.CRYPTO,
                    max_leverage=config.max_leverage,
                    size_decimals=5,
                    min_notional=config.min_notional_usd,
                    current_mid=mid,
                    open_now=is_open_now(AssetClass.CRYPTO),
                )

            # Determine capital
            if is_live:
                try:
                    user_state = info.user_state(load_main_address())
                    capital = float(user_state.get("withdrawable", 50.0))
                except Exception:
                    capital = 50.0
            else:
                capital = 50.0

            # Validate
            is_valid, reason = validate_trade(signal, market, capital, config)
            if not is_valid:
                logger.info("Trade validation failed: %s", reason)
                return

            # Compute order params
            sz_decimals = market.size_decimals
            notional, size = compute_order_params(capital, mid, sz_decimals, config)

            if not is_live:
                # Dry-run: log and update state hypothetically
                logger.info(
                    "DRY-RUN: Would BUY %.5f %s @ ~$%.2f, notional=$%.2f",
                    size, coin, mid, notional,
                )
                state.open_position(coin, mid, size, notional)
                state.last_signal_candle_time = candle_time
            else:
                # Live: set leverage once, place order, sync from exchange
                if not state.leverage_set:
                    set_leverage_if_needed(exchange, coin, config.max_leverage, config.is_cross)
                    state.leverage_set = True

                result = exchange.market_open(coin, is_buy=True, sz=size, slippage=0.05)
                logger.info("market_open result: %s", result)

                main_address = load_main_address()
                user_state = info.user_state(main_address)
                state.sync_from_exchange(user_state, coin)
                state.last_signal_candle_time = candle_time
            return

        # 8. SELL
        if signal == Signal.SELL:
            if not state.is_holding:
                logger.info("Not holding %s, skipping SELL", coin)
                return

            if not is_live:
                # Dry-run: estimate PnL from entry vs current close
                mid = float(last_row["close"])
                if state.position is not None:
                    est_proceeds = state.position.size * mid
                    pnl = est_proceeds - state.position.notional
                    logger.info(
                        "DRY-RUN: Would SELL %s @ ~$%.2f, est. PnL=$%+.4f",
                        coin, mid, pnl,
                    )
                    state.close_position(mid, est_proceeds)
                else:
                    logger.info("DRY-RUN: Would SELL %s (no position details)", coin)
                    state.close_position(0.0, 0.0)
                state.last_signal_candle_time = candle_time
            else:
                # Live: verify position still exists, then close
                main_address = load_main_address()
                user_state_pre = info.user_state(main_address)
                positions = user_state_pre.get("assetPositions", [])
                exchange_pos = None
                for p in positions:
                    pos = p.get("position", {})
                    if pos.get("coin") == coin and abs(float(pos.get("szi", "0"))) > 0:
                        exchange_pos = pos
                        break

                if exchange_pos is None:
                    logger.warning(
                        "Position disappeared for %s before SELL — syncing state", coin
                    )
                    state.sync_from_exchange(user_state_pre, coin)
                    return

                result = exchange.market_close(coin)
                if result is None:
                    logger.warning(
                        "market_close returned None for %s — syncing state", coin
                    )
                    user_state_post = info.user_state(main_address)
                    state.sync_from_exchange(user_state_post, coin)
                    return

                logger.info("market_close result: %s", result)

                user_state_post = info.user_state(main_address)
                # Estimate exit price from last close (ground truth from fill would need
                # a fills query; using mid as conservative approximation)
                mid = float(last_row["close"])
                exit_size = state.position.size if state.position else 0.0
                proceeds = exit_size * mid
                state.close_position(mid, proceeds)
                state.sync_from_exchange(user_state_post, coin)
                state.last_signal_candle_time = candle_time

    except Exception as exc:
        logger.error("Unhandled exception in tick: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live EMA crossover trading loop for Hyperliquid.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Enable real orders (default: dry-run)",
    )
    parser.add_argument("--coin", default="BTC", help="Coin/symbol to trade")
    parser.add_argument(
        "--interval", default="1h", help="Candle interval (e.g. 1h, 4h, 15m)"
    )
    parser.add_argument("--fast", type=int, default=9, help="EMA fast period")
    parser.add_argument("--slow", type=int, default=21, help="EMA slow period")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=3600,
        help="Seconds between ticks",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=48,
        help="Candle lookback window in hours",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = _setup_logging()

    mode = "LIVE" if args.live else "DRY-RUN"
    divider = "=" * 60
    logger.info(divider)
    logger.info("LIVE TRADING LOOP — %s", mode)
    logger.info(
        "Coin: %s | Interval: %s | EMA: %d/%d",
        args.coin, args.interval, args.fast, args.slow,
    )
    logger.info(divider)
    if args.live:
        logger.warning("LIVE MODE — Real orders will be placed!")

    info = make_info(ACTIVE_DEXES)
    exchange = make_exchange(ACTIVE_DEXES) if args.live else None
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=args.fast, slow_period=args.slow))
    state = SessionState(coin=args.coin)
    config = ExecutionConfig()

    # Get main address for balance queries
    main_address = load_main_address()

    # Sync initial position from exchange if live
    if args.live:
        user_state = info.user_state(main_address)
        sync_result = state.sync_from_exchange(user_state, args.coin)
        if sync_result != "no_change":
            logger.warning("Startup sync: %s", sync_result)

    try:
        while True:
            _run_one_tick(
                info=info,
                exchange=exchange,
                strategy=strategy,
                state=state,
                config=config,
                coin=args.coin,
                interval=args.interval,
                interval_seconds=args.interval_seconds,
                lookback_hours=args.lookback_hours,
                is_live=args.live,
                logger=logger,
            )
            logger.info("Sleeping %ds until next tick...", args.interval_seconds)
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        logger.info("Ctrl+C received — stopping loop")
        logger.info("Final state: %s", state.summary())
        if state.is_holding:
            logger.warning("Position still open: %s", state.position)
            logger.warning(
                "Use the Hyperliquid UI or a manual script to close if desired."
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
