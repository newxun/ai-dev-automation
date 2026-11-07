"""脱敏：敏感信息一进门就删掉，既不保存，也不发送。

三道防线：
1. 导入时按列名整列删除（资金账号、股东代码、客户姓名、合同/委托/成交编号……）。
2. 对剩余文字做特征扫描（身份证、手机号、银行卡、股东代码、长数字账号、邮箱、姓名、券商营业部……），替换成 [已隐藏]。
3. 每次发给模型前，对整个请求再扫描一遍兜底（llm.py 调用 guard_messages）。
"""

from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass

import pandas as pd

MASK = "[已隐藏]"

SENSITIVE_COLUMN_KEYWORDS = (
    "姓名", "客户", "户名", "开户人", "资金账号", "资金账户", "资金帐号", "账号", "帐号", "账户", "帐户",
    "股东", "身份证", "证件", "手机", "电话", "券商", "营业部", "银行", "卡号", "地址", "邮箱", "email",
    "合同编号", "合同号", "委托编号", "委托号", "委托序号", "成交编号", "成交号", "申请编号", "流水号", "订单号", "席位",
)

_KEY_VALUE_RE = re.compile(
    r"(资金账号|资金账户|资金帐号|客户号|客户代码|客户编号|账号|帐号|账户|帐户|股东代码|股东账户|股东帐户|证券账户|身份证号?|证件号码?|"
    r"手机号码?|联系电话|电话|银行卡号?|银行账号|卡号|合同编号|合同号|委托编号|委托号|成交编号|成交号|申请编号|流水号|委托序号|订单号)"
    r"(\s*[:：]?\s*)([A-Za-z0-9][A-Za-z0-9\-*]{3,})"
)
_NAME_RE = re.compile(r"(姓名|客户姓名|客户名称|户名|开户人|用户名|投资者姓名|客户)(\s*[:：]\s*)([\u4e00-\u9fa5·]{2,6})")
# 自我介绍里的姓名：要求名字后紧跟标点/空白/结尾，避免把「我叫你帮我看看」误遮
_SELF_NAME_RE = re.compile(r"(我叫|我名叫|名叫|我的名字是|我的名字叫|我姓名是|本人姓名|我是客户)(\s*)([\u4e00-\u9fa5·]{2,4})(?=[，,。.！!？?；;、\s]|$)")
_ADDRESS_RE = re.compile(r"(地址|住址|联系地址|通讯地址)(\s*[:：]\s*)(\S+)")
_BROKER_LABEL_RE = re.compile(r"(券商|开户券商|开户营业部|营业部|开户机构)(\s*[:：]\s*)(\S+)")
_BRANCH_RE = re.compile(
    r"[\u4e00-\u9fa5]{2,12}证券(?:股份)?(?:有限)?(?:责任)?(?:公司)?[\u4e00-\u9fa5()（）]{0,16}?(?:营业部|分公司)"
)
_BROKER_CORP_RE = re.compile(r"[\u4e00-\u9fa5]{2,10}证券(?:股份有限公司|有限责任公司|有限公司)")
_ID18_RE = re.compile(r"(?<![0-9A-Za-z])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9A-Za-z])")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_CARD_GROUPED_RE = re.compile(r"(?<!\d)\d{4}(?:[\s-]\d{4}){3,4}(?:[\s-]?\d{1,3})?(?!\d)")
_SH_HOLDER_RE = re.compile(r"(?<![0-9A-Za-z])[A-Z]\d{9}(?!\d)")
_LONG_DIGITS_RE = re.compile(r"(?<![\d.])\d{10,}(?![\d.])")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@dataclass
class Finding:
    kind: str
    sample: str


def _mask_group3(pattern: re.Pattern, kind: str, text: str, found: list[Finding]) -> str:
    def repl(m: re.Match) -> str:
        found.append(Finding(kind, m.group(3)))
        return f"{m.group(1)}{m.group(2)}{MASK}"

    return pattern.sub(repl, text)


def _mask_whole(pattern: re.Pattern, kind: str, text: str, found: list[Finding]) -> str:
    def repl(m: re.Match) -> str:
        found.append(Finding(kind, m.group(0)))
        return MASK

    return pattern.sub(repl, text)


def scrub_text(text: str) -> tuple[str, list[Finding]]:
    if not text:
        return text, []
    found: list[Finding] = []
    s = text
    s = _mask_group3(_NAME_RE, "姓名", s, found)
    s = _mask_group3(_SELF_NAME_RE, "姓名", s, found)
    s = _mask_group3(_ADDRESS_RE, "地址", s, found)
    s = _mask_group3(_BROKER_LABEL_RE, "券商/营业部", s, found)
    s = _mask_group3(_KEY_VALUE_RE, "账号/编号", s, found)
    s = _mask_whole(_BRANCH_RE, "券商营业部", s, found)
    s = _mask_whole(_BROKER_CORP_RE, "券商名称", s, found)
    s = _mask_whole(_EMAIL_RE, "邮箱", s, found)
    s = _mask_whole(_ID18_RE, "身份证号", s, found)
    s = _mask_whole(_CARD_GROUPED_RE, "银行卡号", s, found)
    s = _mask_whole(_PHONE_RE, "手机号", s, found)
    s = _mask_whole(_SH_HOLDER_RE, "股东代码", s, found)
    s = _mask_whole(_LONG_DIGITS_RE, "长数字账号/卡号", s, found)
    return s, found


def is_sensitive_column(name: object) -> bool:
    n = str(name or "").strip().lower()
    return any(k.lower() in n for k in SENSITIVE_COLUMN_KEYWORDS)


def scrub_dataframe(df: pd.DataFrame, keep_columns: tuple[str, ...] = ()) -> tuple[pd.DataFrame, list[str], list[Finding]]:
    """删除敏感列，并扫描其余文字单元格。返回 (干净的表, 删除的列, 扫描命中)。"""
    dropped = [c for c in df.columns if c not in keep_columns and is_sensitive_column(c)]
    clean = df.drop(columns=dropped).copy()
    findings: list[Finding] = []
    for col in clean.columns:
        if col in keep_columns:
            continue
        series = clean[col]
        if series.dtype != object and not pd.api.types.is_string_dtype(series):
            continue

        def _scrub(v: object) -> object:
            if isinstance(v, str) and v:
                out, f = scrub_text(v)
                findings.extend(f)
                return out
            return v

        clean[col] = series.map(_scrub).astype(object)
    return clean, [str(c) for c in dropped], findings


def scrub_obj(obj: object, findings: list[Finding] | None = None) -> object:
    findings = findings if findings is not None else []
    if isinstance(obj, str):
        out, f = scrub_text(obj)
        findings.extend(f)
        return out
    if isinstance(obj, dict):
        return {k: scrub_obj(v, findings) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_obj(v, findings) for v in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_obj(v, findings) for v in obj)
    return obj


def guard_messages(messages: list[dict]) -> tuple[list[dict], list[Finding]]:
    """发给模型前的兜底过滤：扫描所有文字内容（图片 data URL 不扫描，图片需事先打码并经用户确认）。"""
    findings: list[Finding] = []
    out = copy.deepcopy(messages)
    for m in out:
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = scrub_obj(content, findings)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    part["text"] = scrub_obj(part.get("text", ""), findings)
        for call in m.get("tool_calls") or []:
            fn = call.get("function", {})
            if isinstance(fn.get("arguments"), str):
                fn["arguments"] = scrub_obj(fn["arguments"], findings)
    return out, findings


SENSITIVE_HINT_WORDS = ("姓名", "客户", "账号", "帐号", "账户", "股东", "身份证", "手机", "电话", "银行", "卡号", "营业部", "地址", "邮箱", "合同", "委托编号", "成交编号")


def is_sensitive_ocr_text(text: str) -> bool:
    if any(w in text for w in SENSITIVE_HINT_WORDS):
        return True
    _, f = scrub_text(text)
    return bool(f)


def mask_image(data: bytes, boxes: list[list[list[float]]], pad: int = 4) -> bytes:
    """把敏感文字所在的区域涂黑，返回 PNG。boxes 为 OCR 返回的四点坐标。"""
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(data)).convert("RGB")
    draw = ImageDraw.Draw(img)
    for box in boxes:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        draw.rectangle([min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad], fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
