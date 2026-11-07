"""演示与考卷用的虚构账户：基于冻结快照（真实历史行情），持仓和成交都是虚构的。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import AppConfig, ModelProfile
from .data_source import SnapshotProvider
from .market import CN_TZ, FeeSettings, trade_fee
from .portfolio import Position
from .rules import InvestorProfile, UserRules
from .storage import Storage
from .trades import Trade, apply_trades

SNAPSHOT = Path(__file__).resolve().parent / "exam" / "snapshot.json"
DEMO_AS_OF = "2026-11-05"

# 快照里没有的事件，由考卷注入（标注来源），用于考「明天有事件就不做T」
INJECTED_EVENTS = {
    "000831": [{"date": d, "type": "停牌", "detail": "筹划重大资产重组（2026-10-31 公告）"}
               for d in ("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09")],
    "300750": [{"date": "2026-11-06", "type": "重大事项", "detail": "考卷注入：假设次日召开临时股东大会审议重大资产购买"}],
}

DEMO_POSITIONS_BEFORE = [
    Position("600111", "北方稀土", 3000, None, 38.20),
    Position("300750", "宁德时代", 300, None, 268.00),
    Position("300059", "东方财富", 2000, None, 21.50),
    Position("000831", "中国稀土", 1000, None, 55.00),
    Position("601398", "工商银行", 200, None, 7.80),
    Position("600900", "长江电力", 2000, None, 27.80),
]
DEMO_CASH = 48000.0


def _t(d: str, tm: str, code: str, name: str, side: str, qty: int, price: float, fees: FeeSettings) -> Trade:
    return Trade(trade_date=d, trade_time=tm, code=code, name=name, side=side, quantity=qty, price=price,
                 amount=round(qty * price, 2), commission=trade_fee(code, price, qty, side, fees), fee_estimated=True, source="demo")


def demo_history(fees: FeeSettings) -> list[Trade]:
    """过去两个月的虚构成交：几次做T（有赚有亏）、一次追涨、一次亏损后马上再买。"""
    T = lambda *a: _t(*a, fees=fees)  # noqa: E731
    return [
        T("2026-09-15", "10:02:11", "600111", "北方稀土", "buy", 1000, 45.10),
        T("2026-09-15", "14:21:40", "600111", "北方稀土", "sell", 1000, 45.95),
        T("2026-09-23", "09:47:02", "600111", "北方稀土", "sell", 1000, 44.80),
        T("2026-09-23", "14:40:15", "600111", "北方稀土", "buy", 1000, 45.60),
        T("2026-10-09", "10:15:33", "300059", "东方财富", "buy", 1000, 19.95),
        T("2026-10-09", "13:55:20", "300059", "东方财富", "sell", 1000, 20.30),
        T("2026-10-16", "09:35:08", "002594", "比亚迪", "buy", 500, 84.90),
        T("2026-10-21", "14:52:30", "002594", "比亚迪", "sell", 500, 80.20),
        T("2026-10-22", "09:41:12", "300059", "东方财富", "buy", 500, 18.60),
        T("2026-10-28", "10:30:45", "600111", "北方稀土", "buy", 1000, 34.90),
        T("2026-10-28", "14:35:10", "600111", "北方稀土", "sell", 1000, 35.60),
    ]


def demo_today(fees: FeeSettings) -> list[Trade]:
    T = lambda *a: _t(*a, fees=fees)  # noqa: E731
    return [
        T(DEMO_AS_OF, "09:38:20", "300059", "东方财富", "buy", 1000, 19.38),
        T(DEMO_AS_OF, "14:46:05", "600900", "长江电力", "sell", 500, 29.40),
    ]


def exam_provider(path: Path = SNAPSHOT) -> SnapshotProvider:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ev = data.setdefault("events", {})
    for code, items in INJECTED_EVENTS.items():
        ev[code] = ev.get(code, []) + items
    return SnapshotProvider(data)


def demo_now(as_of: str = DEMO_AS_OF) -> datetime:
    return datetime.strptime(as_of + " 20:30", "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)


def demo_config(base_url: str = "http://127.0.0.1:9876/v1") -> AppConfig:
    cfg = AppConfig()
    cfg.upsert(ModelProfile(name="模拟导师", provider="custom", base_url=base_url, model="mock-mentor", api_key="mock-key",
                            persist_key=True), activate=True)
    cfg.rules = UserRules(max_position_pct=0.30, max_trade_loss_pct=0.02, t_max_ratio=1 / 3, chase_pct=0.05,
                          custom=["不碰 ST 和北交所", "单只亏损超过 15% 必须做去留决策", "不在开盘半小时内追涨"])
    cfg.profile = InvestorProfile(capital_range="30-50万", experience_years="3-5年", max_drawdown="10%", holding_period="几周到几个月",
                                  can_watch="上班族，只能午休和收盘后看盘", t_experience="做过十几次，有赚有亏",
                                  mistakes="追涨、亏了不肯卖、亏完马上想扳回来", goal="稳住本金，慢慢降成本",
                                  summary="上班族，盯盘时间少，容易追涨和死扛；目标是稳住本金、降低持仓成本。")
    cfg.onboarded = True
    return cfg


def seed_demo(storage: Storage, fees: FeeSettings | None = None, with_today: bool = True) -> None:
    """写入虚构账户：昨日持仓快照、历史成交、今日成交，以及按成交推算出的今日持仓。"""
    fees = fees or FeeSettings()
    prev_day = "2026-11-04"
    storage.save_positions(DEMO_POSITIONS_BEFORE, as_of=prev_day, source="演示数据", cash=DEMO_CASH)
    storage.add_trades(demo_history(fees))
    if with_today:
        today = demo_today(fees)
        storage.add_trades(today)
        new_pos, _ = apply_trades(DEMO_POSITIONS_BEFORE, today)
        cash = DEMO_CASH - sum(t.amount + t.fee_total for t in today if t.side == "buy") + sum(t.amount - t.fee_total for t in today if t.side == "sell")
        storage.save_positions(new_pos, as_of=DEMO_AS_OF, source="演示数据（按今日成交更新）", cash=round(cash, 2))
