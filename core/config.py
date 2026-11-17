"""配置与路径解析。

Token 分级读取（先到先用）：
  1. 环境变量 MINERU_TOKEN
  2. doclab/.env.local        ← 本地私有，不入库
  3. doclab/config.json 的 mineru.token
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent          # doclab/

# 工作台自己的工作区：与任何一本具体的书无关。
#   _work/<source_id>/  该源的工程索引、目录树、页码校准、校验结果
#   out/<source_id>/L<N>/  该源按层级切出来的分节 md
# 源（书）从哪儿来由调用方决定：ingest --file <任意路径>，或 adopt 去 mineru_output_root 里找。
WORK_DIR = ROOT / "_work"
OUT_DIR = ROOT / "out"
# 「删源」不是真删：_work/<sid> 与 out/<sid> 整体移到这里，可人工捞回。
# 目录树、页码校准这些都是算出来的，重跑要烧 MinerU 配额和几十分钟，
# 不该有一次误点就永远消失的风险。原 PDF / MinerU 工程目录从不进这里。
TRASH_DIR = ROOT / "_trash"
UI_DIR = ROOT / "ui"
ENV_FILE = ROOT / ".env.local"
CONFIG_FILE = ROOT / "config.json"

DEFAULT_API_BASE = "https://mineru.net/api/v4"
DEFAULT_SHARD_PAGES = 200          # MinerU 单文件页数上限
DEFAULT_SHARD_MODE = "mineru"      # mineru = 交由 MinerU 按 page_ranges 切；local = 本机 pypdf 切
AI_ENV_KEY = "DOCLAB_AI_TOKEN"
AI_ENV_BASE = "DOCLAB_AI_BASE_URL"
AI_ENV_MODEL = "DOCLAB_AI_MODEL"
DEFAULT_AI_BASE = "https://api.deepseek.com/v1"
DEFAULT_AI_MODEL = "deepseek-chat"


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass
class AIConfig:
    """内置 AI 核校的配置。

    与 MinerU 一样，**token 只从环境变量或 .env.local 读**，绝不回传明文；
    base/model 可写 config.json（非秘密）。
    """
    base_url: str = DEFAULT_AI_BASE
    model: str = DEFAULT_AI_MODEL
    token: str = ""
    token_source: str = "none"
    timeout: int = 120
    max_tokens: int = 4096
    max_input_chars: int = 52000
    temperature: float = 0.0
    vision: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "max_input_chars": self.max_input_chars,
            "temperature": self.temperature,
            "vision": self.vision,
            "token_present": bool(self.token),
            "token_source": self.token_source,
            "token_hint": (self.token[:6] + "…" + self.token[-4:]) if self.token else "",
        }


def load_ai_config() -> AIConfig:
    cfg = AIConfig()
    file_cfg = read_json(CONFIG_FILE, {}) or {}
    ai = file_cfg.get("ai") if isinstance(file_cfg, dict) else {}
    if isinstance(ai, dict):
        cfg.base_url = str(ai.get("base_url") or cfg.base_url)
        cfg.model = str(ai.get("model") or cfg.model)
        cfg.timeout = int(ai.get("timeout") or cfg.timeout)
        cfg.max_tokens = int(ai.get("max_tokens") or cfg.max_tokens)
        cfg.max_input_chars = int(ai.get("max_input_chars") or cfg.max_input_chars)
        cfg.temperature = float(ai.get("temperature", cfg.temperature) or 0.0)
        cfg.vision = bool(ai.get("vision", cfg.vision))

    env_map = _load_env_file(ENV_FILE)
    env_base = os.environ.get(AI_ENV_BASE, "").strip() or env_map.get(AI_ENV_BASE, "").strip()
    env_model = os.environ.get(AI_ENV_MODEL, "").strip() or env_map.get(AI_ENV_MODEL, "").strip()
    if env_base:
        cfg.base_url = env_base
    if env_model:
        cfg.model = env_model

    env_token = os.environ.get(AI_ENV_KEY, "").strip()
    if env_token:
        cfg.token, cfg.token_source = env_token, f"env:{AI_ENV_KEY}"
    elif env_map.get(AI_ENV_KEY):
        cfg.token, cfg.token_source = env_map[AI_ENV_KEY], ".env.local"
    elif isinstance(ai, dict) and ai.get("token"):
        # 兼容旧配置，但界面向导永远写 .env.local。
        cfg.token, cfg.token_source = str(ai["token"]), "config.json(ai.token)"
    return cfg


def save_ai_config(patch: dict) -> Path:
    """只合并 ai 段里的非秘密字段；token 走 save_ai_token。"""
    cur = read_json(CONFIG_FILE, {}) or {}
    if not isinstance(cur, dict):
        cur = {}
    ai = cur.get("ai") if isinstance(cur.get("ai"), dict) else {}
    for k in ("base_url", "model", "timeout", "max_tokens", "max_input_chars",
              "temperature", "vision"):
        if k in patch and patch[k] not in (None, ""):
            ai[k] = patch[k]
    cur["ai"] = ai
    return write_json(CONFIG_FILE, cur)


def save_ai_token(token: str) -> None:
    """写入 / 清除 .env.local 里的 DOCLAB_AI_TOKEN（其他行原样保留）。"""
    save_env_value(AI_ENV_KEY, token)


@dataclass
class Config:
    api_base: str = DEFAULT_API_BASE
    token: str = ""
    token_source: str = "none"
    shard_pages: int = DEFAULT_SHARD_PAGES
    shard_mode: str = DEFAULT_SHARD_MODE
    language: str = "ch"
    enable_formula: bool = True
    enable_table: bool = True
    is_ocr: bool = False
    model_version: str = ""        # 空 = 用账号默认
    mineru_output_root: Path = field(default_factory=lambda: Path.home() / "MinerU")
    poll_interval: int = 8
    poll_timeout: int = 3600

    def public(self) -> dict[str, Any]:
        """给 UI / status 用的安全摘要（绝不回传 token 明文）。"""
        return {
            "api_base": self.api_base,
            "token_present": bool(self.token),
            "token_source": self.token_source,
            "token_hint": (self.token[:6] + "…" + self.token[-4:]) if self.token else "",
            "shard_pages": self.shard_pages,
            "shard_mode": self.shard_mode,
            "language": self.language,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "is_ocr": self.is_ocr,
            "model_version": self.model_version or "(account default)",
            "mineru_output_root": str(self.mineru_output_root),
        }


def load_config() -> Config:
    cfg = Config()

    file_cfg = read_json(CONFIG_FILE, {}) or {}
    if isinstance(file_cfg, dict):
        api = file_cfg.get("api", {})
        cfg.api_base = api.get("base_url", cfg.api_base)
        cfg.language = api.get("language", cfg.language)
        cfg.enable_formula = api.get("enable_formula", cfg.enable_formula)
        cfg.enable_table = api.get("enable_table", cfg.enable_table)
        cfg.is_ocr = api.get("is_ocr", cfg.is_ocr)
        cfg.model_version = api.get("model_version", cfg.model_version) or ""
        cfg.shard_pages = int(api.get("shard_pages", cfg.shard_pages))
        mode = str(api.get("shard_mode", cfg.shard_mode) or "").lower()
        cfg.shard_mode = mode if mode in ("mineru", "local") else cfg.shard_mode
        cfg.poll_interval = int(api.get("poll_interval", cfg.poll_interval))
        cfg.poll_timeout = int(api.get("poll_timeout", cfg.poll_timeout))
        if file_cfg.get("mineru_output_root"):
            cfg.mineru_output_root = Path(file_cfg["mineru_output_root"]).expanduser()

    # token 优先级：环境变量 > .env.local > config.json
    env_token = os.environ.get("MINERU_TOKEN", "").strip()
    if env_token:
        cfg.token, cfg.token_source = env_token, "env:MINERU_TOKEN"
    else:
        env_map = _load_env_file(ENV_FILE)
        if env_map.get("MINERU_TOKEN"):
            cfg.token, cfg.token_source = env_map["MINERU_TOKEN"], ".env.local"
        elif isinstance(file_cfg, dict) and file_cfg.get("mineru", {}).get("token"):
            cfg.token = file_cfg["mineru"]["token"]
            cfg.token_source = "config.json(mineru.token)"

    return cfg


def save_env_value(key: str, value: str) -> None:
    """写 / 删 .env.local 里的一个键；其他行原样保留。

    这是所有本机 Key 的统一落盘点：文件在 .gitignore 里，永不入库。
    值为空 = 删除该行（彻底清除，不是留空值）。
    """
    key = str(key or "").strip()
    if not key or "=" in key or "\n" in key:
        raise ValueError(f"非法环境变量名：{key!r}")
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    value = (value or "").strip()
    kept = [l for l in lines
            if not (l.strip() == key or l.strip().startswith(key + "="))]
    if value:
        kept.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def save_env_token(token: str) -> None:
    """写入 / 清除 .env.local 里的 MINERU_TOKEN（其他行原样保留）。

    这是「界面填 Key」的落盘点：token 只进 .env.local —— 它在 .gitignore
    里，永不入库。token 为空 = 删除该行（彻底清除，不是留空值）。
    注意环境变量 MINERU_TOKEN 优先级更高：它存在时这里写的值不生效，
    调用方要能把这一点告诉用户。
    """
    save_env_value("MINERU_TOKEN", token)


def save_config_patch(patch: dict) -> Path:
    """合并写入 config.json（不碰 token 时保持原样）。"""
    cur = read_json(CONFIG_FILE, {}) or {}
    if not isinstance(cur, dict):
        cur = {}
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cur.get(k), dict):
            cur[k].update(v)
        else:
            cur[k] = v
    return write_json(CONFIG_FILE, cur)


# ---------------------------------------------------------------- source_id

_SAFE_ID = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]+")


def make_source_id(stem: str, digest: str) -> str:
    base = _SAFE_ID.sub("-", stem).strip("-")[:48] or "source"
    return f"{base}.{digest[:10]}"


def work_dir(source_id: str) -> Path:
    return WORK_DIR / source_id


def out_dir(source_id: str) -> Path:
    return OUT_DIR / source_id


# ---------------------------------------------------------------- 源解析

def list_sources() -> list[str]:
    if not WORK_DIR.is_dir():
        return []
    return sorted(p.name for p in WORK_DIR.iterdir()
                  if p.is_dir() and (p / "project.json").exists())


def resolve_source(arg: str | None) -> str:
    """source_id 允许前缀/子串匹配；只有一个源时可省略。"""
    ids = list_sources()
    if not ids:
        raise FileNotFoundError("还没有任何源；先跑 ingest 或 adopt")
    if not arg or arg == "auto":
        if len(ids) == 1:
            return ids[0]
        raise ValueError("有多个源，请指定 source_id：\n  " + "\n  ".join(ids))
    if arg in ids:
        return arg
    hits = [i for i in ids if i.startswith(arg) or arg in i]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError(f"找不到源 {arg}；现有：\n  " + "\n  ".join(ids))
    raise ValueError(f"{arg} 匹配到多个源：\n  " + "\n  ".join(hits))
