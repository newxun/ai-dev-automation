"""网页端（FastAPI + 服务端渲染）。电脑和手机（同一局域网）都能用。"""

from __future__ import annotations

import base64
import io
import json
import secrets
import socket
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import DISCLAIMER, __version__
from .. import cards as C
from ..config import PRESETS, AppConfig, ConfigStore, ModelProfile, data_dir
from ..data_source import AkshareProvider, MarketData
from ..demo import demo_now, exam_provider
from ..llm import LLMClient, LLMError
from ..market import FeeSettings, now_cn, trade_fee
from ..mentor.chat import ChatAgent
from ..mentor.core import Mentor
from ..portfolio import parse_holdings_file, positions_from_rows, to_number
from ..rules import InvestorProfile, UserRules
from ..storage import Storage
from ..trades import Trade, apply_trades, parse_trades_file, reconcile
from .auth import SESSION_COOKIE, SESSION_TTL, Auth, is_lan_ip

HERE = Path(__file__).resolve().parent
TOOL_LABELS = {"get_portfolio": "持仓", "get_stock_data": "个股数据", "calc_fee": "手续费计算", "calc_limit_prices": "涨跌停计算",
               "calc_sellable": "可卖数量", "calc_cost_after_add": "补仓成本", "calc_t_result": "做T结果", "calc_breakeven": "保本价差",
               "get_t_plan": "做T预案", "get_trade_stats": "交易统计", "get_open_cards": "进行中的卡片"}
PUBLIC_PATHS = ("/login", "/setup", "/manifest.webmanifest", "/sw.js", "/icon-192.png", "/icon-512.png", "/healthz")


def lan_addresses() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if is_lan_ip(ip) and not ip.startswith("127."))


class AppState:
    def __init__(self, home: Path, demo: bool = False, llm_client: Any | None = None,
                 provider: MarketData | None = None, now_fn: Callable[[], datetime] | None = None,
                 port: int = 8600, https: bool = False) -> None:
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.cfg_store = ConfigStore(self.home / "config.json")
        self.storage = Storage(self.home / "ashare.sqlite")
        self.auth = Auth(self.storage)
        self.demo = demo
        self.llm_client = llm_client
        self._provider = provider
        self.now_fn = now_fn or (demo_now if demo else now_cn)
        self.jobs: dict[str, dict] = {}
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.port = port
        self.https = https
        self._lock = threading.Lock()
        self.session_keys: dict[str, str] = {}  # 选择「不保存 key」时只放在内存里

    def config(self) -> AppConfig:
        cfg = self.cfg_store.load()
        for name, key in self.session_keys.items():
            if name in cfg.profiles and not cfg.profiles[name].api_key:
                cfg.profiles[name].api_key = key
                cfg.profiles[name].persist_key = False
        return cfg

    def save_config(self, cfg: AppConfig) -> None:
        self.cfg_store.save(cfg)

    def provider(self) -> MarketData:
        if self._provider is None:
            cfg = self.config()
            self._provider = exam_provider() if (self.demo or cfg.data_source == "snapshot") else AkshareProvider(self.storage)
        return self._provider

    def mentor(self, cfg: AppConfig | None = None) -> Mentor:
        cfg = cfg or self.config()
        m = Mentor(self.storage, self.provider(), cfg, now=self.now_fn())
        if self.llm_client is not None:
            m._llm = LLMClient(cfg.active, client=self.llm_client, logger=m._log_send)
        return m

    def submit(self, label: str, fn: Callable[[], str]) -> str:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "label": label, "status": "running", "started": time.time(), "url": None, "error": None}
        with self._lock:
            self.jobs[jid] = job
            for k in [k for k, v in self.jobs.items() if time.time() - v["started"] > 6 * 3600]:
                self.jobs.pop(k, None)

        def run() -> None:
            try:
                job["url"] = fn()
                job["status"] = "done"
            except ValueError as exc:
                job["status"], job["error"] = "error", str(exc)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                job["status"], job["error"] = "error", f"出错了：{exc}"
            job["seconds"] = round(time.time() - job["started"], 1)

        self.executor.submit(run)
        return jid


def create_app(home: Path | None = None, demo: bool = False, llm_client: Any | None = None,
               provider: MarketData | None = None, now_fn: Callable[[], datetime] | None = None,
               port: int = 8600, https: bool = False, scheduler: bool = False) -> FastAPI:
    state = AppState(home or data_dir(), demo, llm_client, provider, now_fn, port, https)
    app = FastAPI(title="A股导师", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.s = state
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    env = tpl.env
    env.globals.update(DISCLAIMER=DISCLAIMER, KINDS=C.KINDS, STATUS=C.STATUS_LABELS, version=__version__, demo=demo)
    env.filters["money"] = C.money
    env.filters["pct"] = lambda v, d=1: C.pct(v, d)
    env.filters["signed"] = lambda v: "" if v is None else ("up" if v > 0 else "down" if v < 0 else "")
    env.filters["tojson_pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=1, default=str)

    if scheduler:
        from ..scheduler import start_scheduler

        start_scheduler(state)

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        cfg = ctx.pop("cfg", None) or state.config()
        ctx.setdefault("flash", request.query_params.get("msg"))
        ctx.setdefault("err", request.query_params.get("err"))
        return tpl.TemplateResponse(request, name, {"cfg": cfg, "active": cfg.active, "exam_ok": cfg.exam_passed(cfg.active_profile),
                                                    "path": request.url.path, **ctx})

    def go(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
        if err:
            url += ("&" if "?" in url else "?") + "err=" + quote(err)
        return RedirectResponse(url, status_code=303)

    # ---------------- 访问控制 ----------------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        ip = request.client.host if request.client else None
        if not is_lan_ip(ip):
            return PlainTextResponse("只允许本机或局域网访问。请不要把端口映射到公网。", status_code=403)
        path = request.url.path
        if path.startswith("/static/") or path in PUBLIC_PATHS:
            return await call_next(request)
        if not state.auth.configured:
            return RedirectResponse("/setup", status_code=303)
        if not state.auth.valid(request.cookies.get(SESSION_COOKIE)):
            if path.startswith("/api/"):
                return JSONResponse({"error": "未登录"}, status_code=401)
            return RedirectResponse("/login?next=" + quote(str(request.url.path)), status_code=303)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return PlainTextResponse("来源不匹配，已拒绝。", status_code=403)
        return await call_next(request)

    def set_session(resp: Response, token: str) -> None:
        resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax", secure=state.https)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request):
        if state.auth.configured:
            return go("/login")
        return render(request, "setup.html")

    @app.post("/setup")
    def setup_submit(request: Request, password: str = Form(...), password2: str = Form(...)):
        if state.auth.configured:
            return go("/login")
        if password != password2:
            return go("/setup", err="两次输入的密码不一致")
        try:
            state.auth.set_password(password)
        except ValueError as exc:
            return go("/setup", err=str(exc))
        token, _ = state.auth.login(request.client.host, password, request.headers.get("user-agent", ""))
        resp = go("/onboarding", msg="密码已设置")
        set_session(resp, token)
        return resp

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if not state.auth.configured:
            return go("/setup")
        return render(request, "login.html", next=next if next.startswith("/") else "/")

    @app.post("/login")
    def login_submit(request: Request, password: str = Form(...), next: str = Form("/")):
        token, err = state.auth.login(request.client.host, password, request.headers.get("user-agent", ""))
        if not token:
            return go("/login?next=" + quote(next), err=err)
        resp = go(next if next.startswith("/") and not next.startswith("//") else "/")
        set_session(resp, token)
        return resp

    @app.post("/logout")
    def logout(request: Request):
        state.auth.logout(request.cookies.get(SESSION_COOKIE))
        resp = go("/login", msg="已退出")
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.post("/logout-all")
    def logout_all(request: Request):
        state.auth.logout_all()
        resp = go("/login", msg="所有设备都已退出")
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # ---------------- PWA ----------------
    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse({
            "name": "A股导师", "short_name": "A股导师", "start_url": "/", "display": "standalone",
            "background_color": "#f5f6f8", "theme_color": "#1f3a5f", "lang": "zh-CN",
            "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
        }, media_type="application/manifest+json")

    @app.get("/sw.js")
    def sw():
        return Response("self.addEventListener('fetch', () => {});", media_type="application/javascript")

    icon_cache: dict[int, bytes] = {}

    @app.get("/icon-{size}.png")
    def icon(size: int):
        if size not in (192, 512):
            return Response(status_code=404)
        if size not in icon_cache:
            from PIL import Image, ImageDraw, ImageFont

            img = Image.new("RGB", (size, size), "#1f3a5f")
            d = ImageDraw.Draw(img)
            font = None
            for fp in ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/System/Library/Fonts/PingFang.ttc", "C:/Windows/Fonts/msyh.ttc"):
                try:
                    font = ImageFont.truetype(fp, int(size * 0.42))
                    break
                except OSError:
                    continue
            font = font or ImageFont.load_default()
            d.text((size / 2, size / 2), "导师", fill="white", font=font, anchor="mm")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            icon_cache[size] = buf.getvalue()
        return Response(icon_cache[size], media_type="image/png")

    # ---------------- 后台任务 ----------------
    @app.get("/job/{jid}", response_class=HTMLResponse)
    def job_page(request: Request, jid: str):
        job = state.jobs.get(jid)
        if not job:
            return go("/", err="任务不存在或已过期")
        if job["status"] == "done" and job["url"]:
            return go(job["url"])
        return render(request, "job.html", job=job)

    @app.get("/api/job/{jid}")
    def job_api(jid: str):
        job = state.jobs.get(jid)
        if not job:
            return JSONResponse({"status": "missing"}, status_code=404)
        return {k: job.get(k) for k in ("status", "url", "error", "label")} | {"elapsed": round(time.time() - job["started"])}

    def card_url(cid: int) -> str:
        return f"/card/{cid}"

    # ---------------- 今天 ----------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        cfg = state.config()
        if not cfg.onboarded:
            return go("/onboarding")
        m = state.mentor(cfg)
        st = state.storage
        as_of, nd = m.as_of, m.next_day
        positions, meta = st.latest_positions()
        today_trades = st.trades(start=as_of, end=as_of)
        review = next(iter(st.cards(kind="review", as_of=as_of, status="info")), None)
        tplans = [c for c in st.cards(kind="t_plan", target_date=nd) if c["status"] != "replaced"]
        today_t = [c for c in st.cards(kind="t_plan", target_date=as_of) if c["status"] != "replaced"]
        health = next(iter(st.cards(kind="health", limit=1)), None)
        due = [c for c in st.cards(status="open", limit=200) if c["review_date"] and c["review_date"] <= as_of and c["kind"] in ("plan", "keep")]
        prepared = st.get(f"prepared:{as_of}")
        now = state.now_fn()
        return render(request, "home.html", cfg=cfg, as_of=as_of, next_day=nd, positions=positions, meta=meta,
                      today_trades=today_trades, review=review, tplans=tplans, today_t=today_t, health=health, due=due,
                      prepared=prepared, no_trades=st.get(f"no_trades:{as_of}"), now=now,
                      trading_today=m.provider.is_trading_day(now.strftime("%Y-%m-%d")))

    @app.post("/trades/none")
    def mark_no_trades():
        m = state.mentor()
        state.storage.set(f"no_trades:{m.as_of}", True)
        return go("/", msg="已记录：今天没有成交")

    def run_task(label: str, fn: Callable[[Mentor], str]) -> RedirectResponse:
        cfg = state.config()
        m = state.mentor(cfg)
        jid = state.submit(label, lambda: fn(m))
        return go(f"/job/{jid}")

    @app.post("/run/review")
    def run_review():
        return run_task("导师正在复盘今天并准备明天的做T预案", lambda m: card_url(m.daily_review()))

    @app.post("/run/health")
    def run_health():
        return run_task("导师正在给持仓做体检", lambda m: card_url(m.health_check()))

    @app.post("/run/tplans")
    def run_tplans():
        return run_task("导师正在准备明天的做T预案", lambda m: (m.t_plans(), "/plans?tab=t")[1])

    @app.post("/run/prepare")
    def run_prepare():
        return run_task("正在预取收盘数据", lambda m: (m.prepare_close_materials(), "/?msg=" + quote("收盘数据已备好"))[1])

    @app.post("/run/keep")
    def run_keep(code: str = Form(...), buy_reason: str = Form("")):
        return run_task("导师正在做去留决策", lambda m: card_url(m.keep_decision(code, buy_reason)))

    # ---------------- 卡片 ----------------
    @app.get("/card/{cid}", response_class=HTMLResponse)
    def card_page(request: Request, cid: int):
        c = state.storage.card(cid)
        if not c:
            return go("/", err="卡片不存在")
        related = []
        if c["kind"] == "review":
            related = [state.storage.card(x["card"]) for x in c["payload"].get("extra", {}).get("tomorrow_cards", [])]
            related = [x for x in related if x]
        return render(request, "card_page.html", c=c, related=related)

    @app.post("/card/{cid}/feedback")
    def card_feedback(cid: int, feedback: str = Form(...), note: str = Form("")):
        state.storage.update_card(cid, feedback=feedback, feedback_note=note[:500])
        return go(f"/card/{cid}", msg="收到反馈，导师会参考")

    @app.post("/card/{cid}/status")
    def card_status(cid: int, status: str = Form(...)):
        if status in C.STATUS_LABELS:
            state.storage.update_card(cid, status=status)
        return go(f"/card/{cid}", msg="状态已更新")

    @app.get("/plans", response_class=HTMLResponse)
    def plans(request: Request, tab: str = "open"):
        st = state.storage
        m = state.mentor()
        if tab == "t":
            items = [c for c in st.cards(kind="t_plan", limit=60) if c["status"] != "replaced"]
        elif tab == "all":
            items = [c for c in st.cards(limit=150) if c["status"] != "replaced"]
        else:
            items = [c for c in st.cards(limit=300) if c["status"] in ("open", "triggered")]
        return render(request, "plans.html", items=items, tab=tab, as_of=m.as_of)

    # ---------------- 问导师 ----------------
    @app.get("/chat", response_class=HTMLResponse)
    def chat_home(request: Request, card: int | None = None):
        chats = state.storage.chats()
        c = state.storage.card(card) if card else None
        return render(request, "chat.html", chats=chats, chat=None, messages=[], card=c)

    @app.get("/chat/{chat_id}", response_class=HTMLResponse)
    def chat_page(request: Request, chat_id: int):
        ch = state.storage.chat(chat_id)
        if not ch:
            return go("/chat")
        card = state.storage.card(ch["card_id"]) if ch.get("card_id") else None
        return render(request, "chat.html", chats=state.storage.chats(), chat=ch, messages=state.storage.messages(chat_id), card=card)

    @app.post("/chat")
    def chat_ask(question: str = Form(...), chat_id: int | None = Form(None), card_id: int | None = Form(None)):
        question = question.strip()
        if not question:
            return go(f"/chat/{chat_id}" if chat_id else "/chat")
        st = state.storage
        if not chat_id:
            chat_id = st.new_chat(question[:30], card_id)
        ch = st.chat(chat_id)
        history = []
        for msg in st.messages(chat_id)[-12:]:
            history.append({"role": msg["role"], "content": msg["content"]})
        card = st.card(ch["card_id"]) if ch and ch.get("card_id") else None
        from ..privacy import scrub_text

        st.add_message(chat_id, "user", scrub_text(question)[0])
        m = state.mentor()

        def work() -> str:
            agent = ChatAgent(m)
            res = agent.ask(question, history, card)
            names = list(dict.fromkeys(TOOL_LABELS.get(t["name"], t["name"]) for t in res.get("tools", []) if t["name"] != "submit_card"))
            st.add_message(chat_id, "assistant", res["text"], {"tools": names, "cards": res["cards"]})
            return f"/chat/{chat_id}#end"

        jid = state.submit("导师正在查数据、算数、思考", work)
        return go(f"/job/{jid}")

    # ---------------- 持仓与导入 ----------------
    @app.get("/portfolio", response_class=HTMLResponse)
    def portfolio(request: Request):
        m = state.mentor()
        positions, meta = state.storage.latest_positions()
        pv = m.portfolio() if positions else None
        keep_cards = {c["code"]: c for c in reversed(state.storage.cards(kind="keep", limit=100))}
        return render(request, "portfolio.html", pv=pv, meta=meta, keep_cards=keep_cards, as_of=m.as_of)

    @app.post("/portfolio/cash")
    def set_cash(cash: str = Form(...)):
        v = to_number(cash)
        if v is None or v < 0:
            return go("/portfolio", err="现金金额无效")
        state.storage.set_cash(v)
        return go("/portfolio", msg="可用资金已更新")

    @app.get("/import", response_class=HTMLResponse)
    def import_page(request: Request, kind: str = "holdings"):
        m = state.mentor()
        return render(request, "import.html", kind=kind, as_of=m.as_of)

    def pending_save(data: dict) -> str:
        token = secrets.token_urlsafe(8)
        state.storage.set(f"pending:{token}", data)
        return token

    @app.post("/import/file")
    async def import_file(kind: str = Form("auto"), file: UploadFile = File(...)):
        data = await file.read()
        name = file.filename or "upload.csv"
        cfg = state.config()
        errors = []
        if kind in ("auto", "trades"):
            try:
                tr = parse_trades_file(name, data, cfg.fees)
                rows = [t.to_dict() for t in tr.trades]
                tok = pending_save({"kind": "trades", "rows": rows, "warnings": tr.warnings, "dropped": tr.dropped_columns,
                                    "hits": tr.privacy_hits, "filename": name})
                return go(f"/import/confirm/{tok}")
            except ValueError as exc:
                errors.append(str(exc))
        if kind in ("auto", "holdings"):
            try:
                hp = parse_holdings_file(name, data)
                rows = [p.to_dict() for p in hp.positions]
                tok = pending_save({"kind": "holdings", "rows": rows, "warnings": hp.warnings, "dropped": hp.dropped_columns,
                                    "hits": hp.privacy_hits, "filename": name, "cash": hp.cash})
                return go(f"/import/confirm/{tok}")
            except ValueError as exc:
                errors.append(str(exc))
        return go(f"/import?kind={kind}", err="；".join(errors[-1:]) or "无法识别文件")

    @app.post("/import/manual")
    def import_manual(text: str = Form(...)):
        rows = []
        for line in text.splitlines():
            parts = [p for p in line.replace("，", " ").replace(",", " ").replace("\t", " ").split() if p]
            if not parts:
                continue
            code = next((p for p in parts if p.isdigit() and len(p) == 6), None)
            nums = [p for p in parts if p != code and to_number(p) is not None]
            names = [p for p in parts if p != code and to_number(p) is None]
            rows.append({"code": code or parts[0], "name": names[0] if names else "", "quantity": nums[0] if nums else None,
                         "cost_price": nums[1] if len(nums) > 1 else None, "available": None})
        if not rows:
            return go("/import", err="没有识别到内容。每行写：代码 数量 成本价")
        tok = pending_save({"kind": "holdings", "rows": rows, "warnings": [], "dropped": [], "hits": 0, "filename": "手动录入"})
        return go(f"/import/confirm/{tok}")

    @app.post("/import/screenshot")
    async def import_screenshot(file: UploadFile = File(...)):
        from ..ocr import OCRUnavailable, analyze, parse_positions_from_rows, run_ocr

        data = await file.read()
        try:
            res = analyze(run_ocr(data))
        except OCRUnavailable as exc:
            return go("/import?kind=screenshot", err=f"本地文字识别不可用：{exc}")
        positions = parse_positions_from_rows(res.rows)
        warnings = [f"本地识别出 {len(res.lines)} 段文字，已遮盖 {res.privacy_hits} 处敏感信息。请逐行核对。"]
        tok_data = {"kind": "holdings", "rows": [p.to_dict() for p in positions], "warnings": warnings, "dropped": [],
                    "hits": res.privacy_hits, "filename": "截图", "ocr_text": res.clean_text}
        from ..privacy import mask_image

        tok_data["masked_png"] = base64.b64encode(mask_image(data, res.sensitive_boxes)).decode()
        tok = pending_save(tok_data)
        return go(f"/import/confirm/{tok}")

    @app.post("/import/ocr-llm/{token}")
    def import_ocr_llm(token: str):
        """本地解析不准时：只把脱敏后的识别文字发给模型整理。"""
        from ..ocr import OCR_STRUCTURE_PROMPT, extract_json, positions_from_llm_json

        pend = state.storage.get(f"pending:{token}")
        if not pend or not pend.get("ocr_text"):
            return go("/import", err="导入已过期，请重新上传")
        m = state.mentor()

        def work() -> str:
            text = m.llm.complete([{"role": "user", "content": OCR_STRUCTURE_PROMPT + pend["ocr_text"]}], purpose="截图文字整理")
            positions, warns = positions_from_llm_json(extract_json(text))
            pend["rows"] = [p.to_dict() for p in positions]
            pend["warnings"] = pend["warnings"] + ["已用模型整理（只发送了脱敏后的文字）。"] + warns
            state.storage.set(f"pending:{token}", pend)
            return f"/import/confirm/{token}"

        return go(f"/job/{state.submit('模型正在整理识别文字', work)}")

    @app.post("/import/vision/{token}")
    def import_vision(token: str, confirm: str = Form("")):
        """兜底：用户确认后，把已自动打码的截图发给视觉模型。"""
        from ..ocr import OCR_STRUCTURE_PROMPT, extract_json, positions_from_llm_json

        pend = state.storage.get(f"pending:{token}")
        if not pend or not pend.get("masked_png"):
            return go("/import", err="导入已过期，请重新上传")
        if confirm != "yes":
            return go(f"/import/confirm/{token}", err="请先勾选确认打码后的图片里没有个人信息")
        m = state.mentor()
        if not m.config.active.vision_enabled:
            return go(f"/import/confirm/{token}", err="当前模型不支持看图，请在设置里换成支持视觉的模型")

        def work() -> str:
            img = base64.b64decode(pend["masked_png"])
            text = m.llm.vision_complete(OCR_STRUCTURE_PROMPT.split("识别文字：")[0], [(img, "image/png")])
            positions, warns = positions_from_llm_json(extract_json(text))
            pend["rows"] = [p.to_dict() for p in positions]
            pend["warnings"] = pend["warnings"] + ["已用视觉模型识别（发送的是自动打码后的图片）。"] + warns
            state.storage.set(f"pending:{token}", pend)
            return f"/import/confirm/{token}"

        return go(f"/job/{state.submit('视觉模型正在识别截图', work)}")

    @app.get("/import/confirm/{token}", response_class=HTMLResponse)
    def import_confirm(request: Request, token: str):
        pend = state.storage.get(f"pending:{token}")
        if not pend:
            return go("/import", err="导入已过期，请重新上传")
        m = state.mentor()
        positions, meta = state.storage.latest_positions()
        cash = pend.get("cash") if pend.get("cash") is not None else (meta["cash"] if meta else "")
        return render(request, "import_confirm.html", token=token, p=pend, as_of=m.as_of, cash=cash,
                      has_positions=bool(positions))

    @app.post("/import/confirm/{token}")
    async def import_confirm_submit(request: Request, token: str):
        pend = state.storage.get(f"pending:{token}")
        if not pend:
            return go("/import", err="导入已过期，请重新上传")
        form = await request.form()
        n = int(form.get("n", 0))
        st = state.storage
        cfg = state.config()
        m = state.mentor(cfg)
        if pend["kind"] == "holdings":
            rows = []
            for i in range(n):
                if form.get(f"del_{i}"):
                    continue
                rows.append({k: form.get(f"{k}_{i}") for k in ("code", "name", "quantity", "available", "cost_price")})
            positions, errors = positions_from_rows(rows)
            if errors:
                return go(f"/import/confirm/{token}", err="；".join(errors))
            if not positions:
                return go(f"/import/confirm/{token}", err="没有可保存的持仓")
            cash = to_number(form.get("cash"))
            as_of = str(form.get("as_of") or m.as_of)
            st.save_positions(positions, as_of=as_of, source=pend["filename"], cash=cash)
            st.set(f"pending:{token}", None)
            prev = st.positions_before(as_of)
            notes = reconcile(prev, st.trades(start=as_of, end=as_of), positions) if prev else []
            msg = f"已保存 {len(positions)} 只持仓（{as_of}）"
            return go("/portfolio", msg=msg + ("。对账提示：" + "；".join(notes[:3]) if notes else ""))
        trades = []
        for i in range(n):
            if form.get(f"del_{i}"):
                continue
            base = dict(pend["rows"][i])
            base["quantity"] = int(to_number(form.get(f"quantity_{i}")) or base["quantity"])
            base["price"] = float(to_number(form.get(f"price_{i}")) or base["price"])
            base["side"] = form.get(f"side_{i}") or base["side"]
            t = Trade(**{k: v for k, v in base.items() if k in Trade.__dataclass_fields__})
            t.amount = round(t.quantity * t.price, 2)
            if t.fee_estimated:
                t.commission = trade_fee(t.code, t.price, t.quantity, t.side, cfg.fees)
            trades.append(t)
        added, dup = st.add_trades(trades)
        st.set(f"pending:{token}", None)
        msg = f"已导入 {added} 笔成交" + (f"，{dup} 笔重复已跳过" if dup else "")
        if form.get("update_positions") and trades:
            positions, meta = st.latest_positions()
            new_trades = [t for t in trades if not meta or t.trade_date >= meta["as_of"]]
            if meta and new_trades:
                newer = sorted({t.trade_date for t in new_trades})
                newp, notes = apply_trades(positions, [t for t in new_trades if t.trade_date > meta["as_of"]])
                cash = meta["cash"] or 0.0
                for t in new_trades:
                    if t.trade_date > meta["as_of"]:
                        cash += (-1 if t.side == "buy" else 1) * t.amount - t.fee_total
                st.save_positions(newp, as_of=max(newer[-1], meta["as_of"]), source="按成交更新", cash=round(cash, 2))
                msg += "，持仓已按成交更新" + ("（" + "；".join(notes[:2]) + "）" if notes else "")
        return go("/", msg=msg)

    # ---------------- 交易前对话 ----------------
    @app.get("/pretrade", response_class=HTMLResponse)
    def pretrade_page(request: Request, code: str = "", side: str = "buy"):
        chk = None
        if code:
            try:
                chk = state.mentor().pretrade_check(code.strip(), side)
            except Exception as exc:  # noqa: BLE001
                return go("/pretrade", err=f"检查失败：{exc}")
        return render(request, "pretrade.html", code=code, side=side, chk=chk)

    @app.post("/pretrade")
    def pretrade_submit(code: str = Form(...), side: str = Form("buy"), reason: str = Form(...), gate_reason: str = Form(""),
                        price: str = Form(""), stop: str = Form(""), qty: str = Form("")):
        if len(reason.strip()) < 4:
            return go(f"/pretrade?code={code}&side={side}", err="请写下你的交易理由")
        m = state.mentor()
        chk = m.pretrade_check(code, side)
        if chk["gates"] and len(gate_reason.strip()) < 10:
            return go(f"/pretrade?code={code}&side={side}", err="触发了冷静关卡：请写下至少 10 个字，说明为什么现在必须做")
        p, s, q = to_number(price), to_number(stop), to_number(qty)
        return run_task("导师正在审这笔交易", lambda mm: card_url(mm.pretrade(code, side, reason, gate_reason, p, s, int(q) if q else None)))

    # ---------------- 交割单体检 ----------------
    @app.get("/report", response_class=HTMLResponse)
    def report(request: Request):
        trades = state.storage.trades()
        rep = state.mentor().trade_report() if trades else None
        return render(request, "report.html", rep=rep, n=len(trades))

    # ---------------- 访谈（画像 + 硬规则） ----------------
    @app.get("/onboarding", response_class=HTMLResponse)
    def onboarding(request: Request):
        return render(request, "onboarding.html")

    @app.post("/onboarding")
    async def onboarding_submit(request: Request):
        form = await request.form()
        cfg = state.config()
        g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
        prof = InvestorProfile(capital_range=g("capital_range"), experience_years=g("experience_years"), max_drawdown=g("max_drawdown"),
                               holding_period=g("holding_period"), can_watch=g("can_watch"), t_experience=g("t_experience"),
                               mistakes="、".join(form.getlist("mistakes")) + (("；" + g("mistakes_other")) if g("mistakes_other") else ""),
                               goal=g("goal"), notes=g("notes"))
        prof.summary = (f"{prof.can_watch or '盯盘时间不详'}；炒股{prof.experience_years or '年限不详'}；能承受最大回撤 {prof.max_drawdown or '未说明'}；"
                        f"常犯错误：{prof.mistakes or '未说明'}；目标：{prof.goal or '未说明'}。")
        dd = {"5%": 0.01, "10%": 0.015, "20%": 0.02, "30%": 0.03}.get(prof.max_drawdown, 0.02)
        rules = UserRules(max_position_pct=float(g("max_position_pct") or 30) / 100, max_trade_loss_pct=dd,
                          allow_st=bool(form.get("allow_st")), allow_bj=bool(form.get("allow_bj")),
                          t_max_ratio=float(g("t_max_ratio") or 33.34) / 100, chase_pct=0.05, cooldown_days=1,
                          custom=[x.strip() for x in str(form.get("custom", "")).splitlines() if x.strip()][:5])
        cfg.profile, cfg.rules, cfg.onboarded = prof, rules, True
        state.save_config(cfg)
        return go("/settings" if cfg.exam_results else "/settings?tab=model", msg="画像和硬规则已保存。下一步：配置模型并用考卷试考")

    # ---------------- 设置 ----------------
    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, tab: str = "model"):
        cfg = state.config()
        urls = [f"{'https' if state.https else 'http'}://{ip}:{state.port}" for ip in lan_addresses()]
        qr_svg = ""
        if urls:
            import qrcode
            import qrcode.image.svg

            qr_svg = qrcode.make(urls[0], image_factory=qrcode.image.svg.SvgPathImage, box_size=8).to_string(encoding="unicode")
        reports = sorted((state.home / "exam").glob("exam-*.json"), reverse=True)[:5] if (state.home / "exam").exists() else []
        return render(request, "settings.html", cfg=cfg, tab=tab, presets=PRESETS, urls=urls, qr_svg=qr_svg,
                      sessions=state.storage.session_count(), reports=[r.stem for r in reports])

    @app.post("/settings/model")
    def save_model(name: str = Form(...), provider: str = Form("custom"), base_url: str = Form(""), model: str = Form(""),
                   api_key: str = Form(""), persist_key: str = Form(""), supports_vision: str = Form(""), original: str = Form("")):
        cfg = state.config()
        old = cfg.profiles.get(original or name)
        preset = PRESETS.get(provider)
        p = ModelProfile(name=name.strip(), provider=provider, base_url=base_url.strip() or (preset.base_url if preset else ""),
                         model=model.strip() or (preset.models[0] if preset and preset.models else ""),
                         api_key=api_key.strip() or (old.api_key if old else ""), persist_key=bool(persist_key),
                         supports_vision=bool(supports_vision) if supports_vision else None)
        if original and original != p.name and original in cfg.profiles:
            cfg.profiles.pop(original)
            if cfg.active_profile == original:
                cfg.active_profile = p.name
        first = not any(cfg.exam_passed(n) for n in cfg.profiles)
        cfg.upsert(p, activate=first)
        if p.api_key and not p.persist_key:
            state.session_keys[p.name] = p.api_key
        state.save_config(cfg)
        return go("/settings?tab=model", msg=f"已保存「{p.name}」。请先「测试连接」，再「用考卷试考」")

    @app.post("/settings/model/test")
    def test_model(name: str = Form(...)):
        cfg = state.config()
        p = cfg.profiles.get(name)
        if not p:
            return go("/settings?tab=model", err="配置不存在")
        try:
            client = LLMClient(p, client=state.llm_client, logger=lambda *a: state.storage.log_send(p.name, p.model, "测试连接", a[1], a[2]))
            ok, msg = client.test_connection()
        except LLMError as exc:
            ok, msg = False, str(exc)
        return go("/settings?tab=model", **({"msg": f"「{name}」连接成功：{msg}"} if ok else {"err": f"「{name}」连接失败：{msg}"}))

    @app.post("/settings/model/activate")
    def activate_model(name: str = Form(...), force: str = Form("")):
        cfg = state.config()
        if name not in cfg.profiles:
            return go("/settings?tab=model", err="配置不存在")
        if not cfg.exam_passed(name) and any(cfg.exam_passed(n) for n in cfg.profiles) and not force:
            return go("/settings?tab=model", err=f"「{name}」还没通过考卷，不能设为默认。先点「用考卷试考」")
        cfg.switch(name)
        state.save_config(cfg)
        return go("/settings?tab=model", msg=f"已切换到「{name}」")

    @app.post("/settings/model/delete")
    def delete_model(name: str = Form(...)):
        cfg = state.config()
        try:
            cfg.remove(name)
        except ValueError as exc:
            return go("/settings?tab=model", err=str(exc))
        state.save_config(cfg)
        return go("/settings?tab=model", msg=f"已删除「{name}」")

    @app.post("/settings/exam")
    def run_exam(name: str = Form(...)):
        from ..exam import Exam, summary_for_config, write_report

        cfg = state.config()
        p = cfg.profiles.get(name)
        if not p:
            return go("/settings?tab=model", err="配置不存在")
        job_holder: dict = {}

        def work() -> str:
            ex = Exam(p, client=state.llm_client, workdir=state.home / "exam" / "work", base_config=cfg,
                      progress=lambda s: state.jobs[job_holder["id"]].update(label=f"考卷进行中：{s}"))
            rep = ex.run()
            _, md = write_report(rep, state.home / "exam")
            c2 = state.config()
            c2.exam_results[name] = summary_for_config(rep) | {"report": md.stem}
            if rep["auto_passed"] and not any(c2.exam_passed(n) for n in c2.profiles if n != name):
                c2.active_profile = name
            state.save_config(c2)
            return f"/exam/{md.stem}"

        jid = state.submit(f"「{name}」正在考试（约需几分钟）", work)
        job_holder["id"] = jid
        return go(f"/job/{jid}")

    @app.get("/exam/{stem}", response_class=HTMLResponse)
    def exam_report(request: Request, stem: str):
        path = state.home / "exam" / f"{Path(stem).name}.json"
        if not path.exists():
            return go("/settings", err="报告不存在")
        rep = json.loads(path.read_text(encoding="utf-8"))
        from ..exam.runner import SECTION_NAMES

        scores = state.storage.get(f"human_scores:{stem}", {})
        return render(request, "exam.html", rep=rep, stem=stem, names=SECTION_NAMES, scores=scores)

    @app.post("/exam/{stem}/scores")
    async def exam_scores(request: Request, stem: str):
        form = await request.form()
        scores = {k[6:]: int(v) for k, v in form.items() if k.startswith("score_") and str(v).isdigit()}
        state.storage.set(f"human_scores:{stem}", scores)
        avg = sum(scores.values()) / len(scores) if scores else 0
        return go(f"/exam/{stem}", msg=f"已保存人工评分，均分 {avg:.1f}（通过线 4.0）")

    @app.post("/settings/fees")
    def save_fees(commission_rate: str = Form(...), min_commission: str = Form("5"), waive_min: str = Form("")):
        cfg = state.config()
        rate = to_number(commission_rate)
        if rate is None or not (0 <= rate <= 3):
            return go("/settings?tab=fees", err="佣金费率无效（单位：万分之几）")
        cfg.fees = FeeSettings(commission_rate=rate / 10000, min_commission=0.0 if waive_min else float(to_number(min_commission) or 5))
        state.save_config(cfg)
        return go("/settings?tab=fees", msg="费率已保存")

    @app.post("/settings/rules")
    async def save_rules(request: Request):
        form = await request.form()
        cfg = state.config()
        f = lambda k, d: float(to_number(form.get(k)) if to_number(form.get(k)) is not None else d)  # noqa: E731
        cfg.rules = UserRules(max_position_pct=f("max_position_pct", 30) / 100, max_trade_loss_pct=f("max_trade_loss_pct", 2) / 100,
                              allow_st=bool(form.get("allow_st")), allow_bj=bool(form.get("allow_bj")),
                              t_max_ratio=f("t_max_ratio", 33.34) / 100, chase_pct=f("chase_pct", 5) / 100,
                              cooldown_days=int(f("cooldown_days", 1)),
                              custom=[x.strip() for x in str(form.get("custom", "")).splitlines() if x.strip()][:5])
        cfg.review_prepare_time = str(form.get("review_prepare_time") or "15:45")
        state.save_config(cfg)
        return go("/settings?tab=rules", msg="规则已保存")

    @app.post("/settings/password")
    def change_password(request: Request, old: str = Form(...), new: str = Form(...)):
        from .auth import verify_password

        if not verify_password(old, state.storage.get("auth_password_hash", "")):
            return go("/settings?tab=access", err="原密码不对")
        try:
            state.auth.set_password(new)
        except ValueError as exc:
            return go("/settings?tab=access", err=str(exc))
        token, _ = state.auth.login(request.client.host, new, request.headers.get("user-agent", ""))
        resp = go("/settings?tab=access", msg="密码已修改，其他设备需要重新登录")
        set_session(resp, token)
        return resp

    @app.get("/sendlog", response_class=HTMLResponse)
    def sendlog(request: Request):
        return render(request, "sendlog.html", logs=state.storage.send_logs(200))

    return app
