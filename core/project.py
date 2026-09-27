"""MinerU 工程读取与分片管理。

一个「源」（source）= 若干分片（shard）。MinerU 单文件上限 200 页，长书必须切片，
每片一个独立工程目录（各自带 images/、content_list.json）。

本模块只读工程、不写工程；对工程的引用记在 _work/<source_id>/project.json 里。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .util import read_json, write_json, sha256_file, now_iso

# 结果正文里会被当作「内容」的块类型。
# list / ref_text 是实测另一本书（学位论文）才出现的类型：前者是条目化正文，
# 后者是参考文献条目。它们既不在旧 CONTENT_TYPES 也不在 NOISE_TYPES 里，
# 会从两者之间的缝里漏掉——遇到新书必须先把类型归位，否则正文会静默缺失。
CONTENT_TYPES = {"text", "image", "table", "chart", "equation", "interline_equation",
                 "list", "ref_text"}
NOISE_TYPES = {"header", "footer", "page_number", "aside_text", "discarded"}
# 「是内容，但不进正文块计数」的一类：页下注。
#
# 曾经它被塞在 NOISE_TYPES 里，而 exporter 在渲染前按 NOISE_TYPES 过滤 —— 于是
# render_blocks 里那整段脚注处理（page-end / inline / drop 三档、--footnotes 开关）
# 全是**死代码**，一处都不会执行。实测代价：《中国数字人文发展报告》1012 条、
# 《认识论引论》406 条页下注（基本都是书目引注）一条也没进导出的 md。
# 它不是版面噪声（页眉/页码/页脚那种），是书的一部分，必须单独一类。
NOTE_TYPES = {"page_footnote"}


def unclassified_types(blocks: list) -> dict[str, int]:
    """统计既不在 CONTENT_TYPES / NOTE_TYPES 也不在 NOISE_TYPES 的类型，供体检时报警。

    静默丢内容是最难发现的一类错误（字数会少，但看上去一切正常），
    所以宁可每次跑都把未知类型顶到台面上。
    """
    from collections import Counter
    c: Counter = Counter(b.type for b in blocks
                         if b.type not in CONTENT_TYPES and b.type not in NOISE_TYPES
                         and b.type not in NOTE_TYPES)
    return dict(c)

_IMG_REF = re.compile(r"images/([0-9a-fA-F]{16,}\.(?:jpg|jpeg|png|webp|bmp))")

# MinerU 转换流水线残留在正文里的英文描述（原图是空白页上的一条线/纯色块）。
# 实测本书 P1 物理页 12、14 各有一段，会污染正文，必须剔掉并计数。
# 注意：不要把 `^\s*$` 写进来。image / table / chart 的 text 字段本来就是空的，
# 一旦匹配空串，整类块会被当成噪声丢掉。
NOISE_TEXT_RES = [
    re.compile(r"^The Ground Truth image displays\b", re.I),
    re.compile(r"^The (?:image|figure|illustration) (?:is|shows|displays) "
               r"(?:a|an|the)?\s*(?:single|solid|blank|white)", re.I),
]


def is_noise_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return any(r.search(t) for r in NOISE_TEXT_RES)


# ---------------------------------------------------------------- Block

@dataclass
class Block:
    gid: int                  # 全书全局序号（跨片连续）
    shard: str                # 分片标签，如 P1
    local: int                # 片内序号
    page_idx: int             # 片内物理页（0 基）；EPUB 源里是「阅读顺序单元」序号
    type: str
    text: str = ""
    text_level: int | None = None
    bbox: list | None = None
    img_path: str = ""
    table_body: str = ""
    caption: list[str] = field(default_factory=list)
    footnote: list[str] = field(default_factory=list)
    # 层级是否**权威**。MinerU 的 text_level 不可信（有的书把一切标题都标成 1），
    # 所以 outline 只把它当兜底、主要靠中文编号推断。EPUB 不一样：h1..h6 是出版方
    # 标的真实层级，比中文编号可靠。这个标记就是「这本书的层级可以直接采纳」，
    # 由 core/epub.py 置位；PDF 源恒为 False，判定路径逐位不变。
    level_trusted: bool = False

    @property
    def is_heading(self) -> bool:
        return self.type == "text" and bool(self.text_level)

    @property
    def is_content(self) -> bool:
        return self.type in CONTENT_TYPES

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_heading"] = self.is_heading
        return d


# ---------------------------------------------------------------- 工程读取

def parse_page_number(text: str) -> int | None:
    """把 page_number 块的文本解析成整数；容忍空格与全角数字。"""
    t = (text or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    t = t.strip(" .·-—")
    return int(t) if t.isdigit() and len(t) <= 4 else None


def find_content_list(project_dir: Path) -> Path | None:
    """优先 content_list.json（v1，字段稳定）；退而取 _v2 并做结构兼容。"""
    files = [f for f in sorted(project_dir.glob("*content_list.json"))]
    if not files:
        files = [f for f in sorted(project_dir.glob("content_list.json"))]
    if not files:
        return None
    v1 = [f for f in files if not f.name.endswith("_v2.json")]
    return (v1 or files)[0]


def _norm_item(it: dict) -> dict | None:
    """兼容 v1/v2 两种条目形态，统一成扁平 dict。"""
    if not isinstance(it, dict):
        return None
    if "type" not in it and isinstance(it.get("content"), dict):
        it = it["content"]
    if "type" not in it:
        return None
    return it


def load_project_dir(project_dir: Path) -> tuple[list[dict], Path | None]:
    """读一个 MinerU 工程目录 → (原始条目列表, content_list 路径)。"""
    project_dir = Path(project_dir)
    cl = find_content_list(project_dir)
    if cl is None:
        raise FileNotFoundError(f"{project_dir} 下找不到 content_list.json")
    raw = json.loads(cl.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{cl} 顶层不是列表")
    return [_norm_item(x) for x in raw if _norm_item(x)], cl


def project_pdf(project_dir: Path) -> Path | None:
    """工程自带的原件 PDF（MinerU 会把上传件回存成 *_origin.pdf）。"""
    hits = sorted(Path(project_dir).glob("*_origin.pdf"))
    return hits[0] if hits else None


@dataclass
class Shard:
    tag: str
    dir: Path
    project_dir: Path | None = None
    pdf: Path | None = None
    n_pages: int = 0
    n_items: int = 0
    images_dir: Path | None = None
    content_list: Path | None = None

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "project_dir": str(self.project_dir or self.dir),
            "pdf": str(self.pdf) if self.pdf else None,
            "n_pages": self.n_pages,
            "n_items": self.n_items,
            "images_dir": str(self.images_dir) if self.images_dir else None,
            "content_list": str(self.content_list) if self.content_list else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "Shard":
        p = Path(d["project_dir"])
        return Shard(
            tag=d["tag"], dir=p, project_dir=p,
            pdf=Path(d["pdf"]) if d.get("pdf") else None,
            n_pages=d.get("n_pages", 0), n_items=d.get("n_items", 0),
            images_dir=Path(d["images_dir"]) if d.get("images_dir") else None,
            content_list=Path(d["content_list"]) if d.get("content_list") else None,
        )


def scan_shard(project_dir: Path, tag: str) -> Shard:
    project_dir = Path(project_dir)
    items, cl = load_project_dir(project_dir)
    n_pages = 0
    for it in items:
        if "page_idx" in it:
            n_pages = max(n_pages, int(it["page_idx"]) + 1)
    img_dir = project_dir / "images"
    return Shard(
        tag=tag, dir=project_dir, project_dir=project_dir,
        pdf=project_pdf(project_dir),
        n_pages=n_pages, n_items=len(items),
        images_dir=img_dir if img_dir.is_dir() else None,
        content_list=cl,
    )


def coerce_level(v) -> int | None:
    """text_level 类型不统一：桌面版给 int，API 版给字符串 '2'。统一成 int。"""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s) if s.lstrip("-").isdigit() else None


def iter_blocks(shards: list[Shard]) -> list[Block]:
    """按分片顺序读全部块，赋跨片连续的 gid。"""
    out: list[Block] = []
    gid = 0
    for sh in shards:
        items, _ = load_project_dir(sh.project_dir or sh.dir)
        for local, it in enumerate(items):
            b = Block(
                gid=gid, shard=sh.tag, local=local,
                page_idx=int(it.get("page_idx") or 0),
                type=str(it.get("type") or "unknown"),
                text=(it.get("text") or "").strip(),
                text_level=coerce_level(it.get("text_level")),
                bbox=it.get("bbox"),
                img_path=(it.get("img_path") or "").strip(),
                table_body=(it.get("table_body") or "").strip(),
                caption=list(it.get("image_caption") or it.get("table_caption")
                             or it.get("chart_caption") or []),
                footnote=list(it.get("image_footnote") or it.get("table_footnote")
                              or it.get("chart_footnote") or []),
                # EPUB 源写的标记：h1..h6 是出版方标的真实层级，可直接采纳。
                # MinerU 的 content_list 里没有这个键 → False，PDF 路径逐位不变。
                level_trusted=bool(it.get("_trusted")),
            )
            out.append(b)
            gid += 1
    return out


# ---------------------------------------------------------------- 分片工具

def pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ImportError:
        return 0
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def shard_pdf(src: Path, out_dir: Path, shard_pages: int = 200,
              stem: str | None = None) -> list[Path]:
    """把超过 shard_pages 页的 PDF 切成 _P1/_P2… 分片。返回分片路径列表。

    MinerU 单文件上限 200 页，超限必须切；不到阈值则原样返回单元素列表。
    """
    src = Path(src)
    total = pdf_page_count(src)
    if total == 0 or total <= shard_pages:
        return [src]

    from pypdf import PdfReader, PdfWriter

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = stem or src.stem
    reader = PdfReader(str(src))
    made: list[Path] = []
    part = 1
    for start in range(0, total, shard_pages):
        end = min(start + shard_pages, total)
        w = PdfWriter()
        for i in range(start, end):
            w.add_page(reader.pages[i])
        dest = out_dir / f"{name}_P{part}.pdf"
        with dest.open("wb") as f:
            w.write(f)
        made.append(dest)
        part += 1
    return made


# ---------------------------------------------------------------- 工程登记

PROJECT_JSON = "project.json"


def save_project(wd: Path, data: dict) -> Path:
    data = dict(data)
    data.setdefault("updated_at", now_iso())
    return write_json(wd / PROJECT_JSON, data)


def load_project(wd: Path) -> dict:
    d = read_json(wd / PROJECT_JSON, None)
    if not d:
        raise FileNotFoundError(f"{wd} 下没有 project.json —— 先跑 ingest 或 adopt")
    return d


def load_shards(wd: Path) -> list[Shard]:
    return [Shard.from_dict(d) for d in load_project(wd).get("shards", [])]


def discover_mineru_dirs(root: Path, keyword: str) -> list[Path]:
    """在 MinerU 输出根目录里按关键词找工程目录（用于 adopt 已有成果）。"""
    root = Path(root)
    if not root.is_dir():
        return []
    hits = [d for d in sorted(root.iterdir()) if d.is_dir() and keyword in d.name]
    return [d for d in hits if find_content_list(d) is not None]
