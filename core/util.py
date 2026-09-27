"""通用工具：哈希、JSON 落盘、文件名安全、罗马数字。纯标准库。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------- 控制台编码

def force_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，中文与箭头会炸；统一改 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


# ---------------------------------------------------------------- 时间

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stamp_compact() -> str:
    """用于目录名的紧凑时间戳，如 20260920-224712。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------- 哈希

_CHUNK = 1 << 20  # 1 MiB


def sha256_file(path: os.PathLike | str, full: bool = False) -> str:
    """文件哈希。

    默认只读「首 1MiB + 尾 1MiB + 字节数」——对 60MB 级别的 PDF 足够区分，
    又不必整本读盘。full=True 时算完整 sha256。
    """
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with p.open("rb") as f:
        if full or size <= 2 * _CHUNK:
            for chunk in iter(lambda: f.read(_CHUNK), b""):
                h.update(chunk)
        else:
            h.update(f.read(_CHUNK))
            f.seek(-_CHUNK, os.SEEK_END)
            h.update(f.read(_CHUNK))
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


# ---------------------------------------------------------------- JSON 落盘

def read_json(path: os.PathLike | str, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: os.PathLike | str, obj, indent: int = 2) -> Path:
    """原子写：先写临时文件再替换，避免半截 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return p


def write_text(path: os.PathLike | str, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


# ---------------------------------------------------------------- 文件名

_BAD_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DOTS = re.compile(r"\.+$")
_WS = re.compile(r"\s+")


def safe_filename(name: str, max_len: int = 80, fallback: str = "untitled") -> str:
    """把标题变成安全的文件名片段（不含扩展名）。

    只做机械替换，不改写字词——标题里的专有名词必须原样保留。
    """
    s = _BAD_FS.sub("", name or "").strip()
    s = _WS.sub(" ", s)
    s = s.replace("\u3000", " ").strip()
    s = _DOTS.sub("", s).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s or fallback


# ---------------------------------------------------------------- 罗马数字

_ROMAN = [
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
]


def roman(n: int) -> str:
    if n <= 0:
        return "?"
    out = []
    for v, s in _ROMAN:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


# ---------------------------------------------------------------- 其它

def tree_walk(node: dict):
    """深度优先遍历 outline 节点。"""
    yield node
    for child in node.get("children", []):
        yield from tree_walk(child)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1:.1f}{unit}"
        n /= 1024.0
    return f"{n}B"
