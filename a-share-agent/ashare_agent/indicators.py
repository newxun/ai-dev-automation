"""日线指标：振幅、ATR、均线、趋势与破位、支撑压力、涨跌幅。输入为 data_source.finalize_daily 的结果。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd


def mean_amplitude(df: pd.DataFrame, n: int = 20) -> float | None:
    s = df["amplitude"].dropna().tail(n)
    return float(s.mean()) if len(s) >= min(n, 10) else None


def atr_pct(df: pd.DataFrame, n: int = 14) -> float | None:
    if len(df) < n + 1:
        return None
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean() / df["close"].iloc[-1])


def pct_change(df: pd.DataFrame, n: int) -> float | None:
    if len(df) <= n:
        return None
    return float(df["close"].iloc[-1] / df["close"].iloc[-1 - n] - 1)


@dataclass
class TrendInfo:
    state: str  # 上升 / 横盘 / 下跌 / 破位 / 数据不足
    reasons: list[str] = field(default_factory=list)
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma20_slope_5d: float | None = None
    low_20d: float | None = None
    high_20d: float | None = None
    low_60d: float | None = None
    high_60d: float | None = None
    drawdown_from_60d_high: float | None = None

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def trend(df: pd.DataFrame) -> TrendInfo:
    if len(df) < 25:
        return TrendInfo("数据不足", [f"只有 {len(df)} 个交易日数据"])
    close = df["close"]
    c = float(close.iloc[-1])
    ma5 = float(close.tail(5).mean())
    ma20_series = close.rolling(20).mean()
    ma20 = float(ma20_series.iloc[-1])
    ma60 = float(close.tail(60).mean()) if len(df) >= 60 else None
    slope = float(ma20_series.iloc[-1] / ma20_series.iloc[-6] - 1) if len(df) >= 26 else None
    prior = df.iloc[:-1]
    low_20d, high_20d = float(prior["low"].tail(20).min()), float(prior["high"].tail(20).max())
    low_60d = float(prior["low"].tail(60).min())
    high_60d = float(prior["high"].tail(60).max())
    dd = c / high_60d - 1
    info = TrendInfo("横盘", [], ma5, ma20, ma60, slope, low_20d, high_20d, low_60d, high_60d, dd)

    if c < low_60d:
        info.state = "破位"
        info.reasons.append(f"收盘 {c:.2f} 跌破前 60 日最低价 {low_60d:.2f}")
        return info
    if ma60 and c < ma60 * 0.95 and (slope or 0) < 0 and c < ma20 and dd < -0.15:
        info.state = "破位"
        info.reasons.append(f"收盘低于 60 日均线 5% 以上，20 日均线向下，较 60 日高点回撤 {dd:.1%}")
        return info
    if c > ma20 and (ma60 is None or ma20 > ma60) and (slope or 0) > 0.003:
        info.state = "上升"
        info.reasons.append(f"收盘在 20 日均线之上，20 日均线 5 日内上行 {slope:.1%}")
    elif c < ma20 and (ma60 is None or ma20 < ma60) and (slope or 0) < -0.003:
        info.state = "下跌"
        info.reasons.append(f"收盘在 20 日均线之下，20 日均线 5 日内下行 {abs(slope):.1%}")
    else:
        info.reasons.append(f"收盘在 20 日均线附近（{c / ma20 - 1:+.1%}），均线走平")
    return info


def summary(df: pd.DataFrame) -> dict:
    """给模型和卡片用的日线摘要。"""
    last = df.iloc[-1]
    t = trend(df)
    amt20 = df["amount"].tail(20).mean() if "amount" in df else None
    return {
        "date": last["date"],
        "close": round(float(last["close"]), 3),
        "pct_chg": round(float(last["pct_chg"]), 4) if pd.notna(last["pct_chg"]) else None,
        "high": float(last["high"]),
        "low": float(last["low"]),
        "mean_amplitude_20d": round(mean_amplitude(df) or 0, 4) or None,
        "atr14_pct": round(atr_pct(df) or 0, 4) or None,
        "chg_5d": round(pct_change(df, 5) or 0, 4) if pct_change(df, 5) is not None else None,
        "chg_20d": round(pct_change(df, 20) or 0, 4) if pct_change(df, 20) is not None else None,
        "chg_60d": round(pct_change(df, 60) or 0, 4) if pct_change(df, 60) is not None else None,
        "avg_amount_20d_yi": round(float(amt20) / 1e8, 2) if amt20 and amt20 > 0 else None,
        "trend": t.to_dict(),
    }
