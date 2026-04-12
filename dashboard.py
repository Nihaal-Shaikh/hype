"""
Phase 1 — Read-only Hyperliquid dashboard.

Streamlit app showing:
- Total / spot / perps balances
- Live BTC / ETH / SOL perp mid prices
- Candlestick chart for a selected symbol with an EMA overlay
- Open positions and non-zero spot balances

Purely read-only. No orders, no transfers, no side effects on your account.

Run it with:
    source venv/bin/activate
    streamlit run dashboard.py

Then open http://localhost:8501 in Brave Beta.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from hyperliquid.info import Info

from hype_bot import (
    ACTIVE_DEXES,
    AssetClass,
    classify,
    fetch_candles as _hype_fetch_candles,
    get_balance_all_dexes,
    get_open_orders_all_dexes,
    is_open_now,
    make_info,
)

load_dotenv()

MAIN_ADDRESS = os.environ.get("HL_MAIN_ADDRESS", "").strip()
NETWORK = os.environ.get("HL_NETWORK", "mainnet").lower()

# Top symbols per dex for the chart selector.
CORE_SYMBOLS = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "LINK", "AVAX", "SUI"]
XYZ_SYMBOLS = [
    "xyz:GOLD", "xyz:CL", "xyz:TSLA", "xyz:NVDA", "xyz:SP500",
    "xyz:SILVER", "xyz:AAPL", "xyz:MSFT", "xyz:GOOGL", "xyz:AMZN",
    "xyz:META", "xyz:JPY", "xyz:EUR", "xyz:BRENTOIL", "xyz:NATGAS",
    "xyz:COPPER",
]


def _display_symbol(sym: str) -> str:
    """Friendly display name for a symbol in dropdown menus."""
    return sym.replace("xyz:", "") if sym.startswith("xyz:") else sym


# --- Data layer ------------------------------------------------------------

@st.cache_resource
def get_info_client() -> Info:
    """One Info client per process, multi-dex aware (core + xyz)."""
    return make_info(ACTIVE_DEXES)


@st.cache_data(ttl=10, show_spinner=False)
def fetch_account(_info: Info, address: str) -> dict[str, Any]:
    return {
        "perp": _info.user_state(address),
        "spot": _info.spot_user_state(address),
    }


def fetch_mids_fresh(info: Info) -> dict[str, float]:
    """Uncached fetch — used inside the auto-refresh fragment."""
    return {k: float(v) for k, v in info.all_mids().items()}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_candles(_info: Info, coin: str, interval: str, lookback_hours: int) -> pd.DataFrame:
    return _hype_fetch_candles(_info, coin, interval, lookback_hours)


def add_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
    df = df.copy()
    df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()
    return df


# --- Formatting helpers ----------------------------------------------------

def money(x: float, decimals: int = 2) -> str:
    return f"${x:,.{decimals}f}"


def pct(x: float) -> str:
    return f"{x:+.2f}%"


# --- UI --------------------------------------------------------------------

def render_header() -> None:
    st.title("Hyperliquid Dashboard")
    st.caption(
        f"Network: **{NETWORK}**  ·  "
        f"Main wallet: `{MAIN_ADDRESS[:8]}…{MAIN_ADDRESS[-6:]}`"
    )


def render_sidebar() -> tuple[str, str, str, int, int, bool]:
    st.sidebar.header("Controls")
    if st.sidebar.button("Refresh now", use_container_width=True):
        st.cache_data.clear()

    st.sidebar.divider()
    st.sidebar.subheader("Market")
    dex = st.sidebar.selectbox(
        "Dex",
        ["core", "xyz"],
        index=0,
        help="core = crypto perps (BTC, ETH, SOL, …). xyz = stocks, commodities, forex, indices.",
    )

    symbols = CORE_SYMBOLS if dex == "core" else XYZ_SYMBOLS
    symbol = st.sidebar.selectbox("Symbol", symbols, index=0, format_func=_display_symbol)

    interval = st.sidebar.selectbox("Interval", ["15m", "1h", "4h", "1d"], index=1)
    lookback = st.sidebar.slider("Lookback (hours)", 12, 168, 48, step=12)
    ema_period = st.sidebar.number_input("EMA period", min_value=5, max_value=200, value=20, step=5)

    # Show market status for the selected symbol
    ac = classify("" if dex == "core" else dex, symbol)
    if is_open_now(ac):
        st.sidebar.success(f"{_display_symbol(symbol)}: market OPEN ({ac.value})")
    else:
        st.sidebar.warning(f"{_display_symbol(symbol)}: market CLOSED ({ac.value})")

    st.sidebar.divider()
    show_multidex = st.sidebar.toggle(
        "Multi-dex overview",
        value=False,
        help="Show balances and open orders across all active dexes (core + xyz).",
    )

    st.sidebar.divider()
    st.sidebar.caption(
        "Read-only. Data refreshes via cache TTLs "
        "(mids 5s, account 10s, candles 30s). Click Refresh to force."
    )
    return dex, symbol, interval, lookback, ema_period, show_multidex


def render_account_metrics(perp: dict[str, Any], spot: dict[str, Any]) -> None:
    perp_value = float(perp["marginSummary"]["accountValue"])
    perp_margin = float(perp["marginSummary"]["totalMarginUsed"])
    perp_withdrawable = float(perp.get("withdrawable", "0"))
    spot_balances = [b for b in spot.get("balances", []) if float(b.get("total", "0")) > 0]
    spot_usdc = next((float(b["total"]) for b in spot_balances if b["coin"] == "USDC"), 0.0)
    total = perp_value + spot_usdc

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total on Hyperliquid", money(total))
    c2.metric("Spot USDC", money(spot_usdc))
    c3.metric("Perps account", money(perp_value))
    c4.metric(
        "Perps withdrawable",
        money(perp_withdrawable),
        help="Free collateral in the perps account. Does not include spot holdings.",
    )

    if perp_value == 0 and spot_usdc > 0:
        st.info(
            "Your USDC is currently in the **spot** sub-account. "
            "To trade perpetuals, transfer USDC spot → perps (we'll do this in Phase 3)."
        )


@st.fragment(run_every="3s")
def render_live_prices() -> None:
    """Auto-refreshing live prices section. Re-runs every 3 seconds without
    touching the rest of the page (sidebar state + chart stay intact)."""
    info = get_info_client()
    mids_core = fetch_mids_fresh(info)

    header_col, ts_col = st.columns([3, 1])
    with header_col:
        st.subheader("Live prices")
    with ts_col:
        st.caption(
            f"🟢 Live · {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        )

    # Crypto row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BTC", money(mids_core.get("BTC", 0.0)))
    c2.metric("ETH", money(mids_core.get("ETH", 0.0)))
    c3.metric("SOL", money(mids_core.get("SOL", 0.0)))
    hype = mids_core.get("HYPE")
    if hype is not None:
        c4.metric("HYPE", money(hype, 4))

    # Tradfi row (xyz dex)
    mids_xyz = {k: float(v) for k, v in info.all_mids(dex="xyz").items()}
    x1, x2, x3, x4, x5 = st.columns(5)
    x1.metric("GOLD", money(mids_xyz.get("xyz:GOLD", 0.0)))
    x2.metric("WTI Oil", money(mids_xyz.get("xyz:CL", 0.0)))
    x3.metric("TSLA", money(mids_xyz.get("xyz:TSLA", 0.0)))
    x4.metric("S&P 500", money(mids_xyz.get("xyz:SP500", 0.0)))
    x5.metric("EUR/USD", money(mids_xyz.get("xyz:EUR", 0.0), 4))


def render_chart(df: pd.DataFrame, symbol: str, interval: str, ema_period: int) -> None:
    st.subheader(f"{symbol} perp — {interval} candles with {ema_period}-period EMA")
    if df.empty:
        st.warning("No candle data returned — try a different symbol or interval.")
        return

    df = add_ema(df, ema_period)
    ema_col = f"ema_{ema_period}"

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df["time"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Price",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["time"],
            y=df[ema_col],
            mode="lines",
            name=f"EMA {ema_period}",
            line=dict(width=2, color="#f59e0b"),
        )
    )
    fig.update_layout(
        height=520,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    # Quick stats under the chart
    first_close = df["close"].iloc[0]
    last_close = df["close"].iloc[-1]
    change_pct = (last_close - first_close) / first_close * 100
    vol_total = df["volume"].sum()

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Last close", money(last_close))
    s2.metric("Change over window", pct(change_pct), delta=f"{last_close - first_close:+,.2f}")
    s3.metric("Candles", f"{len(df)}")
    s4.metric("Total volume (window)", f"{vol_total:,.2f}")


def render_multidex_overview(info: Info, address: str) -> None:
    """Multi-dex overview: per-dex balances and open orders across all active dexes."""
    st.subheader("Multi-dex overview")

    # Balances per dex
    dex_balances = get_balance_all_dexes(info, address)
    bal_rows = []
    for b in dex_balances:
        bal_rows.append({
            "Dex": b.dex,
            "Account value": f"${b.account_value:,.2f}",
            "Withdrawable": f"${b.withdrawable:,.2f}",
            "Positions": b.position_count,
            "Spot USDC": f"${b.spot_usdc:,.2f}" if b.spot_usdc > 0 else "—",
        })
    st.caption("Balances by dex")
    st.dataframe(pd.DataFrame(bal_rows), use_container_width=True, hide_index=True)

    # Open orders per dex
    all_orders = get_open_orders_all_dexes(info, address)
    total_orders = sum(len(v) for v in all_orders.values())

    st.caption(f"Open orders across dexes ({total_orders} total)")
    if total_orders == 0:
        st.info("No open orders on any dex.")
    else:
        order_rows = []
        for dex_label, orders in all_orders.items():
            for o in orders:
                order_rows.append({
                    "Dex": dex_label,
                    "Coin": o.get("coin", "?"),
                    "Side": "BUY" if o.get("side") == "B" else "SELL",
                    "Size": o.get("sz", "?"),
                    "Price": o.get("limitPx", "?"),
                    "OID": o.get("oid", "?"),
                })
        st.dataframe(pd.DataFrame(order_rows), use_container_width=True, hide_index=True)


def render_positions_and_balances(perp: dict[str, Any], spot: dict[str, Any]) -> None:
    col_pos, col_spot = st.columns(2)

    with col_pos:
        st.subheader("Open perp positions")
        positions = perp.get("assetPositions", [])
        if not positions:
            st.info("No open positions.")
        else:
            rows = []
            for p in positions:
                pos = p["position"]
                rows.append(
                    {
                        "Coin": pos["coin"],
                        "Size": pos["szi"],
                        "Entry": pos.get("entryPx", "—"),
                        "uPnL": pos.get("unrealizedPnl", "—"),
                        "Leverage": pos.get("leverage", {}).get("value", "—"),
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col_spot:
        st.subheader("Spot balances")
        balances = [b for b in spot.get("balances", []) if float(b.get("total", "0")) > 0]
        if not balances:
            st.info("No spot balances.")
        else:
            rows = [
                {"Coin": b["coin"], "Total": b["total"], "On hold": b["hold"]}
                for b in balances
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# --- Entrypoint ------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Hyperliquid Dashboard", layout="wide", page_icon="📈")

    if not MAIN_ADDRESS:
        st.error("`HL_MAIN_ADDRESS` is not set in `.env`. Aborting.")
        st.stop()

    render_header()
    dex, symbol, interval, lookback, ema_period, show_multidex = render_sidebar()

    info = get_info_client()
    account = fetch_account(info, MAIN_ADDRESS)
    candles = fetch_candles(info, symbol, interval, lookback)

    render_account_metrics(account["perp"], account["spot"])
    st.divider()
    render_live_prices()
    st.divider()
    if show_multidex:
        render_multidex_overview(info, MAIN_ADDRESS)
        st.divider()
    render_chart(candles, symbol, interval, ema_period)
    st.divider()
    render_positions_and_balances(account["perp"], account["spot"])


if __name__ == "__main__":
    main()
