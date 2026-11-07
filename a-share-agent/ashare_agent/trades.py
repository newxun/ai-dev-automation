"""交割单 / 成交记录解析、按成交更新持仓、持仓与成交对账。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd

from .market import FeeSettings, normalize_code, trade_fee
from .portfolio import FIELD_ALIASES as POS_ALIASES
from .portfolio import Position, locate_table, normalize_header, to_number

TRADE_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("成交日期", "发生日期", "交收日期", "交易日期", "业务日期", "清算日期", "日期"),
    "time": ("成交时间", "委托时间", "时间"),
    "code": POS_ALIASES["code"],
    "name": POS_ALIASES["name"],
    "side": ("操作", "业务名称", "买卖标志", "买卖方向", "买卖类别", "委托类别", "交易类别", "业务类型", "摘要", "方向", "交易类型"),
    "quantity": ("成交数量", "成交股数", "发生数量", "成交量", "数量"),
    "price": ("成交价格", "成交均价", "成交价", "均价", "价格"),
    "amount": ("成交金额", "成交额"),
    "commission": ("佣金", "手续费", "净佣金"),
    "stamp_tax": ("印花税",),
    "transfer_fee": ("过户费",),
    "other_fee": ("其他费", "其他杂费", "其它费用", "其他费用", "交易规费", "规费", "经手费", "证管费", "结算费"),
    "net_amount": ("发生金额", "清算金额", "收付金额", "资金发生数", "变动金额"),
    "remaining": ("剩余数量", "股票余额", "库存数量", "证券余额"),
}

_SKIP_WORDS = ("转", "红利", "股息", "利息", "申购", "配号", "中签", "红股", "托管", "指定", "撤", "配售", "缴款", "派息", "扣税", "补缴")


def match_trade_columns(headers: list[str]) -> dict[str, str]:
    norm = {h: normalize_header(h) for h in headers}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for fld, aliases in TRADE_ALIASES.items():
        for alias in aliases:
            hit = next((h for h in headers if h not in used and norm[h] == alias), None)
            if hit is not None:
                mapping[fld] = hit
                used.add(hit)
                break
    for fld, aliases in TRADE_ALIASES.items():
        if fld in mapping:
            continue
        for alias in sorted((a for a in aliases if len(a) >= 3), key=len, reverse=True):
            hit = next((h for h in headers if h not in used and alias in norm[h]), None)
            if hit is not None:
                mapping[fld] = hit
                used.add(hit)
                break
    return mapping


def _find_trade_header(raw: pd.DataFrame, max_scan: int = 30):
    for i in range(min(max_scan, len(raw))):
        headers = [str(x) if not pd.isna(x) else "" for x in raw.iloc[i].tolist()]
        mapping = match_trade_columns([h for h in headers if h])
        if {"code", "quantity", "price"} <= set(mapping) and ("side" in mapping or "net_amount" in mapping):
            return i, mapping
    return None


@dataclass
class Trade:
    trade_date: str
    code: str
    side: str  # buy / sell
    quantity: int
    price: float
    name: str = ""
    trade_time: str = ""
    amount: float = 0.0
    commission: float | None = None
    stamp_tax: float | None = None
    transfer_fee: float | None = None
    other_fee: float | None = None
    fee_estimated: bool = False
    plan_card_id: int | None = None
    source: str = "import"

    @property
    def fee_total(self) -> float:
        return round(sum(x or 0.0 for x in (self.commission, self.stamp_tax, self.transfer_fee, self.other_fee)), 2)

    @property
    def date_obj(self) -> date:
        return datetime.strptime(self.trade_date, "%Y-%m-%d").date()

    def key(self) -> tuple:
        return (self.trade_date, self.trade_time, self.code, self.side, self.quantity, round(self.price, 3))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class TradeParseResult:
    trades: list[Trade]
    mapping: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    dropped_columns: list[str] = field(default_factory=list)
    privacy_hits: int = 0


def parse_date(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().strip('="')
    m = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_time(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip().strip('="')
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}"
    if re.fullmatch(r"\d{5,6}", s):
        s = s.zfill(6)
        return f"{s[:2]}:{s[2:4]}:{s[4:]}"
    return ""


def parse_side(v: object, qty: float | None = None, net: float | None = None) -> str | None:
    s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    if s:
        if any(w in s for w in _SKIP_WORDS) and "买" not in s and "卖" not in s:
            return None
        if "买" in s or s.upper() in ("B", "BUY", "1"):
            return "buy"
        if "卖" in s or s.upper() in ("S", "SELL", "2"):
            return "sell"
        return None
    if net is not None and net != 0:
        return "buy" if net < 0 else "sell"
    if qty is not None and qty < 0:
        return "sell"
    return None


def parse_trades_dataframe(df: pd.DataFrame, fees: FeeSettings | None = None) -> TradeParseResult:
    headers = [str(c) for c in df.columns]
    mapping = match_trade_columns(headers)
    missing = [f for f in ("code", "quantity", "price") if f not in mapping]
    if missing:
        raise ValueError(f"交割单缺少必要的列：{missing}。识别到的列：{headers}")
    warnings: list[str] = []
    trades: list[Trade] = []
    skipped = 0
    no_date = 0

    def get(row: pd.Series, fld: str) -> object:
        col = mapping.get(fld)
        return row[col] if col is not None else None

    for _, row in df.iterrows():
        code = normalize_code(get(row, "code"))
        qty = to_number(get(row, "quantity"))
        price = to_number(get(row, "price"))
        net = to_number(get(row, "net_amount"))
        if not code or not qty or not price or price <= 0:
            skipped += 1
            continue
        side = parse_side(get(row, "side"), qty, net)
        if side is None:
            skipped += 1
            continue
        d = parse_date(get(row, "date"))
        if not d:
            no_date += 1
            continue
        qty_abs = int(abs(qty))
        amount = to_number(get(row, "amount")) or round(qty_abs * price, 2)
        t = Trade(
            trade_date=d,
            trade_time=parse_time(get(row, "time")),
            code=code,
            name=str(get(row, "name") or "").strip(),
            side=side,
            quantity=qty_abs,
            price=price,
            amount=abs(amount),
            commission=to_number(get(row, "commission")),
            stamp_tax=to_number(get(row, "stamp_tax")),
            transfer_fee=to_number(get(row, "transfer_fee")),
            other_fee=to_number(get(row, "other_fee")),
        )
        if t.commission is None and t.stamp_tax is None and t.transfer_fee is None:
            t.commission = trade_fee(code, price, qty_abs, side, fees)
            t.fee_estimated = True
        trades.append(t)
    if skipped:
        warnings.append(f"已跳过 {skipped} 行非买卖记录（银证转账、红利、申购等）。")
    if no_date:
        warnings.append(f"有 {no_date} 行没有成交日期，已跳过。")
    if any(t.fee_estimated for t in trades):
        warnings.append("部分成交没有费用列，已按你设置的费率估算。")
    if not trades:
        raise ValueError("没有解析到买卖成交记录，请确认导出的是「交割单」或「当日成交」。")
    trades.sort(key=lambda t: (t.trade_date, t.trade_time))
    return TradeParseResult(trades, mapping, warnings)


def parse_trades_file(filename: str, data: bytes, fees: FeeSettings | None = None) -> TradeParseResult:
    try:
        body, _, dropped, hits = locate_table(filename, data, _find_trade_header)
    except LookupError:
        raise ValueError("没找到交割单表头（需要包含证券代码、成交数量、成交价格、买卖方向）。") from None
    result = parse_trades_dataframe(body, fees)
    result.dropped_columns = dropped
    result.privacy_hits = hits
    return result


def apply_trades(positions: list[Position], trades: list[Trade]) -> tuple[list[Position], list[str]]:
    """按成交更新持仓（买入摊入费用计算均价；卖出不改均价）。返回 (新持仓, 提示)。"""
    book = {p.code: Position(**p.to_dict()) for p in positions}
    notes: list[str] = []
    for t in sorted(trades, key=lambda x: (x.trade_date, x.trade_time)):
        p = book.get(t.code)
        if t.side == "buy":
            if p is None:
                p = book[t.code] = Position(code=t.code, name=t.name, quantity=0, cost_price=0.0)
            old_cost = (p.cost_price or 0.0) * p.quantity
            p.quantity += t.quantity
            p.cost_price = round((old_cost + t.amount + t.fee_total) / p.quantity, 4)
            if not p.name:
                p.name = t.name
        else:
            if p is None or p.quantity < t.quantity:
                have = 0 if p is None else p.quantity
                notes.append(f"{t.trade_date} 卖出 {t.name or t.code} {t.quantity} 股，但持仓里只有 {have} 股，请核对持仓或补充更早的成交。")
                if p is None:
                    continue
            p.quantity = max(0, p.quantity - t.quantity)
        p.available = None
    return [p for p in book.values() if p.quantity > 0], notes


def reconcile(prev: list[Position], trades: list[Trade], current: list[Position]) -> list[str]:
    """昨日持仓 + 今日成交 应该等于 今日持仓；对不上就提示。"""
    expected, notes = apply_trades(prev, trades)
    exp = {p.code: p.quantity for p in expected}
    cur = {p.code: p.quantity for p in current}
    names = {p.code: p.name for p in [*prev, *current]} | {t.code: t.name for t in trades}
    for code in sorted(set(exp) | set(cur)):
        e, c = exp.get(code, 0), cur.get(code, 0)
        if e != c:
            notes.append(f"{names.get(code) or code}：按成交推算应持有 {e} 股，持仓表里是 {c} 股，请检查是否漏导了成交或持仓。")
    return notes
