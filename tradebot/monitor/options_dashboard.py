# ============================================================
#  tradebot / monitor / options_dashboard.py
#  C1 — Options + Futures unified Streamlit dashboard
#
#  Run:
#    streamlit run monitor/options_dashboard.py
#
#  Shows:
#    - Live option chain with Greeks
#    - Open positions with real-time P&L
#    - IV rank and PCR indicators
#    - Strategy gate check
#    - Payoff diagrams for open positions
#    - Combined futures + options P&L
# ============================================================

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta

from config.settings import INSTRUMENTS, RISK, MARKET

_CUR = "$" if MARKET == "US" else "₹"
_SYMBOLS = list(INSTRUMENTS.keys()) or ["SPY"]
_DEF_SPOT = INSTRUMENTS.get(_SYMBOLS[0], {}).get("default_spot", 500 if MARKET == "US" else 48500)
_TZ_LABEL = "ET" if MARKET == "US" else "IST"

st.set_page_config(
    page_title="TradeBot — Options Dashboard",
    page_icon="📊",
    layout="wide",
)

# ── CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card{background:#161b27;border:1px solid #2a3245;border-radius:10px;padding:14px;margin:4px 0}
.metric-label{font-size:11px;color:#8892a4;text-transform:uppercase;letter-spacing:.5px}
.metric-value{font-size:1.7rem;font-weight:700;margin-top:2px;line-height:1}
.gate-pass{background:#0a2e18;border:1px solid #4ade80;border-radius:6px;padding:8px 12px;color:#4ade80;font-size:13px}
.gate-fail{background:#3d0f0f;border:1px solid #f87171;border-radius:6px;padding:8px 12px;color:#f87171;font-size:13px}
.iv-high{color:#f87171;font-weight:700}
.iv-low{color:#4ade80;font-weight:700}
.iv-mid{color:#fbbf24;font-weight:700}
</style>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────
st.sidebar.title("TradeBot Options")
symbol   = st.sidebar.selectbox("Symbol",   _SYMBOLS)
capital  = st.sidebar.number_input(f"Capital ({_CUR})", value=int(RISK.get("max_capital", 100000)), step=10000)
auto_ref = st.sidebar.checkbox("Auto-refresh (60s)", value=True)
sim_mode = st.sidebar.selectbox("Mode", ["Paper Trading", "Simulation", "Live"])

st.sidebar.markdown("---")
st.sidebar.markdown("**Strategy filters**")
min_iv_rank = st.sidebar.slider("Min IV rank (sell)", 40, 80, 55)
max_dte     = st.sidebar.slider("Max DTE (sell)",      3, 15,  8)


# ── Load trade data ───────────────────────────────────────────
@st.cache_data(ttl=60)
def load_options_trades(symbol: str) -> pd.DataFrame:
    proc = Path("data/processed")
    frames = []
    for pattern in [f"opt_backtest_{symbol}_*.csv",
                    f"options_paper_{symbol}_*.csv"]:
        for f in sorted(proc.glob(pattern)):
            try:
                df = pd.read_csv(f)
                df["_src"] = f.stem
                frames.append(df)
            except Exception:
                pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


@st.cache_data(ttl=60)
def load_futures_trades(symbol: str) -> pd.DataFrame:
    proc = Path("data/processed")
    frames = []
    for f in sorted(proc.glob(f"*{symbol}*.csv")):
        if "opt" in f.stem or "options" in f.stem:
            continue
        try:
            df = pd.read_csv(f)
            frames.append(df)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ── Build synthetic chain for display ────────────────────────
@st.cache_data(ttl=60)
def get_demo_chain(symbol: str, spot: float, iv: float, dte: int):
    from options.chain_feed import ChainFeed
    from datetime import date, timedelta
    feed   = ChainFeed()
    expiry = date.today() + timedelta(days=dte)
    chain  = feed.build_from_spot(symbol, spot, sigma=iv, expiry=expiry, dte=dte)
    return chain


# ── Main page ─────────────────────────────────────────────────
st.title(f"📊 Options Dashboard — {symbol}")
st.caption(f"Updated: {datetime.now().strftime('%H:%M:%S')} {_TZ_LABEL} | Mode: {sim_mode}")

# Spot price input (in live mode this comes from broker)
col_spot, col_iv, col_dte, col_ivr = st.columns(4)
with col_spot:
    spot = st.number_input("Spot price", value=_DEF_SPOT, step=100)
with col_iv:
    iv_pct = st.number_input("IV %", value=18.0, step=0.5, min_value=5.0, max_value=80.0)
    iv = iv_pct / 100
with col_dte:
    dte = st.number_input("DTE", value=5, step=1, min_value=1, max_value=30)
with col_ivr:
    iv_rank_disp = st.number_input("IV Rank", value=65, step=1, min_value=0, max_value=100)

# ── Chain display ─────────────────────────────────────────────
st.markdown("---")
st.subheader("Option Chain")

chain = get_demo_chain(symbol, float(spot), float(iv), int(dte))
chain.iv_rank = float(iv_rank_disp)

if chain and not chain.df.empty:
    df_display = chain.df.copy()

    # Highlight ATM row
    atm = chain.atm
    df_display["ATM"] = df_display["strike"].apply(lambda x: "◀ ATM" if x == atm else "")

    # Format for display
    display_cols = {
        "ce_ltp":   "CE Price",
        "ce_delta": "CE Δ",
        "ce_iv":    "CE IV%",
        "ce_oi":    "CE OI",
        "strike":   "Strike",
        "ATM":      "",
        "pe_oi":    "PE OI",
        "pe_iv":    "PE IV%",
        "pe_delta": "PE Δ",
        "pe_ltp":   "PE Price",
    }

    df_show = df_display[[c for c in display_cols if c in df_display.columns]].copy()
    df_show.columns = [display_cols.get(c, c) for c in df_show.columns]

    # Show 10 strikes around ATM
    atm_idx = df_display[df_display["strike"] == atm].index
    if len(atm_idx) > 0:
        atm_pos = df_display.index.get_loc(atm_idx[0])
        df_show = df_show.iloc[max(0, atm_pos-5): atm_pos+6]

    # Format numerics
    for col in ["CE IV%", "PE IV%"]:
        if col in df_show.columns:
            df_show[col] = df_show[col].apply(lambda x: f"{x*100:.1f}%" if isinstance(x, float) else x)
    for col in ["CE Δ", "PE Δ"]:
        if col in df_show.columns:
            df_show[col] = df_show[col].apply(lambda x: f"{x:.3f}" if isinstance(x, float) else x)
    for col in ["CE OI", "PE OI"]:
        if col in df_show.columns:
            df_show[col] = df_show[col].apply(lambda x: f"{int(x):,}" if isinstance(x, (int, float)) else x)

    st.dataframe(df_show, use_container_width=True, hide_index=True)

# ── Strategy signals ──────────────────────────────────────────
st.markdown("---")
st.subheader("Strategy availability")

from options.strategies import OptionsStrategyBuilder
b = OptionsStrategyBuilder()
strats = [
    ("Short Straddle",  b.short_straddle(chain)),
    ("Short Strangle",  b.short_strangle(chain)),
    ("Iron Condor",     b.iron_condor(chain)),
    ("Long Call",       b.long_call(chain)),
    ("Long Put",        b.long_put(chain)),
]

cols = st.columns(len(strats))
for col, (name, pos) in zip(cols, strats):
    with col:
        if pos:
            st.markdown(f'<div class="gate-pass">✓ {name}<br><small>Max profit: {_CUR}{pos.max_profit:.0f}</small></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="gate-fail">✗ {name}<br><small>Conditions not met</small></div>', unsafe_allow_html=True)

# ── Payoff diagram ────────────────────────────────────────────
st.markdown("---")
st.subheader("Payoff at expiry")

selected_strat = st.selectbox(
    "Select strategy for payoff diagram",
    [n for n, p in strats if p],
    index=0 if any(p for _, p in strats) else -1,
)

pos_to_plot = next((p for n, p in strats if n == selected_strat and p), None)
if pos_to_plot:
    moves  = list(range(-2000, 2001, 50))
    payoff = [pos_to_plot.pnl_at_expiry(spot + m) for m in moves]
    spots  = [spot + m for m in moves]

    pf_df = pd.DataFrame({"Spot": spots, "P&L": payoff})
    pf_df["Color"] = pf_df["P&L"].apply(lambda x: "Profit" if x >= 0 else "Loss")

    # Simple line chart
    st.line_chart(pf_df.set_index("Spot")["P&L"])

    if pos_to_plot.breakevens:
        st.caption(f"Breakevens: {' | '.join([f'{_CUR}{b:.0f}' for b in pos_to_plot.breakevens])}")
    st.caption(f"Max profit: {_CUR}{pos_to_plot.max_profit:.0f}  |  Max loss: {'Unlimited' if pos_to_plot.max_loss == float('inf') else f'{_CUR}{pos_to_plot.max_loss:.0f}'}")

# ── P&L from trades ───────────────────────────────────────────
st.markdown("---")
left, right = st.columns(2)

with left:
    st.subheader("Options trades")
    opt_trades = load_options_trades(symbol)
    if not opt_trades.empty and "net_pnl" in opt_trades.columns:
        pnl  = opt_trades["net_pnl"]
        wins = (pnl > 0).sum()
        n    = len(pnl)
        wr   = wins/n*100 if n else 0
        pf_num = pnl[pnl>0].sum()
        pf_den = abs(pnl[pnl<0].sum())
        pf = pf_num/pf_den if pf_den > 0 else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Net P&L", f"{_CUR}{pnl.sum():,.0f}")
        c2.metric("Win rate", f"{wr:.1f}%")
        c3.metric("PF", f"{pf:.2f}")
        st.dataframe(opt_trades[["strategy","entry_time","net_pnl","exit_reason"]].tail(20),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No options trades yet. Run the simulation first:\n`python -m simulation.options_backtest`")

with right:
    st.subheader("Futures trades")
    fut_trades = load_futures_trades(symbol)
    if not fut_trades.empty and "net_pnl" in fut_trades.columns:
        pnl = fut_trades["net_pnl"]
        st.metric("Net P&L", f"{_CUR}{pnl.sum():,.0f}")
        st.dataframe(fut_trades[["strategy","entry_time","net_pnl"]].tail(20) if "strategy" in fut_trades else fut_trades.tail(20),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No futures trades. Run backtest first:\n`python run_backtest.py`")

if auto_ref:
    import time
    time.sleep(60)
    st.rerun()
