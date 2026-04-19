"""Walk-forward / out-of-sample evaluation.

Phase 5H: single-split train/test.
  - Split candles into train (first N%) and test (last 1-N%)
  - Grid-search strategy params on train, pick best by chosen metric
  - Evaluate chosen params UNCHANGED on test window
  - Delta = test - train. Big negative → overfit.

Full rolling walk-forward (window, step, aggregate) deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Callable

import pandas as pd

from backtest import BacktestConfig, BacktestResult, run_backtest
from strategy import Strategy
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig
from strategies.rsi import Rsi, RsiConfig


# --- Metric selectors ----------------------------------------------------

_METRIC_FNS: dict[str, Callable[[BacktestResult], float]] = {
    "return": lambda r: r.total_return_pct,
    "sharpe": lambda r: r.sharpe_proxy,
    "win_rate": lambda r: r.win_rate,
    "dd": lambda r: -r.max_drawdown_pct,  # less drawdown = better score
}


def metric_value(result: BacktestResult, key: str) -> float:
    if key not in _METRIC_FNS:
        raise ValueError(f"Unknown metric: {key!r} (choices: {list(_METRIC_FNS)})")
    return _METRIC_FNS[key](result)


# --- Split ---------------------------------------------------------------

@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    test: pd.DataFrame


def split_train_test(candles: pd.DataFrame, train_frac: float = 0.7) -> Split:
    """Chronological split: first train_frac bars train, rest test."""
    if not 0.0 < train_frac < 1.0:
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    n = len(candles)
    cut = int(n * train_frac)
    if cut < 1 or cut >= n:
        raise ValueError(f"Split produces empty train or test (n={n}, cut={cut})")
    return Split(
        train=candles.iloc[:cut].reset_index(drop=True),
        test=candles.iloc[cut:].reset_index(drop=True),
    )


# --- Param grids ---------------------------------------------------------

DEFAULT_EMA_GRID: dict[str, list[Any]] = {
    "fast_period": [5, 7, 9, 12],
    "slow_period": [17, 21, 26, 30],
}

DEFAULT_RSI_GRID: dict[str, list[Any]] = {
    "period": [10, 14, 20],
    "oversold": [20.0, 25.0, 30.0],
    "overbought": [70.0, 75.0, 80.0],
}


def _build_ema(params: dict[str, Any]) -> Strategy:
    return EmaCrossover(EmaCrossoverConfig(
        fast_period=params["fast_period"],
        slow_period=params["slow_period"],
    ))


def _build_rsi(params: dict[str, Any]) -> Strategy:
    return Rsi(RsiConfig(
        period=params["period"],
        oversold=params["oversold"],
        overbought=params["overbought"],
    ))


_BUILDERS: dict[str, Callable[[dict[str, Any]], Strategy]] = {
    "ema": _build_ema,
    "rsi": _build_rsi,
}


def _grid_combos(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    return [dict(zip(keys, vals)) for vals in product(*(grid[k] for k in keys))]


def _is_valid_ema_combo(params: dict[str, Any]) -> bool:
    return params["fast_period"] < params["slow_period"]


def _is_valid_rsi_combo(params: dict[str, Any]) -> bool:
    return params["oversold"] < params["overbought"]


_VALIDATORS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "ema": _is_valid_ema_combo,
    "rsi": _is_valid_rsi_combo,
}


# --- Grid search ---------------------------------------------------------

@dataclass(frozen=True)
class GridResult:
    best_params: dict[str, Any]
    best_metric: float
    best_result: BacktestResult
    combos_tried: int


def grid_search(
    strategy_name: str,
    train_candles: pd.DataFrame,
    grid: dict[str, list[Any]] | None = None,
    metric: str = "sharpe",
    config: BacktestConfig | None = None,
) -> GridResult:
    """Brute-force grid-search over `grid`, pick combo maximizing `metric`."""
    if strategy_name not in _BUILDERS:
        raise ValueError(f"Unknown strategy: {strategy_name!r}")
    if grid is None:
        grid = DEFAULT_EMA_GRID if strategy_name == "ema" else DEFAULT_RSI_GRID

    validator = _VALIDATORS[strategy_name]
    builder = _BUILDERS[strategy_name]

    combos = [c for c in _grid_combos(grid) if validator(c)]
    if not combos:
        raise ValueError(f"No valid combos in grid for {strategy_name!r}")

    best: tuple[float, dict[str, Any], BacktestResult] | None = None
    for params in combos:
        strategy = builder(params)
        result = run_backtest(strategy, train_candles, config)
        score = metric_value(result, metric)
        if best is None or score > best[0]:
            best = (score, params, result)

    assert best is not None
    return GridResult(
        best_params=best[1],
        best_metric=best[0],
        best_result=best[2],
        combos_tried=len(combos),
    )


# --- Out-of-sample evaluation --------------------------------------------

@dataclass(frozen=True)
class OosReport:
    strategy: str
    metric: str
    train_frac: float
    n_train: int
    n_test: int
    best_params: dict[str, Any]
    combos_tried: int
    train_result: BacktestResult
    test_result: BacktestResult

    @property
    def delta_return_pct(self) -> float:
        return self.test_result.total_return_pct - self.train_result.total_return_pct

    @property
    def delta_metric(self) -> float:
        return metric_value(self.test_result, self.metric) - metric_value(self.train_result, self.metric)


def out_of_sample_eval(
    strategy_name: str,
    candles: pd.DataFrame,
    train_frac: float = 0.7,
    grid: dict[str, list[Any]] | None = None,
    metric: str = "sharpe",
    config: BacktestConfig | None = None,
) -> OosReport:
    """Full OOS pipeline: split → grid_search(train) → eval(test)."""
    split = split_train_test(candles, train_frac=train_frac)
    grid_res = grid_search(strategy_name, split.train, grid=grid,
                           metric=metric, config=config)
    # Eval the best params on test — no retraining
    builder = _BUILDERS[strategy_name]
    test_result = run_backtest(builder(grid_res.best_params), split.test, config)

    return OosReport(
        strategy=strategy_name,
        metric=metric,
        train_frac=train_frac,
        n_train=len(split.train),
        n_test=len(split.test),
        best_params=grid_res.best_params,
        combos_tried=grid_res.combos_tried,
        train_result=grid_res.best_result,
        test_result=test_result,
    )
