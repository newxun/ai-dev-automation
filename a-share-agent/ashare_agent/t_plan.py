"""次日做T预案：程序先算候选价位和股数并把关硬规则，导师只判断做不做、选哪个方向、解释原因。

硬规则（导师不能绕过）：
- 先决条件：去留结论不是减仓/清仓；明天没有财报、重大事项、停复牌；默认不碰 ST/退市整理；
  非新股无涨跌幅期；趋势未破位；底仓足够；该票累计做T 10 次以上且扣费净降本为负时叫停；
  预期价差至少是保本价差的 3 倍。
- 股数：100 股整数倍（科创板至少 200 股）；不超过底仓 × 做T比例；先买后卖时买入金额不超过可用资金，
  买入后的总仓位不超过单只仓位上限。
- 价位：在明日涨跌停价以内，对齐最小价位，写明推算依据。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from .indicators import mean_amplitude, trend
from .market import (
    FeeSettings,
    breakeven_spread,
    breakeven_spread_per_share,
    has_no_price_limit,
    is_delisting,
    is_st,
    limit_prices,
    lot_rule,
    price_tick,
    round_price,
    t_trade_result,
    trade_fee,
)
from .rules import UserRules

MIN_SPREAD_MULTIPLE = 3.0
AUTO_STOP_MIN_TIMES = 10
BACKTEST_DAYS = 60

# (第一步偏离今收的系数, 第二步偏离今收的系数)，单位是近 20 日平均振幅。
# 系数用 12 只沪深股票/ETF 近 60 个交易日的日线粗验选定：稳健档两边都摸到约 38%，只摸到第一步约 23%。
VARIANTS = {
    "稳健": (0.30, 0.15),
    "宽幅": (0.45, 0.20),
}
MIN_MEAN_AMPLITUDE = 0.015
BLOCKING_EVENT_TYPES = ("财报", "停牌", "重大事项", "复牌")
NO_T_DECISIONS = ("减仓", "清仓", "清仓换股")


@dataclass
class TContext:
    code: str
    name: str
    base_qty: int  # 明日可卖的底仓
    cost: float
    cash: float
    total_assets: float
    daily: pd.DataFrame  # 截至今日（含）的日线
    target_date: str  # 明日
    rules: UserRules = field(default_factory=UserRules)
    fees: FeeSettings = field(default_factory=FeeSettings)
    events_tomorrow: list[dict] = field(default_factory=list)
    events_known: bool = True
    keep_decision: str | None = None  # 去留结论
    listed_trading_days_tomorrow: int | None = None
    t_history: dict | None = None  # {"times": n, "net_total": 元, "per_share": 元/股}
    as_of: str | None = None  # 今天（交易日）；日线最后一天不是今天说明今天没交易

    @property
    def close(self) -> float:
        return float(self.daily["close"].iloc[-1])

    @property
    def position_value(self) -> float:
        return self.base_qty * self.close


@dataclass
class TCandidate:
    id: str
    direction: str  # 正T / 倒T
    variant: str
    qty: int
    buy_price: float
    sell_price: float
    spread_pct: float
    breakeven_pct: float
    breakeven_per_share: float
    fee_total: float
    net_profit: float
    new_cost: float
    cost_drop: float
    basis: list[str]
    orders: list[str]
    abandon: list[str]
    wrap_up: list[str]
    backtest: dict
    qty_note: str = ""
    adjusted: bool = False
    adjust_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TPlanResult:
    code: str
    name: str
    target_date: str
    eligible: bool
    blockers: list[str]
    warnings: list[str]
    facts: dict
    candidates: list[TCandidate]
    preferred: str | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["candidates"] = [c.to_dict() for c in self.candidates]
        return d


def _t_qty_cap(ctx: TContext) -> int:
    min_lot, _ = lot_rule(ctx.code)
    raw = ctx.base_qty * ctx.rules.t_max_ratio
    qty = int(raw // 100 * 100)
    return qty if qty >= min_lot else 0


def _fmt(code: str, p: float) -> str:
    return f"{p:.3f}" if price_tick(code) < 0.01 else f"{p:.2f}"


def rough_backtest(daily: pd.DataFrame, direction: str, kb: float, ks: float, days: int = BACKTEST_DAYS) -> dict:
    """用日线回看：按同样的比例，每天以前一日收盘和前 20 日平均振幅推算价位，看两边是否都摸到。"""
    df = daily.reset_index(drop=True)
    amp = df["amplitude"]
    stats = {"days": 0, "both": 0, "first_only": 0, "second_only": 0, "none": 0}
    start = max(21, len(df) - days)
    for i in range(start, len(df)):
        prev_close = df.at[i - 1, "close"]
        r = amp.iloc[i - 20 : i].mean()
        if pd.isna(r) or r <= 0:
            continue
        if direction == "正T":
            first_hit = df.at[i, "low"] <= prev_close * (1 - kb * r)
            second_hit = df.at[i, "high"] >= prev_close * (1 + ks * r)
        else:
            first_hit = df.at[i, "high"] >= prev_close * (1 + kb * r)
            second_hit = df.at[i, "low"] <= prev_close * (1 - ks * r)
        stats["days"] += 1
        if first_hit and second_hit:
            stats["both"] += 1
        elif first_hit:
            stats["first_only"] += 1
        elif second_hit:
            stats["second_only"] += 1
        else:
            stats["none"] += 1
    return stats


def backtest_text(direction: str, bt: dict, qty: int) -> str:
    first = "买入价" if direction == "正T" else "卖出价"
    extra = f"会多拿 {qty} 股过夜" if direction == "正T" else f"底仓会少 {qty} 股过夜"
    return (
        f"按同样的比例回看过去 {bt['days']} 个交易日，两边价位都摸到的有 {bt['both']} 天，"
        f"只摸到{first}的有 {bt['first_only']} 天（这些天你{extra}）。日线看不出价格先后顺序，只是粗估。"
    )


def gates(ctx: TContext) -> tuple[list[str], list[str]]:
    """返回 (阻断原因, 提醒)。有阻断原因就「明天不做」。"""
    blockers: list[str] = []
    warnings: list[str] = []
    if ctx.keep_decision and any(d in ctx.keep_decision for d in NO_T_DECISIONS):
        blockers.append(f"去留结论是「{ctx.keep_decision}」，该先执行减仓/清仓，不用做T来拖延决定")
    if not ctx.rules.allow_st and (is_st(ctx.name) or is_delisting(ctx.name)):
        blockers.append("ST 或退市整理股，按你的规则默认不碰")
    if has_no_price_limit(ctx.listed_trading_days_tomorrow):
        blockers.append("新股上市前 5 个交易日不设涨跌幅，波动不可控")
    for e in ctx.events_tomorrow:
        if any(t in e.get("type", "") for t in BLOCKING_EVENT_TYPES):
            blockers.append(f"明天有已知事件：{e.get('type')}（{e.get('detail', '')}）")
    if not ctx.events_known:
        warnings.append("明天的事件日历没查全（财报/停复牌可能遗漏），设条件单前请自己看一眼公告")
    if len(ctx.daily) < 25:
        blockers.append(f"日线数据只有 {len(ctx.daily)} 天，不够判断振幅和趋势")
        return blockers, warnings
    last = ctx.daily.iloc[-1]
    if ctx.as_of and str(last["date"]) != ctx.as_of:
        blockers.append(f"最新日线停在 {last['date']}，今天没有交易（可能停牌），复牌前不做")
    elif float(last.get("volume", 1) or 0) == 0:
        blockers.append("今天没有成交（可能停牌）")
    t = trend(ctx.daily)
    if t.state == "破位":
        blockers.append("趋势破位：" + "；".join(t.reasons))
    r = mean_amplitude(ctx.daily, 20)
    if r is not None and r < MIN_MEAN_AMPLITUDE:
        blockers.append(f"振幅不够：近 20 日平均振幅只有 {r:.2%}（低于 {MIN_MEAN_AMPLITUDE:.1%}），扣完手续费基本没有空间")
    if _t_qty_cap(ctx) == 0:
        blockers.append(
            f"底仓太少：{ctx.base_qty} 股 × 做T比例 {ctx.rules.t_max_ratio:.0%} 不足一手（{lot_rule(ctx.code)[0]} 股）"
        )
    h = ctx.t_history or {}
    if h.get("times", 0) >= AUTO_STOP_MIN_TIMES and h.get("net_total", 0) < 0:
        blockers.append(
            f"这只票已做T {h['times']} 次，扣费后累计净降本 {h['net_total']:.0f} 元（为负），默认建议停做"
        )
    amt = ctx.daily["amount"].tail(20).mean() if "amount" in ctx.daily else 0
    if amt and amt < 3e7:
        warnings.append(f"近 20 日日均成交额只有 {amt / 1e4:.0f} 万元，条件单可能成交不了或滑点大")
    return blockers, warnings


def _snap_levels(direction: str, close: float, r: float, kb: float, ks: float, t, code: str) -> tuple[float, float, list[str]]:
    basis = [f"近 20 日平均振幅 {r:.2%}，今收 {_fmt(code, close)} 元"]
    if direction == "正T":
        buy = close * (1 - kb * r)
        sell = close * (1 + ks * r)
        basis.append(f"买入价 ≈ 今收 × (1 − {kb} × 平均振幅)，卖出价 ≈ 今收 × (1 + {ks} × 平均振幅)")
        if t.low_20d and buy < t.low_20d < close:
            buy = t.low_20d * 1.003
            basis.append(f"买入价上移到前 20 日低点 {_fmt(code, t.low_20d)} 元上方一点（支撑位附近更容易成交）")
        elif t.low_20d:
            basis.append(f"参考：前 20 日低点 {_fmt(code, t.low_20d)} 元")
    else:
        sell = close * (1 + kb * r)
        buy = close * (1 - ks * r)
        basis.append(f"卖出价 ≈ 今收 × (1 + {kb} × 平均振幅)，买回价 ≈ 今收 × (1 − {ks} × 平均振幅)")
        if t.high_20d and close < t.high_20d < sell:
            sell = t.high_20d * 0.997
            basis.append(f"卖出价下移到前 20 日高点 {_fmt(code, t.high_20d)} 元下方一点（压力位附近更容易成交）")
        elif t.high_20d:
            basis.append(f"参考：前 20 日高点 {_fmt(code, t.high_20d)} 元")
    return buy, sell, basis


def build_candidate(ctx: TContext, direction: str, variant: str, buy: float, sell: float, basis: list[str],
                    kb: float, ks: float, r: float, adjusted: bool = False, adjust_reason: str = "",
                    qty: int | None = None) -> tuple[TCandidate | None, str]:
    """按给定价位生成候选，返回 (候选, 不合格原因)。所有硬规则在这里检查。"""
    code = ctx.code
    close = ctx.close
    up, down = limit_prices(code, close, ctx.name)
    tick = price_tick(code)
    buy = round_price(code, buy)
    sell = round_price(code, sell)
    if not (down + tick <= buy < sell <= up - tick):
        return None, f"价位越界：买 {buy} / 卖 {sell} 需在跌停 {down} 与涨停 {up} 之间且买价低于卖价"
    cap_qty = _t_qty_cap(ctx)
    qty = cap_qty if qty is None else int(qty)
    min_lot, _ = lot_rule(code)
    if qty <= 0 or qty % 100 != 0 or qty < min_lot:
        return None, f"股数 {qty} 不合规：必须是 100 股整数倍且不少于 {min_lot} 股"
    if qty > cap_qty:
        return None, f"股数 {qty} 超过底仓 {ctx.base_qty} × 做T比例 {ctx.rules.t_max_ratio:.0%} = {cap_qty} 股"
    notes = []
    if direction == "正T":
        cash_cap = max_buy_qty(code, buy, ctx.cash, ctx.fees)
        pos_cap_value = ctx.total_assets * ctx.rules.max_position_pct
        room = pos_cap_value - ctx.position_value
        room_qty = int(max(0.0, room) / buy // 100 * 100)
        allowed = min(qty, cash_cap, room_qty)
        if allowed < qty:
            reason = "可用资金" if cash_cap <= room_qty else "单只仓位上限"
            if allowed < min_lot:
                return None, f"先买后卖需要先买入，但受{reason}限制买不了一手（可用资金 {ctx.cash:.0f} 元，单只上限 {ctx.rules.max_position_pct:.0%}）"
            notes.append(f"受{reason}限制，股数从 {qty} 降到 {allowed}")
            qty = allowed
    spread = (sell - buy) / buy
    be = breakeven_spread(code, buy, qty, ctx.fees)
    if spread < MIN_SPREAD_MULTIPLE * be:
        return None, f"预期价差 {spread:.2%} 不到保本价差 {be:.2%} 的 {MIN_SPREAD_MULTIPLE:.0f} 倍（振幅不够）"
    res = t_trade_result(code, ctx.base_qty, ctx.cost, buy, sell, qty, ctx.fees)
    f = lambda p: _fmt(code, p)  # noqa: E731
    r_pct = f"{r:.1%}"
    if direction == "正T":
        orders = [
            f"股价 ≤ {f(buy)} 元时，限价 {f(buy)} 元买入 {qty} 股，当日有效。",
            f"买入成交后再设：股价 ≥ {f(sell)} 元时，限价 {f(sell)} 元卖出 {qty} 股，当日有效。",
        ]
        gap = round_price(code, close * (1 - r))
        abandon = [
            f"开盘价低于 {f(gap)} 元（跳空低开超过 {r_pct}，可能有利空，先查公告）就撤掉买单。",
            "沪深 300 盘中跌幅超过 2% 时不买。",
            "14:30 前买单没有成交就撤单，当天不再做。",
        ]
        after_value = ctx.position_value + qty * buy
        stop = round_price(code, buy * (1 - 0.6 * r))
        wrap_up = [
            f"只成交了买入、没等到卖出：14:50 撤掉卖单，接受多出的 {qty} 股过夜"
            f"（总仓位约占总资产 {after_value / ctx.total_assets:.0%}，上限 {ctx.rules.max_position_pct:.0%}），"
            f"下一份预案优先把这 {qty} 股卖掉、恢复底仓。",
            f"买入成交后如果跌破 {f(stop)} 元，不再追加买入。",
        ]
    else:
        orders = [
            f"股价 ≥ {f(sell)} 元时，限价 {f(sell)} 元卖出 {qty} 股，当日有效。",
            f"卖出成交后再设：股价 ≤ {f(buy)} 元时，限价 {f(buy)} 元买回 {qty} 股，当日有效。",
        ]
        gap = round_price(code, close * (1 + r))
        abandon = [
            f"开盘价高于 {f(gap)} 元（跳空高开超过 {r_pct}，可能有利好，容易卖飞）就撤掉卖单。",
            "沪深 300 盘中涨幅超过 2% 时不卖。",
            "14:30 前卖单没有成交就撤单，当天不再做。",
        ]
        chase = round_price(code, sell * (1 + 0.6 * r))
        wrap_up = [
            f"只成交了卖出、没等到买回：14:50 时若股价不高于 {f(sell)} 元，就按现价买回 {qty} 股恢复底仓；"
            f"若已高于卖出价，接受底仓暂时少 {qty} 股，下一份预案优先买回。",
            f"卖出后如果涨破 {f(chase)} 元，不追高买回，按收尾规则处理。",
        ]
    bt = rough_backtest(ctx.daily, direction, kb, ks)
    bt["text"] = backtest_text(direction, bt, qty)
    cand = TCandidate(
        id=f"{direction}-{variant}",
        direction=direction,
        variant=variant,
        qty=qty,
        buy_price=buy,
        sell_price=sell,
        spread_pct=round(spread, 4),
        breakeven_pct=round(be, 4),
        breakeven_per_share=breakeven_spread_per_share(code, buy, qty, ctx.fees),
        fee_total=res.fee,
        net_profit=res.net,
        new_cost=res.new_cost,
        cost_drop=res.cost_drop,
        basis=basis,
        orders=orders,
        abandon=abandon,
        wrap_up=wrap_up,
        backtest=bt,
        qty_note="；".join(notes),
        adjusted=adjusted,
        adjust_reason=adjust_reason,
    )
    return cand, ""


def max_buy_qty(code: str, price: float, cash: float, fees: FeeSettings) -> int:
    if cash <= 0 or price <= 0:
        return 0
    qty = int(cash / price // 100 * 100)
    while qty > 0 and qty * price + trade_fee(code, price, qty, "buy", fees) > cash:
        qty -= 100
    return qty


def plan(ctx: TContext) -> TPlanResult:
    blockers, warnings = gates(ctx)
    facts: dict = {"close": ctx.close, "base_qty": ctx.base_qty, "cost": ctx.cost, "cash": ctx.cash}
    if len(ctx.daily) >= 25:
        t = trend(ctx.daily)
        r = mean_amplitude(ctx.daily, 20) or 0.0
        up, down = limit_prices(ctx.code, ctx.close, ctx.name)
        facts.update({
            "mean_amplitude_20d": round(r, 4),
            "trend": t.state,
            "trend_reasons": t.reasons,
            "limit_up": up,
            "limit_down": down,
            "t_qty_cap": _t_qty_cap(ctx),
            "low_20d": t.low_20d,
            "high_20d": t.high_20d,
        })
    if blockers:
        return TPlanResult(ctx.code, ctx.name, ctx.target_date, False, blockers, warnings, facts, [], None)

    t = trend(ctx.daily)
    r = facts["mean_amplitude_20d"]
    candidates: list[TCandidate] = []
    rejects: list[str] = []
    for direction in ("正T", "倒T"):
        for variant, (kb, ks) in VARIANTS.items():
            buy, sell, basis = _snap_levels(direction, ctx.close, r, kb, ks, t, ctx.code)
            cand, why = build_candidate(ctx, direction, variant, buy, sell, list(basis), kb, ks, r)
            if cand:
                candidates.append(cand)
            else:
                rejects.append(f"{direction}-{variant}：{why}")
    if not candidates:
        return TPlanResult(ctx.code, ctx.name, ctx.target_date, False, rejects or ["没有符合硬规则的候选"], warnings, facts, [], None)
    if t.state == "上升":
        pref_dir, why = "正T", "上升趋势里先卖容易卖飞，优先先买后卖"
    elif t.state == "下跌":
        pref_dir, why = "倒T", "下跌趋势里先买等于在下跌途中加仓，优先先卖后买"
    else:
        pref_dir, why = None, "横盘震荡，两个方向都可以"
    facts["direction_hint"] = why

    def score(c: TCandidate) -> float:
        bt = c.backtest
        days = max(bt["days"], 1)
        return (bt["both"] / days) * c.net_profit - (bt["first_only"] / days) * c.fee_total

    pool = [c for c in candidates if pref_dir is None or c.direction == pref_dir] or candidates
    preferred = max(pool, key=score).id
    if rejects:
        warnings.extend(rejects)
    return TPlanResult(ctx.code, ctx.name, ctx.target_date, True, [], warnings, facts, candidates, preferred)


def apply_adjustment(ctx: TContext, base: TCandidate, buy: float | None, sell: float | None, qty: int | None,
                     reason: str) -> tuple[TCandidate, str]:
    """导师改价位/股数：必须给理由，改完由程序重新校验；不通过则退回原候选。"""
    if not reason or len(reason.strip()) < 4:
        return base, "导师修改未附理由，已退回程序候选"
    r = mean_amplitude(ctx.daily, 20) or 0.0
    kb, ks = VARIANTS.get(base.variant, VARIANTS["稳健"])
    basis = base.basis + [f"导师调整：{reason.strip()}"]
    cand, why = build_candidate(
        ctx, base.direction, base.variant, buy or base.buy_price, sell or base.sell_price, basis, kb, ks, r,
        adjusted=True, adjust_reason=reason.strip(), qty=qty or base.qty,
    )
    if cand is None:
        return base, f"导师调整未通过校验（{why}），已退回程序候选"
    return cand, ""


def validate_candidate(ctx: TContext, c: TCandidate) -> list[str]:
    """独立校验一张做T预案是否违反硬规则（考卷与测试用；正常流程中候选已由 build_candidate 保证）。"""
    v: list[str] = []
    blockers, _ = gates(ctx)
    if blockers:
        v.append("存在阻断条件仍给出预案：" + "；".join(blockers))
    up, down = limit_prices(ctx.code, ctx.close, ctx.name)
    tick = price_tick(ctx.code)
    for label, p in (("买", c.buy_price), ("卖", c.sell_price)):
        if not (down <= p <= up):
            v.append(f"{label}价 {p} 超出涨跌停 [{down}, {up}]")
        if abs(p / tick - round(p / tick)) > 1e-6:
            v.append(f"{label}价 {p} 未对齐最小价位")
    min_lot, _ = lot_rule(ctx.code)
    if c.qty % 100 != 0 or c.qty < min_lot:
        v.append(f"股数 {c.qty} 不是合规整手")
    if c.qty > ctx.base_qty * ctx.rules.t_max_ratio + 1e-9:
        v.append(f"股数 {c.qty} 超过底仓的 {ctx.rules.t_max_ratio:.0%}")
    if c.buy_price >= c.sell_price:
        v.append("买价不低于卖价")
    if c.direction == "正T":
        need = c.qty * c.buy_price + trade_fee(ctx.code, c.buy_price, c.qty, "buy", ctx.fees)
        if need > ctx.cash + 1e-6:
            v.append(f"先买需要 {need:.0f} 元，超过可用资金 {ctx.cash:.0f} 元")
        if ctx.position_value + c.qty * c.buy_price > ctx.total_assets * ctx.rules.max_position_pct + 1e-6:
            v.append("买入后超过单只仓位上限")
    be = breakeven_spread(ctx.code, c.buy_price, c.qty, ctx.fees)
    if (c.sell_price - c.buy_price) / c.buy_price < MIN_SPREAD_MULTIPLE * be - 1e-9:
        v.append("预期价差不到保本价差 3 倍")
    for fld in ("orders", "abandon", "wrap_up", "basis"):
        if not getattr(c, fld):
            v.append(f"缺少 {fld}")
    if not c.backtest.get("text"):
        v.append("缺少历史粗验")
    return v


# ---------------- 次日对账 ----------------


def reconcile_plan(card: dict, bar: dict | None, trades: list, index_chg: float | None = None,
                   fees: FeeSettings | None = None) -> dict:
    """对照目标日的日线和成交，核对做T预案：触发了没有、照做了没有、扣费后效果如何。

    card: 做T预案卡 payload（含 direction/qty/buy_price/sell_price/abandon_open_*）。
    bar: 目标日日线 {open, high, low, close}；trades: 目标日该股票的成交（Trade）。
    """
    if not card.get("candidate"):
        return {"status": "明天不做", "summary": "预案是「不做」，无需对账。"}
    c = card["candidate"]
    code = card["code"]
    direction, qty, buy, sell = c["direction"], c["qty"], c["buy_price"], c["sell_price"]
    tick = price_tick(code)
    if bar is None:
        return {"status": "数据缺失", "summary": "没取到目标日日线，无法核对触发情况。"}
    r = card.get("facts", {}).get("mean_amplitude_20d") or 0
    prev_close = card.get("facts", {}).get("close")
    abandon_hit = False
    if prev_close and r:
        if direction == "正T" and bar["open"] < round_price(code, prev_close * (1 - r)):
            abandon_hit = True
        if direction == "倒T" and bar["open"] > round_price(code, prev_close * (1 + r)):
            abandon_hit = True
    if index_chg is not None and abs(index_chg) > 0.02:
        if (direction == "正T" and index_chg < -0.02) or (direction == "倒T" and index_chg > 0.02):
            abandon_hit = True
    buy_touch = bar["low"] <= buy + 1e-9
    sell_touch = bar["high"] >= sell - 1e-9
    buys = [t for t in trades if t.side == "buy"]
    sells = [t for t in trades if t.side == "sell"]
    buy_q = sum(t.quantity for t in buys)
    sell_q = sum(t.quantity for t in sells)
    buy_px = sum(t.price * t.quantity for t in buys) / buy_q if buy_q else None
    sell_px = sum(t.price * t.quantity for t in sells) / sell_q if sell_q else None
    fee = sum(t.fee_total for t in trades)
    out: dict = {
        "buy_touched": buy_touch,
        "sell_touched": sell_touch,
        "abandon_triggered": abandon_hit,
        "bought": buy_q,
        "sold": sell_q,
        "buy_avg": round(buy_px, 3) if buy_px else None,
        "sell_avg": round(sell_px, 3) if sell_px else None,
        "fees": round(fee, 2),
    }
    matched = min(buy_q, sell_q)
    deviations = []
    if buy_q and abs(buy_q - qty) > 0:
        deviations.append(f"买入 {buy_q} 股，预案是 {qty} 股")
    if sell_q and abs(sell_q - qty) > 0:
        deviations.append(f"卖出 {sell_q} 股，预案是 {qty} 股")
    if buy_px and buy_px > buy + tick:
        deviations.append(f"买入均价 {buy_px:.2f} 高于预案价 {buy}")
    if sell_px and sell_px < sell - tick:
        deviations.append(f"卖出均价 {sell_px:.2f} 低于预案价 {sell}")
    if matched:
        buy_fee = sum(t.fee_total * min(1, matched / buy_q) for t in buys)
        sell_fee = sum(t.fee_total * min(1, matched / sell_q) for t in sells)
        net = (sell_px - buy_px) * matched - buy_fee - sell_fee
        out.update(status="两边成交", net=round(net, 2), extra_shares=buy_q - sell_q)
        out["summary"] = f"两边都成交 {matched} 股，扣费后{'赚' if net >= 0 else '亏'} {abs(net):.0f} 元。"
    elif buy_q or sell_q:
        close = bar["close"]
        if buy_q:
            mtm = (close - buy_px) * buy_q - fee
            out.update(status="只成交买入", net_mark_to_market=round(mtm, 2), extra_shares=buy_q)
            out["summary"] = f"只成交买入 {buy_q} 股，多出的仓位按收盘价计浮动{'盈' if mtm >= 0 else '亏'} {abs(mtm):.0f} 元（含手续费）。"
        else:
            mtm = (sell_px - close) * sell_q - fee
            out.update(status="只成交卖出", net_mark_to_market=round(mtm, 2), extra_shares=-sell_q)
            out["summary"] = f"只成交卖出 {sell_q} 股，相对继续持有按收盘价计{'少亏/多赚' if mtm >= 0 else '少赚'} {abs(mtm):.0f} 元（含手续费）。"
    else:
        first_touch = buy_touch if direction == "正T" else sell_touch
        if abandon_hit:
            out.update(status="触发放弃条件", net=0.0, summary="开盘或大盘触发了放弃条件，按预案不做，执行正确。")
        elif first_touch:
            out.update(status="触发但未成交", net=0.0, summary="第一步的价位摸到了，但没有成交记录：条件单没设，或者没有照做。")
        else:
            out.update(status="未触发", net=0.0, summary="价位没有摸到，条件单不会成交，什么也没发生。")
    out["deviations"] = deviations
    if deviations:
        out["summary"] += " 偏离预案：" + "；".join(deviations) + "。"
    return out
