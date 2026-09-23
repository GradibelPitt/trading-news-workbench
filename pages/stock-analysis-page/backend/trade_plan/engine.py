from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


HORIZONS: Dict[str, Dict[str, Any]] = {
    "intraday_short": {
        "label": "日内短线",
        "execution": ("1mo", "5m"),
        "confirmation": ("1mo", "15m"),
        "context": ("3mo", "1d"),
        "fresh_seconds": 180,
    },
    "intraday_trend": {
        "label": "日内趋势",
        "execution": ("1mo", "15m"),
        "confirmation": ("1mo", "1h"),
        "context": ("3mo", "1d"),
        "fresh_seconds": 600,
    },
    "swing_1_3d": {
        "label": "1–3天",
        "execution": ("1mo", "1h"),
        "confirmation": ("6mo", "1d"),
        "context": ("6mo", "1d"),
        "fresh_seconds": 5400,
    },
    "weekly": {
        "label": "周内",
        "execution": ("1y", "1d"),
        "confirmation": ("1mo", "1h"),
        "context": ("1y", "1d"),
        "fresh_seconds": 36 * 3600,
    },
}


@dataclass(frozen=True)
class Level:
    price: float
    kind: str
    label: str
    weight: float


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round_price(value: Optional[float], price: float) -> Optional[float]:
    if value is None:
        return None
    digits = 4 if abs(price) < 10 else 3 if abs(price) < 100 else 2
    return round(float(value), digits)


def _latest(frame: pd.DataFrame, column: str) -> Optional[float]:
    if column not in frame.columns or frame.empty:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return _finite(values.iloc[-1]) if not values.empty else None


def _market_is_open(now: datetime) -> bool:
    local = now.astimezone(ZoneInfo("America/New_York"))
    return local.weekday() < 5 and clock_time(9, 30) <= local.time() <= clock_time(16, 5)


def _last_timestamp(frame: pd.DataFrame) -> Optional[datetime]:
    if frame.empty:
        return None
    stamp = pd.Timestamp(frame.index[-1])
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.to_pydatetime().astimezone(timezone.utc)


def _data_quality(frame: pd.DataFrame, interval: str, fresh_seconds: int, now: datetime) -> Dict[str, Any]:
    missing: List[str] = []
    if len(frame) < 55:
        missing.append("insufficient_candles")
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing.extend(f"missing_{name.lower()}" for name in sorted(required - set(frame.columns)))

    volume_coverage = 0.0
    if "Volume" in frame.columns and not frame.empty:
        recent = pd.to_numeric(frame["Volume"], errors="coerce").tail(20)
        volume_coverage = float((recent.fillna(0) > 0).mean()) if len(recent) else 0.0
        if volume_coverage < 0.8:
            missing.append("unreliable_volume")

    last_bar = _last_timestamp(frame)
    age_seconds = max(0.0, (now - last_bar).total_seconds()) if last_bar else None
    intraday = interval not in {"1d", "1wk", "1mo"}
    stale = bool(intraday and _market_is_open(now) and (age_seconds is None or age_seconds > fresh_seconds))
    if stale:
        missing.append("stale_intraday_data")

    return {
        "status": "ready" if not missing else "degraded",
        "lastBarAt": last_bar.isoformat() if last_bar else None,
        "ageSeconds": round(age_seconds, 1) if age_seconds is not None else None,
        "marketSession": "regular_open" if _market_is_open(now) else "closed_or_off_hours",
        "volumeCoverage": round(volume_coverage, 3),
        "isStale": stale,
        "missing": missing,
    }


def _session_vwap(frame: pd.DataFrame) -> Tuple[Optional[float], Optional[float], Optional[pd.Series]]:
    if frame.empty or "Volume" not in frame.columns:
        return None, None, None
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    volume = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0)
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    local_dates = index.tz_convert("America/New_York").date
    latest_date = local_dates[-1]
    mask = np.asarray(local_dates == latest_date)
    session_tp = typical.iloc[np.flatnonzero(mask)]
    session_volume = volume.iloc[np.flatnonzero(mask)]
    denominator = session_volume.cumsum().replace(0, np.nan)
    values = (session_tp * session_volume).cumsum() / denominator
    values.index = session_tp.index
    clean = values.dropna()
    if clean.empty:
        return None, None, None
    slope_lookback = min(5, max(1, len(clean) - 1))
    slope = float(clean.iloc[-1] - clean.iloc[-1 - slope_lookback]) if len(clean) > slope_lookback else 0.0
    return float(clean.iloc[-1]), slope, values


def _confirmed_swings(frame: pd.DataFrame, window: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    if len(frame) < window * 2 + 3:
        return highs, lows
    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    # The last ``window`` bars are intentionally excluded: their pivots are not confirmed yet.
    for i in range(window, len(frame) - window):
        if high[i] >= np.max(high[i - window:i + window + 1]):
            highs.append((i, float(high[i])))
        if low[i] <= np.min(low[i - window:i + window + 1]):
            lows.append((i, float(low[i])))
    return highs, lows


def _anchored_vwap(frame: pd.DataFrame, anchor: int) -> Optional[float]:
    sliced = frame.iloc[max(0, anchor):]
    if sliced.empty:
        return None
    volume = pd.to_numeric(sliced["Volume"], errors="coerce").fillna(0.0)
    total = float(volume.sum())
    if total <= 0:
        return None
    typical = (sliced["High"] + sliced["Low"] + sliced["Close"]) / 3.0
    return float((typical * volume).sum() / total)


def _previous_day_levels(frame: pd.DataFrame, now: datetime) -> Dict[str, float]:
    if frame.empty:
        return {}
    daily = frame.copy()
    last_date = pd.Timestamp(daily.index[-1]).date()
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    row = daily.iloc[-2] if last_date >= today and len(daily) >= 2 else daily.iloc[-1]
    high, low, close = map(float, (row["High"], row["Low"], row["Close"]))
    pivot = (high + low + close) / 3.0
    return {
        "previous_high": high,
        "previous_low": low,
        "previous_close": close,
        "pivot": pivot,
        "r1": 2 * pivot - low,
        "s1": 2 * pivot - high,
        "r2": pivot + high - low,
        "s2": pivot - high + low,
    }


def _volume_profile_proxy(frame: pd.DataFrame, bins: int = 36) -> Dict[str, Optional[float]]:
    recent = frame.tail(160)
    if recent.empty:
        return {"poc": None, "vah": None, "val": None}
    typical = ((recent["High"] + recent["Low"] + recent["Close"]) / 3.0).to_numpy(dtype=float)
    volume = pd.to_numeric(recent["Volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if not np.isfinite(volume).any() or volume.sum() <= 0 or np.nanmax(typical) <= np.nanmin(typical):
        return {"poc": None, "vah": None, "val": None}
    hist, edges = np.histogram(typical, bins=bins, weights=volume)
    centers = (edges[:-1] + edges[1:]) / 2.0
    poc_idx = int(np.argmax(hist))
    selected = {poc_idx}
    accumulated = float(hist[poc_idx])
    target = float(hist.sum()) * 0.70
    left, right = poc_idx - 1, poc_idx + 1
    while accumulated < target and (left >= 0 or right < len(hist)):
        left_value = hist[left] if left >= 0 else -1
        right_value = hist[right] if right < len(hist) else -1
        chosen = left if left_value >= right_value else right
        selected.add(chosen)
        accumulated += float(hist[chosen])
        if chosen == left:
            left -= 1
        else:
            right += 1
    return {
        "poc": float(centers[poc_idx]),
        "vah": float(edges[max(selected) + 1]),
        "val": float(edges[min(selected)]),
    }


def _regime(execution: pd.DataFrame, confirmation: pd.DataFrame, vwap: Optional[float], vwap_slope: Optional[float]) -> Dict[str, Any]:
    close = _latest(execution, "Close")
    ema20 = _latest(execution, "EMA20")
    ema50 = _latest(execution, "EMA50")
    adx = _latest(execution, "ADX") or 0.0
    chop = _latest(execution, "CHOP")
    confirm_close = _latest(confirmation, "Close")
    confirm_ema20 = _latest(confirmation, "EMA20")
    confirm_ema50 = _latest(confirmation, "EMA50")

    bullish = all(value is not None for value in (close, ema20, ema50)) and close > ema20 > ema50
    bearish = all(value is not None for value in (close, ema20, ema50)) and close < ema20 < ema50
    if vwap is not None and close is not None:
        bullish = bullish and close > vwap and (vwap_slope or 0) >= 0
        bearish = bearish and close < vwap and (vwap_slope or 0) <= 0
    confirm_bullish = all(value is not None for value in (confirm_close, confirm_ema20, confirm_ema50)) and confirm_close > confirm_ema20 > confirm_ema50
    confirm_bearish = all(value is not None for value in (confirm_close, confirm_ema20, confirm_ema50)) and confirm_close < confirm_ema20 < confirm_ema50

    if adx >= 22 and bullish:
        name = "trend_up"
    elif adx >= 22 and bearish:
        name = "trend_down"
    elif adx < 18 or (chop is not None and chop >= 58):
        name = "range"
    else:
        name = "transition"
    aligned = (name == "trend_up" and confirm_bullish) or (name == "trend_down" and confirm_bearish)
    return {"name": name, "adx": adx, "chop": chop, "multiTimeframeAligned": aligned}


def _cluster_levels(levels: Iterable[Level], atr: float) -> List[Dict[str, Any]]:
    ordered = sorted((level for level in levels if math.isfinite(level.price)), key=lambda item: item.price)
    clusters: List[List[Level]] = []
    tolerance = max(atr * 0.25, 1e-9)
    for level in ordered:
        if not clusters:
            clusters.append([level])
            continue
        weighted_center = sum(item.price * item.weight for item in clusters[-1]) / sum(item.weight for item in clusters[-1])
        if abs(level.price - weighted_center) <= tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    result: List[Dict[str, Any]] = []
    for group in clusters:
        total_weight = sum(item.weight for item in group)
        center = sum(item.price * item.weight for item in group) / total_weight
        half_width = max(atr * 0.08, max(abs(item.price - center) for item in group))
        result.append({
            "center": center,
            "low": center - half_width,
            "high": center + half_width,
            "score": total_weight,
            "sources": [item.label for item in sorted(group, key=lambda item: -item.weight)],
        })
    return result


def _best_cluster(clusters: List[Dict[str, Any]], price: float, atr: float, side: str) -> Optional[Dict[str, Any]]:
    if side == "long":
        candidates = [item for item in clusters if item["center"] <= price + 0.35 * atr]
    else:
        candidates = [item for item in clusters if item["center"] >= price - 0.35 * atr]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["score"] * 2.0 - abs(price - item["center"]) / max(atr, 1e-9))


def _scenario(
    side: str,
    cluster: Optional[Dict[str, Any]],
    clusters: List[Dict[str, Any]],
    price: float,
    atr: float,
    swing_highs: List[Tuple[int, float]],
    swing_lows: List[Tuple[int, float]],
    regime: str,
    cost_bps: float,
) -> Optional[Dict[str, Any]]:
    if not cluster:
        return None
    entry = float(cluster["center"])
    if side == "long":
        structural = swing_lows[-1][1] - 0.15 * atr if swing_lows else entry - atr
        stop = min(structural, entry - 0.8 * atr)
        risk = entry - stop
        structural_targets = sorted(item["center"] for item in clusters if item["center"] > entry + 0.25 * atr)
        t1 = structural_targets[0] if structural_targets else entry + 1.5 * risk
        t2 = structural_targets[1] if len(structural_targets) > 1 else entry + 2.5 * risk
        chase = price > entry + 2.0 * atr
    else:
        structural = swing_highs[-1][1] + 0.15 * atr if swing_highs else entry + atr
        stop = max(structural, entry + 0.8 * atr)
        risk = stop - entry
        structural_targets = sorted((item["center"] for item in clusters if item["center"] < entry - 0.25 * atr), reverse=True)
        t1 = structural_targets[0] if structural_targets else entry - 1.5 * risk
        t2 = structural_targets[1] if len(structural_targets) > 1 else entry - 2.5 * risk
        chase = price < entry - 2.0 * atr
    if risk <= 0:
        return None
    round_trip_cost = entry * max(0.0, cost_bps) / 10000.0 * 2.0
    rr1 = max(0.0, abs(t1 - entry) - round_trip_cost) / (risk + round_trip_cost)
    rr2 = max(0.0, abs(t2 - entry) - round_trip_cost) / (risk + round_trip_cost)
    valid_regime = regime in ({"trend_up", "range", "transition"} if side == "long" else {"trend_down", "range", "transition"})
    actionable = rr1 >= 1.5 and valid_regime and not chase
    return {
        "side": side,
        "status": "ready" if actionable else "no_trade",
        "entryZone": {"low": cluster["low"], "high": cluster["high"], "center": entry},
        "stop": stop,
        "target1": t1,
        "target2": t2,
        "riskReward1": rr1,
        "riskReward2": rr2,
        "setupScore": min(100, round(35 + cluster["score"] * 10 + min(rr2, 3) * 8)),
        "sources": cluster["sources"],
        "noTradeReasons": [
            *(["reward_to_risk_below_1_5"] if rr1 < 1.5 else []),
            *(["regime_not_supportive"] if not valid_regime else []),
            *(["price_more_than_2_atr_from_entry"] if chase else []),
        ],
    }


def _serialize_scenario(scenario: Optional[Dict[str, Any]], price: float) -> Optional[Dict[str, Any]]:
    if not scenario:
        return None
    result = dict(scenario)
    result["entryZone"] = {key: _round_price(value, price) for key, value in scenario["entryZone"].items()}
    for key in ("stop", "target1", "target2"):
        result[key] = _round_price(scenario[key], price)
    result["riskReward1"] = round(scenario["riskReward1"], 2)
    result["riskReward2"] = round(scenario["riskReward2"], 2)
    return result


def build_trade_plan(
    symbol: str,
    horizon: str,
    execution: pd.DataFrame,
    confirmation: pd.DataFrame,
    context: pd.DataFrame,
    *,
    execution_interval: str,
    now: Optional[datetime] = None,
    cost_bps: float = 5.0,
) -> Dict[str, Any]:
    if horizon not in HORIZONS:
        raise ValueError(f"Unsupported horizon: {horizon}")
    config = HORIZONS[horizon]
    now = now or datetime.now(timezone.utc)
    quality = _data_quality(execution, execution_interval, int(config["fresh_seconds"]), now)
    hard_missing = {"insufficient_candles", "missing_close", "missing_volume", "unreliable_volume", "stale_intraday_data"}
    if hard_missing.intersection(quality["missing"]):
        return {
            "symbol": symbol,
            "horizon": horizon,
            "horizonLabel": config["label"],
            "status": "insufficient_data",
            "action": "no_trade",
            "dataQuality": quality,
            "message": "数据新鲜度、K线数量或真实成交量不足，系统拒绝生成点位。",
            "disclaimer": "数学计算点位，不是价格预测或收益保证。",
        }

    price = _latest(execution, "Close")
    atr = _latest(execution, "ATR14")
    if price is None or atr is None or atr <= 0:
        quality["missing"].append("missing_atr")
        return {
            "symbol": symbol,
            "horizon": horizon,
            "horizonLabel": config["label"],
            "status": "insufficient_data",
            "action": "no_trade",
            "dataQuality": quality,
            "message": "无法形成有效ATR，系统拒绝生成点位。",
            "disclaimer": "数学计算点位，不是价格预测或收益保证。",
        }

    intraday = execution_interval not in {"1d", "1wk", "1mo"}
    vwap, vwap_slope, _ = _session_vwap(execution) if intraday else (None, None, None)
    swing_highs, swing_lows = _confirmed_swings(execution)
    anchor_candidates = [item[0] for item in (swing_lows[-1:] + swing_highs[-1:])]
    anchor = max(anchor_candidates) if anchor_candidates else max(0, len(execution) - 40)
    avwap = _anchored_vwap(execution, anchor)
    pivots = _previous_day_levels(context, now)
    profile = _volume_profile_proxy(execution)
    regime = _regime(execution, confirmation, vwap, vwap_slope)

    levels: List[Level] = []
    for value, kind, label, weight in [
        (vwap, "vwap", "Session VWAP", 1.0),
        (avwap, "avwap", "Anchored VWAP", 1.0),
        (_latest(execution, "EMA20"), "ema20", "EMA20", 0.75),
        (_latest(execution, "KC_Middle"), "kc_mid", "Keltner中轨", 0.65),
        (profile["poc"], "profile", "Estimated POC", 0.55),
        (profile["vah"], "profile", "Estimated VAH", 0.55),
        (profile["val"], "profile", "Estimated VAL", 0.55),
    ]:
        if value is not None:
            levels.append(Level(float(value), kind, label, weight))
    pivot_labels = {
        "previous_high": "昨日高点", "previous_low": "昨日低点", "previous_close": "昨日收盘",
        "pivot": "Pivot", "r1": "R1", "s1": "S1", "r2": "R2", "s2": "S2",
    }
    for key, value in pivots.items():
        levels.append(Level(value, key, pivot_labels[key], 0.85 if "previous" in key else 0.75))
    if swing_highs:
        levels.append(Level(swing_highs[-1][1], "swing_high", "最近确认Swing High", 1.0))
    if swing_lows:
        levels.append(Level(swing_lows[-1][1], "swing_low", "最近确认Swing Low", 1.0))

    # Fibonacci is a low-weight confluence input only.
    if swing_highs and swing_lows:
        high, low = swing_highs[-1][1], swing_lows[-1][1]
        top, bottom = max(high, low), min(high, low)
        for ratio in (0.382, 0.5, 0.618):
            levels.append(Level(top - (top - bottom) * ratio, "fib", f"Fib {ratio * 100:.1f}%", 0.4))

    clusters = _cluster_levels(levels, atr)
    long_plan = _scenario("long", _best_cluster(clusters, price, atr, "long"), clusters, price, atr, swing_highs, swing_lows, regime["name"], cost_bps)
    short_plan = _scenario("short", _best_cluster(clusters, price, atr, "short"), clusters, price, atr, swing_highs, swing_lows, regime["name"], cost_bps)
    preferred_side = "long" if regime["name"] == "trend_up" else "short" if regime["name"] == "trend_down" else None
    candidates = [item for item in (long_plan, short_plan) if item and item["status"] == "ready"]
    preferred = next((item for item in candidates if item["side"] == preferred_side), None)
    if preferred is None and candidates:
        preferred = max(candidates, key=lambda item: item["setupScore"])
    action = "wait" if preferred is None else ("wait_for_pullback" if abs(price - preferred["entryZone"]["center"]) > 0.25 * atr else "entry_zone_reached")

    return {
        "symbol": symbol,
        "horizon": horizon,
        "horizonLabel": config["label"],
        "status": "ready" if preferred else "no_trade",
        "action": action,
        "calculatedAt": now.isoformat(),
        "currentPrice": _round_price(price, price),
        "atr": _round_price(atr, price),
        "regime": {
            "name": regime["name"],
            "adx": round(regime["adx"], 2),
            "chop": round(regime["chop"], 2) if regime["chop"] is not None else None,
            "multiTimeframeAligned": regime["multiTimeframeAligned"],
        },
        "dataQuality": quality,
        "levels": {
            "vwap": _round_price(vwap, price),
            "anchoredVwap": _round_price(avwap, price),
            "profileProxy": {key: _round_price(value, price) for key, value in profile.items()},
            "pivot": {key: _round_price(value, price) for key, value in pivots.items()},
        },
        "preferredSide": preferred["side"] if preferred else None,
        "long": _serialize_scenario(long_plan, price),
        "short": _serialize_scenario(short_plan, price),
        "costAssumptionBps": round(float(cost_bps), 2),
        "caveats": [
            "Volume Profile来自OHLCV分箱近似，不是逐笔分价成交。",
            "当前数据源不含实时bid/ask；成本使用用户输入的基点估算。",
            "正常交易时段VWAP不包含盘前盘后成交。",
        ],
        "disclaimer": "数学计算点位，不是价格预测、投资建议或收益保证。",
    }
