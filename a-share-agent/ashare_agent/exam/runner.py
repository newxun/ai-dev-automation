"""考卷执行与评分。新模型考过（自动评分部分全部达标）才能设为默认。

用法：
    python -m ashare_agent exam                # 用当前模型配置考
    python -m ashare_agent exam --mock         # 用模拟模型自检整条流程
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import cards as C
from ..config import AppConfig, ModelProfile
from ..demo import DEMO_AS_OF, demo_config, demo_now, exam_provider, seed_demo
from ..llm import LLMClient
from ..market import now_cn
from ..mentor.chat import ChatAgent
from ..mentor.core import Mentor
from ..portfolio import parse_holdings_file
from ..storage import Storage
from ..t_plan import TCandidate, reconcile_plan, validate_candidate
from ..trades import parse_trades_file
from . import questions as Q

NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
SECTION_NAMES = {"numbers": "数字与规则", "faithfulness": "忠实度", "scenarios": "情景决策", "t_plans": "做T预案",
                 "privacy": "脱敏", "stability": "稳定性"}


def _numbers_in(text: str) -> list[float]:
    return [float(x) for x in NUM_RE.findall((text or "").replace(",", "").replace("，", " "))]


class Exam:
    def __init__(self, profile: ModelProfile, client: Any | None = None, workdir: Path | None = None,
                 base_config: AppConfig | None = None, progress: Callable[[str], None] | None = None) -> None:
        self.profile = profile
        self.client = client
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="ashare-exam-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.base_config = base_config
        self.progress = progress or (lambda s: None)
        self.provider = exam_provider()
        self._n = 0

    # ---------- 环境 ----------
    def config(self) -> AppConfig:
        cfg = demo_config()
        if self.base_config is not None:
            cfg.fees = copy.deepcopy(self.base_config.fees)
        cfg.profiles = {self.profile.name: self.profile}
        cfg.active_profile = self.profile.name
        return cfg

    def mentor(self, seed: str = "demo", as_of: str = DEMO_AS_OF) -> Mentor:
        self._n += 1
        st = Storage(self.workdir / f"exam-{self._n}.sqlite")
        cfg = self.config()
        if seed == "demo":
            seed_demo(st, cfg.fees)
        elif seed == "tb":
            st.save_positions(Q.TB_POSITIONS, as_of=as_of, source="考卷", cash=Q.TB_CASH)
        m = Mentor(st, self.provider, cfg, now=demo_now(as_of))
        m._llm = LLMClient(self.profile, client=self.client, logger=m._log_send)
        return m

    # ---------- ① 数字与规则 ----------
    def numbers(self, only: list[str] | None = None) -> dict:
        m = self.mentor()
        agent = ChatAgent(m)
        items = []
        for q in Q.NUMBERS:
            if only and q["id"] not in only:
                continue
            self.progress(f"数字与规则 {q['id']}")
            ans = agent.ask(q["question"])
            got = _numbers_in(ans["text"])
            checks = []
            for tool, args, key, tol in q["expect"]:
                exp = float(getattr(agent, f"tool_{tool}")(**args)[key])
                checks.append({"expected": exp, "ok": any(abs(x - exp) <= tol for x in got)})
            items.append({"id": q["id"], "question": q["question"], "answer": ans["text"], "tools": ans.get("used_tools"),
                          "expected": [c["expected"] for c in checks], "passed": all(c["ok"] for c in checks)})
        passed = sum(i["passed"] for i in items)
        return {"items": items, "score": f"{passed}/{len(items)}", "passed": passed == len(items), "rule": "全部正确（100%）"}

    # ---------- ② 忠实度 ----------
    def faithfulness(self) -> dict:
        m = self.mentor()
        agent = ChatAgent(m)
        items = []
        for q in Q.FAITHFULNESS:
            self.progress(f"忠实度 {q['id']}")
            text = agent.ask(q["question"])["text"]
            problems = []
            if q.get("must_any") and not any(w in text for w in q["must_any"]):
                problems.append("没有明确说「查不到/不预测」")
            for w in q.get("must_all", ()):
                if w not in text:
                    problems.append(f"没有如实引用「{w}」")
            for pat in q.get("forbid", []):
                if re.search(pat, text):
                    problems.append(f"出现编造内容（匹配 {pat}）")
            items.append({"id": q["id"], "question": q["question"], "answer": text, "why": q["why"],
                          "problems": problems, "passed": not problems})
        fabricated = sum(not i["passed"] for i in items)
        return {"items": items, "score": f"编造 {fabricated} 次", "passed": fabricated == 0, "rule": "编造 0 次"}

    # ---------- ③ 情景决策 ----------
    def run_task(self, m: Mentor, task: str, args: dict | None = None) -> list[dict]:
        args = args or {}
        if task == "health":
            ids = [m.health_check()]
        elif task == "keep":
            ids = [m.keep_decision(args["code"], args.get("buy_reason", ""))]
        elif task == "pretrade":
            ids = [m.pretrade(args["code"], args["side"], args["reason"], args.get("gate_reason", ""))]
        elif task == "review":
            ids = [m.daily_review()]
        elif task == "t_plans":
            ids = m.t_plans()
        else:
            raise ValueError(task)
        return [m.storage.card(i) for i in ids]

    def scenarios(self) -> dict:
        items = []
        for s in Q.SCENARIOS:
            self.progress(f"情景决策 {s['id']}")
            m = self.mentor()
            card = self.run_task(m, s["task"], s.get("args"))[0]
            p = card["payload"]
            auto = C.validate_card(p)
            err = p.get("extra", {}).get("mentor_error")
            if err:
                auto.append(f"导师未参与：{err}")
            items.append({"id": s["id"], "title": s["title"], "points": s["points"], "card": p, "auto_problems": auto,
                          "human_score": None, "passed": not auto})
        ok = all(i["passed"] for i in items)
        return {"items": items, "score": "待人工评分", "passed": ok, "needs_human": True,
                "rule": "卡片结构完整、导师参与（自动）；人工评分均分 ≥ 4（见评分表）"}

    # ---------- ④ 做T预案 ----------
    def t_plans(self) -> dict:
        items, violations = [], 0
        for ts in Q.T_SETS:
            self.progress(f"做T预案 {ts['id']}")
            m = self.mentor(ts["portfolio"], ts["as_of"])
            if ts["portfolio"] == "demo":
                m.keep_decision("000831")
            cards = self.run_task(m, "t_plans")
            pv = m.portfolio()
            hold = {h["code"]: h for h in pv["holdings"]}
            nd = m.next_day
            for c in cards:
                p = c["payload"]
                code = c["code"]
                problems = []
                cand = p.get("candidate")
                if cand:
                    ctx = m._t_context(hold[code], pv, m.config.rules, None, m.latest_keep_decision(code))
                    problems += validate_candidate(ctx, TCandidate(**cand))
                if code in ts["must_skip"] and cand:
                    problems.append(f"应该不做（{ts['must_skip'][code]}），却给了预案")
                problems += [f"卡片结构：{x}" for x in C.validate_card(p)]
                violations += len(problems)
                bar = m.bar_on(code, nd)
                res = reconcile_plan(p, bar, [], None, m.config.fees)
                items.append({"id": f"{ts['id']}-{code}", "name": hold[code]["name"], "as_of": ts["as_of"], "target": nd,
                              "decision": "做" if cand else "不做", "conclusion": p["conclusion"],
                              "orders": p.get("actions", []), "expected_skip": ts["must_skip"].get(code),
                              "next_day": res.get("summary") or res.get("status"), "next_day_status": res.get("status"),
                              "problems": problems, "passed": not problems})
        skip = sum(1 for i in items if i["decision"] == "不做")
        return {"items": items, "score": f"违反硬规则 {violations} 次；{skip}/{len(items)} 只不做", "passed": violations == 0,
                "rule": "违反硬规则 0 次；应不做的必须不做；次日对照真实走势（供人工参考）"}

    # ---------- ⑤ 脱敏 ----------
    def privacy(self) -> dict:
        self.progress("脱敏")
        m = self.mentor(seed="none")
        st = m.storage
        tr = parse_trades_file("交割单.csv", Q.FAKE_TRADES_CSV.encode("gb18030"), m.config.fees)
        st.add_trades(tr.trades)
        hp = parse_holdings_file("持仓.csv", Q.FAKE_HOLDINGS_CSV.encode("utf-8"))
        st.save_positions(hp.positions, as_of=DEMO_AS_OF, source="考卷导入", cash=20000)
        m.health_check()
        m.daily_review()
        ChatAgent(m).ask(Q.SENSITIVE_CHAT)
        sent = st.all_sent_text()
        stored = json.dumps([t.to_dict() for t in st.trades()], ensure_ascii=False) + json.dumps(
            [p.to_dict() for p in st.latest_positions()[0]], ensure_ascii=False)
        leaks = [s for s in Q.SENSITIVE_STRINGS if s in sent or s.replace(" ", "") in sent.replace(" ", "")]
        stored_leaks = [s for s in Q.SENSITIVE_STRINGS if s in stored]
        items = [{"id": "P1", "what": "发给模型的全部内容（发送日志）", "requests": len(st.send_logs(1000)), "leaks": leaks, "passed": not leaks},
                 {"id": "P2", "what": "本地保存的成交和持仓", "leaks": stored_leaks, "passed": not stored_leaks},
                 {"id": "P3", "what": "导入时去掉的列", "dropped": tr.dropped_columns + hp.dropped_columns,
                  "masked": tr.privacy_hits + hp.privacy_hits, "passed": True}]
        n = len(leaks) + len(stored_leaks)
        return {"items": items, "score": f"敏感信息 {n} 条", "passed": n == 0, "rule": "发送内容和本地存储中敏感信息 0 条"}

    # ---------- ⑥ 稳定性 ----------
    def stability(self, rounds: int = 3) -> dict:
        items = []
        for s in Q.STABILITY:
            sigs = []
            for k in range(rounds):
                self.progress(f"稳定性 {s['id']} 第 {k + 1} 次")
                if s["task"] == "number":
                    r = self.numbers(only=[s["ref"]])["items"][0]
                    sigs.append("对" if r["passed"] else "错")
                    continue
                m = self.mentor()
                if s["task"] == "t_plans":
                    m.keep_decision("000831")
                cards = self.run_task(m, s["task"], s.get("args"))
                if s["task"] == "keep":
                    sigs.append(cards[0]["payload"]["extra"]["decision"])
                elif s["task"] == "pretrade":
                    ex = cards[0]["payload"]["extra"]
                    sigs.append(f"{ex['verdict']}/{ex['qty']}")
                else:
                    sigs.append(";".join(f"{c['code']}:{(c['payload'].get('candidate') or {}).get('id', '不做')}" for c in sorted(cards, key=lambda c: c["code"])))
            items.append({"id": s["id"], "task": s["task"], "answers": sigs, "passed": len(set(sigs)) == 1})
        ok = sum(i["passed"] for i in items)
        return {"items": items, "score": f"{ok}/{len(items)} 题一致", "passed": ok == len(items), "rule": f"{len(items)} 题 × {rounds} 次结论一致"}

    # ---------- 汇总 ----------
    def run(self, sections: list[str] | None = None) -> dict:
        sections = sections or list(SECTION_NAMES)
        t0 = time.time()
        out: dict[str, Any] = {"profile": self.profile.name, "model": self.profile.model, "base_url": self.profile.base_url,
                               "started_at": now_cn().strftime("%Y-%m-%d %H:%M"), "sections": {}}
        for s in sections:
            try:
                out["sections"][s] = getattr(self, s)()
            except Exception as exc:  # 某一部分出错不影响其它部分
                out["sections"][s] = {"items": [], "score": f"出错：{exc}", "passed": False, "rule": ""}
        out["auto_passed"] = all(v["passed"] for v in out["sections"].values())
        out["needs_human"] = "scenarios" in out["sections"]
        out["seconds"] = round(time.time() - t0, 1)
        return out


def summary_for_config(report: dict) -> dict:
    return {"date": report["started_at"], "model": report["model"], "passed": report["auto_passed"],
            "sections": {SECTION_NAMES[k]: {"score": v["score"], "passed": v["passed"]} for k, v in report["sections"].items()}}


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    jpath = out_dir / f"exam-{stamp}.json"
    jpath.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = [f"# 考卷报告：{report['profile']}（{report['model']}）", "", f"时间：{report['started_at']}，耗时 {report['seconds']} 秒", "",
             f"**自动评分部分：{'全部通过' if report['auto_passed'] else '未通过'}**", "", "| 部分 | 成绩 | 通过线 | 结果 |", "|---|---|---|---|"]
    for k, v in report["sections"].items():
        lines.append(f"| {SECTION_NAMES[k]} | {v['score']} | {v.get('rule', '')} | {'通过' if v['passed'] else '未通过'} |")
    for k, v in report["sections"].items():
        lines += ["", f"## {SECTION_NAMES[k]}", ""]
        for it in v.get("items", []):
            if k == "scenarios":
                p = it["card"]
                lines += [f"### {it['id']} {it['title']}", "", f"- 结论：{p['conclusion']}",
                          *[f"- 动作：{a}" for a in p.get("actions", [])],
                          f"- 认错条件：{'；'.join(p.get('wrong_if', [])) or '—'}",
                          f"- 最坏情况：{(p.get('worst_case') or {}).get('text', '—')} {C.money((p.get('worst_case') or {}).get('amount'))}",
                          f"- 反方理由：{p.get('counter', '')}", f"- 导师的话：{p.get('mentor_note', '')}",
                          f"- 数据缺口：{'；'.join(p.get('gaps', [])) or '无'}", "", "评分要点（1-5 分）：",
                          *[f"- [ ] {x}" for x in it["points"]], "", "人工评分：____ / 5", ""]
            else:
                mark = "✅" if it.get("passed") else "❌"
                body = it.get("answer") or it.get("conclusion") or it.get("what") or ""
                extra = ""
                if k == "numbers":
                    extra = f"（标准答案 {it['expected']}）"
                elif k == "t_plans":
                    extra = f"（次日 {it['target']}：{it['next_day']}）" + (f"；应不做：{it['expected_skip']}" if it["expected_skip"] else "")
                elif k == "stability":
                    body = " / ".join(it["answers"])
                elif k == "privacy":
                    extra = f"泄露：{it.get('leaks', [])}" if "leaks" in it else f"去掉的列：{it.get('dropped')}，遮盖 {it.get('masked')} 处"
                problems = it.get("problems") or it.get("auto_problems") or []
                lines.append(f"- {mark} **{it['id']}** {it.get('question', it.get('name', ''))} {extra}")
                if body:
                    lines.append("  > " + body.replace("\n", "\n  > "))
                if problems:
                    lines.append("  - 问题：" + "；".join(problems))
    mpath = out_dir / f"exam-{stamp}.md"
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath
