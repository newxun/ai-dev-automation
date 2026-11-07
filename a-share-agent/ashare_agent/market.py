"""A股交易规则：代码规范化、板块识别、交易单位、涨跌停、交易费用、交易时段。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def normalize_code(raw: object) -> str | None:
    """把各种写法统一成 6 位代码。

    兼容：600519、'600519'、600519.SH、SH600519、sz000001、="000001"、1（Excel 丢了前导 0）、1.0。
    """
    if raw is None:
        return None
    if isinstance(raw, float):
        if math.isnan(raw):
            return None
        if raw.is_integer():
            raw = int(raw)
    if isinstance(raw, int):
        if 0 < raw < 1_000_000:
            return f"{raw:06d}"
        return None
    s = str(raw).strip().strip('="\' \t').upper()
    if not s:
        return None
    m = _CODE_RE.search(s)
    if m:
        return m.group(1)
    if s.isdigit() and len(s) < 6:
        return s.zfill(6)
    if re.fullmatch(r"\d+\.0+", s):
        return s.split(".")[0].zfill(6)
    return None


@dataclass(frozen=True)
class SecurityInfo:
    code: str
    exchange: str  # sh / sz / bj
    board: str  # 主板 / 创业板 / 科创板 / 北交所 / ETF / LOF / 其他
    is_fund: bool

    @property
    def prefixed(self) -> str:
        return f"{self.exchange}{self.code}"

    @property
    def suffixed(self) -> str:
        return f"{self.code}.{self.exchange.upper()}"


def classify(code: str) -> SecurityInfo:
    c = code
    if c.startswith(("688", "689")):
        return SecurityInfo(c, "sh", "科创板", False)
    if c.startswith(("600", "601", "603", "605")):
        return SecurityInfo(c, "sh", "主板", False)
    if c.startswith(("51", "56", "58")):
        return SecurityInfo(c, "sh", "ETF", True)
    if c.startswith("50"):
        return SecurityInfo(c, "sh", "LOF", True)
    if c.startswith(("300", "301")):
        return SecurityInfo(c, "sz", "创业板", False)
    if c.startswith(("000", "001", "002", "003")):
        return SecurityInfo(c, "sz", "主板", False)
    if c.startswith("159"):
        return SecurityInfo(c, "sz", "ETF", True)
    if c.startswith("16"):
        return SecurityInfo(c, "sz", "LOF", True)
    if c.startswith(("43", "83", "87", "88", "92")):
        return SecurityInfo(c, "bj", "北交所", False)
    if c.startswith("6"):
        return SecurityInfo(c, "sh", "其他", False)
    return SecurityInfo(c, "sz", "其他", False)


def lot_rule(code: str) -> tuple[int, int]:
    """返回 (最小买入股数, 递增单位)。"""
    board = classify(code).board
    if board == "科创板":
        return 200, 1
    if board == "北交所":
        return 100, 1
    return 100, 100


def round_lot(code: str, shares: float) -> int:
    """向下取整到合法的买入数量；不足最小单位返回 0。"""
    min_lot, step = lot_rule(code)
    if shares < min_lot:
        return 0
    n = int(shares // step * step)
    return n if n >= min_lot else 0


ST_MAIN_BOARD_10PCT_FROM = date(2026, 7, 6)
NEW_STOCK_NO_LIMIT_DAYS = 5


def is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


def is_delisting(name: str) -> bool:
    n = (name or "").strip()
    return n.endswith("退") or n.startswith("退市")


def price_limit_pct(code: str, name: str = "", on_date: date | None = None) -> float:
    """涨跌幅限制比例。沪深主板 ST 股自 2026-07-06 起由 5% 调整为 10%。"""
    info = classify(code)
    if info.board in ("创业板", "科创板"):
        return 0.20
    if info.board == "北交所":
        return 0.30
    if info.board == "ETF" and code.startswith("588"):
        return 0.20
    if is_st(name):
        on_date = on_date or now_cn().date()
        return 0.10 if on_date >= ST_MAIN_BOARD_10PCT_FROM else 0.05
    return 0.10


def has_no_price_limit(listed_trading_days: int | None) -> bool:
    """新股上市前 5 个交易日不设涨跌幅。listed_trading_days 为「目标交易日是上市后第几个交易日」（从 1 开始）。"""
    return listed_trading_days is not None and listed_trading_days <= NEW_STOCK_NO_LIMIT_DAYS


def price_tick(code: str) -> float:
    return 0.001 if classify(code).is_fund else 0.01


def round_price(code: str, price: float) -> float:
    tick = price_tick(code)
    digits = 3 if tick < 0.01 else 2
    return round(round(price / tick) * tick, digits)


def limit_prices(code: str, prev_close: float, name: str = "", on_date: date | None = None) -> tuple[float, float]:
    """返回 (涨停价, 跌停价)，按交易所规则四舍五入到最小价位。"""
    pct = price_limit_pct(code, name, on_date)
    return round_price(code, prev_close * (1 + pct)), round_price(code, prev_close * (1 - pct))


@dataclass
class FeeSettings:
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax: float = 0.0005
    transfer_fee: float = 0.00001

    def to_dict(self) -> dict:
        return {
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax": self.stamp_tax,
            "transfer_fee": self.transfer_fee,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "FeeSettings":
        d = d or {}
        base = cls()
        return cls(
            commission_rate=float(d.get("commission_rate", base.commission_rate)),
            min_commission=float(d.get("min_commission", base.min_commission)),
            stamp_tax=float(d.get("stamp_tax", base.stamp_tax)),
            transfer_fee=float(d.get("transfer_fee", base.transfer_fee)),
        )


def trade_fee(code: str, price: float, shares: int, side: str, fees: FeeSettings | None = None) -> float:
    """单笔交易费用（元）。side: buy / sell。ETF/LOF 免印花税和过户费。"""
    fees = fees or FeeSettings()
    amount = price * shares
    if amount <= 0:
        return 0.0
    commission = max(amount * fees.commission_rate, fees.min_commission)
    if classify(code).is_fund:
        return round(commission, 2)
    stamp = amount * fees.stamp_tax if side == "sell" else 0.0
    transfer = amount * fees.transfer_fee
    return round(commission + stamp + transfer, 2)


def round_trip_fee(code: str, buy_price: float, sell_price: float, shares: int, fees: FeeSettings | None = None) -> float:
    return trade_fee(code, buy_price, shares, "buy", fees) + trade_fee(code, sell_price, shares, "sell", fees)


def breakeven_spread(code: str, price: float, shares: int, fees: FeeSettings | None = None) -> float:
    """一买一卖刚好覆盖费用所需的价差比例。"""
    if price <= 0 or shares <= 0:
        return 0.0
    return round_trip_fee(code, price, price, shares, fees) / (price * shares)


def breakeven_spread_per_share(code: str, price: float, shares: int, fees: FeeSettings | None = None) -> float:
    """保本价差（元/股），向上取到 0.01 元。"""
    if shares <= 0:
        return 0.0
    per = round_trip_fee(code, price, price, shares, fees) / shares
    tick = price_tick(code)
    return round(math.ceil(per / tick - 1e-9) * tick, 3)


def sellable_tomorrow(quantity: int, frozen: int = 0) -> int:
    """T+1：收盘后持有的股份次日都可卖（冻结部分除外）。"""
    return max(0, quantity - frozen)


def sellable_today(quantity: int, bought_today: int, frozen: int = 0) -> int:
    return max(0, quantity - bought_today - frozen)


def cost_after_add(qty: int, cost: float, add_qty: int, add_price: float, code: str = "600000",
                   fees: FeeSettings | None = None, include_fee: bool = True) -> float:
    """补仓后的持仓成本（元/股），默认把买入费用计入成本。"""
    total_qty = qty + add_qty
    if total_qty <= 0:
        return 0.0
    fee = trade_fee(code, add_price, add_qty, "buy", fees) if include_fee else 0.0
    return round((qty * cost + add_qty * add_price + fee) / total_qty, 4)


@dataclass
class TResult:
    gross: float
    fee: float
    net: float
    new_cost: float
    cost_drop: float


def t_trade_result(code: str, base_qty: int, cost: float, buy_price: float, sell_price: float, t_qty: int,
                   fees: FeeSettings | None = None) -> TResult:
    """一次完整的做T（一买一卖各 t_qty 股）扣费后的结果，以及对底仓成本的影响。"""
    gross = (sell_price - buy_price) * t_qty
    fee = round_trip_fee(code, buy_price, sell_price, t_qty, fees)
    net = gross - fee
    new_cost = (cost * base_qty - net) / base_qty if base_qty else cost
    return TResult(round(gross, 2), round(fee, 2), round(net, 2), round(new_cost, 4), round(cost - new_cost, 4))


SESSION_PHASES = (
    (time(0, 0), time(9, 15), "盘前"),
    (time(9, 15), time(9, 25), "开盘集合竞价"),
    (time(9, 25), time(9, 30), "待开盘"),
    (time(9, 30), time(10, 0), "早盘"),
    (time(10, 0), time(11, 30), "盘中"),
    (time(11, 30), time(13, 0), "午间休市"),
    (time(13, 0), time(14, 30), "盘中"),
    (time(14, 30), time(14, 57), "尾盘"),
    (time(14, 57), time(15, 0), "收盘集合竞价"),
    (time(15, 0), time(23, 59, 59), "已收盘"),
)


def session_phase(dt: datetime | None = None, is_trading_day: bool | None = None) -> str:
    dt = (dt or now_cn()).astimezone(CN_TZ)
    if is_trading_day is None:
        is_trading_day = dt.weekday() < 5
    if not is_trading_day:
        return "休市"
    t = dt.time()
    for start, end, name in SESSION_PHASES:
        if start <= t < end:
            return name
    return "已收盘"


def is_continuous_trading(phase: str) -> bool:
    return phase in ("早盘", "盘中", "尾盘")
