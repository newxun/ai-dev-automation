"""数字与规则：标准答案都是手算的（见每行注释），程序必须一分不差。"""

from datetime import date

import pytest

from ashare_agent.market import (
    FeeSettings,
    breakeven_spread_per_share,
    classify,
    cost_after_add,
    has_no_price_limit,
    limit_prices,
    lot_rule,
    normalize_code,
    price_limit_pct,
    price_tick,
    round_lot,
    sellable_today,
    sellable_tomorrow,
    t_trade_result,
    trade_fee,
)
from ashare_agent.rules import UserRules, check_security_allowed, cooling_gates, size_by_risk

F = FeeSettings()  # 万 2.5，最低 5 元，印花税卖出 0.05%，过户费 0.001%


@pytest.mark.parametrize("code,price,qty,side,fees,expected", [
    ("600111", 36.83, 1000, "buy", F, 9.58),     # 佣金 36830×0.00025=9.2075 + 过户费 0.3683
    ("300750", 264.06, 300, "sell", F, 60.21),   # 佣金 19.8045 + 印花税 39.609 + 过户费 0.79218
    ("000001", 10.0, 100, "buy", F, 5.01),       # 佣金不足 5 元按 5 元 + 过户费 0.01
    ("000001", 10.0, 100, "buy", FeeSettings(min_commission=0.0), 0.26),  # 免五：0.25 + 0.01
    ("510300", 4.0, 10000, "sell", F, 10.0),     # ETF 只收佣金，无印花税和过户费
])
def test_trade_fee(code, price, qty, side, fees, expected):
    assert trade_fee(code, price, qty, side, fees) == expected


@pytest.mark.parametrize("code,prev,name,on,expected", [
    ("300750", 264.06, "宁德时代", None, (316.87, 211.25)),          # 创业板 20%
    ("688981", 110.69, "中芯国际", None, (132.83, 88.55)),           # 科创板 20%
    ("830799", 20.0, "", None, (26.0, 14.0)),                        # 北交所 30%
    ("600289", 3.12, "*ST信通", date(2026, 11, 6), (3.43, 2.81)),    # 主板 ST 自 2026-07-06 起 10%
    ("600289", 3.12, "*ST信通", date(2026, 7, 3), (3.28, 2.96)),     # 新规前 5%
    ("300289", 3.12, "ST某某", date(2026, 11, 6), (3.74, 2.50)),     # 创业板 ST 仍是 20%
    ("600519", 1452.0, "贵州茅台", None, (1597.2, 1306.8)),          # 主板 10%
])
def test_limit_prices(code, prev, name, on, expected):
    assert limit_prices(code, prev, name, on) == expected


def test_new_stock_no_limit():
    assert has_no_price_limit(1) and has_no_price_limit(5)
    assert not has_no_price_limit(6)
    assert not has_no_price_limit(None)


def test_boards_and_lots():
    assert classify("688981").board == "科创板" and lot_rule("688981") == (200, 1)
    assert classify("830799").exchange == "bj" and lot_rule("830799") == (100, 1)
    assert lot_rule("600000") == (100, 100)
    assert round_lot("688981", 250.7) == 250  # 科创板 200 股起，之后可按 1 股递增
    assert round_lot("600000", 250) == 200
    assert classify("510300").is_fund and price_tick("510300") == 0.001
    assert price_limit_pct("000001") == 0.10


def test_t_plus_one():
    assert sellable_today(1500, bought_today=500) == 1000
    assert sellable_tomorrow(1500) == 1500
    assert sellable_tomorrow(1500, frozen=200) == 1300


def test_cost_after_add():
    # (1000×12.50 + 1000×11.64 + 买入费 5.12) / 2000 = 12.07256
    assert cost_after_add(1000, 12.5, 1000, 11.64, "000001", F) == 12.0726
    assert cost_after_add(1000, 12.5, 1000, 11.64, "000001", F, include_fee=False) == 12.07


def test_t_trade_result():
    # 卖 37.15×1000 费 28.23，买 36.67×1000 费 9.53；毛利 480，净 442.24；成本 (38.2×3000−442.24)/3000
    r = t_trade_result("600111", 3000, 38.2, 36.67, 37.15, 1000, F)
    assert (r.gross, r.fee, r.net, r.new_cost) == (480.0, 37.76, 442.24, 38.0526)


def test_breakeven_per_share():
    # 买费 5.15 + 卖费 12.47 = 17.62，/500 = 0.0352 → 向上取到 0.01
    assert breakeven_spread_per_share("600900", 29.28, 500, F) == 0.04


@pytest.mark.parametrize("raw,code", [("600111", "600111"), (600111, "600111"), ('="000001"', "000001"), ("SZ300750", "300750"),
                                      (1.0, "000001"), ("abc", None)])
def test_normalize_code(raw, code):
    assert normalize_code(raw) == code


def test_security_rules():
    r = UserRules()
    assert check_security_allowed("600289", "*ST信通", r) is not None
    assert check_security_allowed("830799", "", r) is not None
    assert check_security_allowed("600111", "北方稀土", r) is None


def test_size_by_risk_binds_on_worst_loss():
    r = UserRules(max_position_pct=0.30, max_trade_loss_pct=0.02)
    s = size_by_risk("600519", 1452.0, 1350.0, total_assets=400000, cash=200000, current_value=0, rules=r)
    # 最坏亏损上限 8000 元，每股风险 102 元 + 费用 → 最多 78 股 → 不足一手 = 0
    assert s["shares"] == 0
    s2 = size_by_risk("600111", 36.83, 34.0, total_assets=400000, cash=200000, current_value=0, rules=r)
    assert s2["shares"] % 100 == 0 and s2["worst_loss"] <= 8000


def test_cooling_gates():
    r = UserRules()
    gates = cooling_gates("600519", "buy", r, day_change=0.06, change_5d=0.12, holding_cost=None, last_price=100,
                          recent_loss_sells=[], today=date(2026, 11, 6), trading_days_between=lambda a, b: 1)
    assert any(g.kind == "追涨" for g in gates)
    gates = cooling_gates("300059", "buy", r, day_change=-0.01, change_5d=0.0, holding_cost=21.5, last_price=18.98,
                          recent_loss_sells=[], today=date(2026, 11, 6), trading_days_between=lambda a, b: 1)
    assert any("补仓" in g.message for g in gates)
    gates = cooling_gates("600111", "buy", r, day_change=0.0, change_5d=0.0, holding_cost=None, last_price=36,
                          recent_loss_sells=[(date(2026, 11, 5), "比亚迪")], today=date(2026, 11, 6), trading_days_between=lambda a, b: 1)
    assert gates and "亏" in gates[0].message
