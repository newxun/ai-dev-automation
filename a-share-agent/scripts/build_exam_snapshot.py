"""从 AKShare 拉取真实历史数据，冻结成考卷快照 ashare_agent/exam/snapshot.json。

用法：python scripts/build_exam_snapshot.py [截止日期，默认 2026-11-06]
快照冻结后，考卷不受网络波动影响，每道题都能对照“后来实际发生了什么”。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ashare_agent.data_source import AkshareProvider  # noqa: E402

CODES = ["600111", "300750", "002594", "300059", "600900", "000831", "601398", "600519", "000001", "688981", "600036"]
NO_FINANCIALS = {"600036"}  # 故意缺失财务数据，考「查不到就说查不到」
NO_ANNOUNCEMENTS = ["600036"]
INDEXES = ["sh000300", "sh000001", "sz399001", "sz399006"]


def main(end: str = "2026-11-06") -> None:
    p = AkshareProvider(timeout=40)
    snap: dict = {"as_of": end, "daily": {}, "index": {}, "profile": {}, "financials": {}, "announcements": {},
                  "events": {}, "missing_announcements": NO_ANNOUNCEMENTS, "calendar": []}
    keep = ["date", "open", "high", "low", "close", "volume", "amount"]
    for code in CODES:
        d = p.daily(code, end, 160)
        if not d.ok:
            raise SystemExit(f"{code} 日线失败：{d.error}")
        snap["daily"][code] = d.value[keep].to_dict("records")
        prof = p.profile(code)
        if prof.ok:
            snap["profile"][code] = prof.value
        if code not in NO_FINANCIALS:
            fin = p.financials(code)
            if fin.ok:
                snap["financials"][code] = fin.value
        if code not in NO_ANNOUNCEMENTS:
            ann = p.announcements(code, "2026-09-01", end)
            if ann.ok:
                snap["announcements"][code] = ann.value
        print("ok", code, len(snap["daily"][code]))
    for sym in INDEXES:
        f = p.index_daily(sym, end, 160)
        snap["index"][sym] = f.value[["date", "open", "high", "low", "close", "volume"]].to_dict("records")
    snap["calendar"] = [x for x in p.calendar() if "2026-01-01" <= x <= "2026-12-31"]
    out = ROOT / "ashare_agent" / "exam" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("写入", out, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main(*sys.argv[1:])
