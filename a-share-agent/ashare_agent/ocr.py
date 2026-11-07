"""截图识别：先在本机 OCR 出文字，脱敏后再处理；截图原图不发给第三方模型。

流程：
1. RapidOCR 本地识别 → 按坐标拼成表格行。
2. 能按表头直接解析出持仓/成交就用程序结果。
3. 解析不了（手机 App 卡片式布局等），把**脱敏后的文字**交给模型整理成 JSON。
4. 本地 OCR 完全不可用时，才允许改用识图模型：先给敏感区域打码，并要求用户确认打码后的图片。
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from statistics import median

from .portfolio import Position, match_columns, positions_from_rows, to_number
from .privacy import is_sensitive_ocr_text, scrub_text


class OCRUnavailable(RuntimeError):
    pass


@dataclass
class OCRLine:
    box: list[list[float]]
    text: str
    score: float

    @property
    def x(self) -> float:
        return sum(p[0] for p in self.box) / 4

    @property
    def y(self) -> float:
        return sum(p[1] for p in self.box) / 4

    @property
    def h(self) -> float:
        ys = [p[1] for p in self.box]
        return max(ys) - min(ys)


@dataclass
class OCRResult:
    lines: list[OCRLine]
    rows: list[list[OCRLine]] = field(default_factory=list)
    clean_text: str = ""
    sensitive_boxes: list[list[list[float]]] = field(default_factory=list)
    privacy_hits: int = 0


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # noqa: BLE001
            raise OCRUnavailable(f"本地 OCR 组件不可用：{exc}") from exc
        _engine = RapidOCR()
    return _engine


def run_ocr(image: bytes) -> list[OCRLine]:
    from PIL import Image

    engine = _get_engine()
    img = Image.open(io.BytesIO(image)).convert("RGB")
    import numpy as np

    result, _ = engine(np.array(img))
    return [OCRLine([list(map(float, p)) for p in box], str(text), float(score)) for box, text, score in (result or [])]


def group_rows(lines: list[OCRLine]) -> list[list[OCRLine]]:
    if not lines:
        return []
    tol = max(6.0, median(ln.h for ln in lines) * 0.6)
    rows: list[list[OCRLine]] = []
    for ln in sorted(lines, key=lambda x: x.y):
        if rows and abs(rows[-1][0].y - ln.y) <= tol:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [sorted(r, key=lambda x: x.x) for r in rows]


def analyze(lines: list[OCRLine]) -> OCRResult:
    rows = group_rows(lines)
    clean_rows = []
    hits = 0
    boxes = []
    for r in rows:
        cells = []
        for ln in r:
            if is_sensitive_ocr_text(ln.text):
                boxes.append(ln.box)
            t, f = scrub_text(ln.text)
            hits += len(f)
            cells.append(t)
        clean_rows.append(" | ".join(cells))
    return OCRResult(lines, rows, "\n".join(clean_rows), boxes, hits)


def parse_positions_from_rows(rows: list[list[OCRLine]]) -> list[Position]:
    """表格式截图：找到表头行，按列中心对齐取值。"""
    for i, r in enumerate(rows):
        headers = [ln.text.strip() for ln in r]
        mapping = match_columns(headers)
        if "code" not in mapping or "quantity" not in mapping:
            continue
        centers = {fld: next(ln.x for ln in r if ln.text.strip() == h) for fld, h in mapping.items()}
        out_rows = []
        for data_row in rows[i + 1 :]:
            vals: dict[str, str] = {}
            for ln in data_row:
                fld = min(centers, key=lambda f: abs(centers[f] - ln.x))
                vals[fld] = (vals.get(fld, "") + ln.text).strip()
            out_rows.append(vals)
        positions, _ = positions_from_rows(out_rows)
        if positions and all(p.quantity > 0 for p in positions):
            return positions
    return []


OCR_STRUCTURE_PROMPT = """下面是一张券商持仓截图经过本地文字识别（已脱敏）得到的文字，每行用 | 分隔不同文字块。
请整理成持仓 JSON，只输出 JSON，不要解释。格式：
{"positions": [{"code": "6位代码或空", "name": "证券名称", "quantity": 持仓数量, "available": 可用数量或null, "cost_price": 成本价或null}]}
规则：只能使用文字里出现的数字，看不清或没有的填 null；不要编造代码。

识别文字：
"""


def extract_json(text: str) -> object:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = m.group(1) if m else text
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("模型没有返回可解析的 JSON")


def positions_from_llm_json(data: object) -> tuple[list[Position], list[str]]:
    items = data.get("positions", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("JSON 里没有 positions 列表")
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rows.append(
            {
                "code": it.get("code"),
                "name": it.get("name"),
                "quantity": to_number(it.get("quantity")),
                "available": to_number(it.get("available")),
                "cost_price": to_number(it.get("cost_price")),
            }
        )
    return positions_from_rows(rows)
