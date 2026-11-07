"""脱敏：从导入到发给模型，全程敏感信息 0 条。"""

import io
from pathlib import Path

import pandas as pd
import pytest

from ashare_agent.exam import questions as Q
from ashare_agent.llm import LLMClient
from ashare_agent.mentor.chat import ChatAgent
from ashare_agent.mock_llm import MockOpenAIClient
from ashare_agent.portfolio import parse_holdings_file
from ashare_agent.privacy import MASK, guard_messages, is_sensitive_column, scrub_text
from ashare_agent.trades import parse_trades_file

FONT = Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")


def leaks(text: str) -> list[str]:
    flat = text.replace(" ", "")
    return [s for s in Q.SENSITIVE_STRINGS if s in text or s.replace(" ", "") in flat]


@pytest.mark.parametrize("raw,secret", [
    ("客户姓名：张三丰", "张三丰"),
    ("我叫张三丰，想问问持仓", "张三丰"),
    ("资金账号: 3080123456789", "3080123456789"),
    ("身份证 110101199003077777", "110101199003077777"),
    ("联系我 138-1234-5678", "138-1234-5678"),
    ("股东代码A123456789", "A123456789"),
    ("开户在华泰证券南京中山路营业部", "华泰证券南京中山路营业部"),
    ("卡号 6222 0212 3456 7890 123", "6222 0212 3456 7890 123"),
    ("邮箱 zsf@example.com", "zsf@example.com"),
    ("地址：南京市玄武区中山路18号", "南京市玄武区中山路18号"),
])
def test_scrub_text(raw, secret):
    out, found = scrub_text(raw)
    assert secret not in out and MASK in out and found


@pytest.mark.parametrize("text", ["600111 北方稀土 3000 股 成本 38.20", "我叫你帮我看看东方财富", "净利润 36.46亿，同比 136.55%",
                                  "2026-11-05 买入 1000 股", "总资产 383130 元"])
def test_scrub_keeps_normal_text(text):
    assert scrub_text(text)[0] == text


def test_sensitive_columns():
    for c in ("资金账号", "股东代码", "客户姓名", "身份证号", "营业部"):
        assert is_sensitive_column(c)
    for c in ("证券代码", "证券名称", "成交数量", "成本价"):
        assert not is_sensitive_column(c)


def test_trade_file_drops_sensitive():
    r = parse_trades_file("交割单.csv", Q.FAKE_TRADES_CSV.encode("gb18030"))
    assert len(r.trades) == 2 and r.privacy_hits > 0
    assert {"股东代码", "资金账号"} <= set(r.dropped_columns)
    assert not leaks(str([t.to_dict() for t in r.trades]))


def test_holdings_excel_drops_sensitive():
    df = pd.DataFrame({"证券代码": ["600111", "300059"], "证券名称": ["北方稀土", "东方财富"], "股票余额": [3000, 3000],
                       "可用余额": [3000, 2000], "成本价": [38.2, 20.795], "股东代码": ["A123456789", "0123456789"],
                       "资金账号": ["3080123456789"] * 2})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame([["客户：张三丰 资金账号：3080123456789"]]).to_excel(w, index=False, header=False, startrow=0)
        df.to_excel(w, index=False, startrow=2)
    r = parse_holdings_file("持仓.xlsx", buf.getvalue())
    assert [p.code for p in r.positions] == ["600111", "300059"]
    assert not leaks(str([p.to_dict() for p in r.positions]) + str(r.raw.to_dict()))


def test_guard_messages_is_last_line_of_defense():
    msgs = [{"role": "user", "content": Q.SENSITIVE_CHAT}, {"role": "user", "content": [{"type": "text", "text": "身份证 110101199003077777"}]}]
    guarded, found = guard_messages(msgs)
    assert not leaks(str(guarded)) and len(found) >= 4


def test_end_to_end_send_log_has_zero_sensitive(make_mentor):
    m = make_mentor(seed=False)
    st = m.storage
    st.add_trades(parse_trades_file("交割单.csv", Q.FAKE_TRADES_CSV.encode("gb18030")).trades)
    hp = parse_holdings_file("持仓.csv", Q.FAKE_HOLDINGS_CSV.encode("utf-8"))
    st.save_positions(hp.positions, as_of="2026-11-05", source="测试", cash=20000)
    m.health_check()
    m.daily_review()
    m.keep_decision("300059", buy_reason="我叫张三丰，资金账号 3080123456789，当初看好券商")
    ChatAgent(m).ask(Q.SENSITIVE_CHAT)
    sent = st.all_sent_text()
    assert len(st.send_logs(1000)) >= 4
    assert leaks(sent) == []


@pytest.mark.skipif(not FONT.exists(), reason="缺少中文字体，无法生成测试截图")
def test_screenshot_ocr_masks_before_sending(make_mentor):
    from PIL import Image, ImageDraw, ImageFont

    from ashare_agent.ocr import OCR_STRUCTURE_PROMPT, OCRUnavailable, analyze, parse_positions_from_rows, run_ocr
    from ashare_agent.privacy import mask_image

    lines = ["客户姓名：张三丰    资金账号：3080123456789", "手机：13812345678",
             "证券代码      证券名称      持仓数量      可用数量      成本价",
             "600111        北方稀土      3000          3000          38.200",
             "300059        东方财富      3000          2000          20.795"]
    img = Image.new("RGB", (1100, 60 * len(lines) + 40), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), 30)
    for i, t in enumerate(lines):
        d.text((30, 30 + 60 * i), t, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    try:
        res = analyze(run_ocr(buf.getvalue()))
    except OCRUnavailable as exc:
        pytest.skip(str(exc))
    assert not leaks(res.clean_text), res.clean_text
    assert res.sensitive_boxes
    codes = {p.code for p in parse_positions_from_rows(res.rows)}
    assert {"600111", "300059"} <= codes
    masked = Image.open(io.BytesIO(mask_image(buf.getvalue(), res.sensitive_boxes))).convert("L")
    box = res.sensitive_boxes[0]
    cx, cy = int(sum(p[0] for p in box) / 4), int(sum(p[1] for p in box) / 4)
    assert masked.getpixel((cx, cy)) < 30
    m = make_mentor(seed=False)
    m.llm.complete([{"role": "user", "content": OCR_STRUCTURE_PROMPT + res.clean_text}], purpose="截图文字整理")
    assert leaks(m.storage.all_sent_text()) == []
