"""持仓模型与券商导出文件解析。

兼容常见券商（同花顺、通达信、东方财富、华泰、国泰君安、招商等）导出的 CSV / Excel：
- 编码：UTF-8 / UTF-8 BOM / GBK(GB18030) / UTF-16
- 很多「.xls」其实是 Tab 分隔的文本，自动识别
- 表头前有说明行（资金账号、导出时间等）时，自动定位真正的表头
- 代码写成 ="600519"、600519.SH、丢失前导 0 等情况
- 末尾的「合计」行、已清仓的 0 股行自动忽略
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from .market import classify, normalize_code

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("证券代码", "股票代码", "代码", "证券编号", "产品代码", "基金代码", "合约代码", "股票编码"),
    "name": ("证券名称", "股票名称", "名称", "证券简称", "股票简称", "产品名称", "基金名称", "简称"),
    "available": ("可用数量", "可用余额", "可卖数量", "可用股份", "可卖股数", "可用持仓", "可用股数", "可卖余额", "可用", "可卖"),
    "quantity": (
        "持仓数量", "股票余额", "证券余额", "证券数量", "当前持仓", "持股数量", "持有数量", "实际数量",
        "参考持股", "拥股数量", "持仓股数", "股份余额", "库存数量", "当前数量", "总数量", "持仓", "数量", "余额",
    ),
    "cost_price": (
        "成本价", "参考成本价", "摊薄成本价", "买入均价", "持仓成本价", "成本价格", "买入成本价", "持仓均价",
        "成本均价", "平均成本", "持仓成本", "参考成本", "摊薄成本", "保本价", "成本",
    ),
    "current_price": ("当前价", "最新价", "市价", "现价", "参考市价", "当前价格", "最新价格", "收盘价", "市场价格", "市场价"),
    "market_value": ("市值", "最新市值", "参考市值", "证券市值", "持仓市值", "当前市值", "市值价"),
    "pnl_pct": ("盈亏比例", "参考盈亏比例", "盈亏比", "浮动盈亏比例", "收益率", "盈亏率", "浮动盈亏率", "持仓收益率"),
    "pnl": ("浮动盈亏", "参考盈亏", "盈亏", "持仓盈亏", "浮动盈亏额", "盈亏金额", "参考浮动盈亏", "持仓收益"),
    "cost_amount": ("成本金额", "买入金额", "持仓成本金额", "成本总额"),
}

FIELD_LABELS = {
    "code": "代码",
    "name": "名称",
    "quantity": "持仓数量",
    "available": "可用数量",
    "cost_price": "成本价",
    "current_price": "券商现价",
    "market_value": "券商市值",
    "pnl": "券商盈亏",
    "pnl_pct": "券商盈亏比例",
    "cost_amount": "成本金额",
}

_IGNORE_FOR_QUANTITY = ("冻结", "在途", "今买", "今卖", "买入数量", "卖出数量", "股东", "账号", "可用", "可卖", "金额", "市值")
_UNIT_RE = re.compile(r"[\(（\[【][^\)）\]】]*[\)）\]】]")


def normalize_header(h: object) -> str:
    s = str(h or "").strip().replace("\u3000", "").replace(" ", "")
    s = _UNIT_RE.sub("", s)
    return s.strip(":：").strip()


def match_columns(headers: list[str]) -> dict[str, str]:
    """把原始列名映射到标准字段，返回 {字段: 原始列名}。先精确匹配，再按包含关系匹配。"""
    norm = {h: normalize_header(h) for h in headers}
    mapping: dict[str, str] = {}
    used: set[str] = set()

    for fld, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            hit = next((h for h in headers if h not in used and norm[h] == alias), None)
            if hit is not None:
                mapping[fld] = hit
                used.add(hit)
                break

    for fld, aliases in FIELD_ALIASES.items():
        if fld in mapping:
            continue
        for alias in sorted((a for a in aliases if len(a) >= 3), key=len, reverse=True):
            for h in headers:
                if h in used or alias not in norm[h]:
                    continue
                if fld == "quantity" and any(w in norm[h] for w in _IGNORE_FOR_QUANTITY):
                    continue
                mapping[fld] = h
                used.add(h)
                break
            if fld in mapping:
                break
    return mapping


@dataclass
class Position:
    code: str
    name: str = ""
    quantity: int = 0
    available: int | None = None
    cost_price: float | None = None
    current_price: float | None = None  # 券商导出时的价格，仅在实时行情不可用时兜底
    note: str = ""

    @property
    def exchange(self) -> str:
        return classify(self.code).exchange

    @property
    def sellable(self) -> int:
        return self.quantity if self.available is None else min(self.available, self.quantity)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ParseResult:
    positions: list[Position]
    mapping: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    header_row: int = 0
    raw: pd.DataFrame | None = None
    dropped_columns: list[str] = field(default_factory=list)
    privacy_hits: int = 0
    cash: float | None = None


_CASH_RE = re.compile(r"(可用金额|可用资金|资金余额|可用余额)\s*[:：]?\s*(-?[\d,]+(?:\.\d+)?)")


def cash_from_text(text: str) -> float | None:
    m = _CASH_RE.search(text or "")
    return to_number(m.group(2)) if m else None


def to_number(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    s = str(v).strip().strip('="\'').replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
    s = s.replace("%", "").replace("元", "").replace("股", "").strip()
    if s in ("", "-", "--", "---", "nan", "NaN", "None", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _split_text(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    sample = lines[:40]
    tabs = sum(ln.count("\t") for ln in sample)
    commas = sum(ln.count(",") for ln in sample)
    if tabs == 0 and commas == 0:
        return [re.split(r"\s{2,}|\s*\|\s*", ln.strip()) for ln in lines]
    delim = "\t" if tabs >= commas else ","
    return [[c.strip() for c in row] for row in csv.reader(lines, delimiter=delim)]


def read_raw_tables(filename: str, data: bytes) -> list[pd.DataFrame]:
    ext = Path(filename).suffix.lower()
    tables: list[pd.DataFrame] = []
    if ext in (".xlsx", ".xlsm", ".xls"):
        try:
            engine = "xlrd" if ext == ".xls" else "openpyxl"
            sheets = pd.read_excel(io.BytesIO(data), header=None, dtype=str, sheet_name=None, engine=engine)
            tables.extend(df for df in sheets.values() if not df.empty)
        except Exception:  # noqa: BLE001  — 很多 .xls 实际是文本
            tables = []
    if not tables:
        rows = _split_text(_decode(data))
        if rows:
            width = max(len(r) for r in rows)
            tables.append(pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str))
    return tables


def _find_header(raw: pd.DataFrame, max_scan: int = 30) -> tuple[int, dict[str, str]] | None:
    for i in range(min(max_scan, len(raw))):
        headers = [str(x) if not pd.isna(x) else "" for x in raw.iloc[i].tolist()]
        mapping = match_columns([h for h in headers if h])
        if "code" in mapping and ("quantity" in mapping or "available" in mapping):
            return i, mapping
    return None


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for h in headers:
        h = h or "未命名"
        if h in seen:
            seen[h] += 1
            out.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            out.append(h)
    return out


def parse_dataframe(df: pd.DataFrame, mapping: dict[str, str] | None = None) -> ParseResult:
    """解析已带表头的 DataFrame。mapping 可手工指定 {字段: 列名} 覆盖自动识别。"""
    headers = [str(c) for c in df.columns]
    df = df.copy()
    df.columns = headers
    auto = match_columns(headers)
    if mapping:
        auto.update({k: v for k, v in mapping.items() if v in headers})
    mapping = auto
    warnings: list[str] = []
    if "code" not in mapping:
        raise ValueError(f"没有找到「证券代码」列。识别到的列：{headers}")
    if "quantity" not in mapping and "available" not in mapping:
        raise ValueError(f"没有找到「持仓数量」列。识别到的列：{headers}")
    if "cost_price" not in mapping and "cost_amount" not in mapping:
        warnings.append("没有找到成本价列，盈亏无法计算，可在下方表格手动补充成本价。")

    def get(row: pd.Series, fld: str) -> object:
        col = mapping.get(fld)
        return row[col] if col is not None else None

    merged: dict[str, Position] = {}
    skipped_zero = 0
    for _, row in df.iterrows():
        code = normalize_code(get(row, "code"))
        if not code:
            continue
        qty = to_number(get(row, "quantity"))
        avail = to_number(get(row, "available"))
        if qty is None:
            qty = avail
        if not qty or qty <= 0:
            skipped_zero += 1
            continue
        cost = to_number(get(row, "cost_price"))
        price = to_number(get(row, "current_price"))
        cost_amount = to_number(get(row, "cost_amount"))
        if (cost is None or cost <= 0) and cost_amount:
            cost = cost_amount / qty
        if cost and price and cost > price * 20 and cost / qty < price * 20:
            cost = cost / qty  # 「持仓成本」列给的是总金额
        if price is None:
            mv = to_number(get(row, "market_value"))
            if mv:
                price = mv / qty
        name_raw = get(row, "name")
        name = "" if name_raw is None or pd.isna(name_raw) else str(name_raw).strip()
        pos = Position(
            code=code,
            name=name,
            quantity=int(round(qty)),
            available=None if avail is None else int(round(avail)),
            cost_price=None if cost is None or cost <= 0 else round(cost, 4),
            current_price=price,
        )
        if code in merged:
            old = merged[code]
            total = old.quantity + pos.quantity
            if old.cost_price and pos.cost_price:
                old.cost_price = round((old.cost_price * old.quantity + pos.cost_price * pos.quantity) / total, 4)
            old.quantity = total
            if old.available is not None or pos.available is not None:
                old.available = (old.available or 0) + (pos.available or 0)
            warnings.append(f"{code} 出现多行（可能来自不同账户），已合并。")
        else:
            merged[code] = pos
    if skipped_zero:
        warnings.append(f"已忽略 {skipped_zero} 行持仓为 0 的记录（已清仓）。")
    if not merged:
        raise ValueError("文件里没有解析到有效持仓，请确认导出的是「持仓」页面。")
    return ParseResult(list(merged.values()), mapping, warnings)


def locate_table(filename: str, data: bytes, finder) -> tuple[pd.DataFrame, int, list[str], int]:
    """读取文件、定位表头、脱敏。返回 (脱敏后的表体, 表头行号, 删除的敏感列, 扫描命中数)。"""
    from .privacy import scrub_dataframe

    tables = read_raw_tables(filename, data)
    if not tables:
        raise ValueError("文件为空或无法读取。")
    for raw in tables:
        found = finder(raw)
        if not found:
            continue
        idx, mapping = found
        headers = _dedupe_headers([str(x).strip() if not pd.isna(x) else "" for x in raw.iloc[idx].tolist()])
        body = raw.iloc[idx + 1 :].reset_index(drop=True)
        body.columns = headers
        body = body.loc[:, [h for h in headers if not h.startswith("未命名")]]
        keep = tuple(v for k, v in mapping.items() if k in ("code", "name"))
        clean, dropped, hits = scrub_dataframe(body, keep_columns=keep)
        from .privacy import scrub_text

        preamble = " ".join(str(x) for x in raw.iloc[:idx].values.ravel().tolist() if not pd.isna(x))
        discarded = len(scrub_text(preamble)[1])  # 表头上方的账户信息整段丢弃，只计数
        clean.attrs["cash"] = cash_from_text(preamble)
        return clean, idx, dropped, len(hits) + discarded
    raise LookupError


def parse_holdings_file(filename: str, data: bytes) -> ParseResult:
    try:
        body, idx, dropped, hits = locate_table(filename, data, _find_header)
    except LookupError:
        first = read_raw_tables(filename, data)[0].head(3).fillna("").astype(str).values.tolist()
        raise ValueError(f"没找到包含「证券代码」和「持仓数量」的表头。文件前几行：{first}") from None
    result = parse_dataframe(body)
    result.header_row = idx
    result.raw = body
    result.dropped_columns = dropped
    result.privacy_hits = hits
    result.cash = body.attrs.get("cash")
    return result


def positions_from_rows(rows: list[dict]) -> tuple[list[Position], list[str]]:
    """把界面上逐行确认/编辑后的数据转回持仓列表，返回 (持仓, 错误提示)。"""
    out: list[Position] = []
    errors: list[str] = []
    for i, row in enumerate(rows, 1):
        raw_code = row.get("code")
        if raw_code is None or str(raw_code).strip() == "":
            continue
        code = normalize_code(raw_code)
        if not code:
            errors.append(f"第 {i} 行代码「{raw_code}」无效")
            continue
        qty = to_number(row.get("quantity"))
        if not qty or qty <= 0:
            errors.append(f"{code} 持仓数量无效")
            continue
        avail = to_number(row.get("available"))
        cost = to_number(row.get("cost_price"))
        out.append(
            Position(
                code=code,
                name=str(row.get("name") or "").strip(),
                quantity=int(qty),
                available=None if avail is None else int(avail),
                cost_price=cost if cost and cost > 0 else None,
                note=str(row.get("note") or ""),
            )
        )
    return out, errors
