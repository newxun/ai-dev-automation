"""模型接入：用真实的 openai SDK 走 HTTP 连本地模拟服务（OpenAI 兼容接口），验证换模型只需改 base_url/model/key。"""

import socket

import httpx2
import openai
import pytest

from ashare_agent.config import ModelProfile
from ashare_agent.llm import LLMClient, LLMError, ToolSpec
from ashare_agent.mock_llm import MockOpenAIClient, serve


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    port = free_port()
    srv = serve("127.0.0.1", port, background=True)
    yield f"http://127.0.0.1:{port}/v1"
    srv.shutdown()


def test_real_sdk_over_http(server, make_mentor):
    p = ModelProfile(name="本地模拟", provider="custom", base_url=server, model="mock-mentor", api_key="anything")
    ok, msg = LLMClient(p).test_connection()
    assert ok and "在线" in msg
    m = make_mentor()
    m._llm = LLMClient(p, logger=m._log_send)
    card = m.storage.card(m.health_check())
    assert not card["payload"]["extra"]["mentor_error"]
    assert "导师未参与" not in card["payload"]["mentor_note"]


def test_missing_key_is_friendly(make_mentor):
    p = ModelProfile(name="DeepSeek", provider="deepseek", api_key="")
    with pytest.raises(LLMError, match="没有 API Key"):
        LLMClient(p)
    m = make_mentor()
    m.config.profiles = {p.name: p}
    m.config.active_profile = p.name
    m._llm = None
    card = m.storage.card(m.health_check())  # 模型不可用时，程序部分照常出卡，并写明导师未参与
    assert "导师未参与" in card["payload"]["mentor_note"]


def _bad_request(msg: str) -> openai.BadRequestError:
    req = httpx2.Request("POST", "http://x/v1/chat/completions")
    return openai.BadRequestError(msg, response=httpx2.Response(400, request=req), body=None)


class NoToolsNoJsonClient(MockOpenAIClient):
    def _create(self, **kw):
        if "tools" in kw:
            raise _bad_request("tools are not supported by this model")
        if "response_format" in kw:
            raise _bad_request("response_format is not supported")
        return super()._create(**kw)


def test_fallback_when_tools_and_json_mode_unsupported():
    p = ModelProfile(name="x", provider="custom", base_url="http://x/v1", model="m", api_key="k")
    c = LLMClient(p, client=NoToolsNoJsonClient())
    res = c.run_with_tools([{"role": "user", "content": "你好"}], [ToolSpec("t", "d", {"type": "object", "properties": {}}, lambda: {})])
    assert res.used_tools is False and res.text
    data = c.complete_json([{"role": "user", "content": "【任务：次日做T预案】\n数据（JSON）：\n{\"stocks\": []}"}])
    assert data == {"plans": [], "skipped_comments": {}}


def test_send_log_records_every_request(make_mentor):
    m = make_mentor()
    m.health_check()
    logs = m.storage.send_logs()
    assert logs and logs[0]["purpose"] == "持仓体检" and logs[0]["model"] == "mock-mentor"
