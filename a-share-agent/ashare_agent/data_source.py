"""行情与资料数据：只用日线和收盘后数据。

每个取数方法都返回 Fetched（值 + 来源 + 数据时间 + 是否成功）。拉取失败时先用过期缓存，再不行就返回缺失，
由卡片的「数据缺口」栏如实展示，绝不编造。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .market import classify, now_cn

DAILY_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


@dataclass
class Fetched:
    value: Any
    source: str
    as_of: str = ""
    ok: bool = True
    stale: bool = False
    error: str = ""

    @classmethod
    def missing(cls, source: str, error: str) -> "Fetched":
        return cls(None, source, "", False, False, error)

    def label(self) -> str:
        if not self.ok:
            return f"{self.source}：未获取（{self.error}）"
        tag = "，缓存可能过期" if self.stale else ""
        return f"{self.source}，数据时间 {self.as_of}{tag}"


def finalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    df = df[DAILY_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for c in DAILY_COLUMNS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    df["pre_close"] = df["close"].shift(1)
    df["pct_chg"] = df["close"] / df["pre_close"] - 1
    df["amplitude"] = (df["high"] - df["low"]) / df["pre_close"]
    return df


def quarter_periods(d: date) -> list[str]:
    """当前日期附近需要关注的财报期（预约披露表的 period 参数）。"""
    y, m = d.year, d.month
    if m <= 4:
        return [f"{y - 1}年报", f"{y}一季"]
    if m <= 6:
        return [f"{y}半年报"]
    if m <= 8:
        return [f"{y}半年报"]
    if m <= 10:
        return [f"{y}三季"]
    return [f"{y}三季", f"{y}年报"]


class MarketData:
    """数据接口。子类实现 _raw_* 方法。"""

    name = "base"

    def daily(self, code: str, end: str | None = None, days: int = 120) -> Fetched:
        raise NotImplementedError

    def index_daily(self, symbol: str, end: str | None = None, days: int = 30) -> Fetched:
        raise NotImplementedError

    def profile(self, code: str) -> Fetched:
        raise NotImplementedError

    def financials(self, code: str) -> Fetched:
        raise NotImplementedError

    def announcements(self, code: str, start: str, end: str) -> Fetched:
        raise NotImplementedError

    def events(self, code: str, start: str, end: str) -> Fetched:
        raise NotImplementedError

    def calendar(self) -> list[str]:
        raise NotImplementedError

    def name_table(self) -> dict[str, str]:
        return {}

    # ---------- 交易日历 ----------
    def is_trading_day(self, d: str) -> bool:
        cal = self.calendar()
        if cal:
            return d in set(cal)
        return datetime.strptime(d, "%Y-%m-%d").weekday() < 5

    def next_trading_day(self, d: str) -> str:
        cal = self.calendar()
        if cal:
            later = [x for x in cal if x > d]
            if later:
                return later[0]
        x = datetime.strptime(d, "%Y-%m-%d").date() + timedelta(days=1)
        while x.weekday() >= 5:
            x += timedelta(days=1)
        return x.isoformat()

    def prev_trading_day(self, d: str) -> str:
        cal = self.calendar()
        if cal:
            earlier = [x for x in cal if x < d]
            if earlier:
                return earlier[-1]
        x = datetime.strptime(d, "%Y-%m-%d").date() - timedelta(days=1)
        while x.weekday() >= 5:
            x -= timedelta(days=1)
        return x.isoformat()

    def last_closed_trading_day(self, now: datetime | None = None) -> str:
        """最近一个已收盘的交易日（15:00 之后算当天）。"""
        now = now or now_cn()
        today = now.date().isoformat()
        if self.is_trading_day(today) and now.hour >= 15:
            return today
        return self.prev_trading_day(today)

    def trading_days_between(self, a: str, b: str) -> int:
        cal = self.calendar()
        if cal:
            return len([x for x in cal if a < x <= b])
        da, db = (datetime.strptime(x, "%Y-%m-%d").date() for x in (a, b))
        return sum(1 for i in range(1, (db - da).days + 1) if (da + timedelta(days=i)).weekday() < 5)


class AkshareProvider(MarketData):
    name = "akshare"
    TTL = {"daily": 6 * 3600, "index": 6 * 3600, "profile": 86400, "fin": 86400, "ann": 3600, "events": 6 * 3600,
           "calendar": 7 * 86400, "names": 7 * 86400}

    def __init__(self, storage=None, timeout: float = 25.0) -> None:
        self.storage = storage
        self.timeout = timeout
        self._pool = ThreadPoolExecutor(max_workers=8)
        self._mem: dict[str, tuple[float, Any]] = {}

    # ---------- 基础设施 ----------
    def _call(self, fn: Callable[[], Any]) -> Any:
        fut = self._pool.submit(fn)
        try:
            return fut.result(timeout=self.timeout)
        except FutureTimeout as exc:
            raise TimeoutError(f"数据源超过 {self.timeout:.0f} 秒没有响应") from exc

    def _cache_get(self, key: str) -> tuple[float, Any] | None:
        if key in self._mem:
            return self._mem[key]
        if self.storage is not None:
            hit = self.storage.cache_get(key)
            if hit:
                self._mem[key] = hit
            return hit
        return None

    def _cache_set(self, key: str, value: Any) -> None:
        self._mem[key] = (time.time(), value)
        if self.storage is not None:
            self.storage.cache_set(key, value)

    def _cached(self, key: str, ttl: float, loaders: list[tuple[str, Callable[[], Any]]], as_of_fn=None) -> Fetched:
        hit = self._cache_get(key)
        if hit and time.time() - hit[0] < ttl:
            payload = hit[1]
            return Fetched(payload["value"], payload["source"], payload.get("as_of", ""))
        errors = []
        for source, loader in loaders:
            try:
                value = self._call(loader)
                if value is None or (hasattr(value, "__len__") and len(value) == 0 and not isinstance(value, dict)):
                    raise ValueError("返回为空")
                as_of = as_of_fn(value) if as_of_fn else now_cn().strftime("%Y-%m-%d %H:%M")
                self._cache_set(key, {"value": value, "source": source, "as_of": as_of})
                return Fetched(value, source, as_of)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source}: {type(exc).__name__} {str(exc)[:80]}")
        if hit:
            payload = hit[1]
            return Fetched(payload["value"], payload["source"], payload.get("as_of", ""), stale=True)
        return Fetched.missing(loaders[0][0] if loaders else key, "；".join(errors) or "无可用数据源")

    @staticmethod
    def _records(df: pd.DataFrame) -> list[dict]:
        return json.loads(df.to_json(orient="records", force_ascii=False))

    # ---------- 日线 ----------
    def daily(self, code: str, end: str | None = None, days: int = 120) -> Fetched:
        import akshare as ak

        end = end or now_cn().date().isoformat()
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=int(days * 1.6) + 20)).strftime("%Y%m%d")
        e = end.replace("-", "")
        info = classify(code)

        def em() -> list[dict]:
            if info.is_fund:
                df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start, end_date=e, adjust="")
            else:
                df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=e, adjust="")
            df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close",
                                    "成交量": "volume", "成交额": "amount"})
            return self._records(finalize_daily(df).tail(days))

        def sina() -> list[dict]:
            df = ak.stock_zh_a_daily(symbol=info.prefixed, start_date=start, end_date=e, adjust="")
            return self._records(finalize_daily(df).tail(days))

        def tx() -> list[dict]:
            df = ak.stock_zh_a_hist_tx(symbol=info.prefixed, start_date=start, end_date=e, adjust="")
            if "volume" not in df:
                df["volume"] = df.get("amount", 0)
            return self._records(finalize_daily(df).tail(days))

        loaders = [("东方财富日线", em)]
        if not info.is_fund:
            loaders += [("新浪日线", sina), ("腾讯日线", tx)]
        ttl = self.TTL["daily"] if end >= now_cn().date().isoformat() else 30 * 86400
        res = self._cached(f"daily:{code}:{end}:{days}", ttl, loaders, lambda v: v[-1]["date"] if v else "")
        if res.ok:
            res.value = pd.DataFrame(res.value)
        return res

    def index_daily(self, symbol: str, end: str | None = None, days: int = 30) -> Fetched:
        import akshare as ak

        end = end or now_cn().date().isoformat()

        def sina() -> list[dict]:
            df = ak.stock_zh_index_daily(symbol=symbol)
            df["amount"] = 0.0
            df = finalize_daily(df)
            return self._records(df[df["date"] <= end].tail(days))

        def em() -> list[dict]:
            s = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=days * 2 + 10)).strftime("%Y%m%d")
            df = ak.stock_zh_index_daily_em(symbol=symbol, start_date=s, end_date=end.replace("-", ""))
            return self._records(finalize_daily(df).tail(days))

        res = self._cached(f"index:{symbol}:{end}:{days}", self.TTL["index"], [("新浪指数日线", sina), ("东方财富指数日线", em)],
                           lambda v: v[-1]["date"] if v else "")
        if res.ok:
            res.value = pd.DataFrame(res.value)
        return res

    # ---------- 资料 ----------
    def profile(self, code: str) -> Fetched:
        import akshare as ak

        def em() -> dict:
            df = ak.stock_individual_info_em(symbol=code)
            items = dict(zip(df["item"], df["value"]))
            mv = items.get("总市值")
            listed = str(items.get("上市时间") or "")
            return {
                "name": str(items.get("股票简称") or ""),
                "industry": str(items.get("行业") or ""),
                "total_mv_yi": round(float(mv) / 1e8, 1) if mv not in (None, "", "-") else None,
                "list_date": f"{listed[:4]}-{listed[4:6]}-{listed[6:8]}" if len(listed) == 8 else "",
            }

        if classify(code).is_fund:
            return Fetched({"name": "", "industry": "ETF/基金", "total_mv_yi": None, "list_date": ""}, "代码规则")
        return self._cached(f"profile:{code}", self.TTL["profile"], [("东方财富个股资料", em)])

    def financials(self, code: str) -> Fetched:
        import akshare as ak

        keep = ["报告期", "营业总收入", "营业总收入同比增长率", "净利润", "净利润同比增长率", "扣非净利润同比增长率",
                "基本每股收益", "每股净资产", "销售毛利率", "净资产收益率", "资产负债率"]

        def ths() -> list[dict]:
            df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
            df = df[[c for c in keep if c in df.columns]].tail(4).astype(str)
            df = df.replace({"False": "", "nan": ""})
            return self._records(df.iloc[::-1])

        if classify(code).is_fund:
            return Fetched.missing("同花顺财务摘要", "ETF 没有财务报表")
        return self._cached(f"fin:{code}", self.TTL["fin"], [("同花顺财务摘要", ths)], lambda v: v[0].get("报告期", "") if v else "")

    def announcements(self, code: str, start: str, end: str) -> Fetched:
        import akshare as ak

        def cninfo() -> list[dict]:
            df = ak.stock_zh_a_disclosure_report_cninfo(symbol=code, market="沪深京", start_date=start.replace("-", ""),
                                                         end_date=end.replace("-", ""))
            df = df.rename(columns={"公告标题": "title", "公告时间": "date"})[["date", "title"]]
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            return self._records(df.head(15))

        res = self._cached(f"ann:{code}:{start}:{end}", self.TTL["ann"], [("巨潮资讯公告", cninfo)])
        if not res.ok and "返回为空" in res.error:
            return Fetched([], "巨潮资讯公告", end)
        return res

    def _disclosure_table(self, period: str) -> Fetched:
        import akshare as ak

        def em() -> dict:
            try:
                df = ak.stock_report_disclosure(market="沪深京", period=period)
            except ValueError as exc:
                if "Length mismatch" in str(exc):
                    return {}  # 该期预约表尚未发布
                raise
            out = {}
            for _, r in df.iterrows():
                dates = [r.get(c) for c in ("实际披露", "三次变更", "二次变更", "初次变更", "首次预约")]
                dates = [pd.Timestamp(x).strftime("%Y-%m-%d") for x in dates if x is not None and not pd.isna(x)]
                out[str(r["股票代码"]).zfill(6)] = {"name": str(r["股票简称"]).replace(" ", ""), "date": dates[0] if dates else ""}
            return out

        return self._cached(f"disclosure:{period}", 86400, [("东方财富财报预约披露", em)])

    def events(self, code: str, start: str, end: str) -> Fetched:
        """[start, end] 区间内已知的财报披露、限售解禁、停牌事件。"""
        import akshare as ak

        items: list[dict] = []
        errors: list[str] = []
        sources: list[str] = []
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        notes: list[str] = []
        for period in quarter_periods(d0):
            t = self._disclosure_table(period)
            if t.ok and not t.value:
                notes.append(f"{period}预约披露表尚未发布")
                sources.append("财报预约披露")
            elif t.ok:
                sources.append("财报预约披露")
                hit = t.value.get(code)
                if hit and hit["date"] and start <= hit["date"] <= end:
                    items.append({"date": hit["date"], "type": "财报", "detail": f"{period}披露"})
            else:
                errors.append(f"财报预约披露：{t.error}")

        def restricted() -> list[dict]:
            df = ak.stock_restricted_release_detail_em(start_date=start.replace("-", ""), end_date=end.replace("-", ""))
            df = df[df["股票代码"].astype(str).str.zfill(6) == code]
            return [{"date": pd.Timestamp(r["解禁时间"]).strftime("%Y-%m-%d"), "type": "解禁",
                     "detail": f"{r['限售股类型']}，占流通市值 {float(r['占解禁前流通市值比例']):.2%}"} for _, r in df.iterrows()]

        r = self._cached(f"restricted:{code}:{start}:{end}", self.TTL["events"], [("东方财富限售解禁", restricted)])
        if r.ok or "返回为空" in r.error:
            sources.append("限售解禁")
            items += r.value or []
        else:
            errors.append(f"限售解禁：{r.error}")

        def tfp() -> list[dict]:
            df = ak.stock_tfp_em(date=start.replace("-", ""))
            df = df[df["代码"].astype(str).str.zfill(6) == code]
            return [{"date": start, "type": "停牌", "detail": f"{r['停牌原因']}，预计复牌 {r['预计复牌时间']}"} for _, r in df.iterrows()]

        s = self._cached(f"tfp:{code}:{start}", self.TTL["events"], [("东方财富停复牌", tfp)])
        if s.ok or "返回为空" in s.error:
            sources.append("停复牌")
            items += s.value or []
        else:
            errors.append(f"停复牌：{s.error}")
        if not sources:
            return Fetched.missing("事件日历", "；".join(errors))
        f = Fetched(sorted(items, key=lambda x: x["date"]), "、".join(sources), now_cn().strftime("%Y-%m-%d %H:%M"))
        f.error = "；".join(errors + notes)
        return f

    def calendar(self) -> list[str]:
        import akshare as ak

        def sina() -> list[str]:
            df = ak.tool_trade_date_hist_sina()
            return [pd.Timestamp(x).strftime("%Y-%m-%d") for x in df["trade_date"]]

        res = self._cached("calendar", self.TTL["calendar"], [("新浪交易日历", sina)])
        return res.value if res.ok else []

    def name_table(self) -> dict[str, str]:
        for period in quarter_periods(now_cn().date()):
            t = self._disclosure_table(period)
            if t.ok:
                return {k: v["name"] for k, v in t.value.items()}
        return {}


class SnapshotProvider(MarketData):
    """冻结的历史数据快照（考卷、自动测试、离线演示用）。数据只截取到 end，不会泄露「未来」。"""

    name = "snapshot"

    def __init__(self, data: dict, source_label: str = "冻结快照") -> None:
        self.data = data
        self.label = source_label

    @classmethod
    def load(cls, path: Path) -> "SnapshotProvider":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def _src(self, what: str) -> str:
        return f"{self.label}·{what}"

    def daily(self, code: str, end: str | None = None, days: int = 120) -> Fetched:
        rows = self.data.get("daily", {}).get(code)
        if not rows:
            return Fetched.missing(self._src("日线"), "快照中没有该股票")
        df = finalize_daily(pd.DataFrame(rows))
        if end:
            df = df[df["date"] <= end]
        df = df.tail(days).reset_index(drop=True)
        if df.empty:
            return Fetched.missing(self._src("日线"), "该日期之前没有数据")
        return Fetched(df, self._src("日线"), df["date"].iloc[-1])

    def index_daily(self, symbol: str, end: str | None = None, days: int = 30) -> Fetched:
        rows = self.data.get("index", {}).get(symbol)
        if not rows:
            return Fetched.missing(self._src("指数"), "快照中没有该指数")
        df = finalize_daily(pd.DataFrame(rows).assign(amount=lambda x: x.get("amount", 0.0)))
        if end:
            df = df[df["date"] <= end]
        df = df.tail(days).reset_index(drop=True)
        return Fetched(df, self._src("指数"), df["date"].iloc[-1] if len(df) else "")

    def profile(self, code: str) -> Fetched:
        p = self.data.get("profile", {}).get(code)
        return Fetched(p, self._src("个股资料"), self.data.get("as_of", "")) if p else Fetched.missing(self._src("个股资料"), "快照中没有")

    def financials(self, code: str) -> Fetched:
        f = self.data.get("financials", {}).get(code)
        return Fetched(f, self._src("财务摘要"), f[0].get("报告期", "")) if f else Fetched.missing(self._src("财务摘要"), "快照中没有")

    def announcements(self, code: str, start: str, end: str) -> Fetched:
        if code in self.data.get("missing_announcements", []):
            return Fetched.missing(self._src("公告"), "快照中缺少公告数据")
        items = [a for a in self.data.get("announcements", {}).get(code, []) if start <= a["date"] <= end]
        return Fetched(items, self._src("公告"), end)

    def events(self, code: str, start: str, end: str) -> Fetched:
        items = [e for e in self.data.get("events", {}).get(code, []) if start <= e["date"] <= end]
        return Fetched(items, self._src("事件日历"), start)

    def calendar(self) -> list[str]:
        return self.data.get("calendar", [])

    def name_table(self) -> dict[str, str]:
        return {c: p.get("name", "") for c, p in self.data.get("profile", {}).items()}


@dataclass
class DataBundle:
    """一次任务用到的所有数据，以及每项的来源和缺口，便于写进卡片。"""

    facts: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)

    def add(self, key: str, fetched: Fetched, transform: Callable[[Any], Any] | None = None) -> Any:
        if fetched.ok:
            value = transform(fetched.value) if transform else fetched.value
            self.facts[key] = value
            self.sources[key] = fetched.label()
            if fetched.error:
                self.gaps.append(f"{key}（部分）：{fetched.error}")
            return value
        self.gaps.append(f"{key}：{fetched.error or '未获取'}")
        self.sources[key] = fetched.label()
        return None
