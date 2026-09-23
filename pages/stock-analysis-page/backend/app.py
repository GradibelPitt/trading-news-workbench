from __future__ import annotations

import contextvars
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
import talib
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cache import StockCache
from .market_data import (
    SCREENER_PRESETS,
    fetch_screen_preset,
    merge_market_universe,
    option_chain_payload,
    search_equities,
)
from .trade_plan import HORIZONS, build_trade_plan

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="Stock Analysis Page API",
    description="Search a ticker, fetch OHLCV data with yfinance, compute technical indicators, and return JSON for TradingView Lightweight Charts.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def user_context_middleware(request, call_next):
    """Read X-User-Id header, validate scope, set contextvar with finally reset."""
    scope = request.headers.get("X-User-Id", "admin")
    # Only accept "admin" or "u_<digits>" — reject path injection
    if scope != "admin" and not _SCOPE_RE.match(scope):
        scope = "admin"
    token = _current_user_id.set(scope)
    try:
        response = await call_next(request)
    finally:
        _current_user_id.reset(token)
    return response


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

ALLOWED_RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}
ALLOWED_INTERVALS = {"1d", "1wk", "1mo", "1h", "30m", "15m", "5m"}
CACHE_TTL_SECONDS = 300

# Market-data freshness is interval-specific.  A four-hour cache is acceptable
# for daily candles, but unsafe for a five-minute trade plan.
INTERVAL_CACHE_TTL_SECONDS: Dict[str, int] = {
    "5m": 90,
    "15m": 180,
    "30m": 300,
    "1h": 600,
    "1d": 4 * 3600,
    "1wk": 12 * 3600,
    "1mo": 24 * 3600,
}


def _interval_cache_ttl(interval: str) -> int:
    return INTERVAL_CACHE_TTL_SECONDS.get(interval, CACHE_TTL_SECONDS)

# Simple in-memory cache (L1). Keys are scoped by user: "{scope}:payload:{symbol}:{period}:{interval}"
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

# Per-user L2 persistent SQLite cache instances.
# Scope: "admin" -> backend/data/stock_cache.db (original path, never moved)
#         "u_N"  -> backend/data/users/u_N/stock_cache.db (lazy-created)
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'current_user_id', default='admin'
)
_db_caches: Dict[str, Tuple[float, StockCache]] = {}  # scope -> (last_access_ts, cache)
_db_cache_lock = threading.Lock()
_DATA_DIR = Path(__file__).resolve().parent / "data"
_MAX_USER_CACHES = 32
_CACHE_TTL_USER = 86400  # 24h non-admin cache idle time

# Valid scope patterns: "admin" or "u_<digits>"
_SCOPE_RE = re.compile(r"^u_\d+$")


def _get_db_cache() -> StockCache:
    """Return (and lazily create) the StockCache for the current request scope.

    Always acquires _db_cache_lock to avoid check-then-act races between
    the fast-path read and eviction-driven deletes.  For a local desktop
    app the lock hold time is negligible.
    """
    scope = _current_user_id.get()
    now = time.time()

    with _db_cache_lock:
        # Cache hit — update last-access timestamp and return
        entry = _db_caches.get(scope)
        if entry is not None:
            _db_caches[scope] = (now, entry[1])
            return entry[1]

        # Evict idle / excess non-admin caches before creating a new one
        if scope != "admin":
            to_evict: list[str] = []
            # Snapshot via list() — safe to iterate while modifying _db_caches
            for s, (last_ts, _) in list(_db_caches.items()):
                if s == "admin":
                    continue
                if now - last_ts > _CACHE_TTL_USER:
                    to_evict.append(s)
            # Enforce max size: evict the least-recently-used non-admin entry
            if len(_db_caches) - len(to_evict) >= _MAX_USER_CACHES:
                oldest = min(
                    ((s, ts) for s, (ts, _) in _db_caches.items() if s != "admin"),
                    key=lambda x: x[1],
                    default=None,
                )
                if oldest and oldest[0] not in to_evict:
                    to_evict.append(oldest[0])
            for s in to_evict:
                try:
                    del _db_caches[s]
                except KeyError:
                    pass  # concurrently removed — harmless

        # Determine database path (never move admin's original file)
        if scope == "admin":
            db_path = str(_DATA_DIR / "stock_cache.db")
        else:
            user_dir = _DATA_DIR / "users" / scope
            user_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(user_dir / "stock_cache.db")

        cache = StockCache(db_path=db_path)
        cache.cleanup()
        _db_caches[scope] = (now, cache)
        return cache


# Thread pool for parallel HTTP fetches (moomoo corporate events / ratings).
_http_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="moomoo-fetch")


@dataclass
class TickerMeta:
    symbol: str
    name: str
    exchange: str
    currency: str


def _clean_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol == "SPACEX":
        symbol = "SPCX"
    symbol = re.sub(r"[^A-Z0-9.=-]", "", symbol)
    if not symbol or len(symbol) > 20:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol.")
    return symbol


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        value = float(value)
        if math.isfinite(value):
            return round(value, 6)
    except Exception:
        return None
    return None


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _dict_float(data: Optional[Dict[str, Any]], *keys: str) -> Optional[float]:
    if not data:
        return None
    return _first_float(*(data.get(key) for key in keys))


def _dict_value(data: Optional[Dict[str, Any]], *keys: str) -> Any:
    if not data:
        return None
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _format_time(index_value: Any, interval: str) -> Any:
    """Lightweight Charts accepts YYYY-MM-DD for daily data and unix seconds for intraday."""
    ts = pd.Timestamp(index_value)
    if interval in {"1d", "1wk", "1mo"}:
        return ts.strftime("%Y-%m-%d")
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def _series_from_columns(df: pd.DataFrame, columns: List[str], interval: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        item: Dict[str, Any] = {"time": _format_time(idx, interval)}
        valid = True
        for col in columns:
            val = _safe_float(row.get(col))
            if val is None:
                valid = False
                break
            item[col.lower()] = val
        if valid:
            rows.append(item)
    return rows


def _line_series(df: pd.DataFrame, column: str, interval: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        val = _safe_float(row.get(column))
        if val is not None:
            output.append({"time": _format_time(idx, interval), "value": val})
    return output


def _hist_series(df: pd.DataFrame, column: str, interval: str, positive_color: str, negative_color: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        val = _safe_float(row.get(column))
        if val is not None:
            output.append(
                {
                    "time": _format_time(idx, interval),
                    "value": val,
                    "color": positive_color if val >= 0 else negative_color,
                }
            )
    return output


def _volume_series(df: pd.DataFrame, interval: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        value = row.get("Volume")
        if value is None or pd.isna(value):
            continue
        color = "rgba(34, 197, 94, 0.35)" if row.get("Close", 0) >= row.get("Open", 0) else "rgba(239, 68, 68, 0.35)"
        output.append({"time": _format_time(idx, interval), "value": int(value), "color": color})
    return output


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _rma(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period, min_periods=period).apply(lambda values: float(np.dot(values, weights) / weights.sum()), raw=True)


def _dema(series: pd.Series, period: int) -> pd.Series:
    ema1 = _ema(series, period)
    ema2 = _ema(ema1, period)
    return 2 * ema1 - ema2


def _tema(series: pd.Series, period: int) -> pd.Series:
    ema1 = _ema(series, period)
    ema2 = _ema(ema1, period)
    ema3 = _ema(ema2, period)
    return 3 * ema1 - 3 * ema2 + ema3


def _hma(series: pd.Series, period: int) -> pd.Series:
    half = max(1, int(period / 2))
    sqrt_period = max(1, int(math.sqrt(period)))
    return _wma(2 * _wma(series, half) - _wma(series, period), sqrt_period)


def _linear_regression(series: pd.Series, period: int, forecast_offset: int = 0) -> pd.Series:
    x = np.arange(period, dtype=float)
    target_x = period - 1 + forecast_offset

    def calc(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        slope, intercept = np.polyfit(x, values, 1)
        return float(intercept + slope * target_x)

    return series.rolling(period, min_periods=period).apply(calc, raw=True)


def _true_range(df: pd.DataFrame) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    return pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)


def _compute_dmi(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    high = df["High"]
    low = df["Low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr = _rma(_true_range(df), period)
    plus_di = 100 * _rma(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100 * _rma(minus_dm, period) / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = _rma(dx, period)
    return plus_di, minus_di, adx


def _compute_psar(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    if len(df) == 0:
        return pd.Series(dtype=float, index=df.index)
    psar = np.full(len(df), np.nan, dtype=float)
    bull = True
    af = step
    ep = high[0]
    psar[0] = low[0]
    for i in range(1, len(df)):
        prior = psar[i - 1]
        if bull:
            psar[i] = prior + af * (ep - prior)
            if i >= 2:
                psar[i] = min(psar[i], low[i - 1], low[i - 2])
            if low[i] < psar[i]:
                bull = False
                psar[i] = ep
                ep = low[i]
                af = step
            elif high[i] > ep:
                ep = high[i]
                af = min(af + step, max_step)
        else:
            psar[i] = prior + af * (ep - prior)
            if i >= 2:
                psar[i] = max(psar[i], high[i - 1], high[i - 2])
            if high[i] > psar[i]:
                bull = True
                psar[i] = ep
                ep = high[i]
                af = step
            elif low[i] < ep:
                ep = low[i]
                af = min(af + step, max_step)
    return pd.Series(psar, index=df.index)


def _compute_fisher(df: pd.DataFrame, period: int = 10) -> Tuple[pd.Series, pd.Series]:
    median = (df["High"] + df["Low"]) / 2
    low_min = median.rolling(period, min_periods=period).min()
    high_max = median.rolling(period, min_periods=period).max()
    raw = 2 * ((median - low_min) / (high_max - low_min).replace(0, np.nan) - 0.5)
    values: List[float] = []
    prev = 0.0
    prev_fisher = 0.0
    for val in raw.fillna(0).clip(-0.999, 0.999):
        smoothed = 0.33 * float(val) + 0.67 * prev
        smoothed = max(min(smoothed, 0.999), -0.999)
        fisher = 0.5 * math.log((1 + smoothed) / (1 - smoothed)) + 0.5 * prev_fisher
        values.append(fisher)
        prev = smoothed
        prev_fisher = fisher
    fisher_series = pd.Series(values, index=df.index)
    return fisher_series, fisher_series.shift(1)


def _compute_aroon(df: pd.DataFrame, period: int = 25) -> Tuple[pd.Series, pd.Series]:
    high = df["High"]
    low = df["Low"]
    aroon_up = high.rolling(period + 1, min_periods=period + 1).apply(lambda x: 100 * float(np.argmax(x)) / period, raw=True)
    aroon_down = low.rolling(period + 1, min_periods=period + 1).apply(lambda x: 100 * float(np.argmin(x)) / period, raw=True)
    return aroon_up, aroon_down


def _compute_heikin_ashi_diff(df: pd.DataFrame) -> pd.Series:
    ha_close = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    ha_open = pd.Series(index=df.index, dtype=float)
    if len(df):
        ha_open.iloc[0] = (df["Open"].iloc[0] + df["Close"].iloc[0]) / 2
        for i in range(1, len(df)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
    return ha_close - ha_open


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def _iv_proxy(close: pd.Series, period: int = 20) -> pd.Series:
    # This is realized volatility, used as an IV proxy because yfinance OHLCV
    # does not provide option-chain implied volatility for every symbol/range.
    returns = np.log(close / close.shift(1))
    return returns.rolling(period, min_periods=period).std() * math.sqrt(252) * 100


def _rolling_percent_rank(series: pd.Series, period: int = 252) -> pd.Series:
    def calc(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        last = values[-1]
        return float((values <= last).sum() / len(values) * 100)
    return series.rolling(period, min_periods=min(period, 60)).apply(calc, raw=True)


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    open_ = df["Open"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float).replace(0, np.nan)
    typical = (high + low + close) / 3
    median = (high + low) / 2

    # Common main-chart trend indicators using talib.
    df["SMA20"] = pd.Series(talib.SMA(close, timeperiod=20), index=df.index)
    df["SMA50"] = pd.Series(talib.SMA(close, timeperiod=50), index=df.index)
    df["MA5"] = pd.Series(talib.SMA(close, timeperiod=5), index=df.index)
    df["MA10"] = pd.Series(talib.SMA(close, timeperiod=10), index=df.index)
    df["MA20"] = df["SMA20"]
    df["MA60"] = pd.Series(talib.SMA(close, timeperiod=60), index=df.index)

    df["EMA5"] = pd.Series(talib.EMA(close, timeperiod=5), index=df.index)
    df["EMA10"] = pd.Series(talib.EMA(close, timeperiod=10), index=df.index)
    df["EMA12"] = pd.Series(talib.EMA(close, timeperiod=12), index=df.index)
    df["EMA20"] = pd.Series(talib.EMA(close, timeperiod=20), index=df.index)
    df["EMA26"] = pd.Series(talib.EMA(close, timeperiod=26), index=df.index)
    df["EMA50"] = pd.Series(talib.EMA(close, timeperiod=50), index=df.index)
    df["EMA60"] = pd.Series(talib.EMA(close, timeperiod=60), index=df.index)
    df["EMA120"] = pd.Series(talib.EMA(close, timeperiod=120), index=df.index)
    df["EMA250"] = pd.Series(talib.EMA(close, timeperiod=250), index=df.index)

    df["BBI"] = (
        pd.Series(talib.SMA(close, timeperiod=3), index=df.index)
        + pd.Series(talib.SMA(close, timeperiod=6), index=df.index)
        + pd.Series(talib.SMA(close, timeperiod=12), index=df.index)
        + pd.Series(talib.SMA(close, timeperiod=24), index=df.index)
    ) / 4

    upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
    df["BB_Middle"] = pd.Series(middle, index=df.index)
    df["BB_Upper"] = pd.Series(upper, index=df.index)
    df["BB_Lower"] = pd.Series(lower, index=df.index)

    df["PSAR"] = pd.Series(talib.SAR(high, low, acceleration=0.02, maximum=0.2), index=df.index)
    df["ALLIGATOR_JAW"] = pd.Series(talib.SMA(close, timeperiod=13), index=df.index).shift(8)
    df["ALLIGATOR_TEETH"] = pd.Series(talib.SMA(close, timeperiod=8), index=df.index).shift(5)
    df["ALLIGATOR_LIPS"] = pd.Series(talib.SMA(close, timeperiod=5), index=df.index).shift(3)
    for period in [3, 5, 8, 10, 12, 15, 30, 35, 40, 45, 50, 60]:
        df[f"GMMA_{period}"] = pd.Series(talib.EMA(close, timeperiod=period), index=df.index)
    df["TWAP20"] = pd.Series(talib.SMA(typical, timeperiod=20), index=df.index)
    df["VWMA20"] = pd.Series(talib.SMA(close * volume, timeperiod=20) / talib.SMA(volume, timeperiod=20), index=df.index)
    df["WMA20"] = pd.Series(talib.WMA(close, timeperiod=20), index=df.index)
    df["TEMA20"] = pd.Series(talib.TEMA(close, timeperiod=20), index=df.index)
    df["DEMA20"] = pd.Series(talib.DEMA(close, timeperiod=20), index=df.index)
    
    half = 10
    sqrt_period = 4  # int(math.sqrt(20))
    wma_half = pd.Series(talib.WMA(close, timeperiod=half), index=df.index)
    wma_full = pd.Series(talib.WMA(close, timeperiod=20), index=df.index)
    df["HMA20"] = pd.Series(talib.WMA(2 * wma_half - wma_full, timeperiod=sqrt_period), index=df.index)
    
    df["LSMA25"] = pd.Series(talib.LINEARREG(close, timeperiod=25), index=df.index)
    df["TSF14"] = pd.Series(talib.TSF(close, timeperiod=14), index=df.index)

    # Sub-chart indicators using talib.
    macd, macdsignal, macdhist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    df["MACD"] = pd.Series(macd, index=df.index)
    df["MACD_Signal"] = pd.Series(macdsignal, index=df.index)
    df["MACD_Hist"] = pd.Series(macdhist, index=df.index)

    df["RSI"] = pd.Series(talib.RSI(close, timeperiod=14), index=df.index).fillna(50)
    df["Volume_SMA20"] = pd.Series(talib.SMA(df["Volume"].astype(float), timeperiod=20), index=df.index)
    df["ATR14"] = pd.Series(talib.ATR(high, low, close, timeperiod=14), index=df.index)
    
    df["PLUS_DI"] = pd.Series(talib.PLUS_DI(high, low, close, timeperiod=14), index=df.index)
    df["MINUS_DI"] = pd.Series(talib.MINUS_DI(high, low, close, timeperiod=14), index=df.index)
    df["ADX"] = pd.Series(talib.ADX(high, low, close, timeperiod=14), index=df.index)

    df["DMA"] = pd.Series(talib.SMA(close, timeperiod=10) - talib.SMA(close, timeperiod=50), index=df.index)
    df["DMA_SIGNAL"] = pd.Series(talib.SMA(df["DMA"], timeperiod=10), index=df.index)
    mid_move = median.diff()
    box_ratio = volume / (high - low).replace(0, np.nan)
    df["EMV"] = (mid_move / box_ratio) * 100000000
    df["EMV_SIGNAL"] = pd.Series(talib.SMA(df["EMV"].fillna(0), timeperiod=14), index=df.index)
    fisher, fisher_signal = _compute_fisher(df, 10)
    df["FISHER"] = fisher
    df["FISHER_SIGNAL"] = fisher_signal

    df["HV20"] = _iv_proxy(close, 20)
    df["IV"] = df["HV20"]
    hv_min = df["HV20"].rolling(252, min_periods=60).min()
    hv_max = df["HV20"].rolling(252, min_periods=60).max()
    df["IV_RANK"] = 100 * (df["HV20"] - hv_min) / (hv_max - hv_min).replace(0, np.nan)
    df["IV_PERCENTILE"] = _rolling_percent_rank(df["HV20"], 252)
    df["VOLAT"] = df["HV20"]

    vfast = pd.Series(talib.EMA(df["Volume"].astype(float), timeperiod=12), index=df.index)
    vslow = pd.Series(talib.EMA(df["Volume"].astype(float), timeperiod=26), index=df.index)
    df["VMACD"] = vfast - vslow
    df["VMACD_SIGNAL"] = pd.Series(talib.EMA(df["VMACD"], timeperiod=9), index=df.index)
    
    df["TRIX"] = pd.Series(talib.TRIX(close, timeperiod=15), index=df.index)
    df["TRIX_SIGNAL"] = pd.Series(talib.SMA(df["TRIX"], timeperiod=9), index=df.index)
    df["PRICEOSC"] = pd.Series(talib.PPO(close, fastperiod=12, slowperiod=26, matype=1), index=df.index)

    df["DDI"] = 100 * _safe_div(df["PLUS_DI"] - df["MINUS_DI"], df["PLUS_DI"] + df["MINUS_DI"])
    single_ema_range = pd.Series(talib.EMA(high - low, timeperiod=9), index=df.index)
    double_ema_range = pd.Series(talib.EMA(single_ema_range, timeperiod=9), index=df.index)
    df["MI"] = pd.Series(talib.SMA(single_ema_range / double_ema_range.replace(0, np.nan), timeperiod=25), index=df.index) * 25
    prev_close = close.shift(1)
    swing_range = pd.concat([(high - prev_close).abs(), (low - prev_close).abs(), (high - low)], axis=1).max(axis=1)
    df["SI"] = 50 * ((close - prev_close) + 0.5 * (close - open_) + 0.25 * (prev_close - open_.shift(1))) / swing_range.replace(0, np.nan)
    df["AO"] = pd.Series(talib.SMA(median, timeperiod=5) - talib.SMA(median, timeperiod=34), index=df.index)
    df["TOWER"] = pd.Series(talib.SMA(close.diff(), timeperiod=3), index=df.index) * 3
    df["EWO"] = pd.Series(talib.SMA(close, timeperiod=5) - talib.SMA(close, timeperiod=35), index=df.index)
    rvi_raw = (close - open_) / (high - low).replace(0, np.nan)
    df["RVI"] = pd.Series(talib.SMA(rvi_raw, timeperiod=10), index=df.index)
    df["RVI_SIGNAL"] = pd.Series(talib.SMA(df["RVI"], timeperiod=4), index=df.index)
    df["HLVOL"] = 100 * pd.Series(talib.SMA(high - low, timeperiod=20), index=df.index) / close.replace(0, np.nan)

    momentum = close - close.shift(5)
    up = momentum.where(momentum > 0, 0.0)
    down = -momentum.where(momentum < 0, 0.0)
    df["RMI"] = 100 - (100 / (1 + _rma(up, 14) / _rma(down, 14).replace(0, np.nan)))
    tsi_num = pd.Series(talib.EMA(pd.Series(talib.EMA(close.diff(), timeperiod=25), index=df.index), timeperiod=13), index=df.index)
    tsi_den = pd.Series(talib.EMA(pd.Series(talib.EMA(close.diff().abs(), timeperiod=25), index=df.index), timeperiod=13), index=df.index)
    df["SMIE"] = 100 * tsi_num / tsi_den.replace(0, np.nan)
    df["SMIE_SIGNAL"] = pd.Series(talib.EMA(df["SMIE"], timeperiod=5), index=df.index)
    df["SMIEO"] = df["SMIE"] - df["SMIE_SIGNAL"]
    
    aroon_down, aroon_up = talib.AROON(high, low, timeperiod=25)
    df["AROON_UP"] = pd.Series(aroon_up, index=df.index)
    df["AROON_DOWN"] = pd.Series(aroon_down, index=df.index)
    
    tr = _true_range(df)
    sum_tr = pd.Series(talib.SMA(tr, timeperiod=14), index=df.index) * 14
    highest_high = pd.Series(talib.MAX(high, timeperiod=14), index=df.index)
    lowest_low = pd.Series(talib.MIN(low, timeperiod=14), index=df.index)
    df["CHOP"] = 100 * np.log10(sum_tr / (highest_high - lowest_low).replace(0, np.nan)) / np.log10(14)
    df["HADIFF"] = _compute_heikin_ashi_diff(df)
    direction = (close - close.shift(10)).abs()
    volatility = pd.Series(talib.SMA(close.diff().abs(), timeperiod=10), index=df.index) * 10
    df["ER"] = 100 * direction / volatility.replace(0, np.nan)

    kc_mid = pd.Series(talib.EMA(close, timeperiod=20), index=df.index)
    kc_upper = kc_mid + 1.5 * df["ATR14"]
    kc_lower = kc_mid - 1.5 * df["ATR14"]
    df["KC_Upper"] = kc_upper
    df["KC_Middle"] = kc_mid
    df["KC_Lower"] = kc_lower
    squeeze_on = (df["BB_Upper"] < kc_upper) & (df["BB_Lower"] > kc_lower)
    squeeze_base = (highest_high + lowest_low) / 2
    df["SQUEEZE"] = close - squeeze_base
    df["SQUEEZE_ON"] = squeeze_on.astype(float)
    df["FO"] = 100 * (close - df["TSF14"]) / close.replace(0, np.nan)

    # Extra common entries visible in many moomoo-style lists.
    low_min = pd.Series(talib.MIN(low, timeperiod=9), index=df.index)
    high_max = pd.Series(talib.MAX(high, timeperiod=9), index=df.index)
    rsv = 100 * (close - low_min) / (high_max - low_min).replace(0, np.nan)
    df["KDJ_K"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    df["KDJ_D"] = df["KDJ_K"].ewm(alpha=1 / 3, adjust=False).mean()
    df["KDJ_J"] = 3 * df["KDJ_K"] - 2 * df["KDJ_D"]

    # New indicators from VNpy using talib:
    df["KAMA30"] = pd.Series(talib.KAMA(close, timeperiod=30), index=df.index)
    df["MFI14"] = pd.Series(talib.MFI(high, low, close, df["Volume"].astype(float), timeperiod=14), index=df.index)
    df["OBV"] = pd.Series(talib.OBV(close, df["Volume"].astype(float)), index=df.index)
    df["CCI14"] = pd.Series(talib.CCI(high, low, close, timeperiod=14), index=df.index)
    df["BOP"] = pd.Series(talib.BOP(open_, high, low, close), index=df.index)
    df["ULTOSC"] = pd.Series(talib.ULTOSC(high, low, close, timeperiod1=7, timeperiod2=14, timeperiod3=28), index=df.index)
    df["ROC10"] = pd.Series(talib.ROC(close, timeperiod=10), index=df.index)
    df["PPO"] = pd.Series(talib.PPO(close, fastperiod=12, slowperiod=26, matype=0), index=df.index)
    df["APO"] = pd.Series(talib.APO(close, fastperiod=12, slowperiod=26, matype=0), index=df.index)
    df["CMO14"] = pd.Series(talib.CMO(close, timeperiod=14), index=df.index)
    df["NATR14"] = pd.Series(talib.NATR(high, low, close, timeperiod=14), index=df.index)
    df["AD"] = pd.Series(talib.AD(high, low, close, df["Volume"].astype(float)), index=df.index)
    df["ADOSC"] = pd.Series(talib.ADOSC(high, low, close, df["Volume"].astype(float), fastperiod=3, slowperiod=10), index=df.index)

    return df



def _score_row(df: pd.DataFrame, i: int) -> Tuple[int, int, List[str], List[str]]:
    row = df.iloc[i]
    prev = df.iloc[i - 1]

    buy = 0
    sell = 0
    buy_reasons: List[str] = []
    sell_reasons: List[str] = []

    macd_cross_up = prev["MACD"] <= prev["MACD_Signal"] and row["MACD"] > row["MACD_Signal"]
    macd_cross_down = prev["MACD"] >= prev["MACD_Signal"] and row["MACD"] < row["MACD_Signal"]

    if macd_cross_up:
        buy += 30
        buy_reasons.append("MACD bullish crossover")
    if macd_cross_down:
        sell += 30
        sell_reasons.append("MACD bearish crossover")

    if row["RSI"] < 35:
        buy += 20
        buy_reasons.append("RSI oversold zone")
    elif row["RSI"] > 70:
        sell += 20
        sell_reasons.append("RSI overbought zone")

    if prev["Close"] <= prev["SMA20"] and row["Close"] > row["SMA20"]:
        buy += 18
        buy_reasons.append("Close reclaimed SMA20")
    if prev["Close"] >= prev["SMA20"] and row["Close"] < row["SMA20"]:
        sell += 18
        sell_reasons.append("Close lost SMA20")

    if row["Close"] < row["BB_Lower"]:
        buy += 15
        buy_reasons.append("Close below lower Bollinger Band")
    if row["Close"] > row["BB_Upper"]:
        sell += 15
        sell_reasons.append("Close above upper Bollinger Band")

    if row["SMA20"] > row["SMA50"] and row["Close"] > row["SMA20"]:
        buy += 10
        buy_reasons.append("SMA20 above SMA50 trend support")
    if row["SMA20"] < row["SMA50"] and row["Close"] < row["SMA20"]:
        sell += 10
        sell_reasons.append("SMA20 below SMA50 trend pressure")

    if row["Volume_SMA20"] and not pd.isna(row["Volume_SMA20"]) and row["Volume"] > 1.25 * row["Volume_SMA20"]:
        if row["Close"] >= row["Open"]:
            buy += 7
            buy_reasons.append("High-volume green candle")
        else:
            sell += 7
            sell_reasons.append("High-volume red candle")

    return min(buy, 100), min(sell, 100), buy_reasons, sell_reasons


def _generate_markers(df: pd.DataFrame, interval: str) -> List[Dict[str, Any]]:
    markers: List[Dict[str, Any]] = []
    if len(df) < 60:
        return markers

    for i in range(51, len(df)):
        row = df.iloc[i]
        if pd.isna(row[["SMA20", "SMA50", "BB_Upper", "BB_Lower", "MACD", "MACD_Signal", "RSI"]]).any():
            continue
        buy, sell, _, _ = _score_row(df, i)
        t = _format_time(df.index[i], interval)
        if buy >= 60 and buy > sell:
            markers.append(
                {
                    "time": t,
                    "position": "belowBar",
                    "color": "#22c55e",
                    "shape": "arrowUp",
                    "text": f"BUY {buy}",
                }
            )
        elif sell >= 60 and sell > buy:
            markers.append(
                {
                    "time": t,
                    "position": "aboveBar",
                    "color": "#ef4444",
                    "shape": "arrowDown",
                    "text": f"SELL {sell}",
                }
            )

    # Avoid visual overload on long ranges.
    return markers[-80:]


def _latest_score(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < 60:
        return {"buy": 0, "sell": 0, "signal": "insufficient_data", "reasons": []}

    for i in range(len(df) - 1, 50, -1):
        row = df.iloc[i]
        if not pd.isna(row[["SMA20", "SMA50", "BB_Upper", "BB_Lower", "MACD", "MACD_Signal", "RSI"]]).any():
            buy, sell, buy_reasons, sell_reasons = _score_row(df, i)
            if buy >= 60 and buy > sell:
                signal = "buy_candidate"
                reasons = buy_reasons
            elif sell >= 60 and sell > buy:
                signal = "sell_candidate"
                reasons = sell_reasons
            elif buy > sell:
                signal = "neutral_bullish"
                reasons = buy_reasons
            elif sell > buy:
                signal = "neutral_bearish"
                reasons = sell_reasons
            else:
                signal = "neutral"
                reasons = []
            return {"buy": buy, "sell": sell, "signal": signal, "reasons": reasons[:5]}

    return {"buy": 0, "sell": 0, "signal": "insufficient_data", "reasons": []}


def _get_meta(ticker: yf.Ticker, symbol: str) -> TickerMeta:
    name = symbol
    exchange = ""
    currency = "USD"
    try:
        info = ticker.get_info()
        name = info.get("shortName") or info.get("longName") or symbol
        exchange = info.get("exchange") or info.get("fullExchangeName") or ""
        currency = info.get("currency") or currency
    except Exception:
        # yfinance info occasionally times out. The chart can still work without it.
        pass
    return TickerMeta(symbol=symbol, name=name, exchange=exchange, currency=currency)


def _get_meta_cached(ticker: yf.Ticker, symbol: str) -> TickerMeta:
    """Wrapper around _get_meta with 7-day SQLite cache."""
    cache_key = f"{symbol}:meta"
    cached = _get_db_cache().get("meta", cache_key)
    if cached:
        return TickerMeta(**cached)
    meta = _get_meta(ticker, symbol)
    _get_db_cache().set("meta", cache_key, {
        "symbol": meta.symbol, "name": meta.name,
        "exchange": meta.exchange, "currency": meta.currency,
    })
    return meta


def _coerce_plain_dict(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value)
    except Exception:
        return {}


def _get_quote_info_cached(ticker: yf.Ticker, symbol: str) -> Dict[str, Any]:
    """Fetch yfinance quote/valuation info with a short cache."""
    cache_key = f"{symbol}:quote-info"
    cached = _get_db_cache().get("quote", cache_key, max_age=300)
    if cached:
        return cached
    info: Dict[str, Any] = {}
    try:
        info = _coerce_plain_dict(ticker.get_info())
    except Exception:
        try:
            info = _coerce_plain_dict(ticker.info)
        except Exception:
            info = {}
    if info:
        _get_db_cache().set("quote", cache_key, info)
    return info


def _get_fast_info(ticker: yf.Ticker) -> Dict[str, Any]:
    try:
        return _coerce_plain_dict(ticker.fast_info)
    except Exception:
        return {}


def _build_quote_stats(
    hist: pd.DataFrame,
    quote_info: Optional[Dict[str, Any]],
    fast_info: Optional[Dict[str, Any]],
    previous_close: Any,
) -> Dict[str, Any]:
    latest = hist.iloc[-1]
    close = _first_float(latest.get("Close"), _dict_float(fast_info, "last_price"), _dict_float(quote_info, "currentPrice", "regularMarketPrice"))
    open_price = _first_float(_dict_float(fast_info, "open"), _dict_float(quote_info, "open", "regularMarketOpen"), latest.get("Open"))
    day_high = _first_float(_dict_float(fast_info, "day_high"), _dict_float(quote_info, "dayHigh", "regularMarketDayHigh"), latest.get("High"))
    day_low = _first_float(_dict_float(fast_info, "day_low"), _dict_float(quote_info, "dayLow", "regularMarketDayLow"), latest.get("Low"))
    prev_close = _first_float(previous_close, _dict_float(fast_info, "previous_close"), _dict_float(quote_info, "previousClose", "regularMarketPreviousClose"))
    volume = _first_float(_dict_float(fast_info, "last_volume"), _dict_float(quote_info, "volume", "regularMarketVolume"), latest.get("Volume"))
    avg_volume = _first_float(_dict_float(fast_info, "ten_day_average_volume", "three_month_average_volume"), _dict_float(quote_info, "averageVolume10days", "averageDailyVolume10Day", "averageVolume"))
    if avg_volume is None and len(hist) > 1:
        volume_series = hist["Volume"].astype(float).replace(0, np.nan).dropna()
        trailing = volume_series.iloc[:-1].tail(20)
        if not trailing.empty:
            avg_volume = _safe_float(trailing.mean())

    amount = _safe_float(close * volume) if close is not None and volume is not None else None
    market_cap = _first_float(_dict_float(fast_info, "market_cap"), _dict_float(quote_info, "marketCap"))
    total_shares = _dict_float(quote_info, "sharesOutstanding", "impliedSharesOutstanding")
    float_shares = _dict_float(quote_info, "floatShares")
    float_market_cap = _dict_float(quote_info, "floatMarketCap")
    if float_market_cap is None and market_cap is not None and total_shares and float_shares:
        float_market_cap = _safe_float(market_cap * float_shares / total_shares)

    turnover_rate = _safe_float(volume / float_shares * 100) if volume is not None and float_shares else None
    volume_ratio = _safe_float(volume / avg_volume) if volume is not None and avg_volume else None
    amplitude_pct = _safe_float((day_high - day_low) / prev_close * 100) if day_high is not None and day_low is not None and prev_close else None

    return {
        "dayHigh": day_high,
        "dayLow": day_low,
        "open": open_price,
        "previousClose": prev_close,
        "volume": volume,
        "amount": amount,
        "orderRatioPct": None,
        "volumeRatio": volume_ratio,
        "marketCap": market_cap,
        "floatMarketCap": float_market_cap,
        "totalShares": total_shares,
        "floatShares": float_shares,
        "peTtm": _dict_float(quote_info, "trailingPE"),
        "peStatic": _dict_float(quote_info, "trailingPE"),
        "peForward": _dict_float(quote_info, "forwardPE"),
        "pb": _dict_float(quote_info, "priceToBook"),
        "dividendTtm": _first_float(_dict_float(quote_info, "trailingAnnualDividendRate"), _dict_float(quote_info, "dividendRate")),
        "beta": _dict_float(quote_info, "beta"),
        "fiftyTwoWeekHigh": _first_float(_dict_float(fast_info, "year_high"), _dict_float(quote_info, "fiftyTwoWeekHigh")),
        "fiftyTwoWeekLow": _first_float(_dict_float(fast_info, "year_low"), _dict_float(quote_info, "fiftyTwoWeekLow")),
        "rangeHigh": _safe_float(hist["High"].max()),
        "rangeLow": _safe_float(hist["Low"].min()),
        "averagePrice": None,
        "amplitudePct": amplitude_pct,
        "turnoverRatePct": turnover_rate,
        "lotSize": int(_dict_value(quote_info, "lotSize") or 1),
        "epsTtm": _dict_float(quote_info, "trailingEps"),
        "epsForward": _dict_float(quote_info, "forwardEps"),
        "source": "yfinance",
    }


def _get_ohlcv_cached(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Wrapper around _download_history with 4-hour SQLite cache."""
    cache_key = f"{symbol}:{period}:{interval}:ohlcv"
    cached = _get_db_cache().get("ohlcv", cache_key, max_age=_interval_cache_ttl(interval))
    if cached:
        try:
            df = pd.DataFrame(cached["records"])
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date")
            elif "Datetime" in df.columns:
                df["Datetime"] = pd.to_datetime(df["Datetime"])
                df = df.set_index("Datetime")
            if not df.empty:
                return df
        except Exception:
            pass  # Fall through to live fetch.

    df = _download_history(symbol, period, interval)
    # Serialize to JSON-safe format for SQLite storage.
    try:
        records = df.reset_index().to_dict(orient="records")
        # Convert timestamps to ISO strings for JSON serialization.
        for rec in records:
            for k, v in rec.items():
                if isinstance(v, pd.Timestamp):
                    rec[k] = v.isoformat()
        _get_db_cache().set("ohlcv", cache_key, {"records": records})
    except Exception:
        pass  # Non-critical — just skip caching.
    return df


def _get_rating_cached(symbol: str) -> Optional[Dict[str, Any]]:
    """Wrapper around fetch_moomoo_analyst_ratings with 24-hour SQLite cache."""
    cache_key = f"{symbol}:rating"
    cached = _get_db_cache().get("rating", cache_key)
    if cached:
        return cached

    data = fetch_moomoo_analyst_ratings(symbol)
    if data:
        _get_db_cache().set("rating", cache_key, data)
    return data


def _get_events_cached(symbol: str, ticker: yf.Ticker) -> Tuple[List[Dict[str, Any]], str]:
    """Cached corporate events with parallel moomoo fetching.

    Returns (events_list, source_string).
    """
    cache_key = f"{symbol}:events"
    cached = _get_db_cache().get("events", cache_key)
    if cached:
        return cached.get("events", []), cached.get("source", "cache")

    moomoo_events = _fetch_moomoo_events_parallel(symbol)
    fallback_events = _fetch_yfinance_event_fallback(ticker, symbol)
    events = _merge_corporate_events(moomoo_events, fallback_events)
    source = "moomoo/futunn" if moomoo_events else ("yfinance-fallback" if fallback_events else "none")

    _get_db_cache().set("events", cache_key, {"events": events, "source": source})
    return events, source


def _fetch_moomoo_events_parallel(symbol: str) -> List[Dict[str, Any]]:
    """Parallel version of fetch_moomoo_corporate_events using ThreadPoolExecutor."""
    clean_sym = _normalize_moomoo_stock_code(symbol)
    pages = [
        (f"https://www.futunn.com/stock/{clean_sym}/earnings?lang=zh-cn", "https://www.futunn.com/"),
        (f"https://www.futunn.com/stock/{clean_sym}/dividends?lang=zh-cn", "https://www.futunn.com/"),
        (f"https://www.futunn.com/stock/{clean_sym}/forecast?lang=zh-cn", "https://www.futunn.com/"),
        (f"https://www.moomoo.com/stock/{clean_sym}/earnings", "https://www.moomoo.com/"),
        (f"https://www.moomoo.com/stock/{clean_sym}/dividends", "https://www.moomoo.com/"),
    ]

    def fetch_page(url_referer: Tuple[str, str]) -> List[Dict[str, Any]]:
        url, referer = url_referer
        html = _fetch_text(url, timeout=6, referer=referer)
        if not html:
            return []
        page_events: List[Dict[str, Any]] = []
        for state in _extract_initial_state_objects(html):
            page_events.extend(_walk_event_objects(state, [url]))
        return page_events

    events: List[Dict[str, Any]] = []
    try:
        futures = {_http_pool.submit(fetch_page, page): page for page in pages}
        for future in as_completed(futures, timeout=20):
            try:
                events.extend(future.result())
            except Exception:
                pass
    except Exception:
        pass

    return _dedupe_events(events)


def _period_to_start(period: str) -> Optional[str]:
    """Convert UI ranges into a start date for yf.download fallback.

    The earlier standalone script used yf.download(start=...), which is often
    more tolerant than Ticker.history(period=...). We keep period download as a
    first try, then fall back to this for daily/weekly/monthly data.
    """
    if period == "max":
        return None
    days_map = {
        "1mo": 45,
        "3mo": 120,
        "6mo": 220,
        "1y": 400,
        "2y": 800,
        "5y": 1900,
    }
    days = days_map.get(period)
    if not days:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def _flatten_yfinance_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output to single-level OHLCV columns."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    required = {"Open", "High", "Low", "Close", "Volume"}
    level0 = set(map(str, df.columns.get_level_values(0)))
    level1 = set(map(str, df.columns.get_level_values(1))) if df.columns.nlevels > 1 else set()

    out = df.copy()
    if required.issubset(level0):
        out.columns = df.columns.get_level_values(0)
    elif required.issubset(level1):
        out.columns = df.columns.get_level_values(1)
    else:
        out.columns = ["_".join(map(str, col)).strip() for col in df.columns.to_flat_index()]
    return out


def _download_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch OHLCV using the same yfinance.download style as the earlier demo."""
    errors: List[str] = []

    attempts: List[Dict[str, Any]] = [
        {"period": period, "interval": interval},
    ]

    # The previous working one-file demo used start=2024-01-01. For daily-ish
    # charts, this fallback avoids some Ticker.history period/crumb failures.
    start = _period_to_start(period)
    if start and interval in {"1d", "1wk", "1mo"}:
        attempts.append({"start": start, "interval": interval})

    # Last-resort daily fallback for symbols that fail with an exact range.
    if interval == "1d" and period != "2y":
        attempts.append({"period": "2y", "interval": "1d"})

    for kwargs in attempts:
        try:
            df = yf.download(
                symbol,
                auto_adjust=False,
                progress=False,
                threads=False,
                group_by="column",
                **kwargs,
            )
            df = _flatten_yfinance_columns(df)
            if df is not None and not df.empty:
                return df
            errors.append(f"empty result from yf.download({kwargs})")
        except Exception as exc:
            errors.append(f"yf.download({kwargs}) failed: {exc}")

    raise HTTPException(status_code=404, detail=f"No usable price data found for '{symbol}'. It may be an invalid ticker or delisted.")

def _normalize_moomoo_stock_code(symbol: str) -> str:
    clean_sym = symbol.strip().upper()
    if "." in clean_sym:
        parts = clean_sym.split(".")
        if parts[0] in ["US", "HK"] and len(parts) >= 2:
            clean_sym = f"{parts[1]}-{parts[0]}"
        elif len(parts) >= 2 and parts[1] in ["US", "HK"]:
            clean_sym = f"{parts[0]}-{parts[1]}"
    if "-" not in clean_sym:
        clean_sym = f"{clean_sym}-US"
    return clean_sym


def _moomoo_headers(referer: str = "https://www.futunn.com/") -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
    }


def _fetch_text(url: str, timeout: int = 6, referer: str = "https://www.futunn.com/") -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers=_moomoo_headers(referer))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _extract_balanced_json(text: str, start_index: int) -> Optional[str]:
    if start_index < 0 or start_index >= len(text):
        return None
    opening = text[start_index]
    pairs = {"{": "}", "[": "]"}
    closing = pairs.get(opening)
    if not closing:
        return None

    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for pos in range(start_index, len(text)):
        ch = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_string = False
            continue
        if ch in ['"', "'"]:
            in_string = True
            quote = ch
            continue
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start_index:pos + 1]
    return None


def _extract_initial_state_objects(html: str) -> List[Dict[str, Any]]:
    states: List[Dict[str, Any]] = []
    if not html:
        return states

    # Futu / moomoo pages commonly expose hydrated data through window.__INITIAL_STATE__.
    for token in ["window.__INITIAL_STATE__", "window.__NUXT__"]:
        search_from = 0
        while True:
            idx = html.find(token, search_from)
            if idx < 0:
                break
            eq = html.find("=", idx)
            starts = [pos for pos in [html.find("{", eq), html.find("[", eq)] if pos >= 0]
            if starts:
                start = min(starts)
                blob = _extract_balanced_json(html, start)
                if blob:
                    try:
                        parsed = json.loads(blob)
                        if isinstance(parsed, dict):
                            states.append(parsed)
                        elif isinstance(parsed, list):
                            states.append({"items": parsed})
                    except Exception:
                        pass
            search_from = idx + len(token)

    next_match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>([\s\S]*?)</script>', html, re.I)
    if next_match:
        try:
            parsed = json.loads(next_match.group(1))
            if isinstance(parsed, dict):
                states.append(parsed)
        except Exception:
            pass
    return states


def fetch_moomoo_analyst_ratings(symbol: str) -> Optional[Dict[str, Any]]:
    clean_sym = _normalize_moomoo_stock_code(symbol)
    url = f"https://www.futunn.com/stock/{clean_sym}/forecast?lang=zh-cn"

    try:
        html = _fetch_text(url, timeout=5, referer="https://www.futunn.com/")
        if not html:
            return None
        states = _extract_initial_state_objects(html)
        if not states:
            return None
        state_data = states[0]
        analysis = state_data.get("analysis", {})
        analyst_rating = analysis.get("analystRating", {})
        target_estimate = analysis.get("targetEstimate", {}).get("targetInfo", {})
        if not analyst_rating or not isinstance(analyst_rating, dict):
            return None
        return {
            "analyst_rating": analyst_rating,
            "target_estimate": target_estimate
        }
    except Exception:
        return None


_EVENT_DATE_RE = re.compile(r"(20\d{2}|19\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")


def _parse_event_date(value: Any) -> Optional[str]:
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000.0
        if 946684800 <= number <= 4102444800:  # 2000-01-01 to 2100-01-01
            try:
                return datetime.fromtimestamp(number, timezone.utc).date().isoformat()
            except Exception:
                return None
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.isdigit():
        return _parse_event_date(float(text))

    match = _EVENT_DATE_RE.search(text)
    if match:
        try:
            year, month, day = map(int, match.groups())
            return datetime(year, month, day).date().isoformat()
        except Exception:
            return None

    # Common US style used by some calendar pages: 03/23/2026.
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2}|19\d{2})\b", text)
    if match:
        month, day, year = map(int, match.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except Exception:
            return None

    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
        try:
            return datetime.strptime(text[:19], fmt).date().isoformat()
        except Exception:
            pass
    return None


def _strip_html(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_text_value(obj: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        val = obj.get(key)
        if isinstance(val, (str, int, float)) and str(val).strip():
            return _strip_html(val)
    return ""


def _classify_event_context(path: List[str], obj: Dict[str, Any]) -> Optional[str]:
    text = " ".join(path + [str(k) for k in obj.keys()]).lower()
    dividend_keywords = [
        "dividend", "exdate", "ex_date", "ex-dividend", "recorddate", "record_date",
        "paydate", "pay_date", "distribution", "corporateaction", "corporate_action",
        "right", "rights", "split", "bonus", "cashdiv", "股息", "派息", "分红", "除权", "除息", "权益", "送股", "拆股",
    ]
    earnings_keywords = [
        "earning", "earnings", "eps", "revenue", "financialreport", "financial_report",
        "reportdate", "report_date", "fiscal", "quarter", "业绩", "财报", "财务报告", "披露", "公布业绩",
    ]

    if any(k in text for k in dividend_keywords):
        return "dividend"
    if any(k in text for k in earnings_keywords):
        return "earnings"
    return None


def _event_title(event_type: str, obj: Dict[str, Any]) -> str:
    title = _first_text_value(obj, [
        "title", "name", "eventName", "event_name", "description", "desc", "statement", "summary", "subject", "period", "fiscalPeriod", "fiscal_period",
    ])
    if title and len(title) <= 120:
        return title
    return "财报发布" if event_type == "earnings" else "权息变动"


def _event_detail(obj: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in [
        "eps", "actualEps", "actual_eps", "estimateEps", "estimated_eps", "revenue", "actualRevenue", "estimatedRevenue",
        "amount", "cash", "dividend", "dividendAmount", "currency", "process", "status", "record_date", "pay_date", "ex_date",
    ]:
        if key in obj and obj.get(key) not in [None, "", False]:
            val = _strip_html(obj.get(key))
            if val and len(val) <= 80:
                parts.append(f"{key}: {val}")
    return "；".join(parts[:5])


def _candidate_date_values(obj: Dict[str, Any]) -> List[Tuple[str, str]]:
    date_keys = [
        "date", "time", "timestamp", "reportDate", "report_date", "earningsDate", "earnings_date",
        "pubDate", "pub_date", "publishDate", "publish_date", "announceDate", "announce_date", "announcementDate", "announcement_date",
        "exDate", "ex_date", "recordDate", "record_date", "payDate", "pay_date", "paymentDate", "payment_date",
        "除权日", "除息日", "公布日", "披露日", "财报日", "派息日",
    ]
    out: List[Tuple[str, str]] = []
    lowered = {str(k).lower(): k for k in obj.keys()}
    for key in date_keys:
        real_key = obj.get(key, None)
        if key in obj:
            parsed = _parse_event_date(obj.get(key))
            if parsed:
                out.append((key, parsed))
        else:
            original = lowered.get(key.lower())
            if original is not None:
                parsed = _parse_event_date(obj.get(original))
                if parsed:
                    out.append((str(original), parsed))

    # Fallback: scan scalar values only when the object already looks event-like.
    for key, val in obj.items():
        if isinstance(val, (str, int, float)) and any(s in str(key).lower() for s in ["date", "time", "日期", "日"]):
            parsed = _parse_event_date(val)
            if parsed:
                out.append((str(key), parsed))
    return out


def _walk_event_objects(node: Any, path: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    path = path or []
    events: List[Dict[str, Any]] = []
    if isinstance(node, dict):
        event_type = _classify_event_context(path, node)
        if event_type:
            for date_key, date_value in _candidate_date_values(node):
                title = _event_title(event_type, node)
                detail = _event_detail(node)
                label = "财报发布" if event_type == "earnings" else "权息变动"
                events.append({
                    "id": f"{event_type}:{date_value}:{title[:48]}",
                    "type": event_type,
                    "date": date_value,
                    "dateField": date_key,
                    "title": title if title != label else label,
                    "label": label,
                    "detail": detail,
                    "source": "moomoo/futunn",
                })
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                events.extend(_walk_event_objects(value, path + [str(key)]))
    elif isinstance(node, list):
        for idx, item in enumerate(node[:2000]):
            if isinstance(item, (dict, list)):
                events.extend(_walk_event_objects(item, path + [str(idx)]))
    return events


def _dedupe_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for event in events:
        date = event.get("date")
        event_type = event.get("type")
        if not date or event_type not in {"earnings", "dividend"}:
            continue
        title = _strip_html(event.get("title") or event.get("label") or "")[:80]
        key = (event_type, date, title)
        if key not in by_key:
            event = dict(event)
            event["id"] = f"{event_type}:{date}:{len(by_key)}"
            by_key[key] = event
        else:
            old = by_key[key]
            if not old.get("detail") and event.get("detail"):
                old["detail"] = event.get("detail")
    return sorted(by_key.values(), key=lambda item: (item.get("date", ""), item.get("type", "")))[:80]


def fetch_moomoo_corporate_events(symbol: str) -> List[Dict[str, Any]]:
    clean_sym = _normalize_moomoo_stock_code(symbol)
    pages = [
        (f"https://www.futunn.com/stock/{clean_sym}/earnings?lang=zh-cn", "https://www.futunn.com/"),
        (f"https://www.futunn.com/stock/{clean_sym}/dividends?lang=zh-cn", "https://www.futunn.com/"),
        (f"https://www.futunn.com/stock/{clean_sym}/forecast?lang=zh-cn", "https://www.futunn.com/"),
        (f"https://www.moomoo.com/stock/{clean_sym}/earnings", "https://www.moomoo.com/"),
        (f"https://www.moomoo.com/stock/{clean_sym}/dividends", "https://www.moomoo.com/"),
    ]
    events: List[Dict[str, Any]] = []
    for url, referer in pages:
        html = _fetch_text(url, timeout=6, referer=referer)
        if not html:
            continue
        for state in _extract_initial_state_objects(html):
            events.extend(_walk_event_objects(state, [url]))
    return _dedupe_events(events)


def _fetch_yfinance_event_fallback(ticker: yf.Ticker, symbol: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    try:
        calendar_data = ticker.calendar
        if isinstance(calendar_data, pd.DataFrame):
            raw = calendar_data.to_dict()
        elif isinstance(calendar_data, dict):
            raw = calendar_data
        else:
            raw = {}
        for key, value in raw.items():
            if "earning" not in str(key).lower():
                continue
            values = value if isinstance(value, (list, tuple, set, pd.Series)) else [value]
            for val in values:
                parsed = _parse_event_date(val)
                if parsed:
                    events.append({
                        "id": f"earnings:{parsed}:yf",
                        "type": "earnings",
                        "date": parsed,
                        "dateField": str(key),
                        "title": "财报发布",
                        "label": "财报发布",
                        "detail": "yfinance fallback",
                        "source": "yfinance-fallback",
                    })
    except Exception:
        pass

    try:
        dividends = ticker.dividends
        if isinstance(dividends, pd.Series) and not dividends.empty:
            # Keep recent records. The chart range filter below will decide what is actually visible.
            for idx, amount in dividends.tail(80).items():
                parsed = _parse_event_date(idx)
                if parsed:
                    events.append({
                        "id": f"dividend:{parsed}:yf",
                        "type": "dividend",
                        "date": parsed,
                        "dateField": "ex_date",
                        "title": "权息变动",
                        "label": "权息变动",
                        "detail": f"dividend: {round(float(amount), 6)}",
                        "source": "yfinance-fallback",
                    })
    except Exception:
        pass
    return _dedupe_events(events)


def _date_from_index(index_value: Any) -> Optional[datetime.date]:
    try:
        return pd.Timestamp(index_value).date()
    except Exception:
        return None


def _build_event_markers(events: List[Dict[str, Any]], df: pd.DataFrame, interval: str) -> List[Dict[str, Any]]:
    if not events or df is None or df.empty:
        return []
    index_dates = [(_date_from_index(idx), idx) for idx in df.index]
    index_dates = [(d, idx) for d, idx in index_dates if d is not None]
    markers: List[Dict[str, Any]] = []
    used: set = set()

    for event in events:
        parsed = _parse_event_date(event.get("date"))
        if not parsed:
            continue
        try:
            target_date = datetime.strptime(parsed, "%Y-%m-%d").date()
        except Exception:
            continue

        chosen_idx = None
        for candle_date, idx in index_dates:
            if candle_date >= target_date:
                chosen_idx = idx
                break
        if chosen_idx is None:
            continue
        chosen_date = _date_from_index(chosen_idx)
        if chosen_date is None or abs((chosen_date - target_date).days) > 7:
            continue

        event_type = event.get("type")
        text = "E" if event_type == "earnings" else "D"
        color = "#2563eb" if event_type == "earnings" else "#f59e0b"
        key = (event.get("id"), _format_time(chosen_idx, interval))
        if key in used:
            continue
        used.add(key)
        markers.append({
            "time": _format_time(chosen_idx, interval),
            "position": "belowBar",
            "color": color,
            "shape": "circle",
            "text": text,
            "size": 1,
            "eventId": event.get("id"),
            "eventType": event_type,
            "eventDate": parsed,
            "eventLabel": event.get("label"),
        })
    return markers


def _merge_corporate_events(primary: List[Dict[str, Any]], fallback: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if primary:
        combined = primary + [event for event in fallback if event.get("type") == "dividend"]
    else:
        combined = fallback
    return _dedupe_events(combined)

def _indicator_series(df: pd.DataFrame, column: str, interval: str, name: str, color: str, series_type: str = "line") -> Dict[str, Any]:
    data = _line_series(df, column, interval)
    if series_type == "histogram":
        data = _hist_series(df, column, interval, "rgba(34,197,94,0.55)", "rgba(239,68,68,0.55)")
    return {"name": name, "type": series_type, "color": color, "data": data}


def _indicator_item(key: str, name_zh: str, name_en: str, pane: str, series: List[Dict[str, Any]], favorite: bool = False, note: str = "") -> Dict[str, Any]:
    return {
        "key": key,
        "nameZh": name_zh,
        "nameEn": name_en,
        "pane": pane,
        "favorite": favorite,
        "note": note,
        "series": series,
    }


def _build_indicator_payload(df: pd.DataFrame, interval: str) -> Dict[str, Any]:
    c = {
        "blue": "#38bdf8",
        "cyan": "#22d3ee",
        "yellow": "#f59e0b",
        "orange": "#fb923c",
        "purple": "#a78bfa",
        "pink": "#f472b6",
        "green": "#22c55e",
        "red": "#ef4444",
        "slate": "#94a3b8",
        "lime": "#84cc16",
        "teal": "#14b8a6",
    }
    items: List[Dict[str, Any]] = [
        _indicator_item("ma", "MA 移动平均线", "MA Moving Average", "price", [
            _indicator_series(df, "MA5", interval, "MA5", c["green"]),
            _indicator_series(df, "MA10", interval, "MA10", c["blue"]),
            _indicator_series(df, "MA20", interval, "MA20", c["yellow"]),
            _indicator_series(df, "MA60", interval, "MA60", c["purple"]),
        ], True),
        _indicator_item("ema", "EMA 指数移动平均线", "EMA Exponential MA", "price", [
            _indicator_series(df, "EMA5", interval, "EMA5", c["green"]),
            _indicator_series(df, "EMA10", interval, "EMA10", c["cyan"]),
            _indicator_series(df, "EMA20", interval, "EMA20", c["yellow"]),
            _indicator_series(df, "EMA60", interval, "EMA60", c["orange"]),
            _indicator_series(df, "EMA120", interval, "EMA120", c["pink"]),
            _indicator_series(df, "EMA250", interval, "EMA250", c["purple"]),
        ], True),
        _indicator_item("bbi", "BBI 多空指数", "BBI Bull/Bear Index", "price", [_indicator_series(df, "BBI", interval, "BBI", c["orange"])]),
        _indicator_item("sar", "SAR 停损点转向指标", "SAR Parabolic Stop and Reverse", "price", [_indicator_series(df, "PSAR", interval, "SAR", c["pink"])] , True),
        _indicator_item("alligator", "ALLIGAT 鳄鱼线指标", "Alligator", "price", [
            _indicator_series(df, "ALLIGATOR_JAW", interval, "Jaw", c["blue"]),
            _indicator_series(df, "ALLIGATOR_TEETH", interval, "Teeth", c["red"]),
            _indicator_series(df, "ALLIGATOR_LIPS", interval, "Lips", c["green"]),
        ]),
        _indicator_item("gmma", "GMMA 顾比均线", "GMMA Guppy Multiple MA", "price", [
            *[_indicator_series(df, f"GMMA_{p}", interval, f"G{p}", c["cyan"] if p <= 15 else c["purple"]) for p in [3, 5, 8, 10, 12, 15, 30, 35, 40, 45, 50, 60]]
        ]),
        _indicator_item("twap", "TWAP 时间加权平均价", "TWAP Time Weighted Average Price", "price", [_indicator_series(df, "TWAP20", interval, "TWAP20", c["slate"])]),
        _indicator_item("vwma", "VWMA 成交量加权移动平均", "VWMA Volume Weighted MA", "price", [_indicator_series(df, "VWMA20", interval, "VWMA20", c["teal"])]),
        _indicator_item("wma", "WMA 加权移动平均线", "WMA Weighted MA", "price", [_indicator_series(df, "WMA20", interval, "WMA20", c["blue"])]),
        _indicator_item("tema", "TEMA 三重指数移动平均线", "TEMA Triple EMA", "price", [_indicator_series(df, "TEMA20", interval, "TEMA20", c["green"])]),
        _indicator_item("dema", "DEMA 双重指数移动平均线", "DEMA Double EMA", "price", [_indicator_series(df, "DEMA20", interval, "DEMA20", c["yellow"])]),
        _indicator_item("hma", "HMA 船体移动平均线", "HMA Hull MA", "price", [_indicator_series(df, "HMA20", interval, "HMA20", c["pink"])]),
        _indicator_item("lsma", "LSMA 最小二乘移动平均线", "LSMA Least Squares MA", "price", [_indicator_series(df, "LSMA25", interval, "LSMA25", c["orange"])]),
        _indicator_item("tsf", "TSF 时间序列预测指标", "TSF Time Series Forecast", "price", [_indicator_series(df, "TSF14", interval, "TSF14", c["lime"])]),
        _indicator_item("kc", "KC 肯特纳通道", "Keltner Channel", "price", [
            _indicator_series(df, "KC_Upper", interval, "KC Upper", c["orange"]),
            _indicator_series(df, "KC_Middle", interval, "KC Mid", c["slate"]),
            _indicator_series(df, "KC_Lower", interval, "KC Lower", c["orange"]),
        ]),

        _indicator_item("macd", "MACD 指数平滑异同移动平均线", "MACD", "pane", [
            _indicator_series(df, "MACD_Hist", interval, "Hist", c["slate"], "histogram"),
            _indicator_series(df, "MACD", interval, "MACD", c["blue"]),
            _indicator_series(df, "MACD_Signal", interval, "Signal", c["yellow"]),
        ], True),
        _indicator_item("dma", "DMA 平行线差指标", "DMA Difference of MA", "pane", [
            _indicator_series(df, "DMA", interval, "DMA", c["blue"]),
            _indicator_series(df, "DMA_SIGNAL", interval, "Signal", c["yellow"]),
        ], True),
        _indicator_item("dmi", "DMI 趋向指标", "DMI Directional Movement", "pane", [
            _indicator_series(df, "PLUS_DI", interval, "+DI", c["green"]),
            _indicator_series(df, "MINUS_DI", interval, "-DI", c["red"]),
            _indicator_series(df, "ADX", interval, "ADX", c["yellow"]),
        ]),
        _indicator_item("emv", "EMV 简易波动指标", "EMV Ease of Movement", "pane", [
            _indicator_series(df, "EMV", interval, "EMV", c["blue"]),
            _indicator_series(df, "EMV_SIGNAL", interval, "Signal", c["yellow"]),
        ], True),
        _indicator_item("atr", "ATR 真实波幅", "ATR Average True Range", "pane", [_indicator_series(df, "ATR14", interval, "ATR14", c["orange"])]),
        _indicator_item("fisher", "FISHER 费雪变换", "Fisher Transform", "pane", [
            _indicator_series(df, "FISHER", interval, "Fisher", c["blue"]),
            _indicator_series(df, "FISHER_SIGNAL", interval, "Signal", c["yellow"]),
        ]),
        _indicator_item("iv", "IV 隐含波动率", "IV Implied Volatility", "pane", [_indicator_series(df, "IV", interval, "IV proxy", c["pink"])] , True, "OHLCV source has no true option-chain IV; this is realized-volatility proxy."),
        _indicator_item("iv_rank", "IV_RANK 隐含波动率等级", "IV Rank", "pane", [_indicator_series(df, "IV_RANK", interval, "IV Rank", c["purple"])]),
        _indicator_item("iv_percentile", "IV_PERCENTILE 隐含波动率百分位", "IV Percentile", "pane", [_indicator_series(df, "IV_PERCENTILE", interval, "IV Percentile", c["purple"])]),
        _indicator_item("volat", "VOLAT 历史波动率", "Historical Volatility", "pane", [_indicator_series(df, "VOLAT", interval, "HV20", c["pink"])]),
        _indicator_item("vmacd", "VMACD 量指数平滑异同", "Volume MACD", "pane", [
            _indicator_series(df, "VMACD", interval, "VMACD", c["blue"]),
            _indicator_series(df, "VMACD_SIGNAL", interval, "Signal", c["yellow"]),
        ]),
        _indicator_item("trix", "TRIX 三重指数平滑平均线", "TRIX", "pane", [
            _indicator_series(df, "TRIX", interval, "TRIX", c["blue"]),
            _indicator_series(df, "TRIX_SIGNAL", interval, "Signal", c["yellow"]),
        ]),
        _indicator_item("priceosc", "PRICEOSC 价格摆动指标", "Price Oscillator", "pane", [_indicator_series(df, "PRICEOSC", interval, "PriceOSC", c["cyan"])]),
        _indicator_item("ddi", "DDI 方向标准离差指数", "DDI Directional Deviation", "pane", [_indicator_series(df, "DDI", interval, "DDI", c["blue"])]),
        _indicator_item("mi", "MI 动量指标", "Mass Index", "pane", [_indicator_series(df, "MI", interval, "MI", c["orange"])]),
        _indicator_item("si", "SI 摆动指标", "Swing Index", "pane", [_indicator_series(df, "SI", interval, "SI", c["purple"])]),
        _indicator_item("ao", "AO 动量震荡指标", "Awesome Oscillator", "pane", [_indicator_series(df, "AO", interval, "AO", c["green"], "histogram")]),
        _indicator_item("tower", "TOWER 宝塔线", "Tower", "pane", [_indicator_series(df, "TOWER", interval, "Tower", c["pink"], "histogram")]),
        _indicator_item("ewo", "EWO 艾略特波浪震荡指标", "Elliott Wave Oscillator", "pane", [_indicator_series(df, "EWO", interval, "EWO", c["cyan"], "histogram")]),
        _indicator_item("rvi", "RVI 相对离散指标", "RVI Relative Vigor", "pane", [
            _indicator_series(df, "RVI", interval, "RVI", c["blue"]),
            _indicator_series(df, "RVI_SIGNAL", interval, "Signal", c["yellow"]),
        ]),
        _indicator_item("hlvol", "HLVOL HL波动率", "HL Volatility", "pane", [_indicator_series(df, "HLVOL", interval, "HLVOL", c["pink"])]),
        _indicator_item("rmi", "RMI 相对动量指数", "RMI Relative Momentum Index", "pane", [_indicator_series(df, "RMI", interval, "RMI", c["purple"])]),
        _indicator_item("smie", "SMIE 随机动量指数", "SMI Ergodic", "pane", [
            _indicator_series(df, "SMIE", interval, "SMIE", c["blue"]),
            _indicator_series(df, "SMIE_SIGNAL", interval, "Signal", c["yellow"]),
        ]),
        _indicator_item("smieo", "SMIEO 随机动量指数震荡", "SMI Ergodic Oscillator", "pane", [_indicator_series(df, "SMIEO", interval, "SMIEO", c["green"], "histogram")]),
        _indicator_item("aroon", "AROON 阿隆指标", "Aroon", "pane", [
            _indicator_series(df, "AROON_UP", interval, "Aroon Up", c["green"]),
            _indicator_series(df, "AROON_DOWN", interval, "Aroon Down", c["red"]),
        ]),
        _indicator_item("chop", "CHOP 波动指数", "Choppiness Index", "pane", [_indicator_series(df, "CHOP", interval, "CHOP", c["orange"])]),
        _indicator_item("hadiff", "HADIFF 平均K线差", "Heikin-Ashi Diff", "pane", [_indicator_series(df, "HADIFF", interval, "HADIFF", c["blue"], "histogram")]),
        _indicator_item("er", "ER 效率比", "Efficiency Ratio", "pane", [_indicator_series(df, "ER", interval, "ER", c["lime"])]),
        _indicator_item("squeeze", "SQUEEZE 挤牌指标", "Squeeze Momentum", "pane", [_indicator_series(df, "SQUEEZE", interval, "Squeeze", c["green"], "histogram")]),
        _indicator_item("fo", "FO 预测震荡指标", "Forecast Oscillator", "pane", [_indicator_series(df, "FO", interval, "FO", c["cyan"])]),
        _indicator_item("kdj", "KDJ 随机指标", "KDJ", "pane", [
            _indicator_series(df, "KDJ_K", interval, "K", c["blue"]),
            _indicator_series(df, "KDJ_D", interval, "D", c["yellow"]),
            _indicator_series(df, "KDJ_J", interval, "J", c["pink"]),
        ]),
        _indicator_item("kama", "KAMA 适应性移动平均线", "KAMA Kaufman Adaptive MA", "price", [_indicator_series(df, "KAMA30", interval, "KAMA30", c["orange"])]),
        _indicator_item("mfi", "MFI 资金流量指标", "MFI Money Flow Index", "pane", [_indicator_series(df, "MFI14", interval, "MFI", c["cyan"])]),
        _indicator_item("obv", "OBV 能量潮指标", "OBV On Balance Volume", "pane", [_indicator_series(df, "OBV", interval, "OBV", c["purple"])]),
        _indicator_item("cci", "CCI 顺势指标", "CCI Commodity Channel Index", "pane", [_indicator_series(df, "CCI14", interval, "CCI", c["yellow"])]),
        _indicator_item("bop", "BOP 均势指标", "BOP Balance of Power", "pane", [_indicator_series(df, "BOP", interval, "BOP", c["green"])]),
        _indicator_item("ultosc", "ULTOSC 终极波动指标", "Ultimate Oscillator", "pane", [_indicator_series(df, "ULTOSC", interval, "UltOsc", c["orange"])]),
        _indicator_item("roc", "ROC 变动率指标", "ROC Rate of Change", "pane", [_indicator_series(df, "ROC10", interval, "ROC", c["cyan"])]),
        _indicator_item("ppo", "PPO 价格震荡百分比", "PPO Percentage Price Osc", "pane", [_indicator_series(df, "PPO", interval, "PPO", c["blue"])]),
        _indicator_item("apo", "APO 绝对价格振荡器", "APO Absolute Price Osc", "pane", [_indicator_series(df, "APO", interval, "APO", c["yellow"])]),
        _indicator_item("cmo", "CMO 钱德动量摆动指标", "CMO Chande Momentum Osc", "pane", [_indicator_series(df, "CMO14", interval, "CMO", c["pink"])]),
        _indicator_item("natr", "NATR 归一化波动幅度均值", "NATR Normalized ATR", "pane", [_indicator_series(df, "NATR14", interval, "NATR", c["lime"])]),
        _indicator_item("ad", "AD 累积/派发线", "AD Accumulation/Distribution", "pane", [_indicator_series(df, "AD", interval, "AD", c["teal"])]),
        _indicator_item("adosc", "ADOSC 佳庆离散指标", "ADOSC Chaikin Oscillator", "pane", [_indicator_series(df, "ADOSC", interval, "ADOSC", c["red"])]),
    ]
    return {item["key"]: item for item in items}


def _indicator_catalog_from_payload(indicators: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "key": item["key"],
            "nameZh": item["nameZh"],
            "nameEn": item["nameEn"],
            "pane": item["pane"],
            "favorite": item.get("favorite", False),
            "note": item.get("note", ""),
        }
        for item in indicators.values()
    ]


def _build_full_payload(
    symbol: str, period: str, interval: str, hist: pd.DataFrame,
    meta: TickerMeta, rating_data: Optional[Dict[str, Any]],
    quote_info: Optional[Dict[str, Any]], fast_info: Optional[Dict[str, Any]],
    corporate_events: List[Dict[str, Any]], event_source: str,
    cache_hit: bool, stale: bool = False,
) -> Dict[str, Any]:
    """Assemble the final JSON payload from pre-fetched components."""
    latest = hist.iloc[-1]
    previous_close = hist["Close"].iloc[-2] if len(hist) >= 2 else np.nan
    change = latest["Close"] - previous_close if not pd.isna(previous_close) else np.nan
    change_pct = (change / previous_close) * 100 if previous_close and not pd.isna(previous_close) else np.nan

    score_dict = {}
    if rating_data:
        ar = rating_data["analyst_rating"]
        te = rating_data["target_estimate"]

        def _get_pct(val) -> float:
            if val is None or val is False:
                return 0.0
            try:
                return float(val)
            except:
                return 0.0

        strong_buy = _get_pct(ar.get("strongBuy"))
        buy_val = _get_pct(ar.get("buy"))
        hold = _get_pct(ar.get("hold"))
        underperform = _get_pct(ar.get("underperform"))
        sell_val = _get_pct(ar.get("sell"))

        buy_score = round(strong_buy + buy_val)
        sell_score = round(sell_val + underperform)

        consensus_rating_val = ar.get("rating", 3)
        if consensus_rating_val == 5:
            rating_str = "strongBuy"
        elif consensus_rating_val == 4:
            rating_str = "buy"
        elif consensus_rating_val <= 2:
            rating_str = "sell"
        else:
            rating_str = "hold"

        if consensus_rating_val == 5:
            signal = "strong_buy_candidate"
        elif consensus_rating_val >= 4:
            signal = "buy_candidate"
        elif consensus_rating_val <= 2:
            signal = "sell_candidate"
        else:
            signal = "neutral"

        consensus_labels = {
            5: ("强力买入", "Strong Buy"),
            4: ("买入", "Buy"),
            3: ("持有", "Hold"),
            2: ("弱于大盘", "Underperform"),
            1: ("卖出", "Sell"),
        }
        lbl_zh, lbl_en = consensus_labels.get(consensus_rating_val, ("持有", "Hold"))

        total_analysts = ar.get("total", 0)
        reasons = [
            f"基于 {total_analysts} 位分析师评级 / Based on {total_analysts} analysts",
            f"共识评级：{lbl_zh} / Consensus: {lbl_en}",
        ]
        if te and te.get("average"):
            reasons.append(
                f"12个月均价：{te.get('average')} (最高 {te.get('highest')} / 最低 {te.get('lowest')}) / 12M Target: {te.get('average')} (High {te.get('highest')} / Low {te.get('lowest')})"
            )

        score_dict = {
            "buy": buy_score,
            "sell": sell_score,
            "rating": rating_str,
            "source": "analyst",
            "signal": signal,
            "reasons": reasons,
            "details": {
                "strongBuy": strong_buy,
                "buy": buy_val,
                "hold": hold,
                "underperform": underperform,
                "sell": sell_val,
                "total": total_analysts,
                "highest": _safe_float(te.get("highest")),
                "average": _safe_float(te.get("average")),
                "lowest": _safe_float(te.get("lowest")),
            }
        }
    else:
        tech_score = _latest_score(hist)
        buy_score = tech_score.get("buy", 0)
        sell_score = tech_score.get("sell", 0)

        if buy_score >= 60 and buy_score > sell_score:
            rating_str = "buy"
        elif sell_score >= 60 and sell_score > buy_score:
            rating_str = "sell"
        else:
            rating_str = "hold"

        score_dict = {
            "buy": buy_score,
            "sell": sell_score,
            "rating": rating_str,
            "source": "technical",
            "signal": tech_score.get("signal", "neutral"),
            "reasons": tech_score.get("reasons", []),
            "details": {
                "buyScore": buy_score,
                "sellScore": sell_score,
            }
        }

    indicators = _build_indicator_payload(hist, interval)
    event_markers = _build_event_markers(corporate_events, hist, interval)

    return {
        "symbol": meta.symbol,
        "name": meta.name,
        "exchange": meta.exchange,
        "currency": meta.currency,
        "range": period,
        "interval": interval,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "lastPrice": _safe_float(latest["Close"]),
        "previousClose": _safe_float(previous_close),
        "change": _safe_float(change),
        "changePct": _safe_float(change_pct),
        "quoteStats": _build_quote_stats(hist, quote_info, fast_info, previous_close),
        "candles": _series_from_columns(hist, ["Open", "High", "Low", "Close"], interval),
        "volume": _volume_series(hist, interval),
        "sma20": _line_series(hist, "SMA20", interval),
        "sma50": _line_series(hist, "SMA50", interval),
        "bbUpper": _line_series(hist, "BB_Upper", interval),
        "bbMiddle": _line_series(hist, "BB_Middle", interval),
        "bbLower": _line_series(hist, "BB_Lower", interval),
        "macd": _line_series(hist, "MACD", interval),
        "macdSignal": _line_series(hist, "MACD_Signal", interval),
        "macdHist": _hist_series(hist, "MACD_Hist", interval, "rgba(34,197,94,0.55)", "rgba(239,68,68,0.55)"),
        "rsi": _line_series(hist, "RSI", interval),
        "markers": _generate_markers(hist, interval),
        "events": corporate_events,
        "eventMarkers": event_markers,
        "eventSource": event_source,
        "indicators": indicators,
        "indicatorCatalog": _indicator_catalog_from_payload(indicators),
        "score": score_dict,
        "cache": {"hit": cache_hit, "stale": stale, "ttlSeconds": CACHE_TTL_SECONDS},
        "disclaimer": "Signals are technical candidates, not financial advice.",
    }


def _refresh_payload_background(symbol: str, period: str, interval: str, scope: str) -> None:
    """Background task: re-fetch from external sources and update both caches."""
    _current_user_id.set(scope)
    try:
        ticker = yf.Ticker(symbol)
        hist = _get_ohlcv_cached(symbol, period, interval)
        if hist is None or hist.empty:
            return
        hist = hist.rename(columns={c: str(c).title() for c in hist.columns})
        needed = ["Open", "High", "Low", "Close", "Volume"]
        if any(col not in hist.columns for col in needed):
            return
        hist = hist[needed].dropna(subset=["Open", "High", "Low", "Close"])
        hist["Volume"] = hist["Volume"].fillna(0)
        hist = _compute_indicators(hist)

        meta = _get_meta_cached(ticker, symbol)
        rating_data = _get_rating_cached(symbol)
        quote_info = _get_quote_info_cached(ticker, symbol)
        fast_info = _get_fast_info(ticker)
        corporate_events, event_source = _get_events_cached(symbol, ticker)

        payload = _build_full_payload(
            symbol, period, interval, hist, meta, rating_data,
            quote_info, fast_info,
            corporate_events, event_source, cache_hit=False, stale=False,
        )
        cache_key = f"{scope}:payload:{symbol}:{period}:{interval}"
        _CACHE[cache_key] = (time.time(), payload)
        _get_db_cache().set("payload", cache_key, payload)
    except Exception:
        pass  # Background refresh is best-effort.


def _fetch_stock_payload(symbol: str, period: str, interval: str) -> Dict[str, Any]:
    if period not in ALLOWED_RANGES:
        raise HTTPException(status_code=400, detail=f"Unsupported range: {period}")
    if interval not in ALLOWED_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval: {interval}")

    symbol = _clean_symbol(symbol)
    scope = _current_user_id.get()
    cache_key = f"{scope}:payload:{symbol}:{period}:{interval}"
    now = time.time()
    payload_ttl = _interval_cache_ttl(interval)

    # L1: in-memory cache (5 min).
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < payload_ttl and "quoteStats" in cached[1]:
        payload = cached[1].copy()
        payload["cache"] = {"hit": True, "stale": False, "ttlSeconds": payload_ttl}
        return payload

    # L2: SQLite stale-while-revalidate — return stale data instantly,
    # then refresh in background.
    stale_data, is_stale = _get_db_cache().get_stale(
        "payload", cache_key, max_stale_age=payload_ttl * 4, max_age=payload_ttl,
    )
    if stale_data and "quoteStats" not in stale_data:
        stale_data = None
        is_stale = False

    if stale_data and is_stale:
        # Serve stale immediately, refresh in background thread.
        stale_data["cache"] = {"hit": True, "stale": True, "ttlSeconds": payload_ttl}
        _CACHE[cache_key] = (now, stale_data)
        threading.Thread(
            target=_refresh_payload_background,
            args=(symbol, period, interval, scope),
            daemon=True,
        ).start()
        return stale_data
    elif stale_data and not is_stale:
        # Fresh in SQLite — promote to L1.
        _CACHE[cache_key] = (now, stale_data)
        stale_data["cache"] = {"hit": True, "stale": False, "ttlSeconds": payload_ttl}
        return stale_data

    # L3: Full fetch from external sources.
    ticker = yf.Ticker(symbol)
    hist = _get_ohlcv_cached(symbol, period, interval)

    if hist is None or hist.empty:
        raise HTTPException(status_code=502, detail=f"No price data found for {symbol} from yfinance.")

    hist = hist.rename(columns={c: str(c).title() for c in hist.columns})
    needed = ["Open", "High", "Low", "Close", "Volume"]
    missing = [col for col in needed if col not in hist.columns]
    if missing:
        raise HTTPException(status_code=502, detail=f"Missing columns from data source: {missing}")

    hist = hist[needed].dropna(subset=["Open", "High", "Low", "Close"])
    hist["Volume"] = hist["Volume"].fillna(0)
    hist = _compute_indicators(hist)

    meta = _get_meta_cached(ticker, symbol)
    rating_data = _get_rating_cached(symbol)
    quote_info = _get_quote_info_cached(ticker, symbol)
    fast_info = _get_fast_info(ticker)
    corporate_events, event_source = _get_events_cached(symbol, ticker)

    payload = _build_full_payload(
        symbol, period, interval, hist, meta, rating_data,
        quote_info, fast_info,
        corporate_events, event_source, cache_hit=False, stale=False,
    )

    # Store in both L1 and L2.
    _CACHE[cache_key] = (now, payload)
    _get_db_cache().set("payload", cache_key, payload)
    return payload


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/search")
def search_ticker(q: str = Query(..., min_length=1, max_length=30)) -> Dict[str, Any]:
    """Fuzzy symbol/company-name search with ranked equity/ETF suggestions."""
    query = q.strip()
    cache_key = f"search:v2:{query.casefold()}"
    cached = _get_db_cache().get("market_search", cache_key, max_age=24 * 3600)
    if cached:
        return cached
    try:
        payload = {
            "query": query,
            "results": search_equities(query, limit=8),
            "source": "Yahoo Finance Search via yfinance",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        _get_db_cache().set("market_search", cache_key, payload)
        return payload
    except Exception as exc:
        # A failed fuzzy lookup must not turn into a fabricated exact match.
        return {"query": query, "results": [], "source": "Yahoo Finance Search via yfinance", "status": "missing", "detail": str(exc)}


def _market_universe_cached() -> List[Dict[str, Any]]:
    cache_key = "us:market-universe:v1"
    cached = _get_db_cache().get("market_universe", cache_key, max_age=5 * 60)
    if cached and isinstance(cached.get("items"), list):
        return cached["items"]
    items = merge_market_universe(count_each=60)
    _get_db_cache().set("market_universe", cache_key, {"items": items})
    return items


@app.get("/api/screener")
def stock_screener(
    request: Request,
    preset: str = Query("market", description="market or a yfinance predefined equity screener"),
    limit: int = Query(200, ge=1, le=500),
) -> Dict[str, Any]:
    try:
        if preset == "market":
            items = _market_universe_cached()
        elif preset in SCREENER_PRESETS:
            cache_key = f"preset:{preset}:{limit}"
            cached = _get_db_cache().get("market_screener", cache_key, max_age=5 * 60)
            if cached:
                items = cached.get("items", [])
            else:
                items = fetch_screen_preset(preset, min(limit, 250))
                _get_db_cache().set("market_screener", cache_key, {"items": items})
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported screener preset: {preset}")
        params = request.query_params

        def requested_float(name: str) -> Optional[float]:
            try:
                return float(params[name]) if name in params and params[name] != "" else None
            except (TypeError, ValueError):
                return None

        numeric_filters = [
            ("price_min", "price", lambda value, bound: value >= bound),
            ("price_max", "price", lambda value, bound: value <= bound),
            ("market_cap_min", "marketCap", lambda value, bound: value >= bound),
            ("market_cap_max", "marketCap", lambda value, bound: value <= bound),
            ("change_pct_min", "changePct", lambda value, bound: value >= bound),
            ("change_pct_max", "changePct", lambda value, bound: value <= bound),
            ("volume_min", "volume", lambda value, bound: value >= bound),
            ("avg_volume_min", "averageVolume", lambda value, bound: value >= bound),
            ("relative_volume_min", "volumeRatio", lambda value, bound: value >= bound),
            ("beta_max", "beta", lambda value, bound: value <= bound),
            ("pe_min", "peTtm", lambda value, bound: value >= bound),
            ("pe_max", "peTtm", lambda value, bound: value <= bound),
            ("forward_pe_max", "peForward", lambda value, bound: value <= bound),
            ("pb_max", "pb", lambda value, bound: value <= bound),
            ("dividend_yield_pct_min", "dividendYieldPct", lambda value, bound: value >= bound),
        ]
        filtered = list(items)
        warning_parts: List[str] = []
        market = params.get("market", "US").upper()
        if market not in {"US", "ALL"}:
            filtered = []
            warning_parts.append(f"当前Yahoo screener股票池仅覆盖US，{market}数据未接入")
        exchanges = {item.strip().upper() for item in params.get("exchanges", "").split(",") if item.strip()}
        if exchanges:
            def exchange_code(row: Dict[str, Any]) -> str:
                value = str(row.get("exchange") or "").upper()
                if "NASDAQ" in value: return "NASDAQ"
                if "NYSE" in value or "NEW YORK" in value: return "NYSE"
                if "AMEX" in value or "NYSEAMERICAN" in value: return "AMEX"
                return value
            filtered = [row for row in filtered if exchange_code(row) in exchanges]
        sector = params.get("sector")
        industry = params.get("industry")
        if sector:
            filtered = [row for row in filtered if str(row.get("sector") or "").casefold() == sector.casefold()]
        if industry:
            filtered = [row for row in filtered if industry.casefold() in str(row.get("industry") or "").casefold()]
        for query_key, row_key, comparator in numeric_filters:
            bound = requested_float(query_key)
            if bound is None:
                continue
            filtered = [row for row in filtered if row.get(row_key) is not None and comparator(float(row[row_key]), bound)]
        technical = params.get("technical", "")
        if technical == "above_ema50":
            filtered = [row for row in filtered if row.get("above50d")]
        elif technical == "above_ema20":
            warning_parts.append("数据源未提供全市场EMA20，已使用50日均线作为可用近似条件")
            filtered = [row for row in filtered if row.get("above50d")]
        if params.get("near_52w_high") == "true" or requested_float("distance_from_52w_high_pct_max") is not None:
            max_distance = requested_float("distance_from_52w_high_pct_max")
            if max_distance is None: max_distance = 3.0
            filtered = [row for row in filtered if row.get("price") is not None and row.get("fiftyTwoWeekHigh") and (float(row["fiftyTwoWeekHigh"]) - float(row["price"])) / float(row["fiftyTwoWeekHigh"]) * 100 <= max_distance]
        unsupported_requested = [
            key for key in ("peg_max", "ps_max", "eps_growth_pct_min", "revenue_growth_pct_min", "payout_ratio_pct_max", "roe_pct_min", "debt_to_equity_max", "rsi14_min", "rsi14_max", "atr_pct_min")
            if params.get(key) not in {None, ""}
        ]
        if unsupported_requested:
            warning_parts.append("当前实时股票池缺少部分高级字段：" + ", ".join(unsupported_requested))

        sort_key_map = {
            "market_cap": "marketCap", "change_pct": "changePct", "volume": "volume",
            "relative_volume": "volumeRatio", "price": "price", "pe": "peTtm",
        }
        sort_key = sort_key_map.get(params.get("sort_by", "market_cap"), "marketCap")
        reverse = params.get("sort_order", "desc").lower() != "asc"
        filtered.sort(key=lambda row: (row.get(sort_key) is not None, row.get(sort_key) or 0), reverse=reverse)
        page = max(1, int(params.get("page", "1") or 1))
        page_size = max(1, min(100, int(params.get("page_size", "50") or 50)))
        total = len(filtered)
        pages = math.ceil(total / page_size) if total else 0
        paged = filtered[(page - 1) * page_size: page * page_size]

        def with_aliases(row: Dict[str, Any]) -> Dict[str, Any]:
            result = dict(row)
            result.update({
                "change_pct": row.get("changePct"), "market_cap": row.get("marketCap"),
                "avg_volume": row.get("averageVolume"), "relative_volume": row.get("volumeRatio"),
                "pe": row.get("peTtm"), "forward_pe": row.get("peForward"),
                "dividend_yield": (row.get("dividendYieldPct") / 100) if row.get("dividendYieldPct") is not None else None,
                "technical_status": "站上50/200日均线" if row.get("above50d") and row.get("above200d") else "站上50日均线" if row.get("above50d") else "弱于50日均线",
            })
            return result

        return {
            "status": "ready" if items else "missing",
            "preset": preset,
            "items": [with_aliases(row) for row in paged],
            "count": len(paged),
            "page": page,
            "pages": pages,
            "total": total,
            "source": "Yahoo Finance Equity Screener via yfinance",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "as_of": datetime.now(timezone.utc).isoformat(),
            "warning": "；".join(warning_parts) if warning_parts else None,
            "availableSectors": sorted({str(row.get("sector")) for row in items if row.get("sector") not in {None, "", "Unknown"}}),
            "availablePresets": ["market", *SCREENER_PRESETS],
        }
    except HTTPException:
        raise
    except Exception as exc:
        return {"status": "missing", "preset": preset, "items": [], "count": 0, "source": "Yahoo Finance Equity Screener via yfinance", "detail": str(exc)}


@app.get("/api/market/heatmap")
def market_heatmap(
    limit: int = Query(220, ge=1, le=500),
    market: str = Query("US"),
    size_by: str = Query("market_cap"),
) -> Dict[str, Any]:
    try:
        if market.upper() != "US":
            return {
                "status": "missing", "market": market.upper(), "items": [], "count": 0,
                "source": "Yahoo Finance via yfinance", "stale": False,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "detail": f"当前热力图股票池尚未接入 {market.upper()} 市场，未展示模拟数据。",
            }
        items = _market_universe_cached()
        def heatmap_weight(item: Dict[str, Any]) -> float:
            if size_by == "turnover":
                return float(item.get("price") or 0) * float(item.get("volume") or 0)
            return float(item.get("marketCap") or item.get("volume") or 0)
        ranked = sorted(items, key=heatmap_weight, reverse=True)[:limit]
        now_iso = datetime.now(timezone.utc).isoformat()
        heatmap_items = []
        for row in ranked:
            item = dict(row)
            item.update({
                "change_pct": row.get("changePct"),
                "market_cap": row.get("marketCap"),
                "turnover": (row.get("price") * row.get("volume")) if row.get("price") is not None and row.get("volume") is not None else None,
                "data_time": datetime.fromtimestamp(row["regularMarketTime"], timezone.utc).isoformat() if row.get("regularMarketTime") else None,
            })
            heatmap_items.append(item)
        return {
            "status": "ok" if ranked else "missing",
            "market": "US",
            "items": heatmap_items,
            "count": len(ranked),
            "sizeMetric": "marketCap with volume fallback",
            "colorMetric": "regularMarketChangePercent",
            "source": "Yahoo Finance Equity Screener + Sector tables via yfinance",
            "updatedAt": now_iso,
            "as_of": now_iso,
            "stale": False,
        }
    except Exception as exc:
        return {"status": "missing", "market": "US", "items": [], "count": 0, "source": "Yahoo Finance via yfinance", "detail": str(exc)}


@app.get("/api/options/{symbol}")
def stock_options(
    symbol: str,
    expiration: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to nearest listed expiration"),
) -> Dict[str, Any]:
    clean = _clean_symbol(symbol)
    cache_key = f"{clean}:{expiration or 'nearest'}"
    cached = _get_db_cache().get("options", cache_key, max_age=60)
    if cached:
        cached["cache"] = {"hit": True, "ttlSeconds": 60}
        return cached
    try:
        payload = option_chain_payload(clean, expiration)
        payload["cache"] = {"hit": False, "ttlSeconds": 60}
        _get_db_cache().set("options", cache_key, payload)
        return payload
    except Exception as exc:
        return {
            "status": "missing", "symbol": clean, "expirationDates": [],
            "selectedExpiration": expiration, "calls": [], "puts": [],
            "missingFields": ["option_chain"], "source": "Yahoo Finance via yfinance",
            "detail": str(exc), "asOf": datetime.now(timezone.utc).isoformat(),
        }


@app.get("/api/events/{symbol}")
def stock_events(symbol: str) -> Dict[str, Any]:
    clean = _clean_symbol(symbol)
    moomoo_events = fetch_moomoo_corporate_events(clean)
    fallback_events = _fetch_yfinance_event_fallback(yf.Ticker(clean), clean)
    events = _merge_corporate_events(moomoo_events, fallback_events)
    return {
        "symbol": clean,
        "events": events,
        "source": "moomoo/futunn" if moomoo_events else ("yfinance-fallback" if fallback_events else "none"),
    }


@app.get("/api/analyze/{symbol}")
def analyze_stock(
    symbol: str,
    period: str = Query("1y", description="1mo, 3mo, 6mo, 1y, 2y, 5y, max"),
    interval: str = Query("1d", description="1d, 1wk, 1mo, 1h, 30m, 15m, 5m"),
) -> Dict[str, Any]:
    return _fetch_stock_payload(symbol, period, interval)


def _trade_plan_frame(symbol: str, period: str, interval: str) -> pd.DataFrame:
    frame = _get_ohlcv_cached(symbol, period, interval)
    if frame is None or frame.empty:
        raise HTTPException(status_code=502, detail=f"No {interval} market data found for {symbol}.")
    frame = frame.rename(columns={column: str(column).title() for column in frame.columns})
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise HTTPException(status_code=502, detail=f"Missing columns from {interval} data: {missing}")
    frame = frame[required].dropna(subset=["Open", "High", "Low", "Close"])
    frame["Volume"] = frame["Volume"].fillna(0)
    return _compute_indicators(frame)


@app.get("/api/trade-plan/{symbol}")
def calculate_trade_plan(
    symbol: str,
    horizon: str = Query("intraday_short", description="intraday_short, intraday_trend, swing_1_3d, weekly"),
    cost_bps: float = Query(5.0, ge=0.0, le=100.0, description="Estimated one-way fees and slippage in basis points"),
) -> Dict[str, Any]:
    clean = _clean_symbol(symbol)
    config = HORIZONS.get(horizon)
    if config is None:
        raise HTTPException(status_code=400, detail=f"Unsupported trade-plan horizon: {horizon}")
    execution_period, execution_interval = config["execution"]
    confirmation_period, confirmation_interval = config["confirmation"]
    context_period, context_interval = config["context"]
    execution = _trade_plan_frame(clean, execution_period, execution_interval)
    confirmation = _trade_plan_frame(clean, confirmation_period, confirmation_interval)
    if (context_period, context_interval) == (confirmation_period, confirmation_interval):
        context = confirmation
    else:
        context = _trade_plan_frame(clean, context_period, context_interval)
    return build_trade_plan(
        clean,
        horizon,
        execution,
        confirmation,
        context,
        execution_interval=execution_interval,
        cost_bps=cost_bps,
    )


@app.get("/api/cache/stats")
def cache_stats() -> Dict[str, Any]:
    """Return cache statistics for current user scope."""
    scope = _current_user_id.get()
    l1_count = sum(1 for k in _CACHE if k.startswith(f"{scope}:"))
    return {
        "scope": scope,
        "l1_memory_entries": l1_count,
        "l2_sqlite": _get_db_cache().stats(),
    }


# ---------------------------------------------------------------------------
# CLI-driven indicator button toggle state
# The CLI sends POST /api/indicator/toggle to simulate button clicks.
# The frontend polls GET /api/indicator/state and calls .click() on the
# corresponding <button> when it detects a change.
#
# Buttons:
#   "volume" | "ma" | "events" | "reset"  — header control buttons
#   "indicator" + "key": "<catalog_key>"   — sidebar indicator catalog items
# ---------------------------------------------------------------------------

# All valid indicator catalog keys (mirrors _build_indicator_payload).
INDICATOR_KEYS = sorted([
    "ma", "ema", "bbi", "sar", "alligator", "gmma", "twap", "vwma", "wma",
    "tema", "dema", "hma", "lsma", "tsf", "kc", "kama",  # price pane
    "macd", "dma", "dmi", "emv", "atr", "fisher", "iv", "iv_rank",
    "iv_percentile", "volat", "vmacd", "trix", "priceosc", "ddi", "mi",
    "si", "ao", "tower", "ewo", "rvi", "hlvol", "rmi", "smie", "smieo",
    "aroon", "chop", "hadiff", "er", "squeeze", "fo", "kdj", "mfi",
    "obv", "cci", "bop", "ultosc", "roc", "ppo", "apo", "cmo", "natr",
    "ad", "adosc",  # sub pane
])
INDICATOR_PREF_KEY = "indicator_state:v1"

_indicator_state: Dict[str, Any] = {
    "volume": True,
    "ma": True,
    "events": True,
    "resetSeq": 0,
    "toggleSeq": 0,       # incremented on each indicator-catalog toggle
    "toggledKey": None,   # last indicator key toggled via CLI
}
_indicator_state_lock = threading.Lock()

# Frontend-reported state — the actual active indicators in the user's browser.
# Updated via POST /api/indicator/report (debounced by frontend).
_frontend_reported: Dict[str, Any] = {
    "volume": True,
    "ma": True,
    "events": True,
    "activeKeys": [],       # list of active indicator catalog keys
    "lastReportedAt": None, # ISO timestamp
}


def _default_frontend_indicator_state() -> Dict[str, Any]:
    return {
        "volume": True,
        "ma": True,
        "events": True,
        "activeKeys": [],
        "count": 0,
        "lastReportedAt": None,
    }


def _normalize_frontend_indicator_state(body: Dict[str, Any]) -> Dict[str, Any]:
    active_keys: List[str] = []
    raw_keys = body.get("activeKeys")
    if isinstance(raw_keys, list):
        for key in raw_keys:
            clean = str(key or "").strip().lower()
            if clean in INDICATOR_KEYS and clean not in active_keys:
                active_keys.append(clean)

    last_reported = body.get("lastReportedAt")
    if not isinstance(last_reported, str):
        last_reported = datetime.now(timezone.utc).isoformat()

    return {
        "volume": bool(body.get("volume", True)),
        "ma": bool(body.get("ma", True)),
        "events": bool(body.get("events", True)),
        "activeKeys": active_keys,
        "count": len(active_keys),
        "lastReportedAt": last_reported,
    }


def _load_frontend_indicator_state() -> Dict[str, Any]:
    saved = _get_db_cache().get_preference(INDICATOR_PREF_KEY)
    if isinstance(saved, dict) and saved.get("lastReportedAt"):
        return _normalize_frontend_indicator_state(saved)
    return _default_frontend_indicator_state()


@app.get("/api/indicator/state")
def get_indicator_state() -> Dict[str, Any]:
    """Return current indicator toggle state for CLI/frontend sync.
    Includes frontend-reported active indicator keys."""
    frontend = _load_frontend_indicator_state()
    with _indicator_state_lock:
        result = dict(_indicator_state)
        result["frontend"] = frontend
        return result


@app.post("/api/indicator/report")
async def report_indicator_state(body: Dict[str, Any]) -> Dict[str, Any]:
    """Frontend calls this to report which indicators the user currently has active.
    Body: {"volume": bool, "ma": bool, "events": bool, "activeKeys": ["macd", ...]}"""
    frontend = _normalize_frontend_indicator_state(body)
    _get_db_cache().set_preference(INDICATOR_PREF_KEY, frontend)
    with _indicator_state_lock:
        _frontend_reported.update(frontend)
        return {"ok": True, "frontend": dict(frontend)}


@app.get("/api/indicator/active")
def get_active_indicators() -> Dict[str, Any]:
    """Agent-only endpoint: return exactly which indicators the user has checked.
    This is what `cli.py stock active` calls."""
    return _load_frontend_indicator_state()


@app.post("/api/indicator/toggle")
async def toggle_indicator(body: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate a button click. CLI calls this to remotely click indicator buttons.

    Body formats:
      {"button": "volume" | "ma" | "events" | "reset"}
      {"button": "indicator", "key": "<catalog_key>"}
    """
    button = (body.get("button") or "").strip().lower()
    if button not in ("volume", "ma", "events", "reset", "indicator"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown button: '{button}'. "
                   f"Use volume, ma, events, reset, or indicator (with key)."
        )

    with _indicator_state_lock:
        if button == "reset":
            _indicator_state["resetSeq"] += 1
        elif button == "indicator":
            key = (body.get("key") or "").strip().lower()
            if key not in INDICATOR_KEYS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown indicator key: '{key}'. "
                           f"Use GET /api/indicator/state for full key list, "
                           f"or POST with a valid key from INDICATOR_KEYS."
                )
            _indicator_state["toggledKey"] = key
            _indicator_state["toggleSeq"] += 1
        else:
            _indicator_state[button] = not _indicator_state[button]
        return {"ok": True, "button": button, "state": dict(_indicator_state)}


@app.post("/api/cache/clear")
def cache_clear(category: Optional[str] = Query(None, description="Optional: clear only this category")) -> Dict[str, Any]:
    """Clear caches for current user scope."""
    scope = _current_user_id.get()
    # Only clear L1 entries for current scope
    keys_to_remove = [k for k in _CACHE if k.startswith(f"{scope}:")]
    for k in keys_to_remove:
        _CACHE.pop(k, None)
    deleted = _get_db_cache().clear(category)
    return {"cleared": True, "scope": scope, "l1_removed": len(keys_to_remove), "l2_deleted": deleted, "category": category or "all"}
