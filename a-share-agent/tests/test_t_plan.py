"""做T预案硬规则：基于冻结的真实历史行情。"""

from dataclasses import replace

import pytest

from ashare_agent.market import FeeSettings, limit_prices
from ashare_agent.rules import UserRules
from ashare_agent.t_plan import TContext, apply_adjustment, plan, reconcile_plan, validate_candidate

AS_OF, NEXT = "2026-11-05", "2026-11-06"


def ctx(provider, code, base_qty, cost, cash=50000.0, total=400000.0, as_of=AS_OF, name="", **kw):
    d = provider.daily(code, as_of, 120).value
    return TContext(code=code, name=name or provider.profile(code).value["name"], base_qty=base_qty, cost=cost, cash=cash,
                    total_assets=total, daily=d, target_date=provider.next_trading_day(as_of), rules=kw.pop("rules", UserRules()),
                    fees=FeeSettings(), as_of=as_of, **kw)


def test_eligible_candidates_respect_all_hard_rules(provider):
    for code, qty, cost in (("600111", 3000, 38.2), ("300059", 3000, 20.8), ("600900", 1500, 27.8)):
        c = ctx(provider, code, qty, cost)
        r = plan(c)
        assert r.eligible, (code, r.blockers)
        assert r.candidates and r.preferred in {x.id for x in r.candidates}
        up, down = limit_prices(code, c.close, c.name)
        for cand in r.candidates:
            assert validate_candidate(c, cand) == [], (code, cand.id)
            assert down <= cand.buy_price < cand.sell_price <= up
            assert cand.qty % 100 == 0 and cand.qty <= qty / 3
            assert cand.sell_price - cand.buy_price >= 3 * cand.breakeven_per_share - 1e-9
            assert cand.net_profit > 0
            assert cand.orders and cand.abandon and cand.wrap_up


@pytest.mark.parametrize("code,qty,cost,kw,expect", [
    ("601398", 200, 7.8, {}, "底仓太少"),
    ("000831", 1000, 55.0, {}, "停在 2026-10-30"),              # 真实停牌：11-02 起筹划重组
    ("300750", 300, 268.0, {"events_tomorrow": [{"type": "财报", "detail": "三季报"}]}, "已知事件"),
    ("600111", 3000, 38.2, {"keep_decision": "减仓"}, "去留结论"),
    ("600111", 3000, 38.2, {"t_history": {"times": 12, "net_total": -350.0}}, "默认建议停做"),
    ("600111", 3000, 38.2, {"listed_trading_days_tomorrow": 3}, "新股"),
    ("600111", 3000, 38.2, {"name": "ST北稀"}, "ST"),
])
def test_blockers(provider, code, qty, cost, kw, expect):
    r = plan(ctx(provider, code, qty, cost, **kw))
    assert not r.eligible
    assert any(expect in b for b in r.blockers), r.blockers


def test_broken_trend_blocks(provider):
    r = plan(ctx(provider, "688981", 600, 105.0, as_of="2026-10-30"))
    assert not r.eligible and any("破位" in b for b in r.blockers)


def test_low_amplitude_blocks(provider):
    c = ctx(provider, "600900", 3000, 27.8)
    d = c.daily.copy()
    mid = (d["high"] + d["low"]) / 2
    d["high"], d["low"] = mid * 1.004, mid * 0.996
    d["amplitude"] = (d["high"] - d["low"]) / d["close"].shift(1)
    r = plan(replace(c, daily=d))
    assert not r.eligible and any("振幅不够" in b for b in r.blockers)


def test_position_cap_and_cash_remove_buy_first(provider):
    c = ctx(provider, "600111", 3000, 38.2, total=380000.0)  # 3000×36.83=110490，已占 29%
    r = plan(c)
    assert r.eligible and all(x.direction == "倒T" for x in r.candidates)
    c2 = ctx(provider, "300059", 3000, 20.8, cash=1000.0)
    assert all(x.direction == "倒T" for x in plan(c2).candidates)


def test_adjustment_outside_limits_is_rejected(provider):
    c = ctx(provider, "600111", 3000, 38.2)
    r = plan(c)
    base = r.candidates[0]
    up, down = limit_prices("600111", c.close, c.name)
    cand, note = apply_adjustment(c, base, down - 1, up + 1, 150, "我觉得会大涨")
    assert cand is base and "退回" in note
    cand, note = apply_adjustment(c, base, base.buy_price - 0.05, None, None, "")
    assert cand is base and "未附理由" in note
    ok, note = apply_adjustment(c, base, round(base.buy_price - 0.02, 2), None, None, "下方 20 日低点支撑更强")
    assert note == "" and ok.adjusted and validate_candidate(c, ok) == []


def test_validator_catches_violations(provider):
    c = ctx(provider, "600111", 3000, 38.2)
    cand = plan(c).candidates[0]
    bad = replace(cand, qty=1500, buy_price=30.0, sell_price=99.0)
    probs = validate_candidate(c, bad)
    assert any("超出涨跌停" in p for p in probs) and any("超过底仓" in p for p in probs)


def test_reconcile_next_day(provider):
    c = ctx(provider, "600111", 3000, 38.2)
    r = plan(c)
    cand = next(x for x in r.candidates if x.id == r.preferred)
    card = {"code": "600111", "candidate": cand.to_dict(), "facts": r.facts}
    bar = provider.daily("600111", NEXT, 1).value.iloc[-1].to_dict()
    res = reconcile_plan(card, bar, [], 0.0, FeeSettings())
    assert res["status"] in ("未触发", "触发但未成交", "触发放弃条件")
    assert res["summary"]
    assert reconcile_plan({"code": "600111", "candidate": None}, bar, [])["status"] == "明天不做"
