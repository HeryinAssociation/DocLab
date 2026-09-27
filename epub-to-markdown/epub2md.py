#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""epub2md — 把 EPUB 按「书籍逻辑章节」导出为 Markdown（纯标准库，无第三方依赖）。

用法：
    python epub2md.py BOOK.epub -o out_md/
    python epub2md.py BOOK.epub -o out_md/ --chapter-re '第[0-9一二三四五六七八九十]+章|^前言|^序'
    python epub2md.py BOOK.epub --manifest map.json      # 只导出章节映射表，不写 md
    python epub2md.py BOOK.epub -o out_md/ --map map.json # 用人工修正过的映射表

设计要点（针对中文纸书 OCR 件）：
  * 章的归并：spine 里只有节标题（h4/h5）的续页自动并入上一章；命中 --chapter-re 的开新章
  * 文件内再切：命中 --split-re 的标题（如"关键词""人名地名"）把索引类内容从正文章里切出来，
    连续命中同一类的合并为一章
  * 边码：`<div><p>F7</p><p>E1</p></div>` 与粘在正文里的 F7/E423/FXVII → 行内代码 `F7 · E1`
  * 页下注：`<ul style="list-style:none">` 或以带圈数字（①②…）起首的段落 → `> ① …` 引用块
  * OCR 噪声：`<sub>`/纯杂字符 `<sup>` 丢弃；零宽/全角空格、实体、行间硬回车清理
  * 跨页断句：段间夹有边码或页下注、或前段以顿逗冒号未闭合括号收尾 → 并回同一段
  * 保真度自检：逐文件与源正文净字符数比对并打印比例
"""

import argparse, glob, html, io, json, os, re, sys, zipfile

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
from collections import Counter
from html.parser import HTMLParser

# --------------------------------------------------------------------------
# 章起始 / 索引类 判定正则（可用命令行覆盖）
DEFAULT_CHAPTER_RE = (
    r'^(第\s*[0-9一二三四五六七八九十百]+\s*[章篇部讲]|'
    r'前言|序言|序【|导论|导言|引言|绪论|后记|译后记|译序|再版|增订|献\s*辞|题辞|题词|'
    r'体例|凡例|出版说明|中译本|法文版|德文版|英文版|'
    r'附录[一二三四五六七八九十0-9]|' +
    r'索引|术语索引|人名|地名|引用作品|参考文献|年表|'
    r'Chapter|Preface|Introduction|Afterword|Appendix|Index|Notes|Bibliography)')

# 文件内部需要再切一刀的标题（多为混在正文末尾的索引）
DEFAULT_SPLIT_RE = r'^(关键词|索引|术语索引|人名地名|人名索引|主题索引|引用作品|参考文献)'

# 判定为"同一类"从而合并的标题集合
MERGE_CLASSES = [
    (re.compile(r'关键词|索引|术语|人名|地名|引用作品|参考文献'), '索引'),
]

CIRCLED = set('①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚')
VOID = {'meta', 'link', 'img', 'br', 'hr', 'wbr', 'input', 'area', 'base', 'col', 'source', 'track'}
CJK = r'\u4e00-\u9fff'
SENT_END = '。！？…⋯'
EDGE_RE = re.compile(r'^[FE](?:\d{1,4}|[IVXLCDM]{1,7})$', re.I)
EDGE_INL = re.compile(r'^`[FfEe][0-9IVXLCDM]{1,7}`\s*')
EDGE_BARE = re.compile(r'^[FfEe](?:\d{1,4}|[IVXLCDM]{1,7})[ \t]+')
NOTE_FRAGMENT = re.compile(
    r'^((?:.{0,140}?)(?:——\s*中译者注|—中译者注|一中译者注|·中译者注|--中译者注'
    r'|——\s*原注|一原注|--原注|----原注|。一中译者注))(.{40,})$', re.S)


# -------------------------------------------------------------------------- DOM
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
        (self.stack[-1].children if self.stack else self.root.children).append(n)
        self.stack.append(n)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_data(self, data):
        if data and self.stack:
            self.stack[-1].children.append(data)


def parse(xhtml):
    b = Builder()
    b.feed(xhtml)
    q = [b.root]
    while q:
        c = q.pop(0)
        if c.tag == 'body':
            return c
        q += [x for x in c.children if isinstance(x, Node)]
    return b.root


# ---------------------------------------------------------------------- 文本层
def clean(s):
    s = html.unescape(s)
    s = s.replace('\u00a0', ' ').replace('\u2007', ' ').replace('\ufeff', '')
    s = re.sub(r'[\u200b-\u200f\u202a-\u202e\ue000-\uf8ff]', '', s)
    s = s.replace('\r', '\n').replace('\u2028', ' ').replace('\u2029', ' ')
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r' *\n *', '\n', s)
    s = re.sub(r'\n{2,}', '\n', s)
    s = re.sub(r'(?<=[%s，。、；：！“”‘’（）《》〈〉【】—…·])\s+(?=[%s，。、；：！“”‘’（）《》〈〉【】—…·])' % (CJK, CJK), '', s)
    s = re.sub(r'^([0-9]{1,2})[ ,.．]\s*(?=[%s])' % CJK, r'\1. ', s)
    s = re.sub(r'(?<=[%s]) (?=[A-Za-z(])' % CJK, '', s)
    s = re.sub(r'(?<=[A-Za-z)]|[,.\-]) (?=[%s])' % CJK, ' ', s)
    s = re.sub(r'(?<![A-Za-z0-9])([FfEe](?:\d{1,4}|[IVXLCDMivxlcdm]{2,7}))[ \t]*(?=[%s])' % CJK,
               lambda m: '`%s` ' % m.group(1), s)
    s = re.sub(r'(?m)^([FfEe](?:\d{1,4}|[IVXLCDM]{2,7}))[ \t]+(?=[%sA-Z])' % CJK, r'`\1` ', s)
    s = s.replace('<', '&lt;')
    s = re.sub(r'(?m)^>', '&gt;', s)
    return s.strip()


def inline(node):
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
    return '' if t == 'img' else ('\n' if t == 'br' else inner)


def to_line(node):
    return clean(inline(node))


def is_note(txt):
    return bool(txt) and txt[0] in CIRCLED


def is_edge_block(node):
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


# ------------------------------------------------------------------- 块级遍历
def walk(node, out, quoted=False):
    for c in node.children:
        if isinstance(c, str):
            t = clean(c)
            if t:
                out.append(('p', t, quoted))
            continue
        t = c.tag
        if t in ('script', 'style', 'head', 'svg', 'img', 'title', 'meta'):
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


def walk_list(ul, out, quoted):
    plain = 'list-style:none' in ul.style()
    for li in [c for c in ul.children if isinstance(c, Node) and c.tag == 'li']:
        inner = []
        walk(li, inner, quoted)
        if not inner:
            continue
        if plain and is_note(inner[0][1]):
            out.append(('note', '\n'.join(t for _, t, _ in inner), quoted))
        elif plain:
            out.extend(inner)                         # 本书里只是条目容器，不加项目符号
        else:
            out.extend(('li.' + k, t, q) for k, t, q in inner)


def walk_table(tbl, out, quoted):
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


# ------------------------------------------------------------- 段落重构与渲染
def split_note_fragments(items):
    out = []
    for k, txt, q in items:
        if k == 'p':
            m = NOTE_FRAGMENT.match(txt)
            if m and not is_note(m.group(2)) and not is_note(txt):
                out.append(('note', m.group(1), q))
                out.append(('p', m.group(2).lstrip(), q))
                continue
        out.append((k, txt, q))
    return out


def looks_truncated(txt):
    if len(txt) < 8:
        return False
    for o, c in (('(', ')'), ('［', '］'), ('[', ']'), ('【', '】')):
        if txt.count(o) > txt.count(c):
            return True
    return txt[-1] not in SENT_END and txt[-1] not in '）)”』】》"\'.!?'


def strong_cut(txt):
    if not txt:
        return False
    for o, c in (('(', ')'), ('［', '］'), ('[', ']'), ('【', '】'), ('“', '”')):
        if txt.count(o) > txt.count(c):
            return True
    return txt[-1] in '，、：（“‘—·'


def strip_prefixes(txt):
    m = NOTE_FRAGMENT.match(txt)
    t = m.group(2) if m else txt
    return EDGE_INL.sub('', EDGE_BARE.sub('', t))


def starts_continuation(txt):
    t = strip_prefixes(txt)
    return bool(t) and bool(re.match(r'[\u4e00-\u9fffA-Za-z]', t)) and not is_note(txt)


def reflow(items):
    """合并因原版分页被切成数段的句子；其间的注与边码顺移到合并段之后。"""
    out, i, n = [], 0, len(items)
    while i < n:
        k, txt, q = items[i]
        if k != 'p' or not looks_truncated(txt):
            out.append((k, txt, q))
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
            out.append((k, txt, q))
            i += 1
            continue
        out.append(('p', re.sub(r'[\u2028\u2029]|\n', '', merged).strip(), q))
        out.extend(seen)
        i = j
    return out


def render(items, title, drop_title=True):
    want = norm(title)
    if drop_title:
        while items and re.match(r'^h[1-3]$', items[0][0]) and norm(items[0][1]) == want:
            items.pop(0)
    L = ['# ' + title, '']
    for kind, txt, quoted in items:
        if kind == 'note':
            L += ['> ' + txt.replace('\n', '\n> '), '']
        elif kind == 'edge':
            L += ['`%s`' % txt, '']
        elif kind == 'hr':
            L += ['---', '']
        elif kind == 'tr':
            L.append('| ' + txt + ' |')
        elif re.match(r'^h[1-6]$', kind):
            lvl = int(kind[1])
            h = '#' * lvl
            L += ['%s %s' % (h, txt), '']
        elif kind.startswith('li.'):
            L.append('- ' + re.sub(r'\s*\n\s*', ' ', txt))
        else:
            txt = ('> ' + txt) if quoted else txt
            L += [txt.replace('\n', '  \n'), '']
    res, blank = [], 0
    for l in L:
        if l == '':
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        res.append(l.rstrip())
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(res).strip()) + '\n'


def norm(s):
    return re.sub(r'[①②③④⑤⑥⑦⑧⑨⑩*\s·•．\.]', '', s)


def make_name(title):
    s = re.sub(r'[\\/:*?"<>|]', '', title.strip())
    return re.sub(r'\s+', '-', s)


# --------------------------------------------------------------------- TOC 读取
def read_toc(z):
    """返回 [(标题, 源文件), ...]，优先 toc.ncx，其次 EPUB3 nav.xhtml。"""
    ncx = next((n for n in z.namelist() if n.lower().endswith('.ncx')), None)
    if ncx:
        t = z.read(ncx).decode('utf-8', 'replace')
        pairs = re.findall(r'<navLabel><text>(.*?)</text></navLabel>\s*<content src="([^"]+)"', t, re.S)
        if pairs:
            return [(html.unescape(a).strip(), os.path.basename(b.split('#')[0])) for a, b in pairs]
    nav = next((n for n in z.namelist() if 'nav' in n.lower() and n.lower().endswith(('.xhtml', '.html'))), None)
    if nav:
        t = z.read(nav).decode('utf-8', 'replace')
        pairs = re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', t, re.S)
        if pairs:
            return [(html.unescape(re.sub(r'<[^>]+>', '', b)).strip(), os.path.basename(a.split('#')[0]))
                    for a, b in pairs]
    return []


def spine_files(z):
    opf = next((n for n in z.namelist() if n.lower().endswith('.opf')), None)
    if not opf:
        return [os.path.basename(n) for n in z.namelist() if n.lower().endswith(('.xhtml', '.html'))]
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
    return [os.path.basename(o) for o in order if o]


# ------------------------------------------------------------------ 章节分组
def heading_class(title):
    return next((name for rx, name in MERGE_CLASSES if rx.search(norm(title))), None)


def build_groups(z, chap_re, split_re):
    """返回 [{'title':…, 'files':[(源文件, 块切片)], }] —— 按书籍逻辑章归并。

    章起始双判据：
      (a) TOC 标签或首个标题命中 --chapter-re；
      (b) 该文件首个标题的层级等于全书最浅标题层级（应对"节"也被写进 TOC 的书）。
    文件内切刀：命中 --split-re 的标题把该文件切成正文段 + 索引类尾巴；
    尾巴若与当前章同属 MERGE_CLASSES 的一类则并章，否则另起一章。
    """
    toc = read_toc(z)
    labels = {}
    for t, f in toc:
        labels.setdefault(f, t)
    base_of = {}
    for n in z.namelist():
        base_of.setdefault(os.path.basename(n), n)

    files = spine_files(z)
    if labels:
        keep = [f for f in files if f in labels]
        files = keep or files                        # TOC 与 spine 无交集时退回全量
    parsed = []
    for fn in files:
        full = base_of.get(fn)
        if not full or not full.lower().endswith(('.xhtml', '.html', '.htm')):
            continue
        items = []
        walk(parse(z.read(full).decode('utf-8', 'replace')), items)
        heads = [(i, int(k[1]), t) for i, (k, t, _) in enumerate(items) if re.match(r'^h[1-6]$', k)]
        if not heads:
            continue                                    # 封面/版权页等无标题内容
        parsed.append({'fn': fn, 'items': items, 'label': labels.get(fn) or heads[0][2],
                       'first_head': heads[0][2], 'first_level': heads[0][1], 'heads': heads})

    if not parsed:
        return []
    cnt = Counter(p['first_level'] for p in parsed)
    min_level = min(k for k, v in cnt.items() if v == max(cnt.values()))

    groups = []

    def push(title, fn, items):
        cls = heading_class(title)
        if groups and cls and heading_class(groups[-1]['title']) == cls:
            groups[-1]['title'] = merge_titles(groups[-1]['title'], title)
            groups[-1]['files'].append((fn, items))
        else:
            groups.append({'title': title, 'files': [(fn, items)]})

    for p in parsed:
        starts = (re.search(chap_re, norm(p['label'])) or re.search(chap_re, norm(p['first_head']))
                  or p['first_level'] == min_level)
        if groups and not starts:
            groups[-1]['files'].append((p['fn'], p['items']))
        else:
            push(p['label'], p['fn'], p['items'])

        cut = None
        if split_re:
            for i, _lv, t in p['heads']:
                if i and re.search(split_re, norm(t)):   # 首个标题不作切点
                    cut = (i, t)
                    break
        if cut:
            i, t = cut
            cur = groups[-1]
            cur['files'] = [(f, (it[:i] if f == p['fn'] and it is p['items'] else it))
                            for f, it in cur['files']]
            if not any(it for _, it in cur['files']):
                groups.pop()
            push(t, p['fn'], p['items'][i:])
    return groups


def merge_titles(a, b):
    a = re.sub(r'^(索引|关键词索引)\s*', '', a.strip())
    b = re.sub(r'^(索引|关键词索引)\s*', '', b.strip())
    if not a:
        return b
    if a == b:
        return a
    return '索引 ' + '·'.join(dict.fromkeys((a + '·' + b).split('·')))


def norm_title(t):
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'[①②③④⑤⑥⑦⑧⑨⑩]+$', '', t).strip()        # 标题尾部注码
    t = re.sub(r'(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])', '', t)   # "献 辞"→"献辞"
    t = re.sub(r'^(第\s*[0-9一二三四五六七八九十百]+\s*[章节篇部卷辑])(?=[\u4e00-\u9fffA-Za-z])', r'\1 ', t)
    t = re.sub(r'^(附录[0-9一二三四五六七八九十]+)(?=[\u4e00-\u9fff])', r'\1 ', t)
    return t.strip()


def main():
    ap = argparse.ArgumentParser(description='EPUB -> 按逻辑章节的 Markdown')
    ap.add_argument('epub', help='EPUB 路径（或所在目录）')
    ap.add_argument('-o', '--out', default=None, help='输出目录（默认 <书名>/md）')
    ap.add_argument('--chapter-re', default=DEFAULT_CHAPTER_RE)
    ap.add_argument('--split-re', default=DEFAULT_SPLIT_RE)
    ap.add_argument('--map', dest='map_json', default=None, help='使用外部映射表 JSON：[{"title","files":[...]}]')
    ap.add_argument('--manifest', default=None, help='仅导出章节映射表 JSON，不写 Markdown')
    ap.add_argument('--no-reflow', action='store_true')
    args = ap.parse_args()

    ep = args.epub
    if os.path.isdir(ep):
        ep = glob.glob(os.path.join(ep, '*.epub'))[0]
    z = zipfile.ZipFile(ep)

    if args.map_json:
        raw = json.load(io.open(args.map_json, encoding='utf-8'))
        groups = [{'title': g['title'], 'files': [(f, None) for f in g['files']]} for g in raw]
    else:
        groups = build_groups(z, args.chapter_re, args.split_re)

    if args.manifest:
        out = []
        seen = {}                                       # 跨组累计，同一切片文件显示真实区间
        for g in groups:
            srcs = []
            for f, it in g['files']:
                ln = len(it) if it is not None else 0
                srcs.append('%s[%d:%d]' % (f, seen.get(f, 0), seen.get(f, 0) + ln))
                seen[f] = seen.get(f, 0) + ln
            out.append({'title': g['title'], 'files': [f for f, _ in g['files']], 'sources': srcs})
        io.open(args.manifest, 'w', encoding='utf-8').write(
            json.dumps(out, ensure_ascii=False, indent=2))
        print('映射表已写入 %s（%d 章）' % (args.manifest, len(groups)))
        for g in out:
            print('  %-34s %s' % (g['title'], ' + '.join(g['sources'])))
        return

    outdir = args.out or os.path.join(os.path.dirname(os.path.abspath(ep)), 'md')
    os.makedirs(outdir, exist_ok=True)
    def blocks_of(zz, fn):
        if fn not in cache:
            lst = []
            walk(parse(zz.read(next(n for n in zz.namelist() if os.path.basename(n) == fn))
                       .decode('utf-8', 'replace')), lst)
            cache[fn] = lst
        return cache[fn]

    cache = {}
    rows, src_n, md_n = [], 0, 0
    width = len(str(len(groups)))
    for i, g in enumerate(groups, 1):
        title = norm_title(g['title'])
        items = []
        for fn, its in g['files']:
            if its is None:
                full = next(n for n in z.namelist() if os.path.basename(n) == fn)
                its = []
                walk(parse(z.read(full).decode('utf-8', 'replace')), its)
            items += its
        if not args.no_reflow:
            items = split_note_fragments(items)
            items = reflow(items)
        md = render(items, title)
        fname = '%0*d-%s.md' % (width, i, make_name(title))
        io.open(os.path.join(outdir, fname), 'w', encoding='utf-8', newline='\n').write(md)
        s = 0
        for fn, its in g['files']:
            raw = z.read(next(n for n in z.namelist() if os.path.basename(n) == fn)).decode('utf-8', 'replace')
            mbody = re.search(r'<body[^>]*>', raw)
            raw = raw[mbody.end():] if mbody else raw
            raw = re.sub(r'<(sub|sup)[^>]*>(.*?)</\1>',
                         lambda m: m.group(2) if m.group(1) == 'sup' and re.search(r'\d', m.group(2)) else '',
                         raw, flags=re.S)
            tot = len(re.sub(r'\s', '', html.unescape(re.sub(r'<[^>]+>', '', raw))))
            if its is None:
                s += tot
            else:
                allb = blocks_of(z, fn)                 # 共用文件按"清洗后文本长度"加权折算
                w = [len(re.sub(r'\s', '', t)) for _, t, _ in allb]
                wi = [len(re.sub(r'\s', '', t)) for _, t, _ in its]
                s += tot * sum(wi) / max(1, sum(w))
        m = re.sub(r'^#+ .*$', '', md, flags=re.M)
        m = re.sub(r'[`*>|]', '', re.sub(r'(F\d+E\d+|F\d+|E\d+)', '', m))
        src_n, md_n = src_n + s, md_n + len(re.sub(r'\s', '', m))
        notes = sum(1 for k, _, _ in items if k == 'note')
        heads = sum(1 for k, _, _ in items if re.match(r'^h[1-6]$', k))
        rows.append((fname, len(g['files']), len(md), notes, heads, len(re.sub(r'\s', '', m)) / max(1, s)))

    for r in rows:
        print('%-44s src=%d chars=%6d notes=%3d heads=%2d ratio=%.3f' % r)
    print('\n合计：源正文净字符=%d  MD净字符=%d  比=%.4f  文件=%d' % (src_n, md_n, md_n / max(1, src_n), len(rows)))
    print('输出目录：%s' % outdir)


if __name__ == '__main__':
    main()
