"""用户硬规则、违规检查、冷静关卡、仓位计算。全部由程序判断，模型不能绕过。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from .market import classify, is_delisting, is_st, round_lot


@dataclass
class UserRules:
    max_position_pct: float = 0.30  # 单只股票市值占总资产上限
    max_trade_loss_pct: float = 0.02  # 单笔交易最坏亏损占总资产上限
    allow_st: bool = False
    allow_bj: bool = False
    t_max_ratio: float = 1 / 3  # 做T最多用底仓的比例
    chase_pct: float = 0.05  # 当日涨幅超过该值买入视为追涨
    cooldown_days: int = 1  # 亏损卖出后多少个交易日内再买入需要写理由
    custom: list[str] = field(default_factory=list)  # 用户自己写的其他规则（文字，由导师提醒）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "UserRules":
        d = d or {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def describe(self) -> list[str]:
        lines = [
            f"单只股票仓位不超过总资产的 {self.max_position_pct:.0%}",
            f"单笔交易最坏亏损不超过总资产的 {self.max_trade_loss_pct:.1%}",
            "不碰 ST 和退市整理股" if not self.allow_st else "允许交易 ST 股",
            "不碰北交所" if not self.allow_bj else "允许交易北交所",
            f"做T最多用底仓的 {self.t_max_ratio:.0%}",
        ]
        return lines + list(self.custom)


@dataclass
class InvestorProfile:
    capital_range: str = ""
    experience_years: str = ""
    max_drawdown: str = ""
    holding_period: str = ""
    can_watch: str = ""
    t_experience: str = ""
    mistakes: list[str] = field(default_factory=list)
    goal: str = ""
    notes: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "InvestorProfile":
        d = d or {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def filled(self) -> bool:
        return bool(self.capital_range or self.holding_period or self.mistakes)


@dataclass
class Violation:
    code: str
    rule: str
    detail: str
    severity: str = "warn"  # warn / block


def check_security_allowed(code: str, name: str, rules: UserRules) -> Violation | None:
    if not rules.allow_st and (is_st(name) or is_delisting(name)):
        return Violation(code, "不碰 ST/退市整理", f"{name}（{code}）是 ST 或退市整理股", "block")
    if not rules.allow_bj and classify(code).board == "北交所":
        return Violation(code, "不碰北交所", f"{name}（{code}）是北交所股票", "block")
    return None


def portfolio_violations(holdings: list[dict], total_assets: float, rules: UserRules) -> list[Violation]:
    """holdings: [{code, name, market_value}]"""
    out: list[Violation] = []
    for h in holdings:
        v = check_security_allowed(h["code"], h.get("name", ""), rules)
        if v:
            out.append(Violation(v.code, v.rule, v.detail + "，属于你设定的禁区", "warn"))
        if total_assets > 0 and h.get("market_value"):
            w = h["market_value"] / total_assets
            if w > rules.max_position_pct + 1e-9:
                out.append(
                    Violation(h["code"], "单只仓位上限", f"{h.get('name', h['code'])} 占总资产 {w:.1%}，超过上限 {rules.max_position_pct:.0%}")
                )
    return out


def max_position_value(total_assets: float, rules: UserRules) -> float:
    return total_assets * rules.max_position_pct


def size_by_risk(code: str, entry: float, stop: float, total_assets: float, cash: float, current_value: float,
                 rules: UserRules) -> dict:
    """按「单笔最坏亏损」和「单只上限」「可用资金」三个约束算最多能买多少股。"""
    if entry <= 0 or stop <= 0 or stop >= entry:
        return {"shares": 0, "reason": "止损价必须低于买入价"}
    risk_per_share = entry - stop
    by_risk = rules.max_trade_loss_pct * total_assets / risk_per_share
    by_cap = max(0.0, (max_position_value(total_assets, rules) - current_value) / entry)
    by_cash = cash / entry if cash > 0 else 0.0
    limits = {"单笔最坏亏损": by_risk, "单只仓位上限": by_cap, "可用资金": by_cash}
    binding = min(limits, key=limits.get)
    shares = round_lot(code, limits[binding])
    return {
        "shares": shares,
        "binding": binding,
        "limits": {k: int(v) for k, v in limits.items()},
        "worst_loss": round(shares * risk_per_share, 2),
        "worst_loss_pct": round(shares * risk_per_share / total_assets, 4) if total_assets else None,
        "amount": round(shares * entry, 2),
    }


@dataclass
class CoolingGate:
    kind: str
    message: str


def cooling_gates(
    code: str,
    side: str,
    rules: UserRules,
    day_change: float | None = None,
    change_5d: float | None = None,
    holding_cost: float | None = None,
    last_price: float | None = None,
    recent_loss_sells: list[tuple[date, str]] | None = None,
    today: date | None = None,
    trading_days_between=None,
) -> list[CoolingGate]:
    """买入前的冷静关卡：追涨大涨股、给亏损股补仓、亏后马上再买。触发后要求先写下理由。"""
    if side != "buy":
        return []
    gates: list[CoolingGate] = []
    if day_change is not None and day_change >= rules.chase_pct:
        gates.append(CoolingGate("追涨", f"这只票今天已经涨了 {day_change:.1%}，属于追涨买入。"))
    elif change_5d is not None and change_5d >= 0.15:
        gates.append(CoolingGate("追涨", f"这只票近 5 个交易日涨了 {change_5d:.1%}，属于追涨买入。"))
    if holding_cost and last_price and last_price < holding_cost:
        gates.append(
            CoolingGate("给亏损股补仓", f"你持有这只票且浮亏 {(last_price / holding_cost - 1):.1%}，现在是给亏损股补仓。")
        )
    if recent_loss_sells and today:
        for d, c in recent_loss_sells:
            gap = trading_days_between(d, today) if trading_days_between else (today - d).days
            if gap <= rules.cooldown_days:
                gates.append(CoolingGate("亏后马上再买", f"你在 {d:%m-%d} 刚亏损卖出 {c}，现在又要买入。"))
                break
    return gates
