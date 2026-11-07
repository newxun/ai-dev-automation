from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TQDM_DISABLE", "1")

from ashare_agent.demo import DEMO_AS_OF, demo_config, demo_now, exam_provider, seed_demo  # noqa: E402
from ashare_agent.llm import LLMClient  # noqa: E402
from ashare_agent.mentor.core import Mentor  # noqa: E402
from ashare_agent.mock_llm import MockMentorModel, MockOpenAIClient  # noqa: E402
from ashare_agent.storage import Storage  # noqa: E402


@pytest.fixture(scope="session")
def provider():
    return exam_provider()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "t.sqlite")


@pytest.fixture
def make_mentor(tmp_path, provider):
    """make_mentor(as_of=..., seed=True, behavior="good") -> Mentor（模拟模型）"""
    counter = {"n": 0}

    def _make(as_of: str = DEMO_AS_OF, seed: bool = True, behavior: str = "good", storage: Storage | None = None,
              client=None) -> Mentor:
        counter["n"] += 1
        st = storage or Storage(tmp_path / f"m{counter['n']}.sqlite")
        cfg = demo_config()
        if seed:
            seed_demo(st, cfg.fees)
        m = Mentor(st, provider, cfg, now=demo_now(as_of))
        m._llm = LLMClient(cfg.active, client=client or MockOpenAIClient(MockMentorModel(behavior)), logger=m._log_send)
        return m

    return _make
