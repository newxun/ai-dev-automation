"""卡片：所有实质建议都落成卡片。共用骨架 + 五种卡片各自的扩展栏。"""

from __future__ import annotations

from typing import Any

KINDS = {
    "health": "体检卡",
    "keep": "去留卡",
    "plan": "计划卡",
    "t_plan": "做T预案卡",
    "review": "复盘卡",
    "answer": "问答卡",
}

STATUS_LABELS = {
    "open": "进行中",
    "triggered": "已触发",
    "done": "已了结",
    "expired": "已到期",
    "skipped": "不做",
    "info": "存档",
    "replaced": "已被新版替换",
}


def new_card(kind: str, title: str, **fields: Any) -> dict:
    card = {
        "kind": kind,
        "title": title,
        "conclusion": "",
        "basis": [],  # [{"text": ..., "source": ...}]
        "actions": [],
        "valid_until": "",
        "wrong_if": [],
        "worst_case": None,  # {"amount": 元, "pct": 占总资产, "text": ...}
        "counter": "",
        "gaps": [],
        "review_at": "",
        "extra": {},
        "mentor_note": "",
    }
    card.update(fields)
    return card


def validate_card(card: dict) -> list[str]:
    """卡片骨架校验：结论、依据、反方理由、复查时间必须有；有动作就必须有认错条件和最坏情况。"""
    problems = []
    if not str(card.get("conclusion", "")).strip():
        problems.append("缺少结论")
    if not card.get("basis"):
        problems.append("缺少依据")
    for b in card.get("basis", []):
        if isinstance(b, dict) and not b.get("source"):
            problems.append(f"依据「{str(b.get('text', ''))[:20]}」没有注明来源")
    if not str(card.get("counter", "")).strip():
        problems.append("缺少反方理由")
    if not str(card.get("review_at", "")).strip():
        problems.append("缺少复查时间")
    if card.get("actions"):
        if not card.get("wrong_if"):
            problems.append("有动作但缺少认错条件")
        if not card.get("worst_case"):
            problems.append("有动作但缺少最坏情况")
    return problems


def basis(text: str, source: str) -> dict:
    return {"text": text, "source": source}


def worst_case(amount: float, total_assets: float, text: str) -> dict:
    pct = amount / total_assets if total_assets else None
    return {"amount": round(amount, 2), "pct": round(pct, 4) if pct is not None else None, "text": text}


def money(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e8:
        return f"{sign}{v / 1e8:.2f} 亿元"
    if v >= 1e4:
        return f"{sign}{v / 1e4:.2f} 万元"
    return f"{sign}{v:.0f} 元"


def pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"
