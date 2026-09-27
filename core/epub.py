"""EPUB 入库：把 EPUB 解析成 doclab 的块序列（写成 MinerU 形态的工程，下游一行不用改）。

分源规则按 JingyeLab APR-20260920-001《书级文献转换层页锚规范》§2.2：

    EPUB —— 无物理页码，不伪造；按章节切分，定位符合法形式为「章·节 + 版本注明」
             → locator_type = section_only

所以本模块**不产出任何 page_number 块**，页码校准必然降级 section_only、页锚写
`p=pdf-N unmapped`。EPUB 正文里那些原版边码（`F7` / `E423` / `FXVII`）是**外文书页码**，
一律按原文照抄进正文，**绝不拿来当纸书页码建锚** —— 那是伪造。

解析逻辑移植自独立技能 `epub-to-markdown`（脚本 `epub2md.py`，本机已用它转过四本中文书）。
内联一份是为了让 doclab 内核自洽（工作台不该去 import 一个技能目录），技能目录保持原样不动。
相对原脚本的四点改动，都为了对接 doclab 的块模型：

  1. page_idx ＝ **spine 阅读顺序单元**（每个 xhtml 文档一个），不是页码。全书一个分片 P1。
  2. 标题层级取自 EPUB 自己的 `<h1>..<h6>`，并**以「文件首个标题层级」的众数为基准**
     （众数档 → L1），比中文编号可靠，所以标 `level_trusted=True` 交给 outline 直接采纳。
  3. `page_idx` 随块落盘，导出的 md 才能在单元边界插锚。
  4. 图片抽到 `images/` 并生成 image 块（原脚本直接丢图，实测四本书共 37 张）。

跨页断句归并与注码/边码顺移沿用原脚本的 `reflow()`；差别只是它按「章」归并，这里按
**整本书**归并 —— 一本书被切成若干 xhtml 是设备所为，句子照样会被切在中间。
"""
from __future__ import annotations

import hashlib
import html
import posixpath
import re
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from .project import save_project, scan_shard
from .util import sha256_file, now_iso, write_json

Log = Callable[[str], None]

# ------------------------------------------------------------------ 判定规则
DEFAULT_CHAPTER_RE = (
    r'^(第\s*[0-9一二三四五六七八九十百]+\s*[章篇部讲]|'
    r'前言|序言|序【|导论|导言|引言|绪论|后记|译后记|译序|再版|增订|献\s*辞|题辞|题词|'
    r'体例|凡例|出版说明|中译本|法文版|德文版|英文版|'
    r'附录[一二三四五六七八九十0-9]|'
    r'索引|术语索引|人名|地名|引用作品|参考文献|年表|'
    r'Chapter|Preface|Introduction|Afterword|Appendix|Index|Notes|Bibliography)'
)

CIRCLED = set('①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚')
CJK = r'\u4e00-\u9fff'
SENT_END = '。！？…⋯'
EDGE_RE = re.compile(r'^[FE](?:\d{1,4}|[IVXLCDM]{1,7})$', re.I)
EDGE_INL = re.compile(r'^`[FfEe][0-9IVXLCDM]{1,7}`\s*')
EDGE_BARE = re.compile(r'^[FfEe](?:\d{1,4}|[IVXLCDM]{1,7})[ \t]+')
NOTE_FRAGMENT = re.compile(
    r'^((?:.{0,140}?)(?:——\s*中译者注|—中译者注|一中译者注|·中译者注|--中译者注'
    r'|——\s*原注|一原注|--原注|----原注|。一中译者注))(.{40,})$', re.S)

# 非块级空元素（保留 img：它要变成 image 块）
VOID = {'meta', 'link', 'br', 'hr', 'wbr', 'input', 'area', 'base', 'col', 'source', 'track'}
IMG_TAGS = {'img'}
IMG_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')


# ------------------------------------------------------------------ DOM
class Node:
    __slots__ = ('tag', 'attrs', 'children')

    def __init__(self, tag):
        self.tag, self.attrs, self.children = tag, {}, []

    def style(self):
        return (self.attrs.get('style', '') or '').replace(' ', '').lower()


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root, self.stack = Node('#root'), []

    def _attach(self, n: Node) -> None:
        (self.stack[-1].children if self.stack else self.root.children).append(n)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in VOID:
            return
        if tag == 'p' and self.stack and self.stack[-1].tag == 'p':
            self.stack.pop()
        if tag == 'li' and self.stack and self.stack[-1].tag in ('li', 'p'):
            self.stack.pop()
        n = Node(tag)
        n.attrs = dict(attrs)
        self._attach(n)
        if tag not in IMG_TAGS:          # img 是空元素，不压栈
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in IMG_TAGS:              # <img/>：只留节点，不进文本
            n = Node(tag)
            n.attrs = dict(attrs)
            self._attach(n)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data and self.stack:
            self.stack[-1].children.append(data)


def parse(xhtml: str) -> Node:
    b = Builder()
    b.feed(xhtml)
    q = [b.root]
    while q:
        c = q.pop(0)
        if c.tag == 'body':
            return c
        q += [x for x in c.children if isinstance(x, Node)]
    return b.root


# ------------------------------------------------------------------ 文本层
def clean(s: str) -> str:
    s = html.unescape(s)
    s = s.replace('\u00a0', ' ').replace('\u2007', ' ').replace('\ufeff', '')
    s = re.sub(r'[\u200b-\u200f\u202a-\u202e\ue000-\uf8ff]', '', s)
    s = s.replace('\r', '\n').replace('\u2028', ' ').replace('\u2029', ' ')
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r' *\n *', '\n', s)
    s = re.sub(r'\n{2,}', '\n', s)
    s = re.sub(r'(?<=[%s，。、；：！“”‘’（）《》〈〉【】—…·])\s+(?=[%s，。、；：！“”‘’（）《》〈〉【】—…·])'
               % (CJK, CJK), '', s)
    s = re.sub(r'^([0-9]{1,2})[ ,.．]\s*(?=[%s])' % CJK, r'\1. ', s)
    s = re.sub(r'(?<=[%s]) (?=[A-Za-z(])' % CJK, '', s)
    s = re.sub(r'(?<=[A-Za-z)]|[,.\-]) (?=[%s])' % CJK, ' ', s)
    s = re.sub(r'(?<![A-Za-z0-9])([FfEe](?:\d{1,4}|[IVXLCDMivxlcdm]{2,7}))[ \t]*(?=[%s])' % CJK,
               lambda m: '`%s` ' % m.group(1), s)
    s = re.sub(r'(?m)^([FfEe](?:\d{1,4}|[IVXLCDM]{2,7}))[ \t]+(?=[%sA-Z])' % CJK, r'`\1` ', s)
    s = s.replace('<', '&lt;')
    s = re.sub(r'(?m)^>', '&gt;', s)
    return s.strip()


def inline(node) -> str:
    if isinstance(node, str):
        return node
    t = node.tag
    inner = ''.join(inline(c) for c in node.children)
    if t == 'sub':
        return ''                                   # OCR 杂字符
    if t == 'sup':
        s = inner.strip()
        return inner if (re.search(r'\d', s) or s.startswith('[')) else ''
    if t in ('span', 'u', 'font', 'big', 'small', 'center', 'ruby', 'rt', 'rp', 'a',
             'body', 'div', 'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        return inner
    if t in ('i', 'em'):
        core = re.sub(r'\s+', ' ', inner.strip())
        return '*%s*' % core if core and not all(c in CIRCLED for c in core) else inner
    if t in ('b', 'strong'):
        core = re.sub(r'\s+', ' ', inner.strip())
        return '**%s**' % core if core else inner
    if t == 'img':
        return ''
    return '\n' if t == 'br' else inner


def to_line(node) -> str:
    return clean(inline(node))


def is_note(txt: str) -> bool:
    return bool(txt) and txt[0] in CIRCLED


def is_edge_block(node: Node):
    kids = [c for c in node.children if isinstance(c, Node)]
    ps = [c for c in kids if c.tag == 'p']
    if not ps or len(ps) != len(kids):
        return None
    vals = []
    for p in ps:
        t = to_line(p).replace(' ', '')
        if not EDGE_RE.match(t):
            return None
        vals.append(t)
    return vals


# ------------------------------------------------------------------ 块级遍历
def walk(node: Node, out: list, quoted: bool = False) -> None:
    for c in node.children:
        if isinstance(c, str):
            t = clean(c)
            if t:
                out.append(('p', t, quoted))
            continue
        t = c.tag
        if t in ('script', 'style', 'head', 'svg', 'img_void', 'title', 'meta'):
            continue
        if re.match(r'^h[1-6]$', t):
            txt = to_line(c)
            if txt:
                out.append(('h%s' % t[1], txt, quoted))
        elif t == 'p':
            txt = to_line(c)
            if not txt:
                continue
            if EDGE_RE.match(txt) or re.fullmatch(r'`[FfEe][0-9IVXLCDM]{1,7}`', txt):
                out.append(('edge', txt.strip('`'), quoted))
            else:
                out.append(('note' if is_note(txt) else 'p', txt, quoted))
        elif t == 'img':
            src = (c.attrs.get('src') or '').strip()
            if src:
                out.append(('img', src, quoted))
        elif t == 'blockquote':
            walk(c, out, True)
        elif t in ('ul', 'ol'):
            walk_list(c, out, quoted)
        elif t == 'table':
            walk_table(c, out, quoted)
        elif t == 'div':
            edge = is_edge_block(c)
            if edge:
                out.append(('edge', ' · '.join(edge), quoted))
            else:
                walk(c, out, quoted)
        elif t == 'hr':
            out.append(('hr', '', quoted))
        elif t in ('section', 'article', 'main', 'aside', 'header', 'footer', 'figure'):
            walk(c, out, quoted)
        else:
            txt = to_line(c)
            if txt:
                out.append(('p', txt, quoted))


def walk_list(ul: Node, out: list, quoted: bool) -> None:
    plain = 'list-style:none' in ul.style()
    for li in [c for c in ul.children if isinstance(c, Node) and c.tag == 'li']:
        inner: list = []
        walk(li, inner, quoted)
        if not inner:
            continue
        if plain and is_note(inner[0][1]):
            out.append(('note', '\n'.join(t for _, t, _ in inner), quoted))
        elif plain:
            out.extend(inner)                         # 本书里只是条目容器，不加项目符号
        else:
            out.extend(('li.' + k, t, q) for k, t, q in inner)


def walk_table(tbl: Node, out: list, quoted: bool) -> None:
    rows = []
    for tr in [c for c in tbl.children if isinstance(c, Node) and c.tag == 'tr']:
        cells = [clean(inline(td)).replace('\n', ' ')
                 for td in tr.children if isinstance(td, Node) and td.tag in ('td', 'th')]
        if cells:
            rows.append(cells)
    if not rows:
        return
    n = max(len(r) for r in rows)
    rows = [r + [''] * (n - len(r)) for r in rows]
    out.append(('tr', ' | '.join(rows[0]), quoted))
    out.append(('tr', '|'.join(['---'] * n), quoted))
    for r in rows[1:]:
        out.append(('tr', ' | '.join(r), quoted))


# ------------------------------------------------------- 段落重构（跨页断句）
def split_note_fragments(items: list) -> list:
    out = []
    for k, txt, q, f in items:
        if k == 'p':
            m = NOTE_FRAGMENT.match(txt)
            if m and not is_note(m.group(2)) and not is_note(txt):
                out.append(('note', m.group(1), q, f))
                out.append(('p', m.group(2).lstrip(), q, f))
                continue
        out.append((k, txt, q, f))
    return out


def looks_truncated(txt: str) -> bool:
    if len(txt) < 8:
        return False
    for o, c in (('(', ')'), ('［', '］'), ('[', ']'), ('【', '】')):
        if txt.count(o) > txt.count(c):
            return True
    return txt[-1] not in SENT_END and txt[-1] not in '）)”』】》"\'.!?'


def strong_cut(txt: str) -> bool:
    if not txt:
        return False
    for o, c in (('(', ')'), ('［', '］'), ('[', ']'), ('【', '】'), ('“', '”')):
        if txt.count(o) > txt.count(c):
            return True
    return txt[-1] in '，、：（“‘—·'


def strip_prefixes(txt: str) -> str:
    m = NOTE_FRAGMENT.match(txt)
    t = m.group(2) if m else txt
    return EDGE_INL.sub('', EDGE_BARE.sub('', t))


def starts_continuation(txt: str) -> bool:
    t = strip_prefixes(txt)
    return bool(t) and bool(re.match(r'[\u4e00-\u9fffA-Za-z]', t)) and not is_note(txt)


def reflow(items: list) -> list:
    """合并因分页被切成数段的句子；其间的注与边码顺移到合并段之后。

    每一项是 (kind, text, quoted, 阅读顺序单元号)。合并段沿用**首项**的单元号，
    被挪到后面的注/边码各留自己的 —— 于是导出时会在单元边界如实插一个锚。
    """
    out, i, n = [], 0, len(items)
    while i < n:
        k, txt, q, f = items[i]
        if k != 'p' or not looks_truncated(txt):
            out.append((k, txt, q, f))
            i += 1
            continue
        j, pending, seen, merged = i + 1, [], [], txt
        while True:
            while j < n and items[j][0] in ('note', 'edge'):
                pending.append(items[j])
                seen.append(items[j])
                j += 1
            if j >= n or items[j][0] != 'p' or not starts_continuation(items[j][1]):
                break
            if not (pending or strong_cut(merged)):
                break
            nxt = items[j][1]
            merged += (' ' if (EDGE_INL.match(nxt) or EDGE_BARE.match(nxt)) else '') + nxt
            j += 1
            pending = []
            if not looks_truncated(merged):
                break
        if j == i + 1:
            out.append((k, txt, q, f))
            i += 1
            continue
        out.append(('p', re.sub(r'[\u2028\u2029]|\n', '', merged).strip(), q, f))
        out.extend(seen)
        i = j
    return out


# ------------------------------------------------------------------ EPUB 读取
def read_toc(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(标题, 源文件名)]，优先 toc.ncx，其次 EPUB3 nav。"""
    ncx = next((n for n in z.namelist() if n.lower().endswith('.ncx')), None)
    if ncx:
        t = z.read(ncx).decode('utf-8', 'replace')
        pairs = re.findall(r'<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content src="([^"]+)"',
                           t, re.S)
        if pairs:
            return [(html.unescape(a).strip(), posixpath.basename(b.split('#')[0]))
                    for a, b in pairs]
    nav = next((n for n in z.namelist()
                if 'nav' in n.lower() and n.lower().endswith(('.xhtml', '.html'))), None)
    if nav:
        t = z.read(nav).decode('utf-8', 'replace')
        pairs = re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', t, re.S)
        if pairs:
            return [(html.unescape(re.sub(r'<[^>]+>', '', b)).strip(),
                     posixpath.basename(a.split('#')[0])) for a, b in pairs]
    return []


def spine_files(z: zipfile.ZipFile) -> list[str]:
    """spine 顺序的 xhtml 文件名（不含路径）。"""
    ncx_like = [n for n in z.namelist() if n.lower().endswith('.opf')]
    opf = next((n for n in z.namelist()
                if n.lower().endswith('.opf') and '/META-INF/' not in n), None) or \
        (ncx_like[0] if ncx_like else None)
    if not opf:
        return [posixpath.basename(n) for n in z.namelist()
                if n.lower().endswith(('.xhtml', '.html'))]
    t = z.read(opf).decode('utf-8', 'replace')
    id2href = {}
    for m in re.finditer(r'<item\b[^>]*/?>', t):
        tag = m.group(0)
        i = re.search(r'\bid="([^"]+)"', tag)
        h = re.search(r'\bhref="([^"]+)"', tag)
        mt = re.search(r'\bmedia-type="([^"]*)"', tag)
        if i and h and (not mt or 'html' in mt.group(1)):
            id2href[i.group(1)] = h.group(1)
    order = [id2href.get(i, '') for i in re.findall(r'<itemref[^>]*\bidref="([^"]+)"', t)]
    return [posixpath.basename(o) for o in order if o]


def opf_metadata(z: zipfile.ZipFile) -> dict:
    """EPUB 的 dc:* 元数据（只作标题兜底，不当书志用）。"""
    opf = next((n for n in z.namelist()
                if n.lower().endswith('.opf') and '/META-INF/' not in n), None)
    if not opf:
        return {}
    t = z.read(opf).decode('utf-8', 'replace')
    out = {}
    for k in ('title', 'creator', 'publisher', 'date', 'language', 'identifier'):
        m = re.search(r'<dc:%s[^>]*>(.*?)</dc:%s>' % (k, k), t, re.S | re.I)
        if m:
            out[k] = html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
    return out


# ------------------------------------------------------------------ 主转换
PROJECT_SUBDIR = 'projects'
SHARD_TAG = 'P1'


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', '_', name.strip())
    return name or 'img'


def _resolve_href(doc_path: str, href: str) -> str | None:
    from urllib.parse import unquote
    href = unquote(href.split('#')[0].strip())
    if not href or href.startswith(('http://', 'https://', 'data:')):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(doc_path), href))


def convert_epub(epub: Path, log: Log = lambda _: None) -> dict:
    """EPUB → 块序列。返回 {'blocks': [...], 'meta': {...}}，不落盘。"""
    epub = Path(epub)
    z = zipfile.ZipFile(epub)
    base_of: dict[str, str] = {}
    for n in z.namelist():
        base_of.setdefault(posixpath.basename(n), n)

    toc = read_toc(z)
    labels: dict[str, str] = {}
    for t, f in toc:
        labels.setdefault(f, t)

    docs = [f for f in spine_files(z) if base_of.get(f, '').lower().endswith(('.xhtml', '.html', '.htm'))]
    if not docs:
        raise ValueError(f'{epub.name} 的 spine 里没有 xhtml 文档，不像 EPUB')

    items: list = []                 # (kind, text, quoted, unit)
    img_srcs: dict[str, str] = {}    # 存放名 → zip 内路径
    img_missing: list[str] = []
    unit_of_file: list[dict] = []
    first_levels: Counter = Counter()

    for unit, fn in enumerate(docs):
        full = base_of[fn]
        node = parse(z.read(full).decode('utf-8', 'replace'))
        local: list = []
        walk(node, local)
        heads = [int(k[1]) for k, _t, _q in local if re.match(r'^h[1-6]$', k)]
        if heads:
            first_levels[heads[0]] += 1
        kept = 0
        for k, t, q in local:
            if k == 'hr':
                continue
            if k == 'img':
                zp = _resolve_href(full, t)
                if not zp or zp not in z.namelist():
                    img_missing.append(t)
                    continue
                ext = posixpath.splitext(zp)[1].lower()
                if ext not in IMG_EXT:
                    continue
                name = '%s_%s' % (hashlib.sha1(zp.encode('utf-8')).hexdigest()[:8],
                                  _safe_name(posixpath.basename(zp)))
                img_srcs[name] = zp
                items.append(('img', 'images/' + name, q, unit))
                kept += 1
                continue
            items.append((k, t, q, unit))
            kept += 1
        unit_of_file.append({'unit': unit, 'file': fn, 'label': labels.get(fn, ''),
                             'items': kept})

    if not any(k in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6') for k, _t, _q, _f in items):
        log(f'[epub] ⚠️ {epub.name} 里一个标题标签都没有，只能当整本一段处理')

    items = split_note_fragments(items)
    items = reflow(items)

    # 层级基准：以「文件首个标题层级」的众数档为第 1 级（与原 epub2md 同一口径）。
    # 用众数而不是最小：全书只有一处 h2、其余都是 h3 时，最小会把唯一那个 h2 顶成基准，
    # 章就整体下沉一级；众数取的是「这书拿哪一档当章」。
    if first_levels:
        base = min((lv for lv, c in first_levels.items() if c == max(first_levels.values())))
    else:
        base = 1
    shift = base - 1

    blocks: list[dict] = []
    n_head = n_note = n_edge = n_img = n_table = 0
    for k, t, q, unit in items:
        if not t and k not in ('img',):
            continue
        if k in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            lvl = max(1, int(k[1]) - shift)
            blocks.append({'type': 'text', 'text': t, 'page_idx': unit,
                           'text_level': lvl, '_trusted': True})
            n_head += 1
        elif k == 'note':
            blocks.append({'type': 'page_footnote', 'text': t, 'page_idx': unit})
            n_note += 1
        elif k == 'edge':
            # 原版边码按原文照抄成正文，**不建页锚**（那是外文书页码）
            blocks.append({'type': 'text', 'text': '`%s`' % t, 'page_idx': unit})
            n_edge += 1
        elif k == 'tr':
            blocks.append({'type': 'table', 'table_body': t, 'page_idx': unit})
            n_table += 1
        elif k == 'img':
            blocks.append({'type': 'image', 'img_path': t, 'page_idx': unit})
            n_img += 1
        elif k.startswith('li.'):
            blocks.append({'type': 'list', 'text': ('> ' if q else '') + t, 'page_idx': unit})
        else:
            blocks.append({'type': 'text', 'text': ('> ' + t if q else t), 'page_idx': unit})

    meta = {
        'epub_units': len(docs),
        'epub_headings': n_head, 'epub_notes': n_note, 'epub_edges': n_edge,
        'epub_images': n_img, 'epub_tables': n_table,
        'epub_level_base': base, 'epub_first_levels': dict(first_levels),
        'epub_toc_entries': len(toc),
        'epub_images_missing': img_missing[:20],
        'epub_dc': opf_metadata(z),
        'epub_units_detail': unit_of_file,
        'img_srcs': img_srcs,
        'doc_title': labels.get(docs[0], '') or '',
        'sha256': sha256_file(epub),
        'chars': sum(len(re.sub(r'\s', '', b.get('text') or b.get('table_body') or ''))
                     for b in blocks),
    }
    return {'blocks': blocks, 'meta': meta}


def epub_to_project_dir(epub: Path, project_dir: Path, log: Log = lambda _: None) -> dict:
    """把 EPUB 落成一个 MinerU 形态的工程目录（content_list.json + images/）。"""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    conv = convert_epub(epub, log=log)
    meta = conv.pop('meta')
    img_srcs = meta.pop('img_srcs')

    if img_srcs:
        img_dir = project_dir / 'images'
        img_dir.mkdir(exist_ok=True)
        z = zipfile.ZipFile(epub)
        for name, zp in img_srcs.items():
            try:
                (img_dir / name).write_bytes(z.read(zp))
            except KeyError:
                log(f'[epub] ⚠️ 图片缺失：{zp}')

    # content_list.json：块就是块，_trusted 用独立字段带出去（下游读 Block 时取用）
    write_json(project_dir / 'content_list.json', conv['blocks'])
    write_json(project_dir / 'epub_meta.json', meta)
    log(f'[epub] {epub.name} → {len(conv["blocks"])} 块 / {meta["epub_units"]} 个阅读顺序单元'
        f'（标题 {meta["epub_headings"]}｜页下注 {meta["epub_notes"]}｜'
        f'原版边码 {meta["epub_edges"]}｜图 {meta["epub_images"]}｜表 {meta["epub_tables"]}）')
    return {'project_dir': project_dir, 'meta': meta, 'n_blocks': len(conv['blocks'])}


def build_source(epub: Path, wd: Path, source_id: str, doc_title: str,
                 log: Log = lambda _: None) -> dict:
    """落工程 + 写 project.json，返回 project dict。"""
    proj_dir = Path(wd) / PROJECT_SUBDIR / SHARD_TAG
    built = epub_to_project_dir(epub, proj_dir, log=log)
    sh = scan_shard(proj_dir, SHARD_TAG)
    project = {
        'source_id': source_id,
        'doc_title': doc_title,
        'source_file': str(epub),
        'ingested_at': now_iso(),
        'mode': 'epub-local',
        'source_sha256': built['meta']['sha256'],
        'defaults': {'footnotes': 'inline'},   # 页下注本来就随文，页末会把整文件堆成一坨
        'epub': {k: v for k, v in built['meta'].items()
                 if k not in ('epub_units_detail',)},
        'shards': [sh.to_dict()],
    }
    save_project(wd, project)
    log(f'[epub] 登记 source_id={source_id}')
    return project
