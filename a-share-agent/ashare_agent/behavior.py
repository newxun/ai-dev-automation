"""交割单体检：做T成绩单、赚了就跑/亏了死扛（处置效应）、换手与手续费、追涨买入比例。

这部分完全由程序计算，不依赖模型判断。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .trades import Trade


@dataclass
class TEvent:
    date: str
    code: str
    name: str
    direction: str  # 正T / 倒T / 顺序未知
    matched: int
    buy_avg: float
    sell_avg: float
    fees: float
    net: float
    leftover: int  # >0 多出的股数，<0 少掉的股数

    @property
    def success(self) -> bool:
        return self.net > 0


def detect_t_events(trades: list[Trade]) -> list[TEvent]:
    """同一天同一只票既有买入又有卖出，视为一次日内做T。"""
    groups: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in trades:
        groups[(t.trade_date, t.code)].append(t)
    events = []
    for (d, code), ts in sorted(groups.items()):
        buys = [t for t in ts if t.side == "buy"]
        sells = [t for t in ts if t.side == "sell"]
        if not buys or not sells:
            continue
        bq, sq = sum(t.quantity for t in buys), sum(t.quantity for t in sells)
        matched = min(bq, sq)
        b_avg = sum(t.price * t.quantity for t in buys) / bq
        s_avg = sum(t.price * t.quantity for t in sells) / sq
        fees = sum(t.fee_total for t in buys) * matched / bq + sum(t.fee_total for t in sells) * matched / sq
        net = (s_avg - b_avg) * matched - fees
        if all(t.trade_time for t in ts):
            first_buy = min(t.trade_time for t in buys)
            first_sell = min(t.trade_time for t in sells)
            direction = "正T" if first_buy < first_sell else "倒T"
        else:
            direction = "顺序未知"
        name = next((t.name for t in ts if t.name), "")
        events.append(TEvent(d, code, name, direction, matched, round(b_avg, 4), round(s_avg, 4), round(fees, 2), round(net, 2), bq - sq))
    return events


def t_scorecard(trades: list[Trade], holdings: dict[str, int] | None = None) -> dict:
    events = detect_t_events(trades)
    holdings = holdings or {}
    per: dict[str, dict] = {}
    for e in events:
        s = per.setdefault(e.code, {"code": e.code, "name": e.name, "times": 0, "success": 0, "net_total": 0.0,
                                    "fees": 0.0, "extra_shares": 0, "fewer_shares": 0})
        s["times"] += 1
        s["success"] += int(e.success)
        s["net_total"] = round(s["net_total"] + e.net, 2)
        s["fees"] = round(s["fees"] + e.fees, 2)
        if e.leftover > 0:
            s["extra_shares"] += e.leftover
        elif e.leftover < 0:
            s["fewer_shares"] += -e.leftover
    for code, s in per.items():
        s["success_rate"] = round(s["success"] / s["times"], 3) if s["times"] else 0.0
        base = holdings.get(code)
        s["per_share"] = round(s["net_total"] / base, 4) if base else None
        s["auto_stop"] = s["times"] >= 10 and s["net_total"] < 0
    total = {
        "times": len(events),
        "success": sum(int(e.success) for e in events),
        "net_total": round(sum(e.net for e in events), 2),
        "fees": round(sum(e.fees for e in events), 2),
    }
    total["success_rate"] = round(total["success"] / total["times"], 3) if total["times"] else 0.0
    return {"total": total, "by_code": sorted(per.values(), key=lambda x: -x["times"]),
            "events": [asdict(e) | {"success": e.success} for e in events]}


@dataclass
class Disposition:
    win_sells: int = 0
    loss_sells: int = 0
    win_hold_days: float | None = None
    loss_hold_days: float | None = None
    unknown_sells: int = 0
    realized_total: float = 0.0
    open_losers: list[dict] = field(default_factory=list)

    def verdict(self) -> str:
        if self.win_sells + self.loss_sells < 5:
            return "可配对的卖出太少（少于 5 笔），暂时看不出倾向。"
        parts = []
        ratio = self.win_sells / max(self.loss_sells, 1)
        parts.append(f"盈利卖出 {self.win_sells} 笔、亏损卖出 {self.loss_sells} 笔（{ratio:.1f} : 1）")
        if self.win_hold_days is not None and self.loss_hold_days is not None:
            parts.append(f"盈利的票平均拿 {self.win_hold_days:.0f} 天就卖，亏损的票平均拿 {self.loss_hold_days:.0f} 天才卖")
        tendency = ratio >= 1.5 or (
            self.win_hold_days is not None and self.loss_hold_days is not None and self.loss_hold_days > self.win_hold_days * 1.5
        )
        parts.append("有明显的「赚了就跑、亏了死扛」倾向。" if tendency else "没有明显的「赚了就跑、亏了死扛」倾向。")
        return "；".join(parts)


def _days(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def disposition(trades: list[Trade], last_prices: dict[str, float] | None = None, as_of: str | None = None) -> Disposition:
    """先进先出配对买卖，统计盈利卖出与亏损卖出的笔数和持有天数。"""
    lots: dict[str, deque] = defaultdict(deque)
    res = Disposition()
    win_days: list[tuple[int, int]] = []
    loss_days: list[tuple[int, int]] = []
    for t in sorted(trades, key=lambda x: (x.trade_date, x.trade_time)):
        if t.side == "buy":
            unit_cost = (t.amount + t.fee_total) / t.quantity
            lots[t.code].append([t.trade_date, t.quantity, unit_cost])
            continue
        remaining = t.quantity
        pnl = 0.0
        held: list[tuple[int, int]] = []
        unit_proceeds = (t.amount - t.fee_total) / t.quantity
        while remaining > 0 and lots[t.code]:
            lot = lots[t.code][0]
            q = min(remaining, lot[1])
            pnl += (unit_proceeds - lot[2]) * q
            held.append((_days(lot[0], t.trade_date), q))
            lot[1] -= q
            remaining -= q
            if lot[1] == 0:
                lots[t.code].popleft()
        if remaining == t.quantity:
            res.unknown_sells += 1
            continue
        res.realized_total += pnl
        (win_days if pnl > 0 else loss_days).extend(held)
        if pnl > 0:
            res.win_sells += 1
        else:
            res.loss_sells += 1

    def wavg(xs: list[tuple[int, int]]) -> float | None:
        q = sum(n for _, n in xs)
        return sum(d * n for d, n in xs) / q if q else None

    res.win_hold_days = wavg(win_days)
    res.loss_hold_days = wavg(loss_days)
    res.realized_total = round(res.realized_total, 2)
    if last_prices and as_of:
        for code, dq in lots.items():
            price = last_prices.get(code)
            if not price:
                continue
            for d, q, c in dq:
                if q > 0 and price < c:
                    res.open_losers.append({"code": code, "since": d, "days": _days(d, as_of), "qty": q,
                                            "loss_pct": round(price / c - 1, 4)})
    return res


def turnover_and_fees(trades: list[Trade], total_assets: float) -> dict:
    if not trades:
        return {}
    buy_amt = sum(t.amount for t in trades if t.side == "buy")
    sell_amt = sum(t.amount for t in trades if t.side == "sell")
    fees = sum(t.fee_total for t in trades)
    first, last = trades[0].trade_date, trades[-1].trade_date
    span_days = max(_days(first, last), 1)
    annual = (sell_amt / total_assets) * (365 / span_days) if total_assets else None
    return {
        "period": f"{first} 至 {last}",
        "buy_amount": round(buy_amt, 2),
        "sell_amount": round(sell_amt, 2),
        "fees": round(fees, 2),
        "fees_pct_assets": round(fees / total_assets, 4) if total_assets else None,
        "annual_turnover": round(annual, 1) if annual is not None else None,
        "trade_count": len(trades),
        "estimated_fee_rows": sum(1 for t in trades if t.fee_estimated),
    }


def chase_ratio(trades: list[Trade], pre_close: dict[tuple[str, str], float], chase_pct: float = 0.05) -> dict:
    """买入价相对前一日收盘涨幅 ≥ chase_pct 视为追涨。pre_close: {(code, date): 前收}。"""
    buys = [t for t in trades if t.side == "buy"]
    known = [t for t in buys if (t.code, t.trade_date) in pre_close]
    chased = [t for t in known if t.price >= pre_close[(t.code, t.trade_date)] * (1 + chase_pct)]
    return {
        "buys": len(buys),
        "with_data": len(known),
        "chased": len(chased),
        "ratio": round(len(chased) / len(known), 3) if known else None,
        "examples": [f"{t.trade_date} {t.name or t.code} 买入价较前收 +{t.price / pre_close[(t.code, t.trade_date)] - 1:.1%}"
                     for t in chased[:5]],
    }
