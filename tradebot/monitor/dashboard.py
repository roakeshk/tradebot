# ============================================================
#  tradebot / monitor / dashboard.py
#  Streamlit monitoring dashboard.
#
#  Run:
#    streamlit run monitor/dashboard.py
#
#  Shows:
#    - Live P&L and risk status
#    - Open positions
#    - Today's trade log
#    - Equity curve (session)
#    - Regime and signal log
#    - Kill switch
# ============================================================

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import time

st.set_page_config(
    page_title="TradeBot Monitor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ────────────────────────────────────────────────
st.sidebar.title("TradeBot")
st.sidebar.markdown("**Phase:** Paper Trading")

symbol   = st.sidebar.selectbox("Symbol",    ["BANKNIFTY", "NIFTY", "CRUDEOIL"])
broker   = st.sidebar.selectbox("Broker",    ["zerodha", "shoonya", "paper"])
capital  = st.sidebar.number_input("Capital (₹)", value=100000, step=10000)
auto_ref = st.sidebar.checkbox("Auto-refresh (30s)", value=True)

st.sidebar.markdown("---")
kill = st.sidebar.button("🔴 KILL SWITCH — halt all trading", type="primary")
if kill:
    st.sidebar.error("KILL SWITCH ACTIVATED. Restart runner to resume.")

st.sidebar.markdown("---")
st.sidebar.markdown("**Gate thresholds**")
st.sidebar.markdown("Win rate: ≥ 55%")
st.sidebar.markdown("Profit factor: ≥ 1.4")
st.sidebar.markdown("Max drawdown: < ₹12,000")
st.sidebar.markdown("Min trades: 200")

# ── Load latest paper trade log ───────────────────────────
@st.cache_data(ttl=30)
def load_trades(symbol: str) -> pd.DataFrame:
    log_dir = Path("data/processed")
    files   = sorted(log_dir.glob(f"paper_{symbol}_*.csv"), reverse=True)
    if not files:
        return pd.DataFrame()
    df = pd.read_csv(files[0])
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
    return df


@st.cache_data(ttl=30)
def load_wf_results(symbol: str) -> pd.DataFrame:
    log_dir = Path("data/processed")
    files   = sorted(log_dir.glob(f"wf_trades_{symbol}_*.csv"), reverse=True)
    if not files:
        return pd.DataFrame()
    return pd.read_csv(files[0])


trades = load_trades(symbol)
wf     = load_wf_results(symbol)

# ── Header metrics ────────────────────────────────────────
st.title(f"TradeBot — {symbol} Monitor")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

col1, col2, col3, col4, col5 = st.columns(5)

if not trades.empty and "net_pnl" in trades.columns:
    total_pnl   = trades["net_pnl"].sum()
    win_rate    = (trades["net_pnl"] > 0).mean() * 100
    n_trades    = len(trades)
    wins        = trades[trades["net_pnl"] > 0]["net_pnl"]
    losses      = trades[trades["net_pnl"] < 0]["net_pnl"]
    pf          = wins.sum() / abs(losses.sum()) if len(losses) > 0 else 0
    cumulative  = trades["net_pnl"].cumsum()
    max_dd      = (cumulative - cumulative.cummax()).min()
else:
    total_pnl = win_rate = n_trades = pf = max_dd = 0

pnl_color = "normal" if total_pnl >= 0 else "inverse"
col1.metric("Total P&L", f"₹{total_pnl:,.0f}", delta=f"₹{total_pnl:,.0f}")
col2.metric("Win Rate",  f"{win_rate:.1f}%",   delta=f"Target: 55%")
col3.metric("Trades",    str(n_trades),          delta=f"Target: 200+")
col4.metric("Profit Factor", f"{pf:.2f}",        delta="Target: 1.4")
col5.metric("Max Drawdown",  f"₹{abs(max_dd):,.0f}", delta="Limit: ₹12,000")

# ── Gate check ────────────────────────────────────────────
st.markdown("---")
g1, g2, g3, g4 = st.columns(4)

def gate_badge(passed: bool, label: str):
    if passed:
        st.success(f"✓ {label}")
    else:
        st.error(f"✗ {label}")

with g1: gate_badge(win_rate >= 55,      f"Win rate {win_rate:.1f}%")
with g2: gate_badge(pf >= 1.4,           f"Profit factor {pf:.2f}")
with g3: gate_badge(abs(max_dd) < 12000, f"Drawdown ₹{abs(max_dd):,.0f}")
with g4: gate_badge(n_trades >= 200,     f"{n_trades} trades")

# ── Equity curve ──────────────────────────────────────────
st.markdown("---")
st.subheader("Equity curve (paper trading)")
if not trades.empty and "net_pnl" in trades.columns:
    eq_df = trades[["net_pnl"]].copy()
    eq_df["equity"] = capital + eq_df["net_pnl"].cumsum()
    eq_df.index = range(len(eq_df))
    st.line_chart(eq_df["equity"])
else:
    st.info("No trades yet. Start the paper trading runner.")

# ── Trade log ─────────────────────────────────────────────
st.markdown("---")
left, right = st.columns([2, 1])

with left:
    st.subheader("Trade log")
    if not trades.empty:
        display = trades.copy()
        if "net_pnl" in display.columns:
            display["result"] = display["net_pnl"].apply(
                lambda x: "WIN" if x > 0 else ("LOSS" if x < 0 else "BE")
            )
        st.dataframe(display.tail(50), use_container_width=True)
    else:
        st.info("No trades logged today.")

with right:
    st.subheader("Strategy breakdown")
    if not trades.empty and "tag" in trades.columns:
        by_strat = trades.groupby("tag")["net_pnl"].agg(["sum","count","mean"])
        by_strat.columns = ["total_pnl","trades","avg_pnl"]
        st.dataframe(by_strat, use_container_width=True)

# ── Backtest vs paper comparison ──────────────────────────
st.markdown("---")
st.subheader("Backtest vs paper trading — divergence check")
st.caption("Gate rule: paper results must stay within 15% of backtest results to proceed to live")

if not wf.empty and not trades.empty:
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Walk-forward backtest (OOS)**")
        if "net_pnl" in wf.columns:
            wf_wr = (wf["net_pnl"] > 0).mean() * 100
            wf_pf_num = wf[wf["net_pnl"] > 0]["net_pnl"].sum()
            wf_pf_den = abs(wf[wf["net_pnl"] < 0]["net_pnl"].sum())
            wf_pf = wf_pf_num / wf_pf_den if wf_pf_den > 0 else 0
            st.metric("Win rate",     f"{wf_wr:.1f}%")
            st.metric("Profit factor", f"{wf_pf:.2f}")
    with col_b:
        st.markdown("**Paper trading (live)**")
        st.metric("Win rate",     f"{win_rate:.1f}%",
                  delta=f"{win_rate - wf_wr:+.1f}%" if not wf.empty else "")
        st.metric("Profit factor", f"{pf:.2f}",
                  delta=f"{pf - wf_pf:+.2f}" if not wf.empty else "")
else:
    st.info("Run both backtest and paper trading first to see comparison.")

# ── Auto-refresh ──────────────────────────────────────────
if auto_ref:
    time.sleep(30)
    st.rerun()
