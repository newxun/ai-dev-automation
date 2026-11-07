"""模拟导师模型：用于自动测试、考卷流程自检和无 key 演示。

- MockOpenAIClient：进程内替身，接口与 openai.OpenAI().chat.completions.create 一致。
- serve()：本地 OpenAI 兼容 HTTP 服务（POST /v1/chat/completions），供网页演示时当作「模型服务」。

它按程序给出的数据做固定规则的回答，只用来验证整条链路；真实的回答质量要用真实模型考卷来评。
behavior="bad" 时会故意违规（选不存在的候选、改出越界价位、编造数字），用来验证程序把关。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

TASK_MARKERS = {
    "【任务：持仓体检】": "health",
    "【任务：去留决策": "keep",
    "【任务：交易前冷静对话": "pretrade",
    "【任务：每日盘后复盘】": "review",
    "【任务：次日做T预案】": "t_plans",
}


def _content_text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


def _extract_data(text: str) -> dict:
    i = text.find("数据（JSON")
    if i < 0:
        return {}
    j = text.find("\n", i)
    try:
        return json.loads(text[j + 1 :])
    except json.JSONDecodeError:
        return {}


def _pct(v: Any) -> str:
    return "—" if v is None else f"{float(v) * 100:+.2f}%"


def _w(v: Any) -> str:
    return "—" if v is None else f"{float(v) * 100:.1f}%"


_DECIDE = r"该不该|要不要|怎么办|能不能|补仓|加仓|止损"


def _named_holding(q: str, results: list) -> dict | None:
    for r in results:
        if isinstance(r, dict) and "holdings" in r:
            for h in r["holdings"]:
                if h.get("name") and h["name"] in q:
                    return h
    return None


class MockMentorModel:
    def __init__(self, behavior: str = "good", script: dict[str, list[tuple[str, dict]]] | None = None) -> None:
        self.behavior = behavior
        if script is None:
            from .exam import questions as Q

            script = {q["question"]: q["script"] for q in Q.NUMBERS + Q.FAITHFULNESS}
        self.script = script
        self.calls: list[dict] = []

    # ---------- 入口 ----------
    def respond(self, messages: list[dict], tools: list | None = None) -> dict:
        self.calls.append({"messages": messages, "tools": bool(tools)})
        last_user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
        text = _content_text(last_user)
        task_text = text
        if "你的输出有问题" in text:
            task_text = next((_content_text(m) for m in reversed(messages) if m.get("role") == "user"
                              and any(k in _content_text(m) for k in TASK_MARKERS)), text)
        for marker, task in TASK_MARKERS.items():
            if marker in task_text:
                data = _extract_data(task_text)
                out = getattr(self, f"task_{task}")(data)
                return {"content": json.dumps(out, ensure_ascii=False)}
        if any(isinstance(p, dict) and p.get("type") == "image_url" for p in (last_user.get("content") or []) if isinstance(last_user.get("content"), list)):
            return {"content": json.dumps({"positions": []})}
        if "下面是一张券商持仓截图" in text:
            return {"content": json.dumps(self.structure_ocr(text), ensure_ascii=False)}
        if tools:
            return self.chat(messages)
        if "只回复两个字" in text:
            return {"content": "在线"}
        return {"content": "（模拟模型）收到。"}

    # ---------- 任务 ----------
    def task_health(self, d: dict) -> dict:
        stocks = d.get("stocks", [])
        need = [s for s in stocks if s["status"] == "需决策"]
        conc = d.get("concentration", {})
        points = [f"第一大持仓占总资产 {_w(conc.get('top1'))}，前三大占 {_w(conc.get('top3'))}"]
        if d.get("industry_crowded"):
            g = d["industry_crowded"][0]
            points.append(f"{g['industry']} 行业占 {_w(g.get('weight'))}，持有 {len(g['codes'])} 只，行业扎堆")
        if d.get("violations"):
            points.append("违反规则：" + "；".join(v["detail"] for v in d["violations"][:2]))
        return {
            "conclusion": (f"先处理 {need[0]['name']}：{need[0]['reasons'][0]}" if need else "组合没有必须马上处理的票，按计划跟踪。"),
            "stock_comments": {s["code"]: f"{s['status']}：" + ("；".join(s["reasons"][:2]) if s["reasons"] else "趋势和事件都没有异常") for s in stocks},
            "key_points": points,
            "counter": "体检看不到你买入时的逻辑，如果买入理由仍然成立，「需决策」也可以是继续持有。",
            "wrong_if": ["「需决策」的票在复查前放量站回 20 日均线"],
            "actions": [f"对 {s['name']} 生成去留卡，先决定去留再谈做T" for s in need][:3],
            "mentor_note": "先把仓位和规则的问题处理掉，比研究涨跌更重要。",
        }

    def task_keep(self, d: dict) -> dict:
        s = d.get("stock", {})
        qty = int(s.get("qty") or 0)
        pnl = s.get("pnl_pct") or 0
        trend = s.get("trend")
        if self.behavior == "bad":
            return {"decision": "减仓", "reduce_qty": qty + 50, "conclusion": "减仓", "counter": "无"}
        if trend == "破位" or pnl <= -0.2:
            n = max(100, qty // 2 // 100 * 100) if qty >= 200 else qty
            decision = "减仓" if n < qty else "清仓换股"
        else:
            decision, n = "继续持有", None
        paths = {p["name"]: p for p in d.get("paths", [])}
        hold = paths.get("继续持有", {})
        return {
            "decision": decision,
            "reduce_qty": n if decision == "减仓" else None,
            "conclusion": {"继续持有": "继续持有，但用止损价兜底，不再加仓。", "减仓": f"减仓 {n} 股，把仓位降到能睡得着的水平。",
                           "清仓换股": "清仓换股，这只票的理由已经不成立。"}[decision],
            "cash_test": ("如果今天手里是现金，按目前的趋势（" + str(trend) + "）不会在这个价买它。") if decision != "继续持有" else "如果今天是现金，仍可能在这个价买，因为趋势没有破坏。",
            "reasons": [hold.get("detail", ""), f"趋势：{trend}"],
            "counter": "如果这只票近期有利好落地、放量站回 20 日均线，减仓会卖在低位。" if decision != "继续持有" else "继续持有会占用资金，若继续下跌亏损会扩大。",
            "wrong_if": [f"收盘重新站上 20 日均线（{s.get('close')} 元上方）并放量"] if decision != "继续持有" else ["收盘跌破止损价"],
            "ask_user": None if d.get("buy_reason") else "你当初买它的理由是什么？那个理由现在还成立吗？",
            "mentor_note": "解套不是目标，资金用在哪里最有效才是。",
        }

    def task_pretrade(self, d: dict) -> dict:
        sizing = d.get("sizing", {})
        gates = d.get("gates", [])
        if gates:
            return {"verdict": "建议不做", "conclusion": "这笔交易触发了冷静关卡，建议今天不做。",
                    "reasons": [g["message"] for g in gates], "counter": "如果你的理由是基于新的确定信息（不是价格本身），可以缩小仓位试错。",
                    "qty": 0, "wrong_if": [], "valid_days": 1, "mentor_note": "价格涨了不是买入理由。"}
        q = int(sizing.get("shares", 0))
        if self.behavior == "bad":
            q = q + 1000
        return {"verdict": "可以做", "conclusion": f"可以按计划买入 {q} 股，止损设在 {d['intent'].get('stop')} 元。",
                "reasons": [f"按单笔最坏亏损和仓位上限，最多 {q} 股（受「{sizing.get('binding')}」约束）"],
                "counter": "如果大盘走弱，突破可能失败。", "qty": q,
                "wrong_if": [f"收盘跌破 {d['intent'].get('stop')} 元"], "valid_days": 5, "mentor_note": "先想好错了在哪认，再下单。"}

    def task_review(self, d: dict) -> dict:
        idx = d.get("index_changes", {})
        hs = d.get("holdings_today", [])
        market = "；".join(f"{k} {_pct(v)}" for k, v in idx.items()) or "指数数据缺失"
        if hs:
            worst = min(hs, key=lambda h: h.get("pct_chg") or 0)
            best = max(hs, key=lambda h: h.get("pct_chg") or 0)
            market += f"。你的持仓里 {best['name']} 表现最好（{_pct(best.get('pct_chg'))}），{worst['name']} 最弱（{_pct(worst.get('pct_chg'))}）。"
        comments = {}
        for t in d.get("trades", []):
            labels = t.get("labels", [])
            if "计划外" in labels:
                comments[t["id"]] = "不在任何计划里，算一次冲动交易。" + ("而且是追涨。" if any(l.startswith("追涨") for l in labels) else "")
            elif any("偏离" in l for l in labels):
                comments[t["id"]] = "偏离了预案的价格或股数，下次按条件单执行。"
            else:
                comments[t["id"]] = "按计划执行，好。"
        unplanned = d.get("month_unplanned_trades", 0)
        tomorrow = [x["conclusion"] + f"（{x['title'].split('·')[-1].strip()}）" for x in d.get("tomorrow_t_plans", [])][:5]
        return {"market": market, "trade_comments": comments,
                "conclusion": f"今天 {len(d.get('trades', []))} 笔成交，本月计划外交易累计 {unplanned} 笔。",
                "counter": "今天的盈亏不代表决策质量，按纪律的亏损也是好决策。",
                "tomorrow": tomorrow or ["没有需要执行的预案，明天不操作也是一种操作。"],
                "mentor_note": "计划外交易是最贵的习惯，先把它降下来。" if unplanned else "保持按计划交易。"}

    def task_t_plans(self, d: dict) -> dict:
        plans, skipped = [], {}
        for s in d.get("stocks", []):
            if not s.get("eligible"):
                skipped[s["code"]] = "程序判定明天不做：" + "；".join(s.get("blockers", [])[:2])
                continue
            hist = s.get("t_history") or {}
            if self.behavior == "bad":
                plans.append({"code": s["code"], "decision": "做", "candidate_id": s["candidates"][0]["id"], "reason": "随便做",
                              "counter": "无", "adjust": {"buy_price": s["facts"]["limit_down"] - 1, "sell_price": s["facts"]["limit_up"] + 1,
                                                          "qty": 150, "reason": "我觉得会大涨"}})
                continue
            if hist.get("times", 0) >= 5 and hist.get("net_total", 0) < 0:
                plans.append({"code": s["code"], "decision": "不做", "candidate_id": None,
                              "reason": f"这只票做T {hist['times']} 次扣费后净亏 {abs(hist['net_total']):.0f} 元，先停一停。",
                              "counter": "如果明天振幅很大会错过机会。", "adjust": None})
                continue
            pref = s.get("program_preferred") or s["candidates"][0]["id"]
            hint = (s.get("facts") or {}).get("direction_hint", "")
            direction = pref.split("-")[0]
            plans.append({"code": s["code"], "decision": "做", "candidate_id": pref,
                          "reason": f"{hint}；选「{pref}」，历史粗验两边都成交的天数最多、扣费后净收益为正。",
                          "counter": "如果明天放量下跌，你会在下跌途中加仓。" if direction == "正T" else "如果明天放量上涨，卖出的筹码可能买不回来。",
                          "adjust": None})
        return {"plans": plans, "skipped_comments": skipped}

    def structure_ocr(self, text: str) -> dict:
        rows = []
        for line in text.splitlines():
            m = re.search(r"(?<!\d)(\d{6})(?!\d)", line)
            if not m:
                continue
            nums = [float(x) for x in re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", line.replace(m.group(1), " "))]
            name = "".join(re.findall(r"[\u4e00-\u9fa5A-Z]+", line.split("|")[1] if "|" in line else line))[:8]
            ints = [x for x in nums if x.is_integer() and x >= 100]
            decs = [x for x in nums if not x.is_integer()]
            if ints:
                rows.append({"code": m.group(1), "name": name, "quantity": ints[0], "available": ints[1] if len(ints) > 1 else None,
                             "cost_price": decs[0] if decs else None})
        return {"positions": rows}

    # ---------- 对话（工具调用） ----------
    def chat(self, messages: list[dict]) -> dict:
        last = messages[-1]
        user_idx = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        question = _content_text(messages[user_idx])
        tool_msgs = [m for m in messages[user_idx + 1 :] if m.get("role") == "tool"]
        if last.get("role") == "user":
            calls = self.script.get(question) or self.plan_tools(question)
            if calls:
                return {"tool_calls": calls}
            return {"content": self.compose(question, [])}
        results = []
        for m in tool_msgs:
            try:
                results.append(json.loads(m["content"]))
            except (json.JSONDecodeError, TypeError):
                results.append({"raw": m.get("content")})
        submits = [r for r in results if isinstance(r, dict) and "ok" in r]
        if submits:
            data = [r for r in results if not (isinstance(r, dict) and "ok" in r)]
            return {"content": self.compose(question, data, card=bool(submits[-1].get("ok")))}
        named = _named_holding(question, results)
        if named and not any(isinstance(r, dict) and "daily_summary" in r for r in results):
            return {"tool_calls": [("get_stock_data", {"code": named["code"]})]}
        if re.search(_DECIDE, question) and self.behavior == "good":
            return {"tool_calls": [("submit_card", self.card_args(question, results))]}
        return {"content": self.compose(question, results)}

    def plan_tools(self, q: str) -> list[tuple[str, dict]]:
        codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", q)
        calls: list[tuple[str, dict]] = []
        if re.search(r"持仓|组合|仓位|我的票", q) or (not codes and re.search(_DECIDE, q)):
            calls.append(("get_portfolio", {}))
        if re.search(r"成绩|交割单|做T.*(几次|成功)|死扛", q):
            calls.append(("get_trade_stats", {}))
        for c in codes[:2]:
            calls.append(("get_stock_data", {"code": c}))
        return calls

    def compose(self, q: str, results: list, card: bool = False) -> str:
        if self.behavior == "bad":
            return "茅台明天会涨到 1888 元，市盈率 25 倍，放心买。"
        lines = []
        if re.search(r"预测|明天.*涨到|会涨吗|目标价", q):
            lines.append("我不预测明天的涨跌和目标价，这类判断没有可靠依据。可以做的是：给你设好条件和认错位置。")
        missing_words = []
        for r in results:
            if not isinstance(r, dict):
                continue
            if "error" in r:
                lines.append(f"没查到：{r['error']}")
            if "fee" in r:
                lines.append(f"这笔{r.get('side', '')}的交易费用是 {r['fee']:.2f} 元（{r.get('rule', '')}）。")
            if "limit_up" in r:
                lines.append(f"涨停价 {r['limit_up']} 元，跌停价 {r['limit_down']} 元。")
            if "sellable_today" in r:
                lines.append(f"今天可卖 {r['sellable_today']} 股，明天可卖 {r['sellable_tomorrow']} 股（T+1）。")
            if "new_cost" in r and "net" not in r:
                lines.append(f"补仓后成本约 {r['new_cost']} 元/股（含买入费用 {r['buy_fee']} 元）。")
            if "net" in r:
                lines.append(f"两边都成交的话，扣费后净赚 {r['net']:.2f} 元，成本从原来降到约 {r['new_cost']} 元/股。")
            if "breakeven_per_share" in r:
                lines.append(f"保本价差每股 {r['breakeven_per_share']} 元（约 {r['breakeven_pct'] * 100:.2f}%）。")
            if "daily_summary" in r:
                s = r["daily_summary"]
                name = (r.get("profile") or {}).get("name", r["code"])
                lines.append(f"{name}（{r['code']}）{s['date']} 收盘 {s['close']} 元，当日 {_pct(s['pct_chg'])}，趋势：{s['trend']['state']}。")
                if "financials" not in r and re.search(r"财报|利润|增速|业绩", q):
                    lines.append("没查到这只票的财务数据，不能告诉你净利润增速，我不猜。")
                if "financials" in r and re.search(r"财报|利润|增速|业绩", q):
                    f0 = r["financials"][0]
                    lines.append(f"最新报告期 {f0.get('报告期')}：净利润 {f0.get('净利润')}，同比 {f0.get('净利润同比增长率')}（{r['sources'].get('财务摘要', '')}）。")
            if r.get("data_gaps"):
                missing_words += r["data_gaps"]
            if "holdings" in r:
                top = r["holdings"][0] if r["holdings"] else None
                lines.append(f"你的总资产 {r['total_assets']:.0f} 元，持有 {len(r['holdings'])} 只" + (f"，第一大持仓 {top['name']} 占 {_w(top['weight'])}。" if top else "。"))
            if "t_scorecard" in r:
                t = r["t_scorecard"]
                lines.append(f"你一共做T {t['times']} 次，成功 {t['success']} 次，扣费后累计 {t['net_total']:.0f} 元。{r.get('disposition', '')}")
        has_pe = any("市盈率" in json.dumps({k: v for k, v in r.items() if k not in ("note", "data_gaps")}, ensure_ascii=False)
                     for r in results if isinstance(r, dict))
        if re.search(r"市盈率|PE|估值", q, re.I) and not has_pe:
            lines.append("没查到市盈率数据（当前数据源没有提供），我不猜。")
        if re.search(r"公告", q) and any("公告" in g for g in missing_words):
            lines.append("没查到这只票的公告数据，没法判断公告内容。")
        if re.search(r"新闻|消息", q):
            lines.append("没查到新闻数据（当前工具不提供新闻），不做推测。")
        named = _named_holding(q, results)
        if named:
            lines.append(f"你持有{named['name']} {named['qty']} 股，成本 {named['cost']} 元，收盘 {named['close']} 元，"
                         f"浮动盈亏 {_pct(named['pnl_pct'])}，占总资产 {_w(named['weight'])}。")
            if re.search(r"补仓|加仓", q) and (named.get("pnl_pct") or 0) < 0:
                lines.append("亏损中补仓，是在加大一笔已经被市场证明判断有误的仓位。补之前先回答三件事：买入理由还成立吗？"
                             "补完后这只占总资产多少？跌到哪里认错？三个都答不上来，就先别补。")
        if missing_words and not lines:
            lines.append("没查到：" + "；".join(missing_words[:2]))
        if card:
            lines.append("我把建议落成了一张卡片，可在「我的计划」里查看。")
        return "\n".join(lines) or "这个问题我没有查到相关数据，没法回答。"

    def card_args(self, q: str, results: list) -> dict:
        basis = []
        for r in results:
            if isinstance(r, dict) and "daily_summary" in r:
                s = r["daily_summary"]
                basis.append({"text": f"{r['code']} 收盘 {s['close']}，趋势 {s['trend']['state']}", "source": r["sources"].get("日线", "日线")})
        named = _named_holding(q, results)
        if named and re.search(r"补仓|加仓", q) and (named.get("pnl_pct") or 0) < 0:
            basis.insert(0, {"text": f"{named['name']} 浮亏 {_pct(named['pnl_pct'])}，占总资产 {_w(named['weight'])}", "source": "本地持仓快照"})
            value = (named.get("qty") or 0) * (named.get("close") or 0)
            return {"conclusion": f"先别给{named['name']}补仓，先做去留决策。", "basis": basis, "code": named["code"],
                    "counter": "如果买入理由仍成立且仓位不超上限，按计划分批补仓也说得通。",
                    "review_at": "下一个交易日收盘后", "actions": [f"先在「持仓」里给{named['name']}生成去留卡"],
                    "wrong_if": [f"{named['name']}放量站回 20 日均线，说明下跌告一段落，可以重新评估"],
                    "worst_case_amount": round(value * 0.1, 2), "worst_case_text": "不补仓、现有持仓再跌 10% 的额外亏损",
                    "gaps": []}
        return {"conclusion": "先不动，等条件出现再执行。", "basis": basis or [{"text": "持仓数据", "source": "本地持仓快照"}],
                "counter": "如果出现放量突破，观望会错过一段行情。", "review_at": "下一个交易日收盘后", "actions": [], "gaps": []}


class MockOpenAIClient:
    """openai.OpenAI 的进程内替身。"""

    def __init__(self, model: MockMentorModel | None = None) -> None:
        self.model = model or MockMentorModel()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.requests: list[dict] = []

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        out = self.model.respond(kwargs["messages"], kwargs.get("tools"))
        msg = SimpleNamespace(content=out.get("content"), tool_calls=None)
        if out.get("tool_calls"):
            msg.tool_calls = [SimpleNamespace(id=f"call_{uuid.uuid4().hex[:8]}", type="function",
                                              function=SimpleNamespace(name=n, arguments=json.dumps(a, ensure_ascii=False)))
                              for n, a in out["tool_calls"]]
        if kwargs.get("stream"):
            def gen():
                for ch in (out.get("content") or ""):
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=ch))])
            return gen()
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def _to_openai_json(out: dict, model: str) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": out.get("content")}
    if out.get("tool_calls"):
        message["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                                  "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}} for n, a in out["tool_calls"]]
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:10]}", "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if out.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def make_handler(model: MockMentorModel, delay: float = 0.0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._send(200, {"object": "list", "data": [{"id": "mock-mentor", "object": "model"}]})

        def do_POST(self) -> None:
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._send(404, {"error": {"message": "not found"}})
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if delay:
                time.sleep(delay)
            out = model.respond(body.get("messages", []), body.get("tools"))
            self._send(200, _to_openai_json(out, body.get("model", "mock-mentor")))

    return Handler


def serve(host: str = "127.0.0.1", port: int = 9876, behavior: str = "good", delay: float = 0.0,
          background: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(MockMentorModel(behavior), delay))
    if background:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    else:
        print(f"模拟模型服务已启动：http://{host}:{port}/v1 （模型名任意，api_key 任意）")
        server.serve_forever()
    return server
