"""OpenAI 兼容接口封装：普通对话、流式输出、图片输入、工具调用（不支持时自动降级）。"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import openai

from .config import ModelProfile
from .privacy import guard_messages

SendLogger = Callable[[str, list, int], None]


class LLMError(RuntimeError):
    pass


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, LLMError):
        return str(exc)
    if isinstance(exc, openai.AuthenticationError):
        return "API Key 无效或未配置，请在「设置 → 模型」里检查。"
    if isinstance(exc, openai.PermissionDeniedError):
        return "没有权限调用该模型（可能未开通或余额不足）。"
    if isinstance(exc, openai.NotFoundError):
        return "接口地址或模型名不正确（404），请核对 base_url 和模型名。"
    if isinstance(exc, openai.RateLimitError):
        return "请求过于频繁或账户额度不足（429），请稍后再试或检查余额。"
    if isinstance(exc, openai.APITimeoutError):
        return "模型响应超时，请稍后重试或换一个更快的模型。"
    if isinstance(exc, openai.APIConnectionError):
        return "无法连接到模型服务，请检查 base_url 和网络。"
    if isinstance(exc, openai.BadRequestError):
        return f"请求被模型服务拒绝（400）：{_short(exc)}"
    if isinstance(exc, openai.APIStatusError):
        return f"模型服务返回错误（{exc.status_code}）：{_short(exc)}"
    return f"调用模型出错：{exc}"


def _short(exc: Exception, n: int = 200) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    return msg[:n]


def _looks_like_tool_unsupported(exc: Exception) -> bool:
    if not isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError, openai.NotFoundError)):
        return False
    msg = str(exc).lower()
    return any(k in msg for k in ("tool", "function", "not support", "unsupported", "invalid"))


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Any]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass
class ToolRunResult:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    used_tools: bool = True


class LLMClient:
    def __init__(self, profile: ModelProfile, client: Any | None = None, timeout: float = 120.0,
                 logger: SendLogger | None = None) -> None:
        self.profile = profile
        self.logger = logger
        if client is not None:
            self._client = client
        else:
            api_key = profile.resolve_api_key()
            if not api_key:
                raise LLMError(
                    f"当前模型配置「{profile.name}」没有 API Key。请在「设置 → 模型」里填写，"
                    f"或设置环境变量 {profile.env_key_name() or 'ASHARE_LLM_API_KEY'}。"
                )
            if not profile.base_url:
                raise LLMError("请先填写 base_url。")
            self._client = openai.OpenAI(api_key=api_key, base_url=profile.base_url, timeout=timeout, max_retries=1)

    def _create(self, purpose: str = "对话", **kwargs: Any) -> Any:
        """所有请求的唯一出口：先做脱敏兜底，再写发送记录，最后发给模型。"""
        guarded, findings = guard_messages(kwargs["messages"])
        kwargs["messages"] = guarded
        kwargs.setdefault("model", self.profile.model)
        kwargs.setdefault("temperature", self.profile.temperature)
        if self.logger is not None:
            self.logger(purpose, guarded, len(findings))
        return self._client.chat.completions.create(**kwargs)

    def complete_json(self, messages: list[dict], purpose: str = "任务", validate: Callable[[dict], list[str]] | None = None,
                      retries: int = 1) -> dict:
        """要求模型输出 JSON；先用 JSON 模式，不支持就退回普通模式再提取。校验不通过时带着错误重试。"""
        from .ocr import extract_json

        convo = list(messages)
        last_err = ""
        for attempt in range(retries + 1):
            try:
                try:
                    resp = self._create(purpose=purpose, messages=convo, response_format={"type": "json_object"})
                except openai.BadRequestError:
                    resp = self._create(purpose=purpose, messages=convo)
            except Exception as exc:  # noqa: BLE001
                raise LLMError(friendly_error(exc)) from exc
            text = (resp.choices[0].message.content or "").strip()
            try:
                data = extract_json(text)
                if not isinstance(data, dict):
                    raise ValueError("需要 JSON 对象")
                problems = validate(data) if validate else []
                if not problems:
                    return data
                last_err = "；".join(problems)
            except ValueError as exc:
                last_err = str(exc)
            if attempt < retries:
                convo = convo + [
                    {"role": "assistant", "content": text[:4000]},
                    {"role": "user", "content": f"你的输出有问题：{last_err}。请严格按要求重新输出完整 JSON。"},
                ]
        raise LLMError(f"模型输出不符合要求：{last_err}")

    def complete(self, messages: list[dict], purpose: str = "对话", **kwargs: Any) -> str:
        try:
            resp = self._create(purpose=purpose, messages=messages, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(friendly_error(exc)) from exc
        return (resp.choices[0].message.content or "").strip()

    def stream(self, messages: list[dict], purpose: str = "对话", **kwargs: Any) -> Iterator[str]:
        try:
            resp = self._create(purpose=purpose, messages=messages, stream=True, **kwargs)
            for chunk in resp:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    yield text
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMError(friendly_error(exc)) from exc

    def vision_complete(self, prompt: str, images: list[tuple[bytes, str]], system: str | None = None) -> str:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            b64 = base64.b64encode(data).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        try:
            resp = self._create(purpose="截图识别（已打码图片）", messages=messages, temperature=0)
        except openai.BadRequestError as exc:
            raise LLMError(
                f"模型拒绝了图片输入，当前模型「{self.profile.model}」可能不支持识图。"
                f"请切换到 qwen-vl-max、glm-4v-plus、gpt-4o 等多模态模型。（{_short(exc, 120)}）"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(friendly_error(exc)) from exc
        return (resp.choices[0].message.content or "").strip()

    def run_with_tools(self, messages: list[dict], tools: list[ToolSpec], max_rounds: int = 6,
                       purpose: str = "对话") -> ToolRunResult:
        """让模型按需调用工具取数，最后给出回答。模型/服务不支持工具时自动退回普通对话。"""
        if not tools or not self.profile.enable_tools:
            return ToolRunResult(self.complete(messages, purpose=purpose), used_tools=False)
        by_name = {t.name: t for t in tools}
        convo = list(messages)
        trace: list[dict] = []
        for _ in range(max_rounds):
            try:
                resp = self._create(purpose=purpose, messages=convo, tools=[t.schema() for t in tools], tool_choice="auto")
            except Exception as exc:  # noqa: BLE001
                if not trace and _looks_like_tool_unsupported(exc):
                    return ToolRunResult(self.complete(messages, purpose=purpose), used_tools=False)
                raise LLMError(friendly_error(exc)) from exc
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                return ToolRunResult((msg.content or "").strip(), trace)
            convo.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in calls
                    ],
                }
            )
            for c in calls:
                name = c.function.name
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool = by_name.get(name)
                if tool is None:
                    result: Any = {"error": f"未知工具 {name}"}
                else:
                    try:
                        result = tool.handler(**args)
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                trace.append({"name": name, "args": args, "result_preview": payload[:300]})
                convo.append({"role": "tool", "tool_call_id": c.id, "content": payload[:12000]})
        convo.append({"role": "user", "content": "请基于以上已获取的数据直接给出最终回答，不要再调用工具。"})
        return ToolRunResult(self.complete(convo, purpose=purpose), trace)

    def test_connection(self) -> tuple[bool, str]:
        try:
            text = self.complete([{"role": "user", "content": "只回复两个字：在线"}], purpose="测试连接", max_tokens=10)
        except LLMError as exc:
            return False, str(exc)
        return True, f"连接成功，模型回复：{text[:30]}"
