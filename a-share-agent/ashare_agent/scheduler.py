"""收盘后定时准备数据：交易日到了设定时间（默认 15:45）自动预取持仓日线、指数、事件和公告。"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any


def due(now_hm: str, prepare_hm: str) -> bool:
    return now_hm >= prepare_hm


def tick(state: Any) -> bool:
    """检查一次；需要时执行准备。返回是否执行了。"""
    cfg = state.config()
    now = state.now_fn()
    m = state.mentor(cfg)
    today = now.strftime("%Y-%m-%d")
    if not m.provider.is_trading_day(today) or not due(now.strftime("%H:%M"), cfg.review_prepare_time):
        return False
    if state.storage.get(f"prepared:{today}"):
        return False
    positions, _ = state.storage.latest_positions()
    if not positions:
        return False
    m.prepare_close_materials()
    return True


def start_scheduler(state: Any, interval: float = 60.0) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                tick(state)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(interval)

    t = threading.Thread(target=loop, name="ashare-scheduler", daemon=True)
    t.start()
    return t
