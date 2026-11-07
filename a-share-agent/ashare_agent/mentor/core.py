"""导师：程序先取数、算数、把关规则，再让模型判断和解释，最后由程序把结果落成卡片。"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .. import cards as C
from ..behavior import detect_t_events, disposition, t_scorecard
from ..config import AppConfig
from ..data_source import DataBundle, MarketData
from ..indicators import mean_amplitude, summary, trend
from ..llm import LLMClient, LLMError
from ..market import (
    classify,
    cost_after_add,
    limit_prices,
    now_cn,
    round_price,
    sellable_tomorrow,
    trade_fee,
)
from ..portfolio import Position
from ..rules import check_security_allowed, cooling_gates, portfolio_violations, size_by_risk
from ..storage import Storage
from ..t_plan import TContext, apply_adjustment, plan, reconcile_plan
from ..trades import Trade, reconcile
from . import prompts as P

INDEXES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指", "sh000300": "沪深300"}


_GAP_RE = re.compile(r"^(\S+) (事件日历（部分）|事件日历|财务摘要|近期公告)：(.+)$")


def merge_gaps(gaps: list[str]) -> list[str]:
    """多只股票同一原因的缺口合并成一条，避免卡片被重复提示刷屏。"""
    groups: dict[object, list[str]] = {}
    for g in gaps:
        m = _GAP_RE.match(g)
        groups.setdefault((m.group(2), m.group(3)) if m else g, []).append(m.group(1) if m else "")
    out = []
    for key, names in groups.items():
        if not isinstance(key, tuple):
            out.append(key)
        elif len(names) == 1:
            out.append(f"{names[0]} {key[0]}：{key[1]}")
        else:
            who = "、".join(names[:3]) + (f" 等 {len(names)} 只" if len(names) > 3 else "")
            out.append(f"{key[0]}：{key[1]}（{who}）")
    return out


def _r(v: float | None, n: int = 4) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), n)


class Mentor:
    def __init__(self, storage: Storage, provider: MarketData, config: AppConfig, llm: LLMClient | None = None,
                 now: datetime | None = None) -> None:
        self.storage = storage
        self.provider = provider
        self.config = config
        self._llm = llm
        self.now = now or now_cn()
        self._as_of: str | None = None
        self._daily_cache: dict[tuple[str, str, int], Any] = {}

    # ---------------- 基础 ----------------
    @property
    def as_of(self) -> str:
        if self._as_of is None:
            self._as_of = self.provider.last_closed_trading_day(self.now)
        return self._as_of

    @property
    def next_day(self) -> str:
        return self.provider.next_trading_day(self.as_of)

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient(self.config.active, logger=self._log_send)
        return self._llm

    def _log_send(self, purpose: str, messages: list, masked: int) -> None:
        p = self.config.active if self._llm is None else self._llm.profile
        self.storage.log_send(p.name, p.model, purpose, messages, masked)

    def _messages(self, task: str, data: dict) -> list[dict]:
        rules = self.config.rules.describe()
        return [
            {"role": "system", "content": P.SYSTEM},
            {"role": "user", "content": f"{task}\n\n{P.profile_block(self.config.profile.to_dict(), rules)}\n\n{P.data_block(data)}"},
        ]

    def add_trading_days(self, d: str, n: int) -> str:
        for _ in range(n):
            d = self.provider.next_trading_day(d)
        return d

    def daily(self, code: str, end: str | None = None, days: int = 120):
        key = (code, end or self.as_of, days)
        if key not in self._daily_cache:
            self._daily_cache[key] = self.provider.daily(code, end or self.as_of, days)
        return self._daily_cache[key]

    def bar_on(self, code: str, d: str) -> dict | None:
        f = self.daily(code, d, 5)
        if not f.ok:
            return None
        df = f.value
        row = df[df["date"] == d]
        return None if row.empty else row.iloc[-1].to_dict()

    def index_changes(self, d: str | None = None) -> tuple[dict, list[str], dict]:
        d = d or self.as_of
        out, gaps, sources = {}, [], {}
        for sym, name in INDEXES.items():
            f = self.provider.index_daily(sym, d, 10)
            if f.ok and len(f.value) and f.value["date"].iloc[-1] == d:
                out[name] = _r(f.value["pct_chg"].iloc[-1])
                sources[name] = f.label()
            else:
                gaps.append(f"{name}当日行情：{f.error or '未获取到当日数据'}")
        return out, gaps, sources

    # ---------------- 组合视图 ----------------
    def portfolio(self, as_of: str | None = None) -> dict:
        as_of = as_of or self.as_of
        positions, meta = self.storage.latest_positions()
        cash = float(meta["cash"]) if meta else 0.0
        bundle = DataBundle()

        def load(p: Position) -> tuple[Position, Any, Any]:
            return p, self.daily(p.code, as_of, 120), self.provider.profile(p.code)

        with ThreadPoolExecutor(max_workers=6) as ex:
            loaded = list(ex.map(load, positions))
        rows = []
        for p, d, prof in loaded:
            name = p.name
            industry = ""
            list_date = ""
            if prof.ok and prof.value:
                name = name or prof.value.get("name", "")
                industry = prof.value.get("industry", "")
                list_date = prof.value.get("list_date", "")
            else:
                bundle.gaps.append(f"{name or p.code} 个股资料：{prof.error}")
            row: dict[str, Any] = {
                "code": p.code, "name": name, "qty": p.quantity, "sellable_tomorrow": sellable_tomorrow(p.quantity),
                "cost": p.cost_price, "industry": industry or "未知", "list_date": list_date, "board": classify(p.code).board,
            }
            if d.ok:
                df = d.value
                s = summary(df)
                row.update(close=s["close"], pct_chg=s["pct_chg"], data_date=s["date"], trend=s["trend"]["state"],
                           trend_reasons=s["trend"]["reasons"], mean_amp=s["mean_amplitude_20d"], chg_5d=s["chg_5d"],
                           chg_20d=s["chg_20d"], low_60d=s["trend"].get("low_60d"), source=d.label())
                if s["date"] != as_of:
                    bundle.gaps.append(f"{name or p.code} 最新日线是 {s['date']}，不是 {as_of}（可能停牌）")
            else:
                price = p.current_price or p.cost_price
                row.update(close=price, pct_chg=None, data_date="", trend="数据缺失", trend_reasons=[], source="券商导出价/成本价")
                bundle.gaps.append(f"{name or p.code} 日线：{d.error}（市值暂按{'券商导出价' if p.current_price else '成本价'}估算）")
            price = row["close"] or 0.0
            row["value"] = round(price * p.quantity, 2)
            if p.cost_price:
                row["pnl"] = round((price - p.cost_price) * p.quantity, 2)
                row["pnl_pct"] = _r(price / p.cost_price - 1)
            else:
                row["pnl"] = row["pnl_pct"] = None
                bundle.gaps.append(f"{name or p.code} 没有成本价，盈亏无法计算")
            rows.append(row)
        stock_value = sum(r["value"] for r in rows)
        total = stock_value + cash
        for r in rows:
            r["weight"] = _r(r["value"] / total) if total else None
        rows.sort(key=lambda r: -(r["value"] or 0))
        industry: dict[str, dict] = {}
        for r in rows:
            g = industry.setdefault(r["industry"], {"industry": r["industry"], "value": 0.0, "codes": []})
            g["value"] += r["value"]
            g["codes"].append(r["name"] or r["code"])
        ind = sorted(industry.values(), key=lambda x: -x["value"])
        for g in ind:
            g["weight"] = _r(g["value"] / total) if total else None
            g["value"] = round(g["value"], 2)
        weights = [r["weight"] or 0 for r in rows]
        conc = {
            "count": len(rows),
            "top1": _r(weights[0]) if weights else None,
            "top3": _r(sum(weights[:3])) if weights else None,
            "hhi": _r(sum(w * w for w in weights)) if weights else None,
            "cash_weight": _r(cash / total) if total else None,
        }
        return {"as_of": as_of, "cash": round(cash, 2), "stock_value": round(stock_value, 2), "total_assets": round(total, 2),
                "holdings": rows, "industry": ind, "concentration": conc, "gaps": bundle.gaps,
                "positions_updated": meta["created_at"] if meta else None}

    def _try_llm_json(self, task: str, data: dict, purpose: str, validate=None) -> tuple[dict | None, str]:
        try:
            return self.llm.complete_json(self._messages(task, data), purpose=purpose, validate=validate), ""
        except LLMError as exc:
            return None, str(exc)

    # ---------------- ③ 持仓体检 ----------------
    def health_check(self) -> int:
        pv = self.portfolio()
        if not pv["holdings"]:
            raise ValueError("还没有持仓，请先导入或录入持仓。")
        rules = self.config.rules
        total = pv["total_assets"]
        violations = portfolio_violations(pv["holdings"], total, rules)
        start, end = self.next_day, self.add_trading_days(self.next_day, 20)
        gaps = list(pv["gaps"])
        events_all, stocks = [], []
        ann_since = (datetime.strptime(self.as_of, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
        for h in pv["holdings"]:
            code, name = h["code"], h["name"] or h["code"]
            ev = self.provider.events(code, start, end)
            if ev.ok:
                for e in ev.value:
                    events_all.append({"code": code, "name": name, **e})
                if ev.error:
                    gaps.append(f"{name} 事件日历（部分）：{ev.error}")
            else:
                gaps.append(f"{name} 事件日历：{ev.error}")
            fin = self.provider.financials(code)
            fin_line = None
            if fin.ok and fin.value:
                f0 = fin.value[0]
                fin_line = {"period": f0.get("报告期"), "net_profit": f0.get("净利润"), "np_yoy": f0.get("净利润同比增长率"),
                            "rev_yoy": f0.get("营业总收入同比增长率")}
            elif not classify(code).is_fund:
                gaps.append(f"{name} 财务摘要：{fin.error}")
            ann = self.provider.announcements(code, ann_since, self.as_of)
            titles = [a["title"] for a in (ann.value or [])][:5] if ann.ok else []
            if not ann.ok:
                gaps.append(f"{name} 近期公告：{ann.error}")
            status, reasons = self._stock_status(h, [v for v in violations if v.code == code],
                                                 [e for e in events_all if e["code"] == code], fin_line)
            stocks.append({"code": code, "name": name, "status": status, "reasons": reasons, "weight": h["weight"],
                           "pnl_pct": h["pnl_pct"], "trend": h["trend"], "industry": h["industry"],
                           "financial": fin_line, "recent_announcements": titles})
        crowded = [g for g in pv["industry"] if (g["weight"] or 0) >= 0.4 or len(g["codes"]) >= 3]
        data = {
            "as_of": self.as_of, "total_assets": total, "cash": pv["cash"], "concentration": pv["concentration"],
            "industry": pv["industry"], "industry_crowded": crowded,
            "violations": [v.__dict__ for v in violations], "events_next_20_trading_days": events_all, "stocks": stocks,
            "data_gaps": gaps,
        }
        out, err = self._try_llm_json(P.HEALTH, data, "持仓体检", lambda d: [] if d.get("conclusion") and d.get("counter") else ["缺少 conclusion 或 counter"])
        need = [s for s in stocks if s["status"] == "需决策"]
        conc = pv["concentration"]
        card = C.new_card(
            "health", f"持仓体检 · {self.as_of}",
            conclusion=(out or {}).get("conclusion") or (
                f"{len(need)} 只需要做去留决策：{'、'.join(s['name'] for s in need)}" if need else "没有必须马上处理的持仓，按计划跟踪即可。"),
            basis=[
                C.basis(f"总资产 {C.money(total)}，股票 {conc['count']} 只，第一大持仓占 {C.pct(conc['top1'])}，前三大占 {C.pct(conc['top3'])}，现金占 {C.pct(conc['cash_weight'])}",
                        f"持仓快照 {pv['positions_updated']}；收盘价 {self.as_of}"),
                *[C.basis(p, "导师根据上述数据") for p in (out or {}).get("key_points", [])[:3]],
            ],
            actions=(out or {}).get("actions", []) or [f"对「需决策」的 {s['name']} 生成去留卡" for s in need],
            wrong_if=(out or {}).get("wrong_if", []) or ["组合里的「需决策」股票在复查前重新站稳 20 日均线"],
            counter=(out or {}).get("counter") or "体检只看了日线、财务摘要和已知事件，看不到你买入时的逻辑；理由仍成立的票可以保留。",
            gaps=merge_gaps(gaps),
            review_at=f"{self.add_trading_days(self.as_of, 5)}（一周后）或有新事件时",
            mentor_note=(out or {}).get("mentor_note", "") or (f"导师未参与：{err}" if err else ""),
        )
        if card["actions"]:
            worst = sum(max(0.0, (s_h["close"] or 0) - (s_h.get("low_60d") or s_h["close"] or 0)) * s_h["qty"]
                        for s_h in pv["holdings"] if any(s["code"] == s_h["code"] and s["status"] == "需决策" for s in stocks))
            card["worst_case"] = C.worst_case(worst, total, "「需决策」的股票都跌到前 60 日最低价时的额外亏损")
        comments = (out or {}).get("stock_comments", {}) or {}
        for s in stocks:
            s["comment"] = comments.get(s["code"]) or "；".join(s["reasons"]) or "暂无异常"
        card["extra"] = {"concentration": conc, "industry": pv["industry"], "industry_crowded": crowded,
                         "violations": [v.__dict__ for v in violations], "events": events_all, "stocks": stocks,
                         "holdings": pv["holdings"], "mentor_error": err}
        return self.storage.add_card("health", card, title=card["title"], as_of=self.as_of,
                                     review_date=self.add_trading_days(self.as_of, 5), status="info")

    def _stock_status(self, h: dict, violations: list, events: list, fin: dict | None) -> tuple[str, list[str]]:
        decide, watch = [], []
        for v in violations:
            decide.append(v.detail)
        if h["trend"] == "破位":
            decide.append("趋势破位：" + "；".join(h.get("trend_reasons", [])))
        pnl = h.get("pnl_pct")
        if pnl is not None and pnl <= -0.15 and h["trend"] in ("下跌", "破位"):
            decide.append(f"浮亏 {pnl:.1%} 且仍在下跌趋势")
        elif pnl is not None and pnl <= -0.10:
            watch.append(f"浮亏 {pnl:.1%}")
        if h["trend"] == "下跌":
            watch.append("处于下跌趋势")
        for e in events:
            watch.append(f"{e['date']} {e['type']}：{e.get('detail', '')}")
        if fin and fin.get("np_yoy"):
            try:
                yoy = float(str(fin["np_yoy"]).replace("%", "")) / 100
                if yoy <= -0.3:
                    watch.append(f"最新报告期净利润同比 {fin['np_yoy']}")
            except ValueError:
                pass
        w = h.get("weight") or 0
        if w > self.config.rules.max_position_pct * 0.8 and not violations:
            watch.append(f"仓位 {w:.0%} 接近上限 {self.config.rules.max_position_pct:.0%}")
        if decide:
            return "需决策", decide + watch
        if watch:
            return "需关注", watch
        return "健康", []

    # ---------------- ④ 去留决策 ----------------
    def keep_decision(self, code: str, buy_reason: str = "") -> int:
        pv = self.portfolio()
        h = next((x for x in pv["holdings"] if x["code"] == code), None)
        if h is None:
            raise ValueError(f"持仓里没有 {code}")
        if not h.get("cost"):
            raise ValueError("这只股票没有成本价，先在持仓里补上成本价。")
        d = self.daily(code, self.as_of, 250)
        gaps = list(g for g in pv["gaps"] if code in g or h["name"] in g)
        total = pv["total_assets"]
        price, cost, qty = h["close"], h["cost"], h["qty"]
        paths: list[dict] = []
        unreal = (price - cost) * qty
        need_gain = cost / price - 1 if price < cost else 0.0
        hist_text, p_hit = "", None
        if d.ok and len(d.value) >= 120:
            closes = d.value["close"].reset_index(drop=True)
            rets = [closes[i + 60] / closes[i] - 1 for i in range(len(closes) - 60)]
            p_hit = sum(1 for x in rets if x >= need_gain) / len(rets) if rets else None
            hist_text = f"过去 {len(closes)} 个交易日里，任意连续 60 个交易日涨幅达到 {need_gain:.1%} 的窗口占 {p_hit:.0%}（只是历史频率，不是预测）"
        else:
            gaps.append("日线不足 120 天，无法统计回本所需涨幅的历史频率")
        paths.append({"name": "继续持有", "money": round(unreal, 2),
                      "time": hist_text or "无法估计",
                      "detail": f"当前浮动盈亏 {C.money(unreal)}；回本需要上涨 {need_gain:.1%}" if need_gain else f"当前浮盈 {C.money(unreal)}"})
        sell_fee = trade_fee(code, price, qty, "sell", self.config.fees)
        realized = unreal - sell_fee
        paths.append({"name": "止损换股（全部卖出）", "money": round(realized, 2), "time": "立即",
                      "detail": f"按今收卖出 {qty} 股，扣卖出费用 {sell_fee:.0f} 元后实现盈亏 {C.money(realized)}，释放资金 {C.money(price * qty - sell_fee)}"})
        t = trend(d.value) if d.ok and len(d.value) >= 25 else None
        stop = round_price(code, t.low_60d * 0.99) if t and t.low_60d and t.low_60d < price else round_price(code, price * 0.92)
        sizing = size_by_risk(code, price, stop, total, pv["cash"], h["value"], self.config.rules)
        if sizing["shares"] > 0:
            new_cost = cost_after_add(qty, cost, sizing["shares"], price, code, self.config.fees)
            paths.append({"name": "补仓", "money": -sizing["worst_loss"], "time": "立即",
                          "detail": f"按你的规则最多补 {sizing['shares']} 股（受「{sizing['binding']}」约束），成本从 {cost:.3f} 降到 {new_cost:.3f}；"
                                    f"若跌到 {stop} 元，补的部分再亏 {C.money(sizing['worst_loss'])}"})
        else:
            paths.append({"name": "补仓", "money": 0, "time": "—",
                          "detail": f"按你的规则不能再补（受「{sizing.get('binding', '规则')}」约束）"})
        t_line = {"name": "做T降本", "money": 0, "time": "—", "detail": ""}
        if d.ok and unreal < 0:
            ctx = self._t_context(h, pv, self.config.rules)
            res = plan(ctx) if ctx else None
            if res and res.eligible:
                c0 = next(c for c in res.candidates if c.id == res.preferred)
                gap_money = -unreal
                n_times = math.ceil(gap_money / c0.net_profit) if c0.net_profit > 0 else None
                freq = c0.backtest["both"] / max(c0.backtest["days"], 1)
                days_needed = math.ceil(n_times / freq) if n_times and freq > 0 else None
                t_line.update(money=round(c0.net_profit, 2), time=f"约 {days_needed} 个交易日（约 {days_needed / 21:.0f} 个月）" if days_needed else "无法估计",
                              detail=f"按「{c0.id}」每次两边成交约降本 {c0.net_profit:.0f} 元，补回 {C.money(gap_money)} 需要成功 {n_times} 次；"
                                     f"历史上两边都成交的频率约 {freq:.0%}")
            elif res:
                t_line["detail"] = "明天不满足做T条件：" + "；".join(res.blockers[:2])
        elif unreal >= 0:
            t_line["detail"] = "目前没有浮亏，不需要靠做T解套"
        paths.append(t_line)
        fin = self.provider.financials(code)
        ann = self.provider.announcements(code, (datetime.strptime(self.as_of, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d"), self.as_of)
        if not fin.ok and not classify(code).is_fund:
            gaps.append(f"财务摘要：{fin.error}")
        if not ann.ok:
            gaps.append(f"近期公告：{ann.error}")
        prior_plan = next((c for c in self.storage.cards(kind="plan", code=code)), None)
        reason = buy_reason.strip() or (prior_plan["payload"]["extra"].get("reason", "") if prior_plan else "")
        if not reason:
            gaps.append("没有当初的买入理由记录")
        data = {"as_of": self.as_of, "stock": h, "total_assets": total, "paths": paths, "buy_reason": reason or None,
                "daily_summary": summary(d.value) if d.ok else None, "financials": (fin.value or [])[:2] if fin.ok else None,
                "announcements_30d": [a["title"] for a in (ann.value or [])][:8] if ann.ok else None, "data_gaps": gaps}

        def check(o: dict) -> list[str]:
            probs = []
            if o.get("decision") not in ("继续持有", "减仓", "清仓换股"):
                probs.append("decision 必须是 继续持有/减仓/清仓换股")
            if o.get("decision") == "减仓":
                q = o.get("reduce_qty")
                if not isinstance(q, (int, float)) or q <= 0 or int(q) % 100 or q > qty:
                    probs.append(f"减仓股数必须是 100 的整数倍且不超过 {qty}")
            if not o.get("counter"):
                probs.append("缺少 counter")
            return probs

        nd = self.next_day
        ev_next = self.provider.events(code, nd, nd)
        halted = h.get("data_date") != self.as_of or any("停牌" in e.get("type", "") for e in (ev_next.value or []))
        if halted:
            data["halted"] = "这只股票目前停牌（或今天没有交易），复牌前无法买卖"
        out, err = self._try_llm_json(P.KEEP, data, "去留决策", check)
        decision = (out or {}).get("decision") or "继续持有"
        actions, worst = [], None
        when = "复牌后第一个交易日" if halted else nd
        limit_note = "按复牌后开盘价判断，不要挂跌停价恐慌卖出" if halted else f"限价不低于 {round_price(code, price * 0.98)} 元（今收下浮 2%），当日有效"
        if halted:
            gaps.append("目前停牌，复牌时间和复牌价都不确定")
        if decision == "减仓":
            n = int(out["reduce_qty"])
            actions.append(f"{when} 卖出 {n} 股：{limit_note}。")
            worst = C.worst_case(max(0.0, (cost - price) * n), total, f"卖出的 {n} 股锁定亏损（卖后反弹也不再参与）")
        elif decision == "清仓换股":
            actions.append(f"{when} 卖出全部 {qty} 股：{limit_note}；没卖完次日继续。")
            worst = C.worst_case(max(0.0, -realized), total, "锁定的亏损（含卖出费用）")
        else:
            actions.append(f"设止损条件单：股价 ≤ {stop} 元时卖出 {qty} 股（长期有效）。")
            worst = C.worst_case(max(0.0, (price - stop) * qty + trade_fee(code, stop, qty, 'sell', self.config.fees)), total,
                                 f"跌到止损价 {stop} 元卖出时，在今收基础上再亏的金额")
        card = C.new_card(
            "keep", f"去留卡 · {h['name']}（{code}）",
            conclusion=(out or {}).get("conclusion") or f"程序默认：继续持有，并用 {stop} 元止损兜底（导师未参与）",
            basis=[C.basis(f"今收 {price}，成本 {cost}，持仓 {qty} 股，占总资产 {C.pct(h['weight'])}，趋势：{h['trend']}", h.get("source", ""))]
            + [C.basis(r, "导师根据上述数据") for r in (out or {}).get("reasons", [])[:4]],
            actions=actions, valid_until=self.add_trading_days(nd, 4),
            wrong_if=(out or {}).get("wrong_if") or [f"收盘跌破 {stop} 元"], worst_case=worst,
            counter=(out or {}).get("counter") or "继续持有可能错过把资金换到更好标的的机会。",
            gaps=gaps, review_at=f"{self.add_trading_days(self.as_of, 5)} 收盘后",
            mentor_note=(out or {}).get("mentor_note", "") or (f"导师未参与：{err}" if err else ""),
        )
        card["extra"] = {"decision": decision, "paths": paths, "cash_test": (out or {}).get("cash_test", ""),
                         "ask_user": (out or {}).get("ask_user"), "buy_reason": reason, "stop": stop, "mentor_error": err}
        return self.storage.add_card("keep", card, code=code, title=card["title"], as_of=self.as_of, target_date=nd,
                                     review_date=self.add_trading_days(self.as_of, 5), status="open")

    # ---------------- ⑤ 交易前对话 → 计划卡 ----------------
    def pretrade_check(self, code: str, side: str) -> dict:
        """只做程序检查（冷静关卡、规则、仓位），不调模型。"""
        pv = self.portfolio()
        h = next((x for x in pv["holdings"] if x["code"] == code), None)
        d = self.daily(code, self.as_of, 60)
        prof = self.provider.profile(code)
        name = (h or {}).get("name") or (prof.value or {}).get("name", "") if prof.ok else (h or {}).get("name", "")
        res: dict[str, Any] = {"code": code, "name": name, "side": side, "gates": [], "blocks": []}
        if not d.ok:
            res["blocks"].append(f"取不到 {code} 的日线：{d.error}")
            return res
        s = summary(d.value)
        res["summary"] = s
        v = check_security_allowed(code, name, self.config.rules)
        if v and side == "buy":
            res["gates"].append({"kind": "违反硬规则", "message": v.detail + "，属于你的禁区"})
        loss_sells = []
        recent = self.storage.trades(start=self.add_trading_days_back(self.as_of, 5))
        all_trades = self.storage.trades()
        for t in recent:
            if t.side == "sell":
                lots = [x for x in all_trades if x.code == t.code and x.side == "buy" and x.trade_date <= t.trade_date]
                if lots:
                    avg = sum(x.amount + x.fee_total for x in lots) / sum(x.quantity for x in lots)
                    if t.price < avg:
                        loss_sells.append((datetime.strptime(t.trade_date, "%Y-%m-%d").date(), t.name or t.code))
        gates = cooling_gates(code, side, self.config.rules, day_change=s["pct_chg"], change_5d=s["chg_5d"],
                              holding_cost=(h or {}).get("cost"), last_price=s["close"], recent_loss_sells=loss_sells,
                              today=datetime.strptime(self.as_of, "%Y-%m-%d").date() + timedelta(days=1),
                              trading_days_between=lambda a, b: self.provider.trading_days_between(a.isoformat(), b.isoformat()))
        res["gates"] += [g.__dict__ for g in gates]
        res["holding"] = h
        res["total_assets"] = pv["total_assets"]
        res["cash"] = pv["cash"]
        return res

    def add_trading_days_back(self, d: str, n: int) -> str:
        for _ in range(n):
            d = self.provider.prev_trading_day(d)
        return d

    def pretrade(self, code: str, side: str, reason: str, gate_reason: str = "", price: float | None = None,
                 stop: float | None = None, qty: int | None = None) -> int:
        chk = self.pretrade_check(code, side)
        if chk["blocks"]:
            raise ValueError("；".join(chk["blocks"]))
        if chk["gates"] and len(gate_reason.strip()) < 10:
            raise ValueError("触发了冷静关卡，请先写下至少 10 个字的理由：" + "；".join(g["message"] for g in chk["gates"]))
        s = chk["summary"]
        close = s["close"]
        up, down = limit_prices(code, close, chk["name"])
        entry = round_price(code, price or close)
        if not (down <= entry <= up):
            raise ValueError(f"价格 {entry} 不在明日涨跌停 [{down}, {up}] 之内")
        h = chk["holding"] or {}
        total, cash = chk["total_assets"] or 0.0, chk["cash"]
        t = trend(self.daily(code, self.as_of, 60).value)
        sizing: dict = {}
        if side == "buy":
            stop = round_price(code, stop) if stop else round_price(code, min(entry * 0.92, (t.low_20d or entry) * 0.99))
            if stop >= entry:
                raise ValueError("止损价必须低于买入价")
            sizing = size_by_risk(code, entry, stop, total, cash, h.get("value", 0.0), self.config.rules)
        else:
            held = h.get("qty", 0)
            if held <= 0:
                raise ValueError("你没有持有这只股票，不能卖出。")
            sizing = {"shares": held, "binding": "持仓数量"}
        data = {"as_of": self.as_of, "intent": {"code": code, "name": chk["name"], "side": "买入" if side == "buy" else "卖出",
                                                "price": entry, "user_qty": qty, "stop": stop},
                "user_reason": reason, "gates": chk["gates"], "gate_reason": gate_reason or None, "sizing": sizing,
                "daily_summary": s, "holding": h or None, "limit_up": up, "limit_down": down}

        def check(o: dict) -> list[str]:
            probs = []
            if o.get("verdict") not in ("可以做", "缩小仓位", "建议不做"):
                probs.append("verdict 取值不对")
            q = o.get("qty")
            if q is None or not isinstance(q, (int, float)) or q < 0 or int(q) % 100 or q > sizing.get("shares", 0):
                probs.append(f"qty 必须是 0~{sizing.get('shares', 0)} 之间的 100 整数倍")
            if not o.get("counter"):
                probs.append("缺少 counter")
            return probs

        out, err = self._try_llm_json(P.PRETRADE, data, "交易前计划", check)
        verdict = (out or {}).get("verdict") or ("建议不做" if chk["gates"] else "可以做")
        n = int((out or {}).get("qty") if out else min(qty or sizing.get("shares", 0), sizing.get("shares", 0)))
        if qty and n > qty:
            n = int(qty // 100 * 100)
        valid = int((out or {}).get("valid_days") or 5)
        valid = max(1, min(valid, 20))
        until = self.add_trading_days(self.as_of, valid)
        actions, worst = [], None
        if verdict != "建议不做" and n > 0:
            if side == "buy":
                actions = [f"股价 ≤ {entry} 元时，限价 {entry} 元买入 {n} 股，有效期至 {until}。",
                           f"买入成交后设止损条件单：股价 ≤ {stop} 元时卖出 {n} 股。"]
                loss = (entry - stop) * n + trade_fee(code, entry, n, "buy", self.config.fees) + trade_fee(code, stop, n, "sell", self.config.fees)
                worst = C.worst_case(loss, total, f"买入后跌到止损价 {stop} 元卖出")
            else:
                actions = [f"股价 ≥ {entry} 元时，限价 {entry} 元卖出 {n} 股，有效期至 {until}。"]
                cost = h.get("cost") or entry
                worst = C.worst_case(max(0.0, (cost - entry) * n), total, "按计划价卖出锁定的亏损（若有）")
        card = C.new_card(
            "plan", f"计划卡 · {'买入' if side == 'buy' else '卖出'} {chk['name']}（{code}）",
            conclusion=(out or {}).get("conclusion") or (f"{verdict}（导师未参与）"),
            basis=[C.basis(f"今收 {close}，今日涨跌 {C.pct(s['pct_chg'])}，近 5 日 {C.pct(s['chg_5d'])}，趋势：{s['trend']['state']}",
                           self.daily(code, self.as_of, 60).label())]
            + [C.basis(r, "导师根据上述数据") for r in (out or {}).get("reasons", [])[:4]],
            actions=actions, valid_until=until,
            wrong_if=(out or {}).get("wrong_if") or ([f"收盘跌破 {stop} 元"] if side == "buy" and actions else []),
            worst_case=worst, counter=(out or {}).get("counter") or "程序默认：没有导师意见，请自己写下反方理由再执行。",
            gaps=[] if out else [f"导师未参与：{err}"], review_at=f"{until} 收盘后",
            mentor_note=(out or {}).get("mentor_note", ""),
        )
        card["extra"] = {"verdict": verdict, "side": side, "qty": n, "entry": entry, "stop": stop, "reason": reason,
                         "gates": chk["gates"], "gate_reason": gate_reason, "sizing": sizing, "mentor_error": err}
        status = "open" if actions else "skipped"
        return self.storage.add_card("plan", card, code=code, title=card["title"], as_of=self.as_of,
                                     target_date=self.next_day, review_date=until, status=status)

    # ---------------- ⑦ 次日做T预案 ----------------
    def _t_context(self, h: dict, pv: dict, rules, t_hist: dict | None = None, keep: str | None = None) -> TContext | None:
        d = self.daily(h["code"], self.as_of, 120)
        if not d.ok or not h.get("cost"):
            return None
        nd = self.next_day
        ev = self.provider.events(h["code"], nd, nd)
        listed = None
        if h.get("list_date"):
            listed = self.provider.trading_days_between(h["list_date"], nd) + 1 if h["list_date"] <= nd else 0
        return TContext(code=h["code"], name=h["name"], base_qty=h["sellable_tomorrow"], cost=h["cost"], cash=pv["cash"],
                        total_assets=pv["total_assets"], daily=d.value, target_date=nd, rules=rules, fees=self.config.fees,
                        events_tomorrow=ev.value if ev.ok else [], events_known=ev.ok and not ev.error,
                        keep_decision=keep, listed_trading_days_tomorrow=listed, t_history=t_hist, as_of=self.as_of)

    def latest_keep_decision(self, code: str) -> str | None:
        for c in self.storage.cards(kind="keep", code=code, limit=5):
            if c["as_of"] >= self.add_trading_days_back(self.as_of, 20):
                return c["payload"].get("extra", {}).get("decision")
        return None

    def t_plans(self) -> list[int]:
        pv = self.portfolio()
        nd = self.next_day
        holdings_qty = {h["code"]: h["qty"] for h in pv["holdings"]}
        sc = t_scorecard(self.storage.trades(), holdings_qty)
        hist = {s["code"]: s for s in sc["by_code"]}
        results, ctxs = [], {}
        for h in pv["holdings"]:
            ctx = self._t_context(h, pv, self.config.rules, hist.get(h["code"]), self.latest_keep_decision(h["code"]))
            if ctx is None:
                results.append((h, None))
                continue
            ctxs[h["code"]] = ctx
            results.append((h, plan(ctx)))
        eligible = {h["code"]: r for h, r in results if r and r.eligible}
        data = {"as_of": self.as_of, "target_date": nd, "stocks": [], "t_scorecard": sc["total"]}
        for h, r in results:
            item = {"code": h["code"], "name": h["name"], "eligible": bool(r and r.eligible),
                    "base_qty": h["sellable_tomorrow"], "cost": h["cost"], "close": h["close"], "weight": h["weight"],
                    "t_history": hist.get(h["code"]), "keep_decision": self.latest_keep_decision(h["code"])}
            if r is None:
                item["blockers"] = ["缺少日线或成本价"]
            elif not r.eligible:
                item["blockers"] = r.blockers
            else:
                item["facts"] = r.facts
                item["warnings"] = r.warnings
                item["program_preferred"] = r.preferred
                item["candidates"] = [{k: v for k, v in c.to_dict().items() if k not in ("orders", "abandon", "wrap_up", "basis")}
                                      | {"backtest": c.backtest["text"]} for c in r.candidates]
            data["stocks"].append(item)

        def check(o: dict) -> list[str]:
            probs = []
            got = {p.get("code"): p for p in o.get("plans", []) if isinstance(p, dict)}
            for code, r in eligible.items():
                p = got.get(code)
                if p is None:
                    probs.append(f"缺少 {code} 的判断")
                    continue
                if p.get("decision") == "做" and p.get("candidate_id") not in {c.id for c in r.candidates}:
                    probs.append(f"{code} 的 candidate_id 不在候选里")
            return probs

        out, err = (None, "")
        if eligible:
            out, err = self._try_llm_json(P.T_PLANS, data, "次日做T预案", check)
        decisions = {p["code"]: p for p in (out or {}).get("plans", []) if isinstance(p, dict) and p.get("code")}
        skipped_comments = (out or {}).get("skipped_comments", {}) or {}
        for old in self.storage.cards(kind="t_plan", target_date=nd):
            self.storage.update_card(old["id"], status="replaced")
        ids = []
        for h, r in results:
            ids.append(self._t_card(h, r, ctxs.get(h["code"]), decisions.get(h["code"]), skipped_comments.get(h["code"]), err, pv))
        return ids

    def _t_card(self, h: dict, r, ctx: TContext | None, dec: dict | None, skip_comment: str | None, err: str, pv: dict) -> int:
        nd = self.next_day
        code, name = h["code"], h["name"] or h["code"]
        title = f"明日做T预案 · {name}（{code}）"
        head = f"底仓 {h['sellable_tomorrow']} 股，成本 {h['cost']} 元，今收 {h['close']} 元"
        if r is not None and r.facts.get("limit_up"):
            head += f"，明日涨跌停 {r.facts['limit_up']} / {r.facts['limit_down']} 元"
        src = h.get("source", "")
        gaps = [g for g in pv["gaps"] if code in g or name in g]
        if r is not None:
            gaps += [w for w in r.warnings if "没查全" in w]
        if r is None or not r.eligible or (dec and dec.get("decision") == "不做") or (r.eligible and not dec):
            if r is None:
                reasons = ["缺少日线或成本价，无法计算"]
            elif not r.eligible:
                reasons = r.blockers
            elif dec:
                reasons = [f"导师判断：{dec.get('reason', '')}"]
            else:
                reasons = [f"导师未参与（{err}），程序不替你决定做不做，默认不做" if err else "导师没有给出判断，默认不做"]
            card = C.new_card(
                "t_plan", title, conclusion="明天不做T。",
                basis=[C.basis(head, src)] + [C.basis(x, "程序硬规则" if r is not None and not r.eligible else "导师") for x in reasons],
                counter=(dec or {}).get("counter") or "如果明天振幅很大，会错过一次小幅降本的机会；但做T降本本来就很小，错过不可惜。",
                gaps=gaps, review_at=f"{nd} 收盘后", mentor_note=skip_comment or "",
            )
            card["extra"] = {"head": head, "blockers": reasons, "facts": (r.facts if r else {}), "mentor_error": err}
            payload = card | {"code": code, "candidate": None, "facts": (r.facts if r else {})}
            return self.storage.add_card("t_plan", payload, code=code, title=title, as_of=self.as_of, target_date=nd,
                                         review_date=nd, status="skipped")
        cand = next(c for c in r.candidates if c.id == dec["candidate_id"])
        note = ""
        adj = dec.get("adjust")
        if isinstance(adj, dict) and ctx is not None:
            cand, note = apply_adjustment(ctx, cand, adj.get("buy_price"), adj.get("sell_price"), adj.get("qty"), adj.get("reason", ""))
        f = r.facts
        R = f.get("mean_amplitude_20d") or 0
        if cand.direction == "正T":
            stop = round_price(code, cand.buy_price * (1 - 0.6 * R))
            loss = (cand.buy_price - stop) * cand.qty + trade_fee(code, cand.buy_price, cand.qty, "buy", self.config.fees)
            worst = C.worst_case(loss, pv["total_assets"], f"只买入成交、随后跌到 {stop} 元时多出的 {cand.qty} 股的亏损")
            mode = "先买后卖"
        else:
            chase = round_price(code, cand.sell_price * (1 + 0.6 * R))
            loss = (chase - cand.sell_price) * cand.qty + trade_fee(code, cand.sell_price, cand.qty, "sell", self.config.fees)
            worst = C.worst_case(loss, pv["total_assets"], f"只卖出成交、随后涨到 {chase} 元时少赚的金额（踏空）")
            mode = "先卖后买"
        card = C.new_card(
            "t_plan", title,
            conclusion=f"明天可以做一次{cand.direction}（{mode}），{cand.qty} 股。",
            basis=[C.basis(head, src),
                   C.basis(f"近 20 日平均振幅 {R:.1%}；趋势：{f.get('trend')}（{'；'.join(f.get('trend_reasons', []))}）", src),
                   C.basis(f"明天已知事件：无；去留结论：{ctx.keep_decision if ctx and ctx.keep_decision else '未做去留评估（默认持有）'}", "事件日历 / 去留卡"),
                   C.basis(f"导师选择理由：{dec.get('reason', '')}", "导师")],
            actions=cand.orders, valid_until=f"{nd} 当日有效",
            wrong_if=[cand.abandon[0], cand.wrap_up[-1]], worst_case=worst,
            counter=dec.get("counter") or ("如果明天放量下跌，你会在下跌途中加仓。" if cand.direction == "正T" else "如果明天放量上涨，卖出的筹码可能买不回来。"),
            gaps=gaps + ([note] if note else []), review_at=f"{nd} 收盘后（复盘时自动对账）",
        )
        card["extra"] = {
            "head": head, "direction": cand.direction, "direction_reason": dec.get("reason", ""),
            "breakeven": f"一买一卖手续费约 {cand.fee_total:.0f} 元，保本价差每股 {cand.breakeven_per_share} 元（{cand.breakeven_pct:.2%}）",
            "expected": f"两边都成交的话扣费后约赚 {cand.net_profit:.0f} 元，相当于成本从 {h['cost']} 元降到约 {cand.new_cost:.3f} 元。",
            "abandon": cand.abandon, "wrap_up": cand.wrap_up, "backtest": cand.backtest["text"], "basis": cand.basis,
            "alternatives": [f"{c.id}：买 {c.buy_price} / 卖 {c.sell_price}，{c.qty} 股，扣费净 {c.net_profit:.0f} 元" for c in r.candidates if c.id != cand.id],
            "adjust_note": note, "warnings": r.warnings,
        }
        payload = card | {"code": code, "candidate": cand.to_dict(), "facts": f}
        return self.storage.add_card("t_plan", payload, code=code, title=title, as_of=self.as_of, target_date=nd,
                                     review_date=nd, status="open")

    # ---------------- ⑥ 每日盘后复盘 ----------------
    def label_trades(self, trades: list[Trade]) -> list[dict]:
        out = []
        t_cards = {(c["code"], c["target_date"]): c for c in self.storage.cards(kind="t_plan", limit=1000) if c["status"] != "replaced"}
        plan_cards = self.storage.cards(kind="plan", limit=1000)
        all_trades = self.storage.trades()
        for i, t in enumerate(trades, 1):
            labels, plan_ref = [], None
            tc = t_cards.get((t.code, t.trade_date))
            if tc and tc["payload"].get("candidate"):
                cand = tc["payload"]["candidate"]
                plan_ref = tc["id"]
                expect = cand["buy_price"] if t.side == "buy" else cand["sell_price"]
                ok_price = t.price <= expect + 0.011 if t.side == "buy" else t.price >= expect - 0.011
                labels.append("按做T预案" if ok_price and t.quantity == cand["qty"] else "偏离做T预案")
            else:
                pc = next((c for c in plan_cards if c["code"] == t.code and c["payload"].get("extra", {}).get("side") == t.side
                           and c["as_of"] < t.trade_date <= (c["review_date"] or t.trade_date) and c["status"] in ("open", "triggered", "done")), None)
                kc = next((c for c in self.storage.cards(kind="keep", code=t.code, limit=3)
                           if t.side == "sell" and c["payload"].get("extra", {}).get("decision") in ("减仓", "清仓换股")
                           and c["as_of"] < t.trade_date), None)
                if pc:
                    plan_ref = pc["id"]
                    labels.append("按计划卡")
                elif kc:
                    plan_ref = kc["id"]
                    labels.append("按去留卡")
                else:
                    labels.append("计划外")
            d = self.daily(t.code, t.trade_date, 5)
            if t.side == "buy" and d.ok:
                row = d.value[d.value["date"] == t.trade_date]
                if not row.empty and pd.notna(row["pre_close"].iloc[-1]):
                    chg = t.price / float(row["pre_close"].iloc[-1]) - 1
                    if chg >= self.config.rules.chase_pct:
                        labels.append(f"追涨（买入价较前收 +{chg:.1%}）")
            if t.side == "buy":
                prev_sells = [x for x in all_trades if x.side == "sell" and x.trade_date < t.trade_date
                              and self.provider.trading_days_between(x.trade_date, t.trade_date) <= self.config.rules.cooldown_days]
                for x in prev_sells:
                    buys = [b for b in all_trades if b.code == x.code and b.side == "buy" and b.trade_date <= x.trade_date]
                    if buys and x.price < sum(b.amount + b.fee_total for b in buys) / sum(b.quantity for b in buys):
                        labels.append(f"亏后马上再买（{x.trade_date} 亏损卖出 {x.name or x.code}）")
                        break
            out.append({"id": f"T{i}", "time": f"{t.trade_date} {t.trade_time}".strip(), "code": t.code, "name": t.name,
                        "side": "买入" if t.side == "buy" else "卖出", "qty": t.quantity, "price": t.price,
                        "fee": t.fee_total, "labels": labels, "plan_card": plan_ref})
        return out

    def daily_review(self) -> int:
        as_of = self.as_of
        trades = self.storage.trades(start=as_of, end=as_of)
        gaps: list[str] = []
        idx, idx_gaps, idx_src = self.index_changes(as_of)
        gaps += idx_gaps
        positions, meta = self.storage.latest_positions()
        prev = self.storage.positions_before(as_of)
        recon_notes = reconcile(prev, trades, positions) if prev and meta and meta["as_of"] >= as_of else []
        if meta and meta["as_of"] < as_of and trades:
            gaps.append(f"持仓快照停留在 {meta['as_of']}，今天的成交还没更新到持仓（可在「导入」里用成交更新持仓）")
        pv = self.portfolio(as_of)
        gaps += pv["gaps"]
        # 昨日（目标日=今天）的做T预案对账
        t_recon = []
        hs300 = idx.get("沪深300")
        for c in self.storage.cards(kind="t_plan", target_date=as_of):
            if c["status"] == "replaced":
                continue
            bar = self.bar_on(c["code"], as_of)
            res = reconcile_plan(c["payload"], bar, [t for t in trades if t.code == c["code"]], hs300, self.config.fees)
            status = {"两边成交": "done", "只成交买入": "done", "只成交卖出": "done", "触发放弃条件": "done",
                      "未触发": "expired", "触发但未成交": "expired", "明天不做": c["status"], "数据缺失": c["status"]}.get(res["status"], "done")
            self.storage.update_card(c["id"], status=status, result=res)
            t_recon.append({"card": c["id"], "code": c["code"], "name": c["title"].split("·")[-1].strip(), **res})
        # 计划卡状态
        plan_updates = []
        for c in self.storage.cards(kind="plan", status="open"):
            ex = c["payload"].get("extra", {})
            bar = self.bar_on(c["code"], as_of)
            did = [t for t in trades if t.code == c["code"] and t.side == ex.get("side")]
            if did:
                self.storage.update_card(c["id"], status="done", result={"executed": [t.to_dict() for t in did]})
                plan_updates.append(f"{c['title']}：今天已执行")
            elif bar and ex.get("side") == "buy" and ex.get("entry") and bar["low"] <= ex["entry"]:
                self.storage.update_card(c["id"], status="triggered")
                plan_updates.append(f"{c['title']}：价格摸到 {ex['entry']} 元，但没有成交记录")
            elif c["review_date"] and c["review_date"] <= as_of:
                self.storage.update_card(c["id"], status="expired")
                plan_updates.append(f"{c['title']}：已到期未执行")
        for c in self.storage.cards(kind="keep", status="open"):
            ex = c["payload"].get("extra", {})
            if ex.get("decision") in ("减仓", "清仓换股"):
                sold = [t for t in trades if t.code == c["code"] and t.side == "sell"]
                if sold:
                    self.storage.update_card(c["id"], status="done")
                    plan_updates.append(f"{c['title']}：已按结论卖出")
                elif c["review_date"] and c["review_date"] <= as_of:
                    plan_updates.append(f"{c['title']}：结论是「{ex['decision']}」，但到复查日还没执行")
            elif ex.get("stop"):
                bar = self.bar_on(c["code"], as_of)
                if bar and bar["low"] <= ex["stop"]:
                    self.storage.update_card(c["id"], status="triggered")
                    plan_updates.append(f"{c['title']}：跌到止损价 {ex['stop']} 元，按计划应已卖出")
        labeled = self.label_trades(trades)
        month = as_of[:7]
        month_trades = self.storage.trades(start=f"{month}-01", end=as_of)
        month_labels = self.label_trades(month_trades)
        unplanned_month = sum(1 for x in month_labels if "计划外" in x["labels"])
        impulsive_month = sum(1 for x in month_labels if any(l.startswith(("追涨", "亏后")) for l in x["labels"]))
        violations = portfolio_violations(pv["holdings"], pv["total_assets"], self.config.rules)
        sc = t_scorecard(self.storage.trades(), {h["code"]: h["qty"] for h in pv["holdings"]})
        tomorrow_ids = self.t_plans()
        tomorrow_cards = [self.storage.card(i) for i in tomorrow_ids]
        tomorrow = [{"card": c["id"], "title": c["title"], "conclusion": c["payload"]["conclusion"]} for c in tomorrow_cards]
        holdings_today = [{"code": h["code"], "name": h["name"], "pct_chg": h["pct_chg"], "pnl_pct": h["pnl_pct"],
                           "weight": h["weight"], "industry": h["industry"], "trend": h["trend"]} for h in pv["holdings"]]
        data = {"as_of": as_of, "index_changes": idx, "holdings_today": holdings_today, "trades": labeled,
                "reconcile_notes": recon_notes, "t_plan_reconcile": t_recon, "plan_updates": plan_updates,
                "month_unplanned_trades": unplanned_month, "month_impulsive_trades": impulsive_month,
                "violations": [v.__dict__ for v in violations], "t_scorecard_by_code": sc["by_code"],
                "tomorrow_t_plans": tomorrow, "data_gaps": gaps}
        out, err = self._try_llm_json(P.REVIEW, data, "盘后复盘", lambda o: [] if o.get("market") and o.get("conclusion") else ["缺少 market 或 conclusion"])
        comments = (out or {}).get("trade_comments", {}) or {}
        for x in labeled:
            x["comment"] = comments.get(x["id"], "")
        idx_text = "，".join(f"{k} {C.pct(v, 2)}" for k, v in idx.items()) or "指数数据缺失"
        card = C.new_card(
            "review", f"今日复盘 · {as_of}",
            conclusion=(out or {}).get("conclusion") or (f"今天 {len(trades)} 笔成交，其中计划外 {sum(1 for x in labeled if '计划外' in x['labels'])} 笔。"),
            basis=[C.basis(idx_text, "；".join(idx_src.values()) or "指数行情")]
            + [C.basis(f"{h['name']} 今日 {C.pct(h['pct_chg'])}，浮盈亏 {C.pct(h['pnl_pct'])}", next((x.get('source', '') for x in pv['holdings'] if x['code'] == h['code']), "")) for h in holdings_today[:6]],
            counter=(out or {}).get("counter") or "单日结果说明不了决策好坏，要看是否守住了计划。",
            gaps=gaps, review_at=f"{self.next_day} 收盘后",
            mentor_note=(out or {}).get("mentor_note", "") or (f"导师未参与：{err}" if err else ""),
        )
        card["extra"] = {
            "market": (out or {}).get("market") or idx_text, "trades": labeled, "t_reconcile": t_recon,
            "plan_updates": plan_updates, "reconcile_notes": recon_notes,
            "violations": [v.__dict__ for v in violations], "month_unplanned": unplanned_month, "month_impulsive": impulsive_month,
            "t_scorecard": sc["by_code"], "tomorrow": (out or {}).get("tomorrow") or [x["conclusion"] for x in tomorrow],
            "tomorrow_cards": tomorrow, "mentor_error": err,
        }
        for old in self.storage.cards(kind="review", as_of=as_of):
            self.storage.update_card(old["id"], status="replaced")
        return self.storage.add_card("review", card, title=card["title"], as_of=as_of, target_date=self.next_day,
                                     review_date=self.next_day, status="info")

    # ---------------- ⑧ 交割单体检 ----------------
    def trade_report(self) -> dict:
        trades = self.storage.trades()
        pv = self.portfolio()
        holdings = {h["code"]: h["qty"] for h in pv["holdings"]}
        sc = t_scorecard(trades, holdings)
        last_prices = {h["code"]: h["close"] for h in pv["holdings"] if h.get("close")}
        disp = disposition(trades, last_prices, self.as_of)
        from ..behavior import chase_ratio, turnover_and_fees

        pre_close: dict[tuple[str, str], float] = {}
        gaps = []
        for code in sorted({t.code for t in trades if t.side == "buy"}):
            dates = sorted({t.trade_date for t in trades if t.code == code and t.side == "buy"})
            f = self.provider.daily(code, dates[-1], 400)
            if not f.ok:
                gaps.append(f"{code} 日线：{f.error}")
                continue
            m = dict(zip(f.value["date"], f.value["pre_close"]))
            for d in dates:
                if d in m and pd.notna(m[d]):
                    pre_close[(code, d)] = float(m[d])
        return {"as_of": self.as_of, "t": sc, "disposition": disp.__dict__ | {"verdict": disp.verdict()},
                "turnover": turnover_and_fees(trades, pv["total_assets"]),
                "chase": chase_ratio(trades, pre_close, self.config.rules.chase_pct), "gaps": gaps,
                "t_events": [e.__dict__ for e in detect_t_events(trades)][-30:]}

    def prepare_close_materials(self) -> dict:
        """收盘后预取：持仓日线、指数、事件、公告，写入缓存。"""
        pv = self.portfolio()
        self.index_changes()
        nd = self.next_day
        for h in pv["holdings"]:
            self.provider.events(h["code"], nd, self.add_trading_days(nd, 20))
            self.provider.announcements(h["code"], self.add_trading_days_back(self.as_of, 10), self.as_of)
        info = {"as_of": self.as_of, "prepared_at": now_cn().strftime("%Y-%m-%d %H:%M"), "holdings": len(pv["holdings"]),
                "gaps": pv["gaps"]}
        self.storage.set(f"prepared:{self.as_of}", info)
        return info


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)
