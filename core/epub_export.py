"""Markdown → EPUB 装订：把 exporter 切好的分节 md 合成一本可读的 EPUB。

为什么读 md 而不是重新读块：md 是**已经导出、已经核校过**的那一份产物，EPUB 是它的
另一种装帧，不是第二条转换链。这样 md 里是什么，EPUB 里就是什么；不会出现
「EPUB 与 md 内容不一致」这种最难查的问题。和 `core/epub.py`（EPUB 入库 → 块）
方向相反，两者互不调用。

保留的东西：
  - 章节顺序 = manifest.json 的 seq 顺序（与 00-目录.md 一致）
  - 页锚 `<!-- p=N -->` → 不可见的 `epub:type=pagebreak` 锚点：可跳转、可引用，
    **不把页码印成正文数字**（那会污染正文，也可能被误当成原书排版）
  - 脚注（随文 `<!-- footnote -->` 与页末 `**脚注**` 列表）、图片、表格、引文、
    标题层级、`> 归属：…` 标题链
  - 书名 / 作者 / 语言 / 稳定标识符等 OPF 元数据；EPUB3 nav + NCX 双目录

不承诺：
  - 不在阅读器里做视觉验收。`verify_epub` 只做机械核验（zip 结构、OPF、spine、
    nav、XHTML 合法性、图片引用），排版效果仍需人打开看。
  - 不修改 md，也不写 Zotero。纯标准库，不引入新依赖。

产出命名：<书名>.epub（与 md 同目录）。重跑会覆盖同名、清掉同目录其他 .epub
（那些只可能是上一轮换名字留下的），不会动 .md / images。
"""
from __future__ import annotations

import hashlib
import html
import os
import posixpath
import re
import tempfile
import time
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

from .util import now_iso, read_json, safe_filename, write_json

EPUB_MIMETYPE = b"application/epub+zip"
EPUB_NS = "http://www.idpf.org/2007/ops"
XHTML_NS = "http://www.w3.org/1999/xhtml"
# zip 条目用固定时间戳：同样的输入装两次应当得到同样的字节，便于比对和回归。
ZIP_DATE = (2010, 1, 1, 0, 0, 0)
BODY_MD_RE = re.compile(r"^\d{3,}_.+\.md$")
EXCLUDED_MD = {"00-目录.md", "校验报告.md", "AI核校报告.md", "AI校验报告.md"}
MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}
# EPUB3 核心媒体类型之外的图片：能装进 zip，但阅读器不保证渲染，如实记 warning。
NON_CORE_IMAGE = {".webp", ".bmp"}
COVER_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg",
}

CSS = """/* doclab markdown → EPUB；只做可读性，不模拟纸书版式 */
body { margin: 0 1em; line-height: 1.8;
       font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC",
                    "SimSun", serif; }
h1, h2, h3, h4, h5, h6 { font-weight: 600; line-height: 1.4; text-indent: 0; }
h1 { font-size: 1.55em; margin: 1.4em 0 1em; text-align: center; }
h2 { font-size: 1.3em; margin: 1.5em 0 0.6em; }
h3 { font-size: 1.15em; margin: 1.2em 0 0.5em; }
h4, h5, h6 { font-size: 1.05em; margin: 1em 0 0.4em; }
p { text-indent: 2em; margin: 0 0 0.65em; }
p.crumbs, p.fn-head, p.footnote, p.meta, figcaption, .titlepage p { text-indent: 0; }
p.crumbs { font-size: 0.9em; color: #666; margin-bottom: 1.1em; }
p.fn-head { font-weight: 600; font-size: 0.92em; margin: 1.3em 0 0.3em; }
p.footnote { font-size: 0.9em; color: #333; margin: 0.35em 0 0.35em 1.5em; }
blockquote { margin: 1em 1.4em; padding-left: 0.9em; border-left: 3px solid #bbb;
             color: #444; }
blockquote p { text-indent: 0; }
ul, ol { margin: 0.6em 0 0.8em 1.6em; padding: 0; }
ul.fn-list { font-size: 0.9em; margin-left: 3em; }
li { margin-bottom: 0.25em; }
figure { margin: 1.3em 0; text-align: center; page-break-inside: avoid; }
img { max-width: 100%; height: auto; }
figcaption { font-size: 0.9em; color: #555; margin-top: 0.4em; }
table { border-collapse: collapse; margin: 1em auto; font-size: 0.92em; }
th, td { border: 1px solid #999; padding: 0.3em 0.6em; vertical-align: top; }
th { background: #f2f2f2; }
code { font-family: "Consolas", "Courier New", monospace; font-size: 0.92em; }
hr { border: 0; border-top: 1px solid #ccc; margin: 1.4em 0; }
/* 页锚只做定位，不显示 —— 正文里出现纸书页码会与 EPUB 阅读进度混淆 */
.pagebreak { display: none; }
p.missing-image { text-indent: 0; color: #888; font-size: 0.9em; }
.titlepage { text-align: center; margin-top: 22%; }
.titlepage h1.booktitle { font-size: 2em; margin-bottom: 1.2em; }
.titlepage p.author { font-size: 1.2em; margin: 0 0 2.6em; }
.titlepage p.meta { font-size: 0.85em; color: #777; margin: 0.3em 0; }
/* 封面：单独一页，铺满阅读器可视区；不参与章节计数 */
body.cover { margin: 0; padding: 0; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100vh; width: auto; height: auto; }
"""


# ---------------------------------------------------------------- 文本层

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag else ""


def _text(value) -> str:
    """XML 文本转义。先 unescape 一次：md 里可能残留 `&lt;` 这类实体。"""
    return html.escape(html.unescape(str(value or "")), quote=False)


def _attr(value) -> str:
    return html.escape(html.unescape(str(value or "")), quote=True)


_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
# MinerU 有时直接给原始 HTML 片段（上标、换行）；这些标签本身是安全且合法的 XHTML。
_RAW_INLINE = re.compile(r"</?(?:sup|sub)>|<br\s*/?>", re.I)


def _inline(text: str) -> str:
    """行内 markdown → XHTML。支持 exporter 会产出的三类标记与裸 sup/sub/br。"""
    tags: list[str] = []

    def stash(m):
        tags.append(m.group(0))
        return "\x00%d\x00" % (len(tags) - 1)

    s = _RAW_INLINE.sub(stash, text or "")
    s = _text(s)
    s = _CODE_RE.sub(r"<code>\1</code>", s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = _ITALIC_RE.sub(r"<em>\1</em>", s)
    for i, tag in enumerate(tags):
        low = tag.lower()
        if low.startswith("<br"):
            rep = "<br/>"
        elif low.startswith("</sup"):
            rep = "</sup>"
        elif low.startswith("<sup"):
            rep = "<sup>"
        elif low.startswith("</sub"):
            rep = "</sub>"
        elif low.startswith("<sub"):
            rep = "<sub>"
        else:
            rep = ""
        s = s.replace("\x00%d\x00" % i, rep)
    return s


def _unique_id(base: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "-", base or "p") or "p"
    if not (base[0].isalpha() or base[0] == "_"):
        base = "p" + base
    cand, n = base, 2
    while cand in used:
        cand = f"{base}-{n}"
        n += 1
    used.add(cand)
    return cand


def _anchor_span(value: str, chapter_no: int, used: set[str]) -> str:
    """`<!-- p=... -->` → 空 span。id/title 保留可定位信息，不产生可见文字。"""
    raw = (value or "").strip()
    if re.fullmatch(r"\d+", raw):
        anchor_id, label = f"p{raw}", f"第 {raw} 页"
    elif re.fullmatch(r"front-\d+", raw):
        anchor_id, label = "p" + raw, f"前置页 {raw.split('-', 1)[1]}"
    elif re.fullmatch(r"pdf-\d+(?:\s+unmapped)?", raw):
        num = raw.split()[0].split("-", 1)[1]
        anchor_id, label = f"ppdf-{num}", f"PDF 第 {num} 页（未映射）"
    elif raw in ("unmapped", ""):
        anchor_id, label = f"punmapped-{chapter_no}", "未映射页"
    else:
        anchor_id, label = "p" + raw, raw
    anchor_id = _unique_id(anchor_id, used)
    return ('<span class="pagebreak" id="%s" epub:type="pagebreak" '
            'role="doc-pagebreak" title="%s"></span>' % (_attr(anchor_id), _attr(label)))


def _image_src(src: str) -> str | None:
    """把 md 里的图片地址归一成 `images/<name>`；不接受外部地址。"""
    s = unquote((src or "").strip()).split("#", 1)[0]
    if not s or s.startswith(("http://", "https://", "data:")):
        return None
    s = posixpath.normpath(s.replace("\\", "/"))
    if s.startswith("../images/"):
        s = s[3:]
    if not s.startswith("images/") or s == "images/":
        return None
    return s


def _table_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_separator(line: str) -> bool:
    s = line.strip().strip("|").strip()
    if not s:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in s.split("|"))


def _table_html(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head = "".join("<th>%s</th>" % _inline(c) for c in rows[0])
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r)
                   for r in rows[1:])
    return ("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
            % (head, body))


class _HtmlTable(HTMLParser):
    """把 MinerU 直接写进 `table_body` 的原始 HTML 表格收成行/单元格。

    只关心结构（tr/td/th、colspan/rowspan），单元格内嵌套标签按文本收平；
    多层嵌套表格不展开 —— 这比把整段 `<table>` 当正文输出已经好得多。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._depth += 1
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._row = []
            self.rows.append(self._row)
        elif tag in ("td", "th"):
            if self._row is None:
                self._row = []
                self.rows.append(self._row)
            cell = {"tag": tag, "colspan": 1, "rowspan": 1, "parts": []}
            for k, v in attrs:
                k = (k or "").lower()
                if k not in ("colspan", "rowspan"):
                    continue
                try:
                    n = int(str(v).strip())
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    cell[k] = n
            self._cell = cell
            self._row.append(cell)
        elif tag == "br" and self._cell is not None:
            self._cell["parts"].append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._depth == 1 and self._cell is not None:
            self._cell["parts"].append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table":
            if self._depth:
                self._depth -= 1
            return
        if self._depth != 1:
            return
        if tag in ("td", "th"):
            self._cell = None
        elif tag == "tr":
            self._row = None


def _html_table_to_xhtml(raw: str) -> str | None:
    parser = _HtmlTable()
    try:
        parser.feed(raw or "")
        parser.close()
    except Exception:                                     # noqa: BLE001
        return None
    rows = [r for r in parser.rows if r]
    if not rows:
        return None

    def cell(c: dict, header: bool) -> str:
        txt = re.sub(r"\s+", " ", "".join(c["parts"])).strip()
        attrs = ""
        if c.get("colspan", 1) > 1:
            attrs += ' colspan="%d"' % c["colspan"]
        if c.get("rowspan", 1) > 1:
            attrs += ' rowspan="%d"' % c["rowspan"]
        tag = "th" if header else "td"
        return "<%s%s>%s</%s>" % (tag, attrs, _text(txt), tag)

    head = "".join(cell(c, True) for c in rows[0])
    body = "".join("<tr>%s</tr>" % "".join(cell(c, False) for c in r)
                   for r in rows[1:])
    return ("<table><thead><tr>%s</tr></thead>%s</table>"
            % (head, "<tbody>%s</tbody>" % body if body else ""))


# ---------------------------------------------------------------- 段落渲染

_COMMENT_ANCHOR = re.compile(r"^<!--\s*p=(.*?)\s*-->\s*$")
_COMMENT_FOOTNOTE = re.compile(r"^<!--\s*footnote\s*-->\s*(.*)$")
_COMMENT_ANY = re.compile(r"^<!--.*-->\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_IMAGE = re.compile(r"^!\[(.*)\]\((.*)\)\s*$")
_HR = re.compile(r"^([-*_])\1{2,}$")


def _norm_compare(value: str) -> str:
    """比对标题用：去掉空白与 markdown 标记，不做语义改写。"""
    return re.sub(r"\s+", "", re.sub(r"[*`#>]", "", value or ""))


def _render_body(text: str, chapter_no: int, used_ids: set[str],
                 chapter_title: str = "") -> tuple[str, list[str], dict]:
    """一节的 md 正文 → (XHTML 片段, 引用到的 images/ 路径, 统计)。"""
    lines = text.splitlines()
    out: list[str] = []
    images: list[str] = []
    stats: Counter = Counter()
    i, n = 0, len(lines)
    skipped_title = False
    emitted = False
    fn_mode = False
    title_key = _norm_compare(chapter_title)

    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        m = _COMMENT_ANCHOR.match(stripped)
        if m:
            out.append(_anchor_span(m.group(1), chapter_no, used_ids))
            stats["anchors"] += 1
            i += 1
            continue
        m = _COMMENT_FOOTNOTE.match(stripped)
        if m:
            out.append('<p class="footnote">%s</p>' % _inline(m.group(1)))
            stats["footnotes"] += 1
            fn_mode = False
            emitted = True
            i += 1
            continue
        if _COMMENT_ANY.match(stripped):
            i += 1                       # doclab 头注释 / source_range，不进正文
            continue

        m = _HEADING.match(stripped)
        if m:
            level = len(m.group(1))
            if level == 1 and not emitted and not skipped_title:
                skipped_title = True     # 章节标题由外层统一渲染，避免重复 h1
                i += 1
                continue
            out.append("<h%d>%s</h%d>" % (level, _inline(m.group(2)), level))
            stats["headings"] += 1
            fn_mode = emitted = True
            i += 1
            continue

        m = re.match(r"^>\s*归属[：:]\s*(.*)$", stripped)
        if m:
            # 标题链只是导航信息，不算正文开始 —— 后面若跟着章节标题块，仍应被吞掉
            out.append('<p class="crumbs">归属：%s</p>' % _inline(m.group(1)))
            i += 1
            continue

        m = re.match(r"^\*\*脚注\*\*\s*(.*)$", stripped)
        if m:
            out.append('<p class="fn-head">%s</p>' % _inline("脚注" + m.group(1)))
            stats["footnote_blocks"] += 1
            fn_mode = emitted = True
            i += 1
            continue

        m = _IMAGE.match(stripped)
        if m:
            cap, src = m.group(1).strip(), m.group(2).strip()
            rel = _image_src(src)
            if rel:
                images.append(rel)
                out.append('<figure><img src="../%s" alt="%s"/>%s</figure>'
                           % (_attr(_href_url(rel)), _attr(cap),
                              "<figcaption>%s</figcaption>" % _inline(cap) if cap else ""))
                # exporter 会在图片下一行再写一遍 *说明*；已经放进 figcaption 了
                if cap and i + 1 < n and lines[i + 1].strip() == "*%s*" % cap:
                    i += 1
                stats["images"] += 1
            else:
                out.append("<p>%s</p>" % _inline(cap or src))
            fn_mode = False
            emitted = True
            i += 1
            continue

        if stripped.lower().startswith("<table"):
            chunk = [stripped]
            j = i
            while "</table>" not in chunk[-1].lower() and j + 1 < n:
                j += 1
                chunk.append(lines[j].strip())
            table_html = _html_table_to_xhtml("\n".join(chunk))
            if table_html:
                out.append(table_html)
                stats["tables"] += 1
                fn_mode = False
                emitted = True
                i = j + 1
                continue

        if "|" in stripped and i + 1 < n and _is_table_separator(lines[i + 1]):
            rows: list[list[str]] = []
            head_width = len(_table_cells(stripped))
            j = i
            while j < n:
                cur = lines[j].strip()
                if not cur or "|" not in cur:
                    break
                cells = _table_cells(cur)
                if rows and abs(len(cells) - head_width) > 1:
                    break
                rows.append(cells)
                j += 1
            # 第二行是 markdown 的 `---|---|---`，只用于定列，不进表体
            if len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", c) for c in rows[1]):
                rows = [rows[0]] + rows[2:]
            out.append(_table_html(rows))
            stats["tables"] += 1
            fn_mode = False
            emitted = True
            i = j
            continue

        m = _BULLET.match(stripped)
        if m:
            items: list[str] = []
            j = i
            while j < n:
                cur = _BULLET.match(lines[j].strip())
                if not cur:
                    break
                items.append("<li>%s</li>" % _inline(cur.group(1)))
                j += 1
            cls = ' class="fn-list"' if fn_mode else ""
            out.append("<ul%s>%s</ul>" % (cls, "".join(items)))
            stats["lists"] += 1
            fn_mode = False
            emitted = True
            i = j
            continue

        if stripped.startswith(">"):
            paras: list[str] = []
            j = i
            while j < n:
                cur = lines[j].strip()
                if not cur.startswith(">"):
                    break
                body = cur[1:].strip()
                if body:
                    paras.append("<p>%s</p>" % _inline(body))
                j += 1
            out.append("<blockquote>%s</blockquote>" % "".join(paras))
            stats["quotes"] += 1
            fn_mode = False
            emitted = True
            i = j
            continue

        if _HR.match(stripped):
            out.append("<hr/>")
            i += 1
            continue

        # exporter 把章节标题块也当普通正文写进了 md（`# 标题` 之外还有一行同样文字）。
        # 外层已经渲染 h1，这一行不再重复成段落；只吞第一段且只在标题完全一致时。
        if (not emitted and title_key and _norm_compare(stripped) == title_key):
            emitted = True
            i += 1
            continue

        if stripped.startswith(("[图片缺文件", "[图片格式不受支持")):
            out.append('<p class="missing-image">%s</p>' % _inline(stripped))
        else:
            out.append("<p>%s</p>" % _inline(stripped))
        stats["paragraphs"] += 1
        fn_mode = False
        emitted = True
        i += 1

    return "\n".join(out), sorted(set(images)), dict(stats)


def _first_heading(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^#\s+(.*)$", line.strip())
            if m:
                return m.group(1).strip()
            if line.strip() and not line.strip().startswith("<!--"):
                break
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------- 章节清单

def _chapter_records(out_dir: Path, chapters: list[dict] | None) -> list[dict]:
    """章节顺序与标题：优先用调用方给的（exporter 的 outputs），否则读 manifest。"""
    raw: list[dict] = []
    if chapters:
        raw = [dict(c) for c in chapters]
    else:
        meta = read_json(out_dir / "conversion_meta.json", {}) or {}
        manifest = read_json(out_dir / "manifest.json", {}) or {}
        src = manifest.get("files") or meta.get("outputs") or []
        raw = [dict(c) for c in src if isinstance(c, dict)]

    records: list[dict] = []
    seen: set[str] = set()
    for rec in raw:
        name = str(rec.get("path") or "")
        if not name or name in seen:
            continue
        p = out_dir / name
        if p.suffix.lower() != ".md" or p.name in EXCLUDED_MD or not p.is_file():
            continue
        seen.add(name)
        title = str(rec.get("title") or "").strip() or _first_heading(p)
        try:
            level = max(1, int(rec.get("level") or 1))
        except (TypeError, ValueError):
            level = 1
        records.append({
            "seq": int(rec.get("seq") or len(records) + 1),
            "path": name,
            "title": title or "第 %d 节" % (len(records) + 1),
            "level": level,
            "belongs_to": str(rec.get("belongs_to") or ""),
        })
    records.sort(key=lambda r: (r["seq"], r["path"]))

    if not records:                      # manifest 缺失时的兜底：只认 doclab 切出来的文件名
        for p in sorted(out_dir.glob("*.md")):
            if p.name in EXCLUDED_MD or not BODY_MD_RE.match(p.name):
                continue
            records.append({"seq": len(records) + 1, "path": p.name,
                            "title": _first_heading(p) or p.stem,
                            "level": 1, "belongs_to": ""})
    return records


def _book_title(out_dir: Path, title: str | None) -> str:
    if (title or "").strip():
        return title.strip()
    meta = read_json(out_dir / "conversion_meta.json", {}) or {}
    t = str(meta.get("source_file") or "").strip()
    if t:
        return t
    index = out_dir / "00-目录.md"
    if index.is_file():
        try:
            first = index.read_text(encoding="utf-8").splitlines()[0].strip()
            t = re.sub(r"^#\s*", "", first)
            t = re.sub(r"\s*——\s*分节目录\s*$", "", t).strip()
            if t:
                return t
        except (OSError, IndexError):
            pass
    return out_dir.parent.name or "doclab 导出"


def _language(value: str | None) -> str:
    s = re.sub(r"[^A-Za-z0-9-]", "", (value or "").strip())
    return s or "zh"


def _iso_utc(value: str | None) -> str:
    if value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _zip_path(base: str, href: str) -> str:
    href = unquote(str(href or "")).split("#", 1)[0]
    return posixpath.normpath(posixpath.join(base or "", href))


def _href_url(href: str) -> str:
    """OPF/正文里的引用必须是合法 URI：空格、中文等按百分号编码。

    zip 里的条目名保持原样；读取方（本仓库的 converter 与常规阅读器）会先 unquote
    再落地，所以编码只影响引用文本，不影响文件名。
    """
    return quote(str(href or ""), safe="/._-~")


def _sha256_file(path: Path) -> str:
    """整个文件的裸 sha256（不掺文件长度）。

    util.sha256_file 默认会给哈希加长度前缀，适合去重；归档回执用的是裸 sha256，
    这里必须与 intake/Zotero 的 digest 保持一致，否则「文件没变」会被判成已变化。
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 目录 / 元数据

def _nav_tree(chapters: list[dict], hrefs: list[str]) -> list[dict]:
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for ch, href in zip(chapters, hrefs):
        node = {"title": ch["title"], "href": href, "children": []}
        level = max(1, int(ch["level"]))
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots


def _nav_ol(nodes: list[dict]) -> str:
    if not nodes:
        return ""
    items = []
    for node in nodes:
        sub = _nav_ol(node["children"])
        items.append('<li><a href="%s">%s</a>%s</li>'
                     % (_attr(_href_url(node["href"])), _text(node["title"]), sub))
    return "<ol>%s</ol>" % "".join(items)


def _nav_count(nodes: list[dict]) -> int:
    return sum(1 + _nav_count(n["children"]) for n in nodes)


def _nav_xhtml(roots: list[dict], lang: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" lang="%s" xml:lang="%s">\n'
            '<head><meta charset="utf-8"/><title>目录</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            '<body>\n<nav epub:type="toc" id="toc"><h1>目录</h1>\n%s\n</nav>\n'
            '<nav epub:type="landmarks" hidden="hidden"><ol>'
            '<li><a epub:type="toc" href="nav.xhtml">目录</a></li>'
            '</ol></nav>\n</body>\n</html>\n'
            % (XHTML_NS, EPUB_NS, _attr(lang), _attr(lang), _nav_ol(roots)))


def _ncx(title: str, roots: list[dict], identifier: str, lang: str) -> str:
    order = [0]

    def points(nodes: list[dict], depth: int) -> tuple[str, int]:
        out, max_depth = [], depth
        for node in nodes:
            order[0] += 1
            n = order[0]
            sub, sub_depth = points(node["children"], depth + 1)
            max_depth = max(max_depth, sub_depth)
            out.append('<navPoint id="nav%d" playOrder="%d">'
                       '<navLabel><text>%s</text></navLabel>'
                       '<content src="%s"/>%s</navPoint>'
                       % (n, n, _text(node["title"]), _attr(_href_url(node["href"])), sub))
        return "".join(out), max_depth

    body, depth = points(roots, 1)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" '
            'xml:lang="%s">\n'
            '<head><meta name="dtb:uid" content="%s"/><meta name="dtb:depth" content="%d"/>'
            '<meta name="dtb:totalPageCount" content="0"/>'
            '<meta name="dtb:maxPageNumber" content="0"/></head>\n'
            '<docTitle><text>%s</text></docTitle>\n<navMap>%s</navMap>\n</ncx>\n'
            % (_attr(lang), _attr(identifier), max(1, depth), _text(title), body))


def _titlepage_xhtml(title: str, author: str, subtitle: str, lang: str) -> str:
    author_p = ('<p class="author">%s</p>' % _text(author)) if author else ""
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" lang="%s" xml:lang="%s">\n'
            '<head><meta charset="utf-8"/><title>%s</title>'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head>\n'
            '<body>\n<section class="titlepage" epub:type="titlepage">\n'
            '<h1 class="booktitle">%s</h1>\n%s\n'
            '<p class="meta">%s</p>\n<p class="meta">Doclab 装订</p>\n'
            '</section>\n</body>\n</html>\n'
            % (XHTML_NS, EPUB_NS, _attr(lang), _attr(lang), _text(title),
               _text(title), author_p, _text(subtitle)))


def _cover_xhtml(href: str, lang: str) -> str:
    """封面页：只放一张图，不加标题/说明，避免阅读器封面页出现多余文字。"""
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" lang="%s" xml:lang="%s">\n'
            '<head><meta charset="utf-8"/><title>封面</title>'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head>\n'
            '<body class="cover">\n<section class="cover" epub:type="cover">\n'
            '<img src="%s" alt="封面"/>\n</section>\n</body>\n</html>\n'
            % (XHTML_NS, EPUB_NS, _attr(lang), _attr(lang), _attr(_href_url(href))))


def _chapter_xhtml(ch: dict, body: str, lang: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" lang="%s" xml:lang="%s">\n'
            '<head><meta charset="utf-8"/><title>%s</title>'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head>\n'
            '<body>\n<section class="chapter" epub:type="chapter">\n'
            '<h1>%s</h1>\n%s\n</section>\n</body>\n</html>\n'
            % (XHTML_NS, EPUB_NS, _attr(lang), _attr(lang), _text(ch["title"]),
               _text(ch["title"]), body))


def _package_opf(title: str, author: str, lang: str, identifier: str,
                 modified: str, source_id: str, items: list[dict],
                 spine_ids: list[str], text_href: str,
                 cover_href: str | None = None) -> str:
    meta = [
        '<dc:identifier id="bookid">%s</dc:identifier>' % _text(identifier),
        '<dc:title>%s</dc:title>' % _text(title),
        '<dc:language>%s</dc:language>' % _text(lang),
    ]
    if author:
        meta.append('<dc:creator>%s</dc:creator>' % _text(author))
    if source_id:
        meta.append('<dc:source>%s</dc:source>' % _text(source_id))
    meta.append('<meta property="dcterms:modified">%s</meta>' % _text(modified))
    if cover_href:
        # EPUB2 兼容元数据；EPUB3 阅读器主要看 manifest 的 cover-image。
        meta.append('<meta name="cover" content="cover-image"/>')

    man = []
    for it in items:
        props = ' properties="%s"' % _attr(it["properties"]) if it.get("properties") else ""
        man.append('<item id="%s" href="%s" media-type="%s"%s/>'
                   % (_attr(it["id"]), _attr(_href_url(it["href"])),
                      _attr(it["media_type"]), props))
    spine = "".join('<itemref idref="%s"/>' % _attr(i) for i in spine_ids)
    guide = '<guide><reference type="toc" title="目录" href="nav.xhtml"/>'
    if cover_href:
        guide += '<reference type="cover" title="封面" href="%s"/>' % _attr(
            _href_url(cover_href))
    guide += '<reference type="text" title="正文" href="%s"/></guide>' % _attr(
        _href_url(text_href))
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid" xml:lang="%s">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n%s\n</metadata>\n'
            '<manifest>\n%s\n</manifest>\n'
            '<spine toc="ncx">%s</spine>\n'
            '%s\n'
            '</package>\n'
            % (_attr(lang), "\n".join(meta), "\n".join(man), spine, guide))


def _container_xml(opf_path: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '<rootfiles><rootfile full-path="%s" '
            'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n'
            % _attr(opf_path))


# ---------------------------------------------------------------- 写 zip

def _write_zip(dest: Path, files: list[tuple[str, bytes, int]]) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".epub-", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w") as z:
            for name, data, compress in files:
                info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
                info.compress_type = compress
                info.external_attr = 0o600 << 16
                z.writestr(info, data)
        return Path(tmp)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- 主入口

def build_epub(out_dir: Path, dest: Path | str | None = None,
               chapters: list[dict] | None = None, title: str | None = None,
               author: str | None = None, language: str | None = None,
               source_id: str | None = None, identifier: str | None = None,
               modified: str | None = None, strict: bool = False,
               record: bool = False, cover: dict | None = None,
               cover_warning: str = "") -> dict:
    """把 out_dir 里的分节 md 装订成 EPUB，写完立刻做结构自检。

    cover：可选的封面 dict（data/media_type/width/height/kind/source），
    由 core.cover 从原 PDF 第一页或原 EPUB 提取；为空则不生成封面页。
    strict=True：缺图/不支持的图片格式直接报错（宁可失败也不交付残书）。
    strict=False（默认）：坏图行换成占位文字并记 warning，由调用方决定是否放行；
    入库侧会把缺图并入 coverage，阶段回到 needs_review，不会静默归档。
    record=True：把本次结果合并回 conversion_meta.json 的 `epub` 字段（单独重跑用）。
    校验不通过一律删掉临时产物并抛 ValueError —— 不把坏文件留在产物目录里。
    """
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        raise ValueError(f"导出目录不存在：{out_dir}")

    records = _chapter_records(out_dir, chapters)
    if not records:
        raise ValueError(f"{out_dir} 里没有可装订的章节 markdown —— 先跑 export")

    cover_item = None
    cover_meta = None
    cover_rel = None
    if cover and cover.get("data"):
        media = str(cover.get("media_type") or "image/jpeg").lower()
        ext = COVER_EXT.get(media, ".jpg")
        cover_rel = "images/cover" + ext
        cover_item = {"id": "cover-image", "href": cover_rel,
                      "media_type": media, "properties": "cover-image"}
        cover_meta = {
            "href": cover_rel, "media_type": media,
            "width": cover.get("width"), "height": cover.get("height"),
            "kind": cover.get("kind") or "", "source": cover.get("source") or "",
            "bytes": len(cover["data"]),
        }

    book_title = _book_title(out_dir, title)
    author = (author or "").strip()
    lang = _language(language)
    source_id = (source_id or "").strip()
    if not identifier:
        seed = "doclab-book:" + (source_id or book_title)
        identifier = "urn:uuid:%s" % uuid.uuid5(uuid.NAMESPACE_URL, seed)
    if not modified:
        # 同一份导出反复装订应当得到同样字节：优先沿用导出时间，而不是每次 now()。
        converted = (read_json(out_dir / "conversion_meta.json", {}) or {}).get("converted_at")
        modified = converted
    modified = _iso_utc(modified)

    # 1) 渲染各节。缺图 / 不受支持的图在预处理阶段就换成占位文字，不把坏 <img> 带进 EPUB。
    missing: list[str] = []
    unsupported: list[str] = []
    warnings: list[str] = []
    if cover_warning:
        warnings.append(cover_warning)
    rendered: list[dict] = []
    image_refs: list[str] = []
    totals: Counter = Counter()
    for no, rec in enumerate(records, 1):
        path = out_dir / rec["path"]
        text = path.read_text(encoding="utf-8")
        text, gone, bad = _strip_unusable_images(text, out_dir)
        missing.extend(gone)
        unsupported.extend(bad)
        used_ids: set[str] = set()
        body, imgs, stats = _render_body(text, no, used_ids, rec["title"])
        image_refs.extend(imgs)
        totals.update(stats)
        rendered.append({"seq": no, "title": rec["title"], "level": rec["level"],
                         "path": rec["path"], "body": body,
                         "href": "text/ch%03d.xhtml" % no})

    missing = sorted(set(missing))
    unsupported = sorted(set(unsupported))
    if strict and (missing or unsupported):
        reasons = []
        if missing:
            reasons.append("缺图 " + "、".join(missing[:8]))
        if unsupported:
            reasons.append("不支持的格式 " + "、".join(unsupported[:8]))
        raise ValueError("图片没有随导出搬运或格式不受支持，不能装订：" + "；".join(reasons))
    for rel in missing:
        warnings.append("缺图，已置为占位：%s" % rel)
    for rel in unsupported:
        warnings.append("图片格式不受 EPUB 支持，已置为占位：%s" % rel)

    image_refs = sorted(set(image_refs))
    image_items: list[dict] = []
    image_allowed: list[str] = []
    for rel in image_refs:
        p = out_dir / rel
        ext = p.suffix.lower()
        if not p.is_file() or ext not in MEDIA_TYPES:
            # 预处理已挡住；这里只留一道防线，不再重复报同一张图
            warnings.append("图片未能装进 EPUB，已跳过：%s" % rel)
            continue
        if ext in NON_CORE_IMAGE:
            warnings.append("图片不是 EPUB3 核心媒体类型，阅读器可能不显示：%s" % rel)
        image_allowed.append(rel)
        image_items.append({"id": "img%03d" % (len(image_items) + 1),
                            "href": rel, "media_type": MEDIA_TYPES[ext],
                            "properties": ""})
    # 2) 目录与文档
    roots = _nav_tree(rendered, [r["href"] for r in rendered])
    nav_entries = _nav_count(roots)
    items = [
        {"id": "nav", "href": "nav.xhtml", "media_type": "application/xhtml+xml",
         "properties": "nav"},
        {"id": "ncx", "href": "toc.ncx", "media_type": "application/x-dtbncx+xml",
         "properties": ""},
        {"id": "css", "href": "style.css", "media_type": "text/css", "properties": ""},
    ]
    if cover_item:
        items.append({"id": "cover", "href": "text/cover.xhtml",
                      "media_type": "application/xhtml+xml", "properties": ""})
    items.append({"id": "titlepage", "href": "text/titlepage.xhtml",
                  "media_type": "application/xhtml+xml", "properties": ""})
    for r in rendered:
        items.append({"id": "ch%03d" % r["seq"], "href": r["href"],
                      "media_type": "application/xhtml+xml", "properties": ""})
    if cover_item:
        items.append(cover_item)
    items.extend(image_items)

    spine = (["cover"] if cover_item else []) + ["titlepage"] + [
        "ch%03d" % r["seq"] for r in rendered]
    opf = _package_opf(book_title, author, lang, identifier, modified, source_id,
                       items, spine, rendered[0]["href"],
                       "text/cover.xhtml" if cover_item else None)
    subtitle = "共 %d 节" % len(rendered)

    files: list[tuple[str, bytes, int]] = [
        ("mimetype", EPUB_MIMETYPE, zipfile.ZIP_STORED),
        ("META-INF/container.xml", _container_xml("OEBPS/content.opf").encode("utf-8"),
         zipfile.ZIP_DEFLATED),
        ("OEBPS/content.opf", opf.encode("utf-8"), zipfile.ZIP_DEFLATED),
        ("OEBPS/nav.xhtml", _nav_xhtml(roots, lang).encode("utf-8"),
         zipfile.ZIP_DEFLATED),
        ("OEBPS/toc.ncx", _ncx(book_title, roots, identifier, lang).encode("utf-8"),
         zipfile.ZIP_DEFLATED),
        ("OEBPS/style.css", CSS.encode("utf-8"), zipfile.ZIP_DEFLATED),
        ("OEBPS/text/titlepage.xhtml",
         _titlepage_xhtml(book_title, author, subtitle, lang).encode("utf-8"),
         zipfile.ZIP_DEFLATED),
    ]
    if cover_item:
        files.append(("OEBPS/text/cover.xhtml",
                      _cover_xhtml("../" + cover_rel, lang).encode("utf-8"),
                      zipfile.ZIP_DEFLATED))
        files.append(("OEBPS/" + cover_rel, cover["data"], zipfile.ZIP_STORED))
    for r in rendered:
        files.append(("OEBPS/" + r["href"],
                      _chapter_xhtml(r, r["body"], lang).encode("utf-8"),
                      zipfile.ZIP_DEFLATED))
    for rel in image_allowed:
        files.append(("OEBPS/" + rel, (out_dir / rel).read_bytes(), zipfile.ZIP_STORED))

    dest = Path(dest) if dest else out_dir / (safe_filename(book_title, 80) + ".epub")
    if dest.is_dir():
        dest = dest / (safe_filename(book_title, 80) + ".epub")
    if dest.suffix.lower() != ".epub":
        dest = dest.with_name(dest.name + ".epub")

    tmp = _write_zip(dest, files)
    try:
        report = verify_epub(tmp, expected_chapters=len(rendered))
        if not report["ok"]:
            raise ValueError("EPUB 结构自检未通过：" + "；".join(report["errors"][:6]))
        # 目标 EPUB 可能正被阅读器打开（Windows 上 replace 会抛 PermissionError）。
        # 与 util.write_json 同样的退避重试：文件没有被改坏，只是被短暂占用。
        last = None
        for attempt in range(20):
            try:
                os.replace(tmp, dest)
                last = None
                break
            except PermissionError as e:                 # WinError 5
                last = e
                time.sleep(0.05 * (1 + attempt % 4))
        if last is not None:
            raise last
    finally:
        tmp.unlink(missing_ok=True)

    # 换过书名的上一轮产物：只清**导出目录里**的 .epub，md/images 一律不动。
    # 用 --out 指到别处时不能在用户目录里删东西 —— 那里可能有别人的 EPUB。
    if dest.parent.resolve() == out_dir.resolve():
        for old in dest.parent.glob("*.epub"):
            if old != dest:
                try:
                    old.unlink()
                except OSError:
                    pass

    info = {
        "path": str(dest), "name": dest.name, "bytes": dest.stat().st_size,
        "sha256": _sha256_file(dest),
        "title": book_title, "author": author, "language": lang,
        "identifier": identifier, "modified": modified,
        "chapters": len(rendered), "images": len(image_allowed),
        "cover": cover_meta,
        "cover_warning": cover_warning or None,
        "nav_entries": nav_entries, "source_id": source_id,
        "warnings": warnings, "missing_images": missing,
        "unsupported_images": unsupported,
        "stats": dict(totals),
        "verify": report,
        "generated_at": now_iso(),
    }
    if record and dest.parent.resolve() == out_dir.resolve():
        p = out_dir / "conversion_meta.json"
        meta = read_json(p, None)
        if isinstance(meta, dict):
            meta["epub"] = dict(info)
            write_json(p, meta)
    return info


def _strip_unusable_images(text: str, out_dir: Path) -> tuple[str, list[str], list[str]]:
    """把缺文件 / 格式不受支持的图片行换成占位文字，并顺手吃掉重复的 *说明* 行。

    返回 (新 md 文本, 缺图列表, 格式不支持列表)。两类都进不了 EPUB：前者是导出没搬图，
    后者是阅读器不保证渲染。占位文字比坏 <img> 诚实，也便于 `verify_epub` 过结构检查。
    """
    lines = text.splitlines()
    out: list[str] = []
    missing: list[str] = []
    unsupported: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _IMAGE.match(line.strip())
        if m:
            cap, src = m.group(1).strip(), m.group(2).strip()
            rel = _image_src(src)
            if rel:
                p = out_dir / rel
                label = None
                if not p.is_file():
                    missing.append(rel)
                    label = cap or rel
                    placeholder = "[图片缺文件：%s]" % label
                elif p.suffix.lower() not in MEDIA_TYPES:
                    unsupported.append(rel)
                    label = cap or rel
                    placeholder = "[图片格式不受支持：%s]" % label
                if label is not None:
                    out.append(placeholder)
                    if cap and i + 1 < len(lines) and lines[i + 1].strip() == "*%s*" % cap:
                        i += 1
                    i += 1
                    continue
        out.append(line)
        i += 1
    return "\n".join(out), missing, unsupported


# ---------------------------------------------------------------- 结构自检

def verify_epub(path: Path | str, expected_chapters: int | None = None) -> dict:
    """机械核验 EPUB：zip 布局、OPF/spine/nav、XHTML 合法性、图片引用。

    这不是阅读器视觉验收；只回答「这个包是否结构完整、链接是否都落地」。
    """
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict = {}
    if not path.is_file():
        return {"ok": False, "errors": ["文件不存在：%s" % path], "warnings": [],
                "stats": stats}
    stats["bytes"] = path.stat().st_size
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        return {"ok": False, "errors": ["不是有效 ZIP：%s" % e], "warnings": [],
                "stats": stats}

    with z:
        names = z.namelist()
        stats["entries"] = len(names)
        try:
            bad = z.testzip()
            if bad:
                errors.append("ZIP 条目 CRC 校验失败：%s" % bad)
        except Exception as e:                       # noqa: BLE001
            errors.append("ZIP 完整性检查失败：%s" % e)
        if not names:
            errors.append("ZIP 是空的")
        elif names[0] != "mimetype":
            errors.append("mimetype 不是 ZIP 的第一个条目")
        if "mimetype" not in names:
            errors.append("缺少 mimetype")
        else:
            info = z.getinfo("mimetype")
            if info.compress_type != zipfile.ZIP_STORED:
                errors.append("mimetype 必须不压缩（ZIP_STORED）")
            try:
                if z.read("mimetype") != EPUB_MIMETYPE:
                    errors.append("mimetype 内容不是 application/epub+zip")
            except (OSError, zipfile.BadZipFile) as e:
                errors.append("读取 mimetype 失败：%s" % e)

        opf_path = None
        if "META-INF/container.xml" not in names:
            errors.append("缺少 META-INF/container.xml")
        else:
            try:
                root = ET.fromstring(z.read("META-INF/container.xml"))
                rootfile = next((e for e in root.iter()
                                 if _local(e.tag) == "rootfile"), None)
                if rootfile is None or not rootfile.get("full-path"):
                    errors.append("container.xml 没有 rootfile")
                else:
                    opf_path = posixpath.normpath(rootfile.get("full-path"))
                    if opf_path not in names:
                        errors.append("container.xml 指向的 OPF 不存在：%s" % opf_path)
                        opf_path = None
            except ET.ParseError as e:
                errors.append("container.xml 不是合法 XML：%s" % e)
            except (zipfile.BadZipFile, OSError) as e:
                errors.append("读取 container.xml 失败：%s" % e)

        if opf_path:
            opf_dir = posixpath.dirname(opf_path)
            items, spine_ids = _parse_opf(z, opf_path, names, errors, warnings, stats)
            if items is not None:
                stats["spine"] = len(spine_ids)
                chapters = []
                img_refs: set[str] = set()
                for idref in spine_ids:
                    item = items.get(idref)
                    if item is None:
                        continue
                    rel = _zip_path(opf_dir, item.get("href"))
                    if re.search(r"(^|/)ch\d+\.xhtml$", rel):
                        chapters.append(rel)
                    doc = _parse_xhtml(z, rel, errors)
                    if doc is None:
                        continue
                    for el in doc.iter():
                        if _local(el.tag) != "img":
                            continue
                        src = (el.get("src") or "").strip()
                        if not src:
                            errors.append("%s 里有无 src 的图片" % rel)
                            continue
                        if src.startswith(("http://", "https://", "data:")):
                            warnings.append("%s 引用了外部资源：%s" % (rel, src[:60]))
                            continue
                        target = _zip_path(posixpath.dirname(rel), src)
                        img_refs.add(target)
                        if target not in names:
                            errors.append("%s 引用的图片不存在：%s" % (rel, src))
                stats["chapters"] = len(chapters)
                stats["images"] = len(img_refs)
                if expected_chapters is not None and len(chapters) != int(expected_chapters):
                    errors.append("章节数不符：spine 中 %d，预期 %s"
                                  % (len(chapters), expected_chapters))

                nav_done = False
                for item in items.values():
                    if "nav" not in (item.get("properties") or "").split():
                        continue
                    nav_rel = _zip_path(opf_dir, item.get("href"))
                    nav_doc = _parse_xhtml(z, nav_rel, errors)
                    if nav_doc is None:
                        continue
                    toc = None
                    for nav in nav_doc.iter():
                        if _local(nav.tag) != "nav":
                            continue
                        if (nav.get("{%s}type" % EPUB_NS) or "").strip() == "toc":
                            toc = nav
                            break
                    if toc is None:
                        errors.append("nav 文档里没有 epub:type=\"toc\" 的目录")
                        continue
                    links = [a.get("href") for a in toc.iter()
                             if _local(a.tag) == "a" and a.get("href")]
                    stats["nav_entries"] = len(links)
                    if not links:
                        errors.append("目录 nav 是空的")
                    for href in links:
                        if href.startswith(("http://", "https://")):
                            continue
                        target = _zip_path(posixpath.dirname(nav_rel), href.split("#")[0])
                        if target not in names:
                            errors.append("目录链接指向不存在的文件：%s" % href)
                    nav_done = True
                    break
                if not nav_done:
                    errors.append("manifest 里没有可用的 nav 文档")

    return {"ok": not errors, "errors": errors[:60], "warnings": warnings[:40],
            "stats": stats}


def _parse_opf(z: zipfile.ZipFile, opf_path: str, names: list[str],
               errors: list[str], warnings: list[str], stats: dict):
    try:
        opf = ET.fromstring(z.read(opf_path))
    except ET.ParseError as e:
        errors.append("OPF 不是合法 XML：%s" % e)
        return None, []
    except (zipfile.BadZipFile, OSError) as e:
        errors.append("读取 OPF 失败：%s" % e)
        return None, []
    stats["opf"] = opf_path
    version = (opf.get("version") or "").strip()
    if version and not version.startswith("3"):
        warnings.append("OPF 版本是 %s，本工具按 EPUB3 生成" % version)

    metadata = next((e for e in opf if _local(e.tag) == "metadata"), None)
    if metadata is None:
        errors.append("OPF 缺少 metadata")
    else:
        title_el = next((e for e in metadata
                         if _local(e.tag) == "title" and (e.text or "").strip()), None)
        if title_el is None:
            errors.append("OPF 缺少 dc:title")
        else:
            stats["title"] = title_el.text.strip()
        ident = next((e for e in metadata
                      if _local(e.tag) == "identifier" and (e.text or "").strip()), None)
        if ident is None:
            errors.append("OPF 缺少 dc:identifier")
        else:
            stats["identifier"] = ident.text.strip()
        lang = next((e for e in metadata if _local(e.tag) == "language"), None)
        if lang is None or not (lang.text or "").strip():
            errors.append("OPF 缺少 dc:language")
        else:
            stats["language"] = lang.text.strip()
        modified = next((e for e in metadata if _local(e.tag) == "meta"
                         and (e.get("property") or "").strip() == "dcterms:modified"), None)
        if modified is None:
            errors.append("OPF 缺少 dcterms:modified")
        else:
            stats["modified"] = (modified.text or "").strip()
        uid = opf.get("unique-identifier")
        if uid and not any(e.get("id") == uid for e in metadata):
            errors.append("unique-identifier 指向不存在的 dc:identifier")

    manifest = next((e for e in opf if _local(e.tag) == "manifest"), None)
    items: dict[str, dict] = {}
    if manifest is None:
        errors.append("OPF 缺少 manifest")
    else:
        for it in manifest:
            if _local(it.tag) != "item":
                continue
            iid, href, media = it.get("id"), it.get("href"), it.get("media-type")
            if not iid or not href:
                errors.append("manifest 条目缺少 id 或 href")
                continue
            if iid in items:
                errors.append("manifest id 重复：%s" % iid)
            items[iid] = {"id": iid, "href": href, "media_type": media or "",
                          "properties": it.get("properties") or ""}
            rel = _zip_path(posixpath.dirname(opf_path), href)
            if rel not in names:
                errors.append("manifest 指向的文件不存在：%s" % href)
            if not media:
                warnings.append("manifest 条目缺 media-type：%s" % href)

    spine = next((e for e in opf if _local(e.tag) == "spine"), None)
    spine_ids: list[str] = []
    if spine is None:
        errors.append("OPF 缺少 spine")
    else:
        for ref in spine:
            if _local(ref.tag) != "itemref":
                continue
            idref = ref.get("idref")
            if idref not in items:
                errors.append("spine 引用不存在的 manifest id：%s" % idref)
                continue
            spine_ids.append(idref)
    if not spine_ids:
        errors.append("spine 是空的")
    navs = [it for it in items.values()
            if "nav" in (it.get("properties") or "").split()]
    if not navs:
        errors.append("manifest 里没有 properties=\"nav\" 的目录文档")
    covers = [it for it in items.values()
              if "cover-image" in (it.get("properties") or "").split()]
    stats["cover"] = covers[0]["href"] if covers else None
    return items, spine_ids


def _parse_xhtml(z: zipfile.ZipFile, rel: str, errors: list[str]):
    try:
        return ET.fromstring(z.read(rel))
    except KeyError:
        return None
    except ET.ParseError as e:
        errors.append("%s 不是合法 XHTML：%s" % (rel, e))
        return None
    except (zipfile.BadZipFile, OSError) as e:
        errors.append("读取 %s 失败：%s" % (rel, e))
        return None
