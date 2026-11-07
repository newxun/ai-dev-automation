"""命令行入口。

    python -m ashare_agent serve [--port 8600] [--https] [--demo]   启动网页（电脑和手机都能访问）
    python -m ashare_agent exam [--mock] [--profile 名称]           用考卷考当前模型
    python -m ashare_agent mock-llm [--port 9876]                  启动本地模拟模型服务（无 key 演示用）
    python -m ashare_agent autostart                               开机自启的设置方法
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import DISCLAIMER, __version__
from .config import ConfigStore, data_dir


def make_cert(home: Path) -> tuple[Path, Path]:
    """生成局域网自签名证书（手机首次访问会提示不受信任，选择继续即可）。"""
    import datetime as dt
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from .web.app import lan_addresses

    cert, key = home / "tls-cert.pem", home / "tls-key.pem"
    if cert.exists() and key.exists():
        return cert, key
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "A股导师（本机）")])
    alt = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in lan_addresses()]
    now = dt.datetime.now(dt.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pk.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(now + dt.timedelta(days=3650)).add_extension(x509.SubjectAlternativeName(alt), critical=False)
         .sign(pk, hashes.SHA256()))
    key.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(key, 0o600)
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .web.app import create_app, lan_addresses

    home = Path(args.home) if args.home else data_dir()
    if args.demo:
        home = Path(args.home) if args.home else data_dir() / "demo"
    llm_client = None
    if args.mock_llm:
        from .mock_llm import MockOpenAIClient

        llm_client = MockOpenAIClient()
    app = create_app(home, demo=args.demo, llm_client=llm_client, port=args.port, https=args.https, scheduler=not args.demo)
    if args.demo:
        from .demo import demo_config, seed_demo

        st = app.state.s.storage
        if not st.latest_positions()[0]:
            seed_demo(st)
            store = app.state.s.cfg_store
            if not store.path.exists():
                store.save(demo_config())
    scheme = "https" if args.https else "http"
    print(f"A股导师 v{__version__}  ·  {DISCLAIMER}")
    print(f"数据目录：{home}")
    print(f"本机打开：{scheme}://127.0.0.1:{args.port}")
    for ip in lan_addresses():
        print(f"手机打开（同一 Wi-Fi）：{scheme}://{ip}:{args.port}")
    print("只允许局域网访问，请不要做端口映射/内网穿透。按 Ctrl+C 退出。")
    kw = {}
    if args.https:
        cert, key = make_cert(home)
        kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", **kw)


def cmd_exam(args: argparse.Namespace) -> None:
    from .exam import Exam, summary_for_config, write_report

    home = Path(args.home) if args.home else data_dir()
    store = ConfigStore(home / "config.json")
    cfg = store.load()
    client = None
    if args.mock:
        from .config import ModelProfile
        from .mock_llm import MockMentorModel, MockOpenAIClient

        profile = ModelProfile(name="模拟导师", provider="custom", base_url="http://mock/v1", model="mock-mentor", api_key="mock")
        client = MockOpenAIClient(MockMentorModel(args.mock_behavior))
    else:
        profile = cfg.profiles.get(args.profile) if args.profile else cfg.active
        if profile is None:
            sys.exit(f"没有名为「{args.profile}」的模型配置")
    ex = Exam(profile, client=client, workdir=home / "exam" / "work", base_config=cfg, progress=lambda s: print("  ·", s))
    print(f"开始考试：{profile.name}（{profile.model}）")
    rep = ex.run(args.sections.split(",") if args.sections else None)
    jpath, mpath = write_report(rep, home / "exam")
    for k, v in rep["sections"].items():
        print(f"{'✅' if v['passed'] else '❌'} {k:13s} {v['score']}")
    print("自动评分部分：", "全部通过" if rep["auto_passed"] else "未通过")
    print("报告：", mpath)
    if not args.mock:
        cfg = store.load()
        cfg.exam_results[profile.name] = summary_for_config(rep) | {"report": mpath.stem}
        store.save(cfg)
    sys.exit(0 if rep["auto_passed"] else 1)


def cmd_mock(args: argparse.Namespace) -> None:
    from .mock_llm import serve

    serve(args.host, args.port, args.behavior)


def cmd_autostart(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parent.parent
    py = sys.executable
    home = str(Path(args.home).resolve()) if args.home else ""
    cmd = f'"{py}" -m ashare_agent' + (f' --home "{home}"' if home else "") + f" serve --port {args.port}"
    if sys.platform.startswith("win"):
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
        target = startup / "ashare-agent.bat"
        text = f'@echo off\ncd /d "{root}"\nstart "" /min {cmd}\n'
    elif sys.platform == "darwin":
        target = Path.home() / "Library/LaunchAgents/com.ashare.agent.plist"
        text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.ashare.agent</string>
<key>WorkingDirectory</key><string>{root}</string>
<key>ProgramArguments</key><array><string>{py}</string><string>-m</string><string>ashare_agent</string>{f"<string>--home</string><string>{home}</string>" if home else ""}<string>serve</string><string>--port</string><string>{args.port}</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
"""
    else:
        target = Path.home() / ".config/systemd/user/ashare-agent.service"
        text = f"""[Unit]
Description=A股导师
After=network-online.target

[Service]
WorkingDirectory={root}
ExecStart={cmd}
Restart=on-failure

[Install]
WantedBy=default.target
"""
    if args.dry_run:
        print(f"将写入：{target}\n\n{text}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"已写入：{target}")
    if sys.platform == "darwin":
        print(f"执行一次：launchctl load {target}")
    elif not sys.platform.startswith("win"):
        print("执行一次：systemctl --user daemon-reload && systemctl --user enable --now ashare-agent")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="ashare_agent", description="A股导师（" + DISCLAIMER + "）")
    p.add_argument("--home", help="数据目录（默认 a-share-agent/data，或环境变量 ASHARE_AGENT_HOME）")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="启动网页")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8600)
    s.add_argument("--https", action="store_true", help="使用自签名 HTTPS")
    s.add_argument("--demo", action="store_true", help="演示模式：冻结行情 + 虚构账户（数据存放在 data/demo）")
    s.add_argument("--mock-llm", action="store_true", help="使用内置模拟模型（无 key 演示）")
    s.set_defaults(func=cmd_serve)
    e = sub.add_parser("exam", help="用考卷考模型")
    e.add_argument("--profile")
    e.add_argument("--mock", action="store_true", help="用模拟模型自检考卷流程")
    e.add_argument("--mock-behavior", default="good", choices=["good", "bad"])
    e.add_argument("--sections", help="只考部分，如 numbers,faithfulness")
    e.set_defaults(func=cmd_exam)
    mk = sub.add_parser("mock-llm", help="启动本地模拟模型服务")
    mk.add_argument("--host", default="127.0.0.1")
    mk.add_argument("--port", type=int, default=9876)
    mk.add_argument("--behavior", default="good", choices=["good", "bad"])
    mk.set_defaults(func=cmd_mock)
    a = sub.add_parser("autostart", help="设置开机自启")
    a.add_argument("--port", type=int, default=8600)
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(func=cmd_autostart)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
