"""导师主线：体检、去留、计划卡、复盘 + 次日做T预案、次日自动对账、自由问答。全部用冻结快照 + 模拟模型。"""

import pytest

from ashare_agent import cards as C
from ashare_agent.market import FeeSettings, trade_fee
from ashare_agent.mentor.chat import ChatAgent
from ashare_agent.trades import Trade


def test_health_check_card(make_mentor):
    m = make_mentor()
    c = m.storage.card(m.health_check())
    p = c["payload"]
    assert C.validate_card(p) == []
    status = {s["code"]: s["status"] for s in p["extra"]["stocks"]}
    assert status["000831"] == "需决策"            # 停牌 + 破位 + 浮亏 13.5%
    assert status["600111"] == "需关注"            # 仓位接近 30% 上限
    assert status["300750"] == "需关注"            # 次日有重大事项（考卷注入）
    assert any(g["industry"] == "小金属" for g in p["extra"]["industry_crowded"])
    assert any("停牌" in g for g in p["gaps"])     # 数据缺口必须写出来
    assert p["worst_case"]["amount"] >= 0


def test_keep_decision_halted_stock(make_mentor):
    m = make_mentor()
    p = m.storage.card(m.keep_decision("000831"))["payload"]
    assert p["extra"]["decision"] in ("减仓", "清仓换股")
    assert all("复牌" in a for a in p["actions"])  # 停牌期间不能写「明天卖」
    assert p["extra"]["ask_user"]                   # 没有买入理由时要问
    assert {x["name"] for x in p["extra"]["paths"]} >= {"继续持有", "止损换股（全部卖出）", "补仓", "做T降本"}
    assert C.validate_card(p) == []


def test_keep_decision_blocks_t(make_mentor):
    m = make_mentor()
    m.keep_decision("000831")
    cards = {c["code"]: c for c in (m.storage.card(i) for i in m.t_plans())}
    assert cards["000831"]["status"] == "skipped"
    assert any("去留结论" in b for b in cards["000831"]["payload"]["extra"]["blockers"])


def test_pretrade_cooling_gate(make_mentor):
    m = make_mentor()
    with pytest.raises(ValueError, match="冷静关卡"):
        m.pretrade("300059", "buy", "跌多了补一点")
    p = m.storage.card(m.pretrade("300059", "buy", "跌多了补一点", gate_reason="回踩前低支撑，只补很小一部分"))["payload"]
    assert p["extra"]["gates"] and p["extra"]["verdict"] == "建议不做" and not p["actions"]


def test_pretrade_plan_card_sizing(make_mentor):
    m = make_mentor()
    p = m.storage.card(m.pretrade("600036", "buy", "银行股估值低、分红稳定，回调到 20 日线附近", price=42.3, stop=40.5))["payload"]
    ex = p["extra"]
    assert ex["verdict"] == "可以做" and ex["qty"] % 100 == 0 and 0 < ex["qty"] <= ex["sizing"]["shares"]
    assert p["worst_case"]["pct"] <= m.config.rules.max_trade_loss_pct + 1e-9
    assert C.validate_card(p) == []


def test_bad_model_cannot_break_rules(make_mentor):
    m = make_mentor(behavior="bad")
    p = m.storage.card(m.pretrade("600036", "buy", "银行股估值低，回调到 20 日线附近", price=42.3, stop=40.5))["payload"]
    assert p["extra"]["qty"] <= p["extra"]["sizing"]["shares"]   # 模型想多买 1000 股被拦下
    assert "导师未参与" in " ".join(p["gaps"])
    ids = m.t_plans()
    for c in (m.storage.card(i) for i in ids):
        cand = c["payload"].get("candidate")
        if cand:
            f = c["payload"]["facts"]
            assert f["limit_down"] <= cand["buy_price"] < cand["sell_price"] <= f["limit_up"]
            assert not cand["adjusted"]


def test_daily_review_and_next_day_reconcile(make_mentor, storage):
    m = make_mentor(storage=storage)
    review = storage.card(m.daily_review())["payload"]
    labels = {t["code"]: t["labels"] for t in review["extra"]["trades"]}
    assert "计划外" in labels["300059"] and "计划外" in labels["600900"]
    tomorrow = [storage.card(x["card"]) for x in review["extra"]["tomorrow_cards"]]
    doing = [c for c in tomorrow if c["status"] == "open"]
    assert doing and len(doing) < len(tomorrow)
    assert C.validate_card(review) == []

    # 第二天：照着其中一张预案两边都成交，复盘时自动对账
    c = doing[0]
    cand, code = c["payload"]["candidate"], c["code"]
    fees = FeeSettings()
    first, second = ("buy", "sell") if cand["direction"] == "正T" else ("sell", "buy")
    px = {"buy": cand["buy_price"], "sell": cand["sell_price"]}
    trades = [Trade("2026-11-06", code, s, cand["qty"], px[s], trade_time=tm, amount=round(cand["qty"] * px[s], 2),
                    commission=trade_fee(code, px[s], cand["qty"], s, fees)) for s, tm in ((first, "10:01:00"), (second, "13:40:00"))]
    storage.add_trades(trades)
    m2 = make_mentor(as_of="2026-11-06", seed=False, storage=storage)
    r2 = storage.card(m2.daily_review())["payload"]
    rec = {x["code"]: x for x in r2["extra"]["t_reconcile"]}
    assert rec[code]["status"] == "两边成交" and rec[code]["net"] > 0
    assert all(l == "按做T预案" for t in r2["extra"]["trades"] for l in t["labels"] if "预案" in l)
    assert storage.card(c["id"])["status"] == "done"


def test_trade_report(make_mentor):
    rep = make_mentor().trade_report()
    assert rep["t"]["total"]["times"] >= 3
    assert rep["chase"]["buys"] >= 1 and rep["turnover"]["fees"] > 0


def test_chat_uses_tools_and_lands_card(make_mentor):
    m = make_mentor()
    agent = ChatAgent(m)
    a = agent.ask("300059 要不要加仓？")
    assert a["cards"] and m.storage.card(a["cards"][0])["kind"] == "answer"
    b = agent.ask("贵州茅台（600519）现在的市盈率是多少？")
    assert "没查到" in b["text"]


def test_merge_gaps_groups_same_reason():
    from ashare_agent.mentor.core import merge_gaps

    gaps = [f"{n} 事件日历（部分）：年报预约表未发布" for n in ("北方稀土", "宁德时代", "比亚迪", "长江电力")]
    gaps += ["中国稀土 最新日线是 2026-10-30", "东方财富 财务摘要：超时"]
    out = merge_gaps(gaps)
    assert out[0] == "事件日历（部分）：年报预约表未发布（北方稀土、宁德时代、比亚迪 等 4 只）"
    assert out[1:] == ["中国稀土 最新日线是 2026-10-30", "东方财富 财务摘要：超时"]


def test_chat_add_to_loser_creates_validated_card(make_mentor):
    m = make_mentor()
    res = ChatAgent(m).ask("东方财富我还要不要继续补仓？")
    assert res["cards"], res
    card = m.storage.card(res["cards"][0])
    assert card["payload"]["wrong_if"] and card["payload"]["worst_case"]["amount"] > 0
    assert "补仓" in card["payload"]["conclusion"]
