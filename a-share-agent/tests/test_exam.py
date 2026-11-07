"""考卷本身的自检：模拟的「好学生」应全部通过自动评分；「坏学生」必须被判不及格，但做T硬规则依然 0 违规（程序把关）。"""

from ashare_agent.config import ModelProfile
from ashare_agent.exam import Exam, write_report
from ashare_agent.mock_llm import MockMentorModel, MockOpenAIClient

PROFILE = ModelProfile(name="模拟导师", provider="custom", base_url="http://mock/v1", model="mock-mentor", api_key="k")


def test_good_model_passes_auto_sections(tmp_path):
    rep = Exam(PROFILE, client=MockOpenAIClient(MockMentorModel("good")), workdir=tmp_path).run()
    s = rep["sections"]
    assert s["numbers"]["score"] == "10/10"
    assert s["faithfulness"]["score"] == "编造 0 次"
    assert s["privacy"]["score"] == "敏感信息 0 条"
    assert s["t_plans"]["passed"]
    skip = sum(1 for i in s["t_plans"]["items"] if i["decision"] == "不做")
    assert skip >= len(s["t_plans"]["items"]) / 2          # 约一半应该「不做」
    assert all(i["next_day"] for i in s["t_plans"]["items"])  # 每张预案都对照了次日真实走势
    assert s["stability"]["passed"] and s["scenarios"]["passed"]
    assert rep["auto_passed"]
    j, md = write_report(rep, tmp_path / "out")
    text = md.read_text(encoding="utf-8")
    assert "人工评分：____ / 5" in text and "全部通过" in text


def test_bad_model_fails_but_rules_hold(tmp_path):
    rep = Exam(PROFILE, client=MockOpenAIClient(MockMentorModel("bad")), workdir=tmp_path).run(
        ["numbers", "faithfulness", "t_plans", "scenarios"])
    s = rep["sections"]
    assert not s["numbers"]["passed"] and not s["faithfulness"]["passed"] and not s["scenarios"]["passed"]
    assert s["t_plans"]["passed"]
    assert not rep["auto_passed"]
