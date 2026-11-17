"""封面提取：PDF 第一页优先，EPUB 读原书封面。

只做「把原书封面拿给 EPUB 用」这一件事：
  - PDF：用 PyMuPDF 渲染第 1 页为 JPEG（项目 .runtime 里已有，按需加载）；
  - EPUB：从原书 OPF 的 cover-image / guide cover / 文件名含 cover 的图片里取；
  - 找不到不阻断导出，返回 warning，由调用方记进 EPUB warnings。

不修改原 PDF/EPUB，不访问网络。
"""
from __future__ import annotations

import posixpath
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .config import ROOT
from .util import read_json

IMAGE_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg",
}
EXT_MEDIA = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag else ""


def pdf_first_page(pdf: Path, max_edge: int = 1800) -> dict:
    """PDF 第 1 页 → JPEG bytes。高分辨率但限制长边，避免 EPUB 过大。"""
    pdf = Path(pdf)
    sys.path.insert(0, str(ROOT / ".runtime"))
    import fitz                                               # noqa: PLC0415

    with fitz.open(str(pdf)) as doc:
        if doc.page_count <= 0:
            raise ValueError("PDF 没有页面")
        page = doc[0]
        rect = page.rect
        scale = 2.0
        long_edge = max(float(rect.width), float(rect.height)) * scale
        if long_edge > max_edge:
            scale *= max_edge / long_edge
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        data = pix.tobytes("jpeg", jpg_quality=88)
        return {
            "data": data,
            "media_type": "image/jpeg",
            "width": pix.width,
            "height": pix.height,
            "kind": "pdf-first-page",
            "source": str(pdf),
        }


def _opf_path(z: zipfile.ZipFile, names: set[str]) -> str | None:
    if "META-INF/container.xml" not in names:
        return None
    try:
        root = ET.fromstring(z.read("META-INF/container.xml"))
    except ET.ParseError:
        return None
    rootfile = next((e for e in root.iter() if _local(e.tag) == "rootfile"), None)
    if rootfile is None or not rootfile.get("full-path"):
        return None
    p = posixpath.normpath(rootfile.get("full-path"))
    return p if p in names else None


def _epub_cover_path(z: zipfile.ZipFile, opf_path: str, names: set[str]) -> tuple[str, str] | None:
    try:
        opf = ET.fromstring(z.read(opf_path))
    except ET.ParseError:
        return None
    base = posixpath.dirname(opf_path)
    manifest = next((e for e in opf if _local(e.tag) == "manifest"), None)
    items = [e for e in (manifest or []) if _local(e.tag) == "item"]
    by_id = {it.get("id"): it for it in items if it.get("id")}

    def resolve_against(base_dir: str, href: str, media: str | None = None) -> tuple[str, str] | None:
        p = posixpath.normpath(posixpath.join(base_dir, unquote(href or "")))
        if p not in names:
            return None
        mt = media or EXT_MEDIA.get(Path(p).suffix.lower(), "image/jpeg")
        if not mt.startswith("image/"):
            return None
        return p, mt

    def resolve(href: str, media: str | None = None) -> tuple[str, str] | None:
        return resolve_against(base, href, media)

    def first_image_in_page(page_path: str) -> tuple[str, str] | None:
        try:
            html = z.read(page_path).decode("utf-8", "replace")
        except Exception:                                  # noqa: BLE001
            return None
        m = re.search(r'<img[^>]+src=["\']([^"\']+)', html, re.I)
        if not m:
            return None
        return resolve_against(posixpath.dirname(page_path), m.group(1))

    # 1) EPUB3 标准：properties="cover-image"
    for it in items:
        if "cover-image" in (it.get("properties") or "").split():
            got = resolve(it.get("href"), it.get("media-type"))
            if got:
                return got
    # 2) guide reference type="cover"
    guide = next((e for e in opf if _local(e.tag) == "guide"), None)
    if guide is not None:
        for ref in guide:
            if _local(ref.tag) != "reference":
                continue
            if (ref.get("type") or "").lower() != "cover":
                continue
            href = ref.get("href")
            if href:
                got = resolve(href)
                if got:
                    return got
    # 3) 兜底：id/href 里带 cover 的图片
    for it in items:
        href = it.get("href") or ""
        if not (it.get("media-type") or "").startswith("image/"):
            continue
        if "cover" in (it.get("id") or "").lower() or "cover" in href.lower():
            got = resolve(href, it.get("media-type"))
            if got:
                return got
    # 4) guide href 指向 xhtml 封面页时，取页面里第一张图
    if guide is not None:
        for ref in guide:
            if (ref.get("type") or "").lower() != "cover":
                continue
            got = resolve(ref.get("href"))
            if not got:
                continue
            got2 = first_image_in_page(got[0])
            if got2:
                return got2
    # 5) 没有 guide/cover-image 的 EPUB：找 id/href 带 cover 的 xhtml，取首图
    for it in items:
        href = it.get("href") or ""
        if not (it.get("media-type") or "").startswith("application/xhtml+xml"):
            continue
        if "cover" not in (it.get("id") or "").lower() and "cover" not in href.lower():
            continue
        got = resolve(href)
        if got:
            got2 = first_image_in_page(got[0])
            if got2:
                return got2
    return None


def epub_cover(epub: Path) -> dict | None:
    epub = Path(epub)
    try:
        with zipfile.ZipFile(epub) as z:
            names = set(z.namelist())
            opf = _opf_path(z, names)
            if not opf:
                return None
            got = _epub_cover_path(z, opf, names)
            if not got:
                return None
            path, media = got
            data = z.read(path)
            if not data:
                return None
            return {
                "data": data,
                "media_type": media,
                "width": None,
                "height": None,
                "kind": "epub-cover",
                "source": f"{epub}!{path}",
            }
    except (zipfile.BadZipFile, OSError):
        return None


def resolve_cover(wd: Path) -> tuple[dict | None, str]:
    """从 project.json 的 source_file 取封面；返回 (cover, warning)。"""
    pj = read_json(Path(wd) / "project.json", {}) or {}
    src = Path(str(pj.get("source_file") or ""))
    if not src.is_file():
        return None, "找不到源 PDF/EPUB，EPUB 未加封面"
    try:
        if src.suffix.lower() == ".pdf":
            return pdf_first_page(src), ""
        if src.suffix.lower() == ".epub":
            cover = epub_cover(src)
            if cover:
                return cover, ""
            return None, "原 EPUB 没有可识别的封面图，EPUB 未加封面"
        return None, f"源文件类型 {src.suffix or '?'} 不支持封面提取"
    except Exception as e:                                    # noqa: BLE001
        return None, f"封面提取失败：{type(e).__name__}: {e}"
