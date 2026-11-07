"""模型配置：内置 OpenAI 兼容预设、多套配置保存与切换、api_key 环境变量兜底。

配置文件默认在 <项目>/data/config.json（已 gitignore），可用环境变量 ASHARE_AGENT_HOME 改目录。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .market import FeeSettings
from .rules import InvestorProfile, UserRules

GENERIC_KEY_ENV = "ASHARE_LLM_API_KEY"


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    base_url: str
    models: tuple[str, ...]
    vision_models: tuple[str, ...] = ()
    env_key: str = ""
    console_url: str = ""
    default_api_key: str = ""


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        Preset(
            "deepseek",
            "DeepSeek",
            "https://api.deepseek.com/v1",
            ("deepseek-chat", "deepseek-reasoner"),
            (),
            "DEEPSEEK_API_KEY",
            "https://platform.deepseek.com/api_keys",
        ),
        Preset(
            "qwen",
            "通义千问（阿里云百炼）",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ("qwen-plus", "qwen-max", "qwen-turbo", "qwen-vl-max", "qwen-vl-plus"),
            ("qwen-vl-max", "qwen-vl-plus"),
            "DASHSCOPE_API_KEY",
            "https://bailian.console.aliyun.com/",
        ),
        Preset(
            "kimi",
            "Kimi（月之暗面）",
            "https://api.moonshot.cn/v1",
            ("kimi-latest", "moonshot-v1-32k", "moonshot-v1-8k-vision-preview"),
            ("kimi-latest", "moonshot-v1-8k-vision-preview", "moonshot-v1-32k-vision-preview"),
            "MOONSHOT_API_KEY",
            "https://platform.moonshot.cn/console/api-keys",
        ),
        Preset(
            "zhipu",
            "智谱 GLM",
            "https://open.bigmodel.cn/api/paas/v4",
            ("glm-4-plus", "glm-4-flash", "glm-4v-plus", "glm-4v-flash"),
            ("glm-4v-plus", "glm-4v-flash", "glm-4v"),
            "ZHIPUAI_API_KEY",
            "https://open.bigmodel.cn/usercenter/apikeys",
        ),
        Preset(
            "openai",
            "OpenAI",
            "https://api.openai.com/v1",
            ("gpt-4o", "gpt-4o-mini", "gpt-4.1"),
            ("gpt-4o", "gpt-4o-mini", "gpt-4.1"),
            "OPENAI_API_KEY",
            "https://platform.openai.com/api-keys",
        ),
        Preset(
            "siliconflow",
            "硅基流动 SiliconFlow",
            "https://api.siliconflow.cn/v1",
            ("deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen2.5-VL-72B-Instruct"),
            ("Qwen/Qwen2.5-VL-72B-Instruct",),
            "SILICONFLOW_API_KEY",
            "https://cloud.siliconflow.cn/account/ak",
        ),
        Preset(
            "ollama",
            "本地 Ollama",
            "http://localhost:11434/v1",
            ("qwen2.5:7b", "qwen2.5vl:7b"),
            ("qwen2.5vl:7b",),
            "",
            "https://ollama.com/",
            "ollama",
        ),
        Preset("custom", "自定义（任意 OpenAI 兼容接口）", "", ()),
    )
}

_VISION_HINTS = ("vl", "vision", "4v", "gpt-4o", "gpt-4.1", "gpt-5", "kimi-latest", "gemini", "claude")


def guess_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in _VISION_HINTS)


@dataclass
class ModelProfile:
    name: str
    provider: str = "deepseek"
    base_url: str = PRESETS["deepseek"].base_url
    model: str = "deepseek-chat"
    api_key: str = ""
    persist_key: bool = True
    supports_vision: bool | None = None  # None = 按模型名自动判断
    temperature: float = 0.3
    enable_tools: bool = True

    @property
    def vision_enabled(self) -> bool:
        if self.supports_vision is not None:
            return self.supports_vision
        preset = PRESETS.get(self.provider)
        if preset and self.model in preset.vision_models:
            return True
        return guess_vision(self.model)

    def env_key_name(self) -> str:
        preset = PRESETS.get(self.provider)
        return preset.env_key if preset else ""

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_name = self.env_key_name()
        if env_name and os.environ.get(env_name):
            return os.environ[env_name]
        if os.environ.get(GENERIC_KEY_ENV):
            return os.environ[GENERIC_KEY_ENV]
        preset = PRESETS.get(self.provider)
        return preset.default_api_key if preset else ""

    def masked_key(self) -> str:
        key = self.resolve_api_key()
        if not key:
            return "未配置"
        return f"••••{key[-4:]}" if len(key) > 4 else "••••"

    def key_source(self) -> str:
        if self.api_key:
            return "配置文件" if self.persist_key else "仅本次会话"
        env_name = self.env_key_name()
        if env_name and os.environ.get(env_name):
            return f"环境变量 {env_name}"
        if os.environ.get(GENERIC_KEY_ENV):
            return f"环境变量 {GENERIC_KEY_ENV}"
        preset = PRESETS.get(self.provider)
        if preset and preset.default_api_key:
            return "无需 key"
        return "未配置"

    @classmethod
    def from_preset(cls, provider: str, name: str | None = None, model: str | None = None) -> "ModelProfile":
        preset = PRESETS[provider]
        return cls(
            name=name or preset.label,
            provider=provider,
            base_url=preset.base_url,
            model=model or (preset.models[0] if preset.models else ""),
        )

    def to_dict(self, include_key: bool = True) -> dict:
        d = asdict(self)
        if not include_key or not self.persist_key:
            d["api_key"] = ""
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModelProfile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class AppConfig:
    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    active_profile: str = ""
    data_source: str = "auto"  # auto / akshare / snapshot
    fees: FeeSettings = field(default_factory=FeeSettings)
    rules: UserRules = field(default_factory=UserRules)
    profile: InvestorProfile = field(default_factory=InvestorProfile)
    exam_results: dict[str, dict] = field(default_factory=dict)  # 配置名 -> 最近一次考卷结果摘要
    review_prepare_time: str = "15:45"
    onboarded: bool = False

    def __post_init__(self) -> None:
        if not self.profiles:
            default = ModelProfile.from_preset("deepseek")
            self.profiles = {default.name: default}
        if self.active_profile not in self.profiles:
            self.active_profile = next(iter(self.profiles))

    @property
    def active(self) -> ModelProfile:
        return self.profiles[self.active_profile]

    def switch(self, name: str) -> ModelProfile:
        if name not in self.profiles:
            raise KeyError(f"不存在的模型配置：{name}")
        self.active_profile = name
        return self.active

    def upsert(self, profile: ModelProfile, activate: bool = True) -> None:
        if not profile.name.strip():
            raise ValueError("配置名称不能为空")
        self.profiles[profile.name] = profile
        if activate:
            self.active_profile = profile.name

    def remove(self, name: str) -> None:
        if name not in self.profiles:
            return
        if len(self.profiles) == 1:
            raise ValueError("至少保留一个模型配置")
        del self.profiles[name]
        if self.active_profile == name:
            self.active_profile = next(iter(self.profiles))

    def exam_passed(self, name: str) -> bool:
        return bool(self.exam_results.get(name, {}).get("passed"))

    def to_dict(self) -> dict:
        return {
            "active_profile": self.active_profile,
            "profiles": [p.to_dict() for p in self.profiles.values()],
            "data_source": self.data_source,
            "fees": self.fees.to_dict(),
            "rules": self.rules.to_dict(),
            "profile": self.profile.to_dict(),
            "exam_results": self.exam_results,
            "review_prepare_time": self.review_prepare_time,
            "onboarded": self.onboarded,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        profiles = {}
        for item in d.get("profiles", []):
            p = ModelProfile.from_dict(item)
            profiles[p.name] = p
        return cls(
            profiles=profiles,
            active_profile=d.get("active_profile", ""),
            data_source=d.get("data_source", "auto"),
            fees=FeeSettings.from_dict(d.get("fees")),
            rules=UserRules.from_dict(d.get("rules")),
            profile=InvestorProfile.from_dict(d.get("profile")),
            exam_results=d.get("exam_results", {}),
            review_prepare_time=d.get("review_prepare_time", "15:45"),
            onboarded=bool(d.get("onboarded", False)),
        )


def data_dir() -> Path:
    env = os.environ.get("ASHARE_AGENT_HOME")
    path = Path(env).expanduser() if env else Path(__file__).resolve().parent.parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "config.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            return AppConfig.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError):
            backup = self.path.with_suffix(".broken.json")
            self.path.replace(backup)
            return AppConfig()

    def save(self, cfg: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
