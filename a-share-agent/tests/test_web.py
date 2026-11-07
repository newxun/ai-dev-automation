"""网页：首次设密码、登录、7 天会话、退出、错 5 次锁 15 分钟、只允许局域网、Key 不外露、考卷把关、主线页面。"""

import re
import time

import pytest
from fastapi.testclient import TestClient

from ashare_agent.config import ModelProfile
from ashare_agent.demo import demo_config, seed_demo
from ashare_agent.mock_llm import MockOpenAIClient
from ashare_agent.web.app import create_app
from ashare_agent.web.auth import SESSION_COOKIE, is_lan_ip, verify_password

PW = "mentor-2026"


@pytest.fixture
def app(tmp_path):
    a = create_app(tmp_path / "home", demo=True, llm_client=MockOpenAIClient())
    seed_demo(a.state.s.storage)
    a.state.s.save_config(demo_config())
    return a


@pytest.fixture
def client(app):
    c = TestClient(app)
    r = c.post("/setup", data={"password": PW, "password2": PW})
    assert r.status_code == 200
    return c


def wait_job(c, r):
    m = re.search(r'data-job="(\w+)"', r.text)
    assert m, r.text[:500]
    for _ in range(200):
        j = c.get(f"/api/job/{m.group(1)}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    return c.get(j["url"])


def test_first_run_requires_password(app):
    c = TestClient(app)
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
    r = c.post("/setup", data={"password": "123", "password2": "123"})
    assert "至少 6 位" in r.text
    stored = app.state.s.storage.get("auth_password_hash")
    assert stored is None


def test_password_hashed_and_session_cookie(app, client):
    stored = app.state.s.storage.get("auth_password_hash")
    assert stored.startswith("scrypt$") and PW not in stored and verify_password(PW, stored)
    c = TestClient(app)
    r = c.post("/login", data={"password": PW, "next": "/"}, follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "Max-Age=604800" in cookie and "HttpOnly" in cookie
    assert c.get("/plans").status_code == 200
    c.post("/logout")
    assert c.get("/plans", follow_redirects=False).status_code == 303


def test_lockout_after_five_failures(app, client):
    c = TestClient(app)
    for i in range(5):
        r = c.post("/login", data={"password": "wrong", "next": "/"})
    assert "锁定" in r.text or "过多" in r.text
    r = c.post("/login", data={"password": PW, "next": "/"})  # 正确密码也要等 15 分钟
    assert "分钟后再试" in r.text
    assert c.get("/plans", follow_redirects=False).status_code == 303


def test_logout_all_devices(app, client):
    other = TestClient(app)
    other.post("/login", data={"password": PW, "next": "/"})
    assert other.get("/plans").status_code == 200
    client.post("/logout-all")
    assert other.get("/plans", follow_redirects=False).status_code == 303


def test_lan_only(app):
    assert is_lan_ip("192.168.1.8") and is_lan_ip("10.0.0.2") and is_lan_ip("127.0.0.1") and is_lan_ip("fe80::1")
    assert not is_lan_ip("8.8.8.8") and not is_lan_ip("2001:4860::8888")
    c = TestClient(app, client=("8.8.8.8", 50000))
    r = c.get("/login")
    assert r.status_code == 403 and "局域网" in r.text


def test_cross_origin_post_rejected(client):
    r = client.post("/run/health", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_api_key_never_shown(app, client):
    client.post("/settings/model", data={"name": "DeepSeek", "provider": "deepseek", "api_key": "sk-supersecret-9876", "persist_key": "1"})
    for path in ("/settings", "/", "/settings?tab=access"):
        html = client.get(path).text
        assert "sk-supersecret" not in html
    assert "9876" in client.get("/settings").text


def test_exam_gate_for_default_model(app, client):
    s = app.state.s
    cfg = s.config()
    cfg.exam_results["模拟导师"] = {"passed": True, "date": "2026-11-07 10:00", "sections": {}}
    cfg.upsert(ModelProfile(name="新模型", provider="custom", base_url="http://x/v1", model="m", api_key="k"), activate=False)
    s.save_config(cfg)
    r = client.post("/settings/model/activate", data={"name": "新模型"})
    assert "还没通过考卷" in r.text and s.config().active_profile == "模拟导师"


def test_main_flows_render(client):
    r = wait_job(client, client.post("/run/review"))
    assert "今日复盘" in r.text and "明天的做T预案" in r.text and "计划外" in r.text
    home = client.get("/").text
    assert "明日（2026-11-06）做T预案" in home and "明天不做T" in home and "仅供参考，不构成投资建议" in home
    t_card = re.search(r'href="/card/(\d+)"[^>]*>\s*<div class="card-head"><span class="kind">做T预案卡</span><span class="badge st-open">', home)
    assert t_card
    t = client.get(f"/card/{t_card.group(1)}").text
    for word in ("动作", "放弃条件", "收尾规则", "最坏情况", "反方理由", "历史粗验", "保本价差"):
        assert word in t
    r = wait_job(client, client.post("/run/health"))
    assert "逐只体检" in r.text and "需决策" in r.text
    r = wait_job(client, client.post("/chat", data={"question": "我的持仓里哪只最该处理？"}))
    assert "查了：持仓" in r.text
    assert "持仓" in client.get("/sendlog").text


def test_import_confirm_flow(client):
    from ashare_agent.exam import questions as Q

    r = client.post("/import/file", data={"kind": "trades"}, files={"file": ("交割单.csv", Q.FAKE_TRADES_CSV.encode("gb18030"))})
    assert "逐行确认成交" in r.text and "已去掉敏感列" in r.text and "张三丰" not in r.text
    token = re.search(r'action="/import/confirm/([\w-]+)"', r.text).group(1)
    r = client.post(f"/import/confirm/{token}", data={"n": "2", "quantity_0": "1000", "price_0": "19.38", "side_0": "buy",
                                                       "quantity_1": "500", "price_1": "29.40", "side_1": "sell"})
    assert r.status_code == 200 and "重复已跳过" in r.text   # 演示账户里已有同样的两笔
    r = client.post("/import/manual", data={"text": "600111 北方稀土 3000 38.2\n300059 2000 21.5"})
    token = re.search(r'action="/import/confirm/([\w-]+)"', r.text).group(1)
    r = client.post(f"/import/confirm/{token}", data={"n": "2", "code_0": "600111", "quantity_0": "3000", "cost_price_0": "38.2",
                                                       "code_1": "300059", "quantity_1": "2000", "cost_price_1": "21.5",
                                                       "as_of": "2026-11-05", "cash": "50000"})
    assert "已保存 2 只持仓" in r.text


def test_pwa_assets(client):
    assert client.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert client.get("/icon-512.png").headers["content-type"] == "image/png"
