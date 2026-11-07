from datetime import datetime

from ashare_agent.demo import demo_config, seed_demo
from ashare_agent.market import CN_TZ
from ashare_agent.mock_llm import MockOpenAIClient
from ashare_agent.scheduler import tick
from ashare_agent.web.app import create_app


def _state(tmp_path, when: str):
    now = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
    app = create_app(tmp_path / when.replace(" ", "_").replace(":", ""), demo=True, llm_client=MockOpenAIClient(), now_fn=lambda: now)
    s = app.state.s
    seed_demo(s.storage)
    s.save_config(demo_config())
    return s


def test_prepares_after_1545_on_trading_day(tmp_path):
    s = _state(tmp_path, "2026-11-05 15:50")
    assert tick(s) is True
    assert s.storage.get("prepared:2026-11-05")["holdings"] == 6
    assert tick(s) is False  # 同一天只准备一次


def test_skips_before_time_and_on_weekend(tmp_path):
    assert tick(_state(tmp_path, "2026-11-05 15:30")) is False
    assert tick(_state(tmp_path, "2026-11-07 16:00")) is False  # 周六
