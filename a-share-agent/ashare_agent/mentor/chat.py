"""结合持仓的自由问答：模型通过工具查数据、调用程序计算；涉及操作的回答落成卡片。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .. import cards as C
from ..behavior import disposition, t_scorecard
from ..indicators import summary
from ..llm import LLMError, ToolSpec
from ..market import (
    breakeven_spread,
    breakeven_spread_per_share,
    cost_after_add,
    limit_prices,
    normalize_code,
    sellable_today,
    sellable_tomorrow,
    t_trade_result,
    trade_fee,
)
from . import prompts as P
from .core import Mentor


def _num(x: Any) -> float:
    return float(str(x).replace(",", ""))


class ChatAgent:
    def __init__(self, mentor: Mentor) -> None:
        self.m = mentor
        self.created_cards: list[int] = []

    # ---------------- 工具 ----------------
    def tool_get_portfolio(self) -> dict:
        pv = self.m.portfolio()
        keep = ("code", "name", "qty", "sellable_tomorrow", "cost", "close", "pct_chg", "value", "weight", "pnl", "pnl_pct",
                "industry", "trend")
        return {"as_of": pv["as_of"], "total_assets": pv["total_assets"], "cash": pv["cash"],
                "holdings": [{k: h.get(k) for k in keep} for h in pv["holdings"]], "concentration": pv["concentration"],
                "industry": pv["industry"], "data_gaps": pv["gaps"],
                "source": f"本地持仓快照 + 日线（截至 {pv['as_of']}）"}

    def tool_get_stock_data(self, code: str) -> dict:
        c = normalize_code(code)
        if not c:
            return {"error": f"代码 {code} 无效"}
        m = self.m
        out: dict[str, Any] = {"code": c, "as_of": m.as_of, "sources": {}, "data_gaps": []}
        d = m.daily(c, m.as_of, 120)
        if d.ok:
            out["daily_summary"] = summary(d.value)
            out["last_5_days"] = d.value.tail(5)[["date", "open", "high", "low", "close", "pct_chg"]].round(4).to_dict("records")
            out["sources"]["日线"] = d.label()
        else:
            out["data_gaps"].append(f"日线：{d.error}")
        prof = m.provider.profile(c)
        name = ""
        if prof.ok:
            out["profile"] = prof.value
            name = prof.value.get("name", "")
            out["sources"]["个股资料"] = prof.label()
        else:
            out["data_gaps"].append(f"个股资料：{prof.error}")
        if d.ok:
            up, down = limit_prices(c, float(d.value["close"].iloc[-1]), name)
            out["next_day_limits"] = {"date": m.next_day, "limit_up": up, "limit_down": down, "source": "程序按交易所规则计算"}
        fin = m.provider.financials(c)
        if fin.ok:
            out["financials"] = fin.value[:2]
            out["sources"]["财务摘要"] = fin.label()
        else:
            out["data_gaps"].append(f"财务摘要：{fin.error}（没有市盈率、净利润等数据时不要猜）")
        since = (datetime.strptime(m.as_of, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
        ann = m.provider.announcements(c, since, m.as_of)
        if ann.ok:
            out["announcements_14d"] = ann.value[:8]
            out["sources"]["公告"] = ann.label()
        else:
            out["data_gaps"].append(f"公告：{ann.error}")
        ev = m.provider.events(c, m.next_day, m.add_trading_days(m.next_day, 20))
        if ev.ok:
            out["events_next_20d"] = ev.value
            out["sources"]["事件日历"] = ev.label()
        else:
            out["data_gaps"].append(f"事件日历：{ev.error}")
        out["note"] = "以上之外的数据（实时价格、市盈率、新闻等）本工具没有提供，回答时不要编造。"
        return out

    def tool_calc_fee(self, code: str, price: float, qty: int, side: str) -> dict:
        s = "sell" if str(side).lower() in ("sell", "卖", "卖出") else "buy"
        fee = trade_fee(normalize_code(code) or code, _num(price), int(_num(qty)), s, self.m.config.fees)
        f = self.m.config.fees
        return {"fee": fee, "side": "卖出" if s == "sell" else "买入",
                "rule": f"佣金 {f.commission_rate * 1e4:.1f}‱（最低 {f.min_commission:.0f} 元）+ 过户费 0.001%" + (" + 卖出印花税 0.05%" if s == "sell" else "")}

    def tool_calc_limit_prices(self, code: str, prev_close: float, name: str = "", date: str = "") -> dict:
        from datetime import date as _date

        on = _date.fromisoformat(date) if date else _date.fromisoformat(self.m.next_day)
        up, down = limit_prices(normalize_code(code) or code, _num(prev_close), name, on)
        return {"limit_up": up, "limit_down": down, "note": "新股上市前 5 个交易日不设涨跌幅"}

    def tool_calc_sellable(self, holding_qty: int, bought_today: int = 0) -> dict:
        q, b = int(_num(holding_qty)), int(_num(bought_today))
        return {"sellable_today": sellable_today(q, b), "sellable_tomorrow": sellable_tomorrow(q), "rule": "T+1：当天买入的股份次日才能卖"}

    def tool_calc_cost_after_add(self, code: str, qty: int, cost: float, add_qty: int, add_price: float) -> dict:
        c = normalize_code(code) or code
        new = cost_after_add(int(_num(qty)), _num(cost), int(_num(add_qty)), _num(add_price), c, self.m.config.fees)
        return {"new_cost": round(new, 3), "buy_fee": trade_fee(c, _num(add_price), int(_num(add_qty)), "buy", self.m.config.fees),
                "note": "买入费用已计入成本"}

    def tool_calc_t_result(self, code: str, base_qty: int, cost: float, buy_price: float, sell_price: float, t_qty: int) -> dict:
        r = t_trade_result(normalize_code(code) or code, int(_num(base_qty)), _num(cost), _num(buy_price), _num(sell_price),
                           int(_num(t_qty)), self.m.config.fees)
        return {"gross": r.gross, "fee": r.fee, "net": r.net, "new_cost": round(r.new_cost, 3), "cost_drop_per_share": round(r.cost_drop, 4)}

    def tool_calc_breakeven(self, code: str, price: float, qty: int) -> dict:
        c = normalize_code(code) or code
        return {"breakeven_per_share": breakeven_spread_per_share(c, _num(price), int(_num(qty)), self.m.config.fees),
                "breakeven_pct": round(breakeven_spread(c, _num(price), int(_num(qty)), self.m.config.fees), 5)}

    def tool_get_t_plan(self, code: str) -> dict:
        c = normalize_code(code) or code
        cards = [x for x in self.m.storage.cards(kind="t_plan", code=c, limit=5) if x["status"] != "replaced"]
        if not cards:
            return {"note": "还没有这只票的做T预案，可以让用户在「明日预案」页生成。"}
        x = cards[0]
        p = x["payload"]
        return {"card_id": x["id"], "target_date": x["target_date"], "conclusion": p["conclusion"], "actions": p["actions"],
                "extra": {k: p["extra"].get(k) for k in ("breakeven", "expected", "abandon", "wrap_up", "backtest", "blockers")}}

    def tool_get_trade_stats(self) -> dict:
        trades = self.m.storage.trades()
        if not trades:
            return {"note": "还没有导入成交记录"}
        sc = t_scorecard(trades)
        disp = disposition(trades)
        return {"t_scorecard": sc["total"], "t_by_code": sc["by_code"][:10], "disposition": disp.verdict(),
                "period": f"{trades[0].trade_date} 至 {trades[-1].trade_date}"}

    def tool_get_open_cards(self) -> dict:
        items = []
        for st in ("open", "triggered"):
            for x in self.m.storage.cards(status=st, limit=30):
                items.append({"id": x["id"], "kind": C.KINDS.get(x["kind"]), "title": x["title"], "status": C.STATUS_LABELS.get(st),
                              "conclusion": x["payload"].get("conclusion"), "review_date": x["review_date"]})
        return {"cards": items}

    def tool_submit_card(self, conclusion: str, basis: list, counter: str, review_at: str, actions: list | None = None,
                         wrong_if: list | None = None, worst_case_text: str = "", worst_case_amount: float | None = None,
                         gaps: list | None = None, code: str = "") -> dict:
        norm_basis = []
        for b in basis or []:
            if isinstance(b, dict):
                norm_basis.append({"text": str(b.get("text", "")), "source": str(b.get("source", ""))})
            else:
                norm_basis.append({"text": str(b), "source": ""})
        total = self.m.portfolio()["total_assets"] if worst_case_amount is not None else 0
        card = C.new_card(
            "answer", f"问答卡 · {conclusion[:18]}", conclusion=conclusion, basis=norm_basis, actions=list(actions or []),
            wrong_if=list(wrong_if or []), counter=counter, gaps=list(gaps or []), review_at=review_at,
            worst_case=C.worst_case(float(worst_case_amount), total, worst_case_text) if worst_case_amount is not None else None,
        )
        problems = C.validate_card(card)
        if problems:
            return {"ok": False, "problems": problems, "hint": "补全后再调用 submit_card"}
        cid = self.m.storage.add_card("answer", card, code=normalize_code(code) or "", title=card["title"], as_of=self.m.as_of,
                                      review_date=self.m.add_trading_days(self.m.as_of, 5), status="open" if card["actions"] else "info")
        self.created_cards.append(cid)
        return {"ok": True, "card_id": cid}

    def tools(self) -> list[ToolSpec]:
        S = lambda props, req=None: {"type": "object", "properties": props, "required": req or list(props)}  # noqa: E731
        num, intg, s = {"type": "number"}, {"type": "integer"}, {"type": "string"}
        return [
            ToolSpec("get_portfolio", "读取用户当前持仓、成本、收盘价、盈亏、仓位、行业分布（程序计算）", S({}), self.tool_get_portfolio),
            ToolSpec("get_stock_data", "查询一只股票的日线摘要、近 5 日行情、明日涨跌停价、个股资料、财务摘要、近期公告、未来事件；附来源与缺口",
                     S({"code": s}), self.tool_get_stock_data),
            ToolSpec("calc_fee", "计算一笔买入或卖出的交易费用（佣金含最低 5 元、印花税、过户费）",
                     S({"code": s, "price": num, "qty": intg, "side": {"type": "string", "enum": ["buy", "sell"]}}), self.tool_calc_fee),
            ToolSpec("calc_limit_prices", "按交易所规则计算涨停价、跌停价（区分主板、ST、创业板、科创板、北交所）",
                     S({"code": s, "prev_close": num, "name": s, "date": s}, ["code", "prev_close"]), self.tool_calc_limit_prices),
            ToolSpec("calc_sellable", "按 T+1 计算今天和明天可卖股数", S({"holding_qty": intg, "bought_today": intg}, ["holding_qty"]),
                     self.tool_calc_sellable),
            ToolSpec("calc_cost_after_add", "计算补仓后的持仓成本（含买入费用）",
                     S({"code": s, "qty": intg, "cost": num, "add_qty": intg, "add_price": num}), self.tool_calc_cost_after_add),
            ToolSpec("calc_t_result", "计算一次做T（一买一卖）扣费后的净收益和降本后的成本",
                     S({"code": s, "base_qty": intg, "cost": num, "buy_price": num, "sell_price": num, "t_qty": intg}), self.tool_calc_t_result),
            ToolSpec("calc_breakeven", "计算一买一卖的保本价差", S({"code": s, "price": num, "qty": intg}), self.tool_calc_breakeven),
            ToolSpec("get_t_plan", "读取某只股票最新的做T预案卡", S({"code": s}), self.tool_get_t_plan),
            ToolSpec("get_trade_stats", "读取交割单统计：做T成绩单、赚了就跑/亏了死扛倾向", S({}), self.tool_get_trade_stats),
            ToolSpec("get_open_cards", "列出还没了结的计划卡片", S({}), self.tool_get_open_cards),
            ToolSpec("submit_card", "把涉及具体操作的建议落成卡片（必须有结论、带来源的依据、反方理由、复查时间；有动作就要有认错条件和最坏情况）",
                     S({"conclusion": s, "basis": {"type": "array", "items": {"type": "object", "properties": {"text": s, "source": s}}},
                        "actions": {"type": "array", "items": s}, "wrong_if": {"type": "array", "items": s},
                        "worst_case_text": s, "worst_case_amount": num, "counter": s,
                        "gaps": {"type": "array", "items": s}, "review_at": s, "code": s},
                       ["conclusion", "basis", "counter", "review_at"]), self.tool_submit_card),
        ]

    # ---------------- 对话 ----------------
    def system_messages(self, card: dict | None = None) -> list[dict]:
        m = self.m
        sys = P.SYSTEM + "\n\n" + P.CHAT + f"\n\n今天是 {m.now:%Y-%m-%d}，最近一个已收盘交易日是 {m.as_of}，下一个交易日是 {m.next_day}。"
        msgs = [{"role": "system", "content": sys},
                {"role": "user", "content": P.profile_block(m.config.profile.to_dict(), m.config.rules.describe())},
                {"role": "assistant", "content": "好的，我记住了你的画像和规则。"}]
        if card:
            brief = {k: card["payload"].get(k) for k in ("title", "conclusion", "basis", "actions", "wrong_if", "worst_case", "counter", "gaps", "extra")}
            msgs += [{"role": "user", "content": "我想就这张卡片追问：\n" + json.dumps(brief, ensure_ascii=False, default=str)[:6000]},
                     {"role": "assistant", "content": "好的，请问。"}]
        return msgs

    def ask(self, question: str, history: list[dict] | None = None, card: dict | None = None) -> dict:
        msgs = self.system_messages(card) + list(history or []) + [{"role": "user", "content": question}]
        try:
            res = self.m.llm.run_with_tools(msgs, self.tools(), purpose="自由问答")
        except LLMError as exc:
            return {"text": f"导师暂时无法回答：{exc}", "tools": [], "cards": [], "error": str(exc)}
        return {"text": res.text, "tools": res.tool_calls, "cards": list(self.created_cards), "used_tools": res.used_tools}
