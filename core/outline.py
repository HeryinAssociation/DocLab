"""目录树构建：从 MinerU 块序列推断章节层级，并抽出书内印刷目录做交叉校验。

层级从哪来（重要，跨书通用性就靠这条）：
  MinerU 的 text_level 不可靠，且**不同书差异极大**：
    · 有的书给 1=章 / 2=节，能用；
    · 有的书（实测某学位论文）把封面、摘要、章、节、一、**全部标成 1**，
      层级信号完全塌掉。
  所以 text_level 只作**兜底**，层级首先由中文编号本身推断：
    第X部分/第X编 > 第X章/第X回 > 第X节 > 一、 > （一） > 1. > （1）
  编号缺失时才回退到 text_level（1→章，2→plain）。

为什么不用 Agent 做这一步：
  规则推断可复现、可回查、可写进产物；Agent 每次结果不同、不可审计。
  Agent 只适合对 flags 里标了 needs_review 的标题给建议，不进主链路。

经验规则（全部显式记录在 dropped_headings 里）：
  R1 目录页里的条目会被 MinerU 标成标题，必须整页排除，不能当标题；
  R2 封面/书名页/版权页的标题（书名、机构名）不是章，整区排除；
  R3 「引言/绪论」「结语/结论」是无编号标题，与「一、二、三」同级，不能当它们的父节点；
  R4 「目录」是终端节点，后续标题不得嵌套进它；
  R5 用书内印刷目录补齐 MinerU 漏标的章（需已完成页码校准）；
  R6 页眉/页脚/书名这类**在多个页面上重复出现**的行是版面元素，不是标题。
     判定靠词频，不靠写死书名——写死只能对一本书生效。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .project import Block, is_noise_text, unclassified_types
from .util import now_iso, write_json, read_json

# ---------------------------------------------------------------- 编号规则

PART_RE = re.compile(r"^第\s*([一二三四五六七八九十百零]{1,3})\s*(?:部分|编|篇)")
# 学位论文/专著常用的「第X章」「第X节」，与「一、」是不同层级，必须单独认。
# 注意：不要在这些模式尾部补 `\b?`——Python 的 re 对「重复零宽断言」直接报
# "nothing to repeat"，是硬错误。
CHAPTER_RE = re.compile(r"^第\s*([一二三四五六七八九十百零\d]{1,3})\s*(?:章|回|讲)")
SEC_RE = re.compile(r"^第\s*([一二三四五六七八九十百零\d]{1,3})\s*(?:节|小节)")
ZH_DUN_RE = re.compile(r"^([一二三四五六七八九十百]{1,3})\s*、\s*(?=\S)")
PAREN_ZH_RE = re.compile(r"^[（(]\s*([一二三四五六七八九十百]{1,3})\s*[）)]\s*(?=\S)")
ARABIC_RE = re.compile(r"^(\d{1,2})\s*[.．、]\s*(?=\S)")
PAREN_ARABIC_RE = re.compile(r"^[（(]\s*(\d{1,2})\s*[）)]\s*(?=\S)")

# R3：章内开篇/收束类无编号标题，实际与「一、」同级
OPENING_RE = re.compile(r"^(引言|导言|导论|绪论|概述|本章导读|问题的提出|研究背景)\s*$")
CLOSING_RE = re.compile(r"^(结\s*语|结论|小结|本章小结|余论|代结语|结束语|总结与展望)\s*$")

# 书级前后件：与章同级。
# 必须分成两段，因为二者的**从属关系相反**：
#   前置件（序/出版说明/凡例）之后跟的是正文，若把后续标题收进来会吞掉整本书；
#   后置件（后记/附录/参考文献/索引）之后的标题属于它自己（见 R7）。
# 注意：「前言」**不在**此列——它是章内开篇（见 OPENING_RE）。若放进这里，
# 它会升成章级节点并把整章正文吞进自己名下（实测吞掉 1.6–4 万字）。
FRONT_RE = re.compile(
    r"^(序|序言|自序|代序|出版说明|凡例|编者的话)\s*[一二三四五六七八九十\d]*\s*$")
BACK_RE = re.compile(
    r"^(后记|跋|附录|附\s*录|参考文献|索\s*引|致谢|编后记|大事记)\s*[一二三四五六七八九十\d]*\s*$")

TOC_LINE_RE = re.compile(r"[.．·…]{3,}\s*\d{1,4}\s*$")
TOC_NUMBERED_RE = re.compile(r"^(.{2,60}?)\s+(\d{1,4})\s*$")

# R9：图/表题注。命中即认定该页是图表页，页上短且无编号的行是图内标签碎片。
FIGCAP_RE = re.compile(r"^(图|表|Figure|Table|Fig\.)\s*\d+")

# 版面噪声 / 非标题行（通用模式；**不写任何具体书名**，书名靠 R6 词频判定）
NOISE_TITLE_RES = [
    re.compile(r"^\d{1,4}\s*$"),                    # 纯页码
    re.compile(r"^[\s.·…\-—_=]+$"),                  # 纯点线/分隔符
    re.compile(r"^(?:图|表|Figure|Table)\s*\d+"),     # 图表题注
    re.compile(r"^(?:ISBN|DOI)\b", re.I),
]

RANK = {
    "part": 10,        # 第X部分 / 第X编
    "toc": 12,         # 目录（终端节点）
    "chapter": 20,     # 第X章 / text_level==1 兜底 / 前置件
    "backmatter": 20,  # 后置件（附录/参考文献/后记）——与章同级，但会收拢其内部标题（R7）
    "sec": 30,         # 第X节
    "subsec": 40,      # 一、
    "subsubsec": 50,   # （一）
    "item": 60,        # 1.
    "subitem": 65,     # （1）
    "plain": 25,       # 运行时按上下文修正
}
NUMBERED_MARKERS = ("sec", "subsec", "subsubsec", "item", "subitem")


@dataclass
class Node:
    nid: str = ""
    level: int = 0
    title: str = ""
    marker: str = ""
    rank: int = 0
    parent: str = ""
    # key ＝ 人工核定层（manual_edits.json）的**稳定主键**。
    #   真实块节点：str(gid_start)
    #   目录补出来的合成节点：形如 "toc:P1:19:一认识论的对象"
    # 为什么不能只用 gid_start：合成节点的 gid 是**借**来的（取该页第一个真实块的
    # gid，见 _toc_repair），于是会和那个真实节点撞在同一个数字上。实测《认识论引论》
    # 里「第一章」(真实, gid=19) 与「一认识论的对象」(合成, gid=19) 共用主键，
    # 人在界面上点「第一章」，另一条跟着变 —— 不是层级算法的问题，是键本身不唯一。
    key: str = ""
    gid_start: int = -1
    gid_end: int = -1
    # 断点落在 gid_start 这个块**内部**的第几个字符（0 = 块首）。
    # 只对人工新增的标题有意义：MinerU 把标题和正文并进同一个段落时，断点不能
    # 落在段落边界上，得能切进段落中间。其余节点的 offset 恒为 0。
    offset: int = 0
    shard: str = ""
    page_idx: int = 0
    n_blocks: int = 0
    n_chars: int = 0
    flags: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["children"] = [c.to_dict() for c in self.children]
        return d


SYNTH_PREFIX = "toc:"
# 人工新增标题（在某个正文块处补一个断点）的键前缀，见 core/manual.set_added。
# 它的 gid 同样是**借**的（借它落点的那个块），所以真伪判定要和 toc: 一样处理。
ADD_PREFIX = "add:"


def node_key(n: Node) -> str:
    """取节点的稳定主键。老 outline.json 里没有 key 字段，退化成 str(gid_start)。"""
    return n.key or str(n.gid_start)


def is_synth_key(key: str) -> bool:
    """是不是「从印刷目录补出来的合成节点」的键（只这一种，别把人工新增也算进来：
    from_toc / needs_review 标记靠它，人工新增的标题并不来自目录）。"""
    return str(key).startswith(SYNTH_PREFIX)


def is_added_key(key: str) -> bool:
    """是不是「人工新增的标题断点」。"""
    return str(key).startswith(ADD_PREFIX)


def is_borrowed_key(key: str) -> bool:
    """键对应的 gid 是不是借来的（合成补章 / 人工新增都属于此类）。"""
    return is_synth_key(key) or is_added_key(key)


def doc_order(gid: int, key: str) -> tuple[int, int]:
    """文档序排序键：先 gid，同一 gid 上**真实块排在借 gid 的节点之前**。

    借 gid 的节点（目录补章 toc:、人工新增 add:）必然与那个真实节点同号。
    谁先谁后不能依赖 sort 的稳定性 —— 那是隐式契约，换个地方排一次序就散架。
    写成显式规则。
    """
    return (gid, 1 if is_borrowed_key(key) else 0)


# ---------------------------------------------------------------- R1/R2：目录区与前置区

def _toc_like_lines(text: str) -> int:
    """数一个块里有几行像目录条目（点线+页码，或标题+页码）。"""
    n = 0
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if TOC_LINE_RE.search(line) or TOC_NUMBERED_RE.match(line):
            n += 1
    return n


def detect_toc_region(blocks: list[Block]) -> dict:
    """定位书内印刷目录页（R1）。

    判据：某页出现「目录」标题，或该页 TOC 样式行 ≥3 行；从起点向后扩展到连续结束。
    """
    by_page: dict[tuple[str, int], list[Block]] = {}
    for b in blocks:
        if b.type == "text":
            by_page.setdefault((b.shard, b.page_idx), []).append(b)

    head_page = None
    for b in blocks:
        if b.type == "text" and b.text.strip() in ("目录", "目 录", "Contents", "CONTENTS"):
            head_page = (b.shard, b.page_idx)
            break

    counts = {k: sum(_toc_like_lines(x.text) for x in v) for k, v in by_page.items()}
    if head_page is None:
        # 没有「目录」标题时，退而找 TOC 行最密的页
        cands = [(k, c) for k, c in counts.items() if c >= 3]
        if not cands:
            return {"found": False, "pages": [], "shard": None, "heading_page": None}
        head_page = max(cands, key=lambda x: x[1])[0]

    shard = head_page[0]
    start = head_page[1]
    pages = [start]
    p = start + 1
    while p < start + 12:
        if counts.get((shard, p), 0) >= 3:
            pages.append(p)
            p += 1
        else:
            break
    return {"found": True, "shard": shard, "heading_page": start,
            "pages": pages, "page_range": [start, pages[-1]],
            "toc_line_counts": {str(k[1]): v for k, v in counts.items() if k[0] == shard}}


# ---------------------------------------------------------------- R6：版面重复行

_LEAD_NUM_RE = re.compile(r"^[\s\d.·\-—]{0,6}")
_WS_RE = re.compile(r"\s+")


def _has_numbering(t: str) -> bool:
    """行首是否带任何一档编号（部分/章/节/一、/（一）/1./（1））。"""
    return bool(PART_RE.match(t) or CHAPTER_RE.match(t) or SEC_RE.match(t)
                or ZH_DUN_RE.match(t) or PAREN_ZH_RE.match(t)
                or ARABIC_RE.match(t) or PAREN_ARABIC_RE.match(t))


def _is_structural(t: str) -> bool:
    """命中编号/前后件/开篇收束模式的，是结构性标题，不参与「重复行」判定。

    否则「结语」这种每章来一次的标题，会被词频规则误杀。
    """
    return bool(PART_RE.match(t) or CHAPTER_RE.match(t) or SEC_RE.match(t)
                or ZH_DUN_RE.match(t) or PAREN_ZH_RE.match(t) or ARABIC_RE.match(t)
                or OPENING_RE.match(t) or CLOSING_RE.match(t)
                or FRONT_RE.match(t) or BACK_RE.match(t)
                or t in ("目录", "目 录", "Contents", "CONTENTS"))


def _running_key(t: str, max_len: int = 60) -> str | None:
    """把行归一成「跨页比较用」的键：去掉行首页码、压缩空白。"""
    t = t.strip()
    if not t or len(t) > max_len or "\n" in t or _is_structural(t):
        return None
    k = _LEAD_NUM_RE.sub("", t)          # 页眉常带页码前缀，去掉才好比
    k = _WS_RE.sub(" ", k).strip(" .·-—")
    return k if len(k) >= 4 else None


def detect_running_texts(blocks: list[Block], min_pages: int = 5,
                         ratio: float = 0.08) -> set[str]:
    """R6：找出在多页上重复出现的版面行（页眉/页脚/书名），返回归一化键集合。

    纯词频判定，不依赖任何具体书名——写死书名只能对一本书生效。

    统计必须**涵盖所有块类型**，不能只看 type=="text"：MinerU 通常把页眉标成
    `header`、页脚标成 `footer`，而少数页上同样的内容会漏成 `text`。
    只看 text 会把这些漏网页当成真标题收进来（实测就是这样漏掉了一个「2 书名」节点）。

    阈值取 max(min_pages, 总页数 × ratio)：页眉几乎每页都有，远高于此；
    而「本章小结」这类每章一次的标题到不了这个量级。
    结构性标题（第X部分/第X章/结语/附录…）由 _running_key 直接豁免，不受影响。
    """
    seen: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for b in blocks:
        k = _running_key(b.text)
        if k:
            seen[k].add((b.shard, b.page_idx))
    n_pages = len({(b.shard, b.page_idx) for b in blocks}) or 1
    thr = max(min_pages, int(n_pages * ratio))
    return {k for k, pages in seen.items() if len(pages) >= thr}


# ---------------------------------------------------------------- 标题判定

def classify(t: str, text_level: int | None, running: set[str] | None = None,
             trusted: bool = False) -> str | None:
    """给候选标题定类别；None 表示不是标题。

    判定顺序是关键：**编号模式先于 text_level**。
    有些书（学位论文）MinerU 把一切标题都标成 level 1，若先吃 text_level，
    整本书会塌成单层目录。

    trusted=True 时反过来：这个源（EPUB）的 text_level 来自 `<h1>..<h6>`，是
    出版方标的真实层级，比中文编号可靠，所以**标签优先**、编号只在没标签时兜底。
    为什么必须优先：标签是「第几层」的**绝对**陈述，而编号是相对的 —— 同一本书里
    h3 的「一、」与 h2 的「第一节」在编号规则下会被排成 subsec/subsecsec 等固定档，
    与标签自己的层数不一定对齐，混着用会错位。
    """
    t = t.strip()
    if not t or is_noise_text(t) or any(r.search(t) for r in NOISE_TITLE_RES):
        return None
    if t in ("目录", "目 录", "Contents", "CONTENTS"):
        return "toc"
    if running and _running_key(t) in running:
        return None                                    # R6 版面重复行
    # 标签权威源：有 text_level 就照它定档，不再看编号。
    # （没有 text_level 的块仍走下面的编号规则 —— 这类是「用 <p> 写的节标题」，
    #   标签给不出层级，编号是唯一线索，不能一并丢掉。）
    if trusted and text_level:
        return "chapter" if text_level == 1 else "plain"
    if PART_RE.match(t):
        return "part"
    if CHAPTER_RE.match(t):
        return "chapter"
    if FRONT_RE.match(t):              # 序/出版说明/凡例 → 与章同级
        return "chapter"
    if BACK_RE.match(t):               # 附录/参考文献/后记 → 与章同级，但收拢内部标题
        return "backmatter"
    # 开篇/收束类必须排在 text_level==1 之前：
    # 有的书每章开头的「前言」被 MinerU 标成了 level 1，若先吃 level 1 就成了章级节点，
    # 会把整章正文吞进「前言」里。
    if OPENING_RE.match(t):
        return "opening"
    if CLOSING_RE.match(t):
        return "closing"
    if SEC_RE.match(t):
        return "sec"
    if ZH_DUN_RE.match(t):
        return "subsec"
    if PAREN_ZH_RE.match(t):
        return "subsubsec"
    if PAREN_ARABIC_RE.match(t):
        return "subitem"
    if ARABIC_RE.match(t):
        return "item"
    # 编号全不匹配，才退回 MinerU 的层级信号
    if text_level == 1:
        return "chapter"
    if text_level == 2:
        return "plain"
    # 编号类标题即使 MinerU 没给 text_level 也要认（封面/摘要这类无编号的除外）
    return None


def is_plausible_heading(b: Block) -> bool:
    t = b.text.strip()
    if not t or len(t) > 60:
        return False
    if t[-1] in "。，；,;！？":
        return False
    if "\n" in t:
        return False
    return True


def collect_headings(blocks: list[Block], skip_pages: set[tuple[str, int]] | None = None,
                     front_zone: tuple[str, int] | None = None,
                     running: set[str] | None = None,
                     trusted: bool = False) -> tuple[list[tuple[Block, str]], list[dict]]:
    """收集标题候选。

    skip_pages  —— 目录页：整页排除（R1）
    front_zone  —— (shard, page)：该页之前（不含）的标题全部排除（R2）
    running     —— R6 版面重复行集合，命中即丢弃
    trusted     —— 该源的 text_level 是否权威（EPUB）。见 classify。
    """
    skip_pages = skip_pages or set()
    cands: list[tuple[Block, str]] = []
    dropped: list[dict] = []

    running = running or set()
    # R9 预备：哪些页上有图/表题注
    figure_pages = {(b.shard, b.page_idx) for b in blocks
                    if b.type == "text" and FIGCAP_RE.match(b.text.strip())}

    def drop(b: Block, reason: str):
        dropped.append({"gid": b.gid, "shard": b.shard, "page_idx": b.page_idx,
                        "text": b.text[:80], "reason": reason})

    for b in blocks:
        if b.type != "text":
            continue
        t = b.text.strip()
        # 候选门槛：MinerU 给了 text_level，或者行首是**章级/节级**编号。
        # 只放章级以上（部分/章/节）进来：这几档在正文里几乎不会以整行形式出现。
        # 细粒度编号（一、/（一）/1.）不进门槛——正文里「一、」式列举很常见，
        # 一旦放宽会成批误收（实测把第一本的节点从 415 撑到 502）。
        looks_structural = bool(PART_RE.match(t) or CHAPTER_RE.match(t) or SEC_RE.match(t))
        if not (b.text_level or looks_structural):
            continue
        key = (b.shard, b.page_idx)
        # R1 无条件生效：目录页上出现的任何行都是目录条目，不是正文标题。
        # （曾经给「第X部分」开过后门，结果放宽到编号类之后，
        #   整页目录条目被当成真章收进来，前 9 个「章」全是目录抄下来的。）
        if key in skip_pages:
            drop(b, "R1 位于书内目录页（条目误标为标题）")
            continue
        if front_zone and b.shard == front_zone[0] and b.page_idx < front_zone[1]:
            drop(b, "R2 位于前置区（封面/书名页/版权页）")
            continue
        # R9 图表页上的碎片：图里每个节点标签都会被 MinerU 抽成独立 text 块，
        # 其中一部分还带 text_level=1。带图注的页面上，短且无编号的行一律不认作标题。
        # 标签权威源（EPUB）不做这条：EPUB 的「页」是一个 xhtml 文档，一章里本来就
        # 可能有图，把整个单元当「图表页」会连这一章的真标题一起误杀。
        if not trusted and key in figure_pages and not _has_numbering(t) and len(t) < 30:
            drop(b, "R9 图表页上的图注碎片")
            continue
        # 长度/标点校验**无条件生效**，编号类不豁免。
        # 曾经的写法让「第X部分」跳过这一步，等我加上「第X节」之后，
        # 正文里以「第一节：…」开头的整段散文就被收成了节标题（实测 250 字一段）。
        # 编号只是「可能是标题」的线索，能不能当标题仍要看它长得像不像标题。
        if not is_plausible_heading(b):
            drop(b, "不像标题（过长或带句末标点）")
            continue
        marker = classify(b.text, b.text_level, running=running, trusted=trusted)
        if marker is None:
            drop(b, "噪声模式或不可判定")
            continue
        # cands 的第三项是稳定主键。真实块用 str(gid)；目录补出来的合成节点
        # 在 _toc_repair 里换成各自的 "toc:..." 键 —— 否则会和它借 gid 的那个
        # 真实块共用主键，人工核定就会「改一条动两条」。
        cands.append((b, marker, str(b.gid)))
    return cands, dropped


# ---------------------------------------------------------------- 印刷目录条目

# 目录行的页号：引导符有点线（……57、....57）、横线（--114、--- -----281）、
# 以及「空格+点线」（… …59）。**点线/横线紧贴页号是中文目录的常态**，旧写法只认
# 「空格+数字」，于是这类整行掉进 pending，再拿去和下一行的页码错配 —— 抽取凭空
# 少一半还配错对象（丁华东 6/52、胡鸿杰 6/38）。
_TOC_PAGE_RE = re.compile(r"(?:[ \u3000][.．·…\-—–]*|[.．·…\-—–]{2,})(\d{1,4})\s*$")
_TOC_DOT_RE = re.compile(r"[.．·…]{2,}")


def extract_printed_toc(blocks: list[Block], region: dict) -> dict:
    """把目录页抽成条目表。

    目录的排版不统一，实测本书有三种行形态，必须都覆盖：
      A 一行式（点线引导）  见证、相遇与发现……冯惠玲 001
      B 两行式（标题独占行，下行是「作者+页码」）
            技术与政策：中国数字人文发展的外部条件环视
            ……刘越男 李少建 余敏 003
      C 两行式（无点线）    中国史学（含思想史）数字人文发展报告
                            邱伟云 胡 恒 王 涛 等 244
    """
    if not region.get("found"):
        return {"found": False, "entries": [], "pages": [], "raw": ""}

    shard = region["shard"]
    pages = region["pages"]
    lines: list[str] = []
    for b in blocks:
        if b.type == "text" and b.shard == shard and b.page_idx in pages:
            lines.extend(b.text.split("\n"))

    entries: list[dict] = []
    pending: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line or line in ("目录", "目 录", "Contents", "CONTENTS"):
            continue

        if PART_RE.match(line):
            entries.append({"title": line, "printed": None, "kind": "part",
                            "page_idx": region["page_range"][0]})
            pending = None
            continue

        m_num = _TOC_PAGE_RE.search(line)
        if not m_num:
            pending = line          # 纯标题行，等下一行给页码
            continue

        printed = int(m_num.group(1))
        # 页码的引导符有两种：点线（……57、....57）与空格（… 57）。**点线紧贴页号是
        # 中文目录的常态**，旧写法只认空格，于是「二 认识论的历史……12」这类整行掉进
        # pending，被拿去和**下一行**的页码配对 —— 抽取凭空少一半，还配错对象。
        m_dot = _TOC_DOT_RE.search(line)
        if m_dot:
            head = line[:m_dot.start()].strip()   # 点线之前是标题（或作者串）
        else:
            head = line[:m_num.start()].strip()

        # 这里**不做**「上一行是标题前半、这一行是后半」的折行拼接：实测它会把
        # 目录里两条独立的行（「序言」／「见证、相遇与发现……冯惠玲 001」）粘成
        # 一条并不存在的标题。折行续段留给 audit 的语义判定去处理，抽取只管
        # 「这一行自己写了什么」——宁可截断，不许造标题。
        if _looks_like_authors(head):
            title = pending or head
        else:
            title = head if len(head) >= 3 else (pending or "")

        pending = None
        title = clean_title(title)
        # 页码被点线/横线引导时，head 已切在点线之前；但「标题……作者 …… 57」这种
        # 两段点线的行，切完仍会在尾部留一截点线（目录节点因此叫「主要参考文献 …」）。
        # 标题不该以点线收尾，砍掉。
        title = re.sub(r"[\s.．·…\-—–_]+$", "", title).strip()
        if len(title) >= 3:
            entries.append({"title": title, "printed": printed, "kind": "chapter",
                            "page_idx": None})

    return {"found": bool(entries), "entries": entries, "pages": pages,
            "shard": shard, "page_idx_range": region.get("page_range"),
            "raw": "\n".join(lines)}


def _looks_like_authors(head: str) -> bool:
    """判断一行是不是「作者+页码」里的作者串（而非标题）。"""
    h = head.strip()
    if not h:
        return True
    norm = re.sub(r"[\s　]", "", h)
    if len(norm) <= 4:
        return True
    return (" " in h or "　" in h or h.endswith("等")) and len(norm) <= 20


def clean_title(t: str) -> str:
    """清掉 MinerU 从 markdown 带过来的转义符与首尾杂符。"""
    s = (t or "").strip()
    s = re.sub(r"\\+([*_`#\[\]()~])", r"\1", s)      # \* → *
    s = re.sub(r"^[#*\s]+", "", s)
    s = re.sub(r"[*\s]+$", "", s)
    return s.strip()


_PUNCT_RE = re.compile(r"[\s·・:：,，.。;；\-—–_()（）\[\]【】\"'“”‘’!！?？]+")


def _norm_title(t: str) -> str:
    """标题归一化，只用于「同一标题的两种写法」比对。

    目录页与正文标题的写法常有细微差异（半角/全角括号、空白、标点），
    实测《伦敦宪章 THE LONDON CHARTER (中英文版)》在目录里是半角括号、
    正文里是全角括号——直接字符串比较会判成两条。
    """
    return _PUNCT_RE.sub("", (t or "").strip()).lower()


# ---------------------------------------------------------------- 用目录补章（R5）

def repair_from_toc(cands: list[tuple[Block, str]], blocks: list[Block],
                    toc: dict, calib) -> tuple[list[tuple[Block, str]], list[dict]]:
    """书内目录权威、MinerU 标题识别会漏。

    实测本书第二部分有两个章标题没被标成 text_level，正文被并进相邻的「前言」节点。
    这里以目录条目为准：凡目录列了、检出中找不到对应标题的章，按目录页码反查物理页，
    在该页首个块处补一个合成标题（marker=chapter，flags 标 from_toc/needs_review）。
    """
    if not toc.get("found") or calib is None:
        return cands, []

    norm = lambda s: re.sub(r"[\s·．.…\-—_、，,：:（）()\*]+", "", s or "")
    existing = [norm(clean_title(b.text)) for b, _, _ in cands]

    def matched(title: str) -> bool:
        k = norm(title)
        if not k:
            return True
        # 短标题（<4 字，如「附录」）必须完全相等才算命中，避免误配
        if len(k) < 4:
            return any(k == e for e in existing if e)
        if any(min(len(k), len(e)) >= 4 and (k in e or e in k)
               for e in existing if e):
            return True
        # 正文那一条只被 OCR 认出了编号（「第一章」），目录给的是全标题
        # （「第一章 社会记忆理论与档案记忆研究的学术坐标」）—— 前者正是后者的前缀。
        # 长度门槛（≥4）会漏掉它，于是又补一个合成节点，跟真实节点**同档同号**
        # （丁华东 6 处、胡鸿杰 3 处「同档位序号重复」全是这么长出来的）。
        for e in existing:
            if not e or len(e) < 2 or len(e) > len(k):
                continue
            if k.startswith(e):
                return True
        return False

    page_first_gid: dict[tuple[str, int], int] = {}
    for b in blocks:
        pk = (b.shard, b.page_idx)
        if pk not in page_first_gid:
            page_first_gid[pk] = b.gid

    added: list[dict] = []
    for e in toc["entries"]:
        if e.get("kind") != "chapter" or not e.get("printed"):
            continue
        if matched(e["title"]):
            continue
        phys = calib.physical_of(int(e["printed"]))
        if phys is None:
            added.append({"title": e["title"], "printed": e["printed"],
                          "status": "跳过", "reason": "目录页码无法映射到物理页"})
            continue
        shard, page_idx = phys
        gid = None
        for probe in range(page_idx, page_idx + 4):        # 空白页/插图页向后找
            gid = page_first_gid.get((shard, probe))
            if gid is not None:
                break
        if gid is None:
            added.append({"title": e["title"], "printed": e["printed"],
                          "status": "跳过", "reason": f"{shard} p{page_idx} 起 4 页内无块"})
            continue
        block = next((b for b in blocks if b.gid == gid), None)
        if block is None:
            continue
        synth = Block(gid=gid, shard=block.shard, local=block.local, page_idx=block.page_idx,
                      type="text", text=e["title"], text_level=1)
        # 合成节点的 gid 是借来的（该页第一个真实块），**主键必须另起**，
        # 否则与那个真实节点共用 key，人工改一条会连带改到它。
        # 键取「分片 + 所借 gid + 目录标题」，都由 OCR 与目录决定，跨重跑稳定。
        cands.append((synth, "chapter", f"{SYNTH_PREFIX}{shard}:{gid}:{_norm_title(e['title'])}"))
        existing.append(norm(e["title"]))
        added.append({"title": e["title"], "printed": e["printed"], "status": "已补",
                      "shard": shard, "page_idx": block.page_idx, "gid": gid,
                      "key": cands[-1][2]})

    cands.sort(key=lambda x: doc_order(x[0].gid, x[2]))
    return cands, added


def apply_manual_added(cands: list[tuple[Block, str, str]], blocks: list[Block],
                       added: dict | None
                       ) -> tuple[list[tuple[Block, str, str]], list[dict]]:
    """把**人工新增的标题断点**接进候选表（MinerU 漏识别标题时人来补）。

    added 的键是 gid（断点落在哪个块），值是 {title, level, offset}。
    这里只把断点变成候选节点 —— 级数不在这里定（marker 只喂自动建树的层级栈），
    真正的级数由 build_tree 把 added 的 level 并进 manual_levels、交给 reflow 重排。

    **正文一字不动**：这块原文照旧留在正文里，新增的标题只是多出一个 `# 标题`。
    """
    if not added:
        return cands, []
    by_gid = {b.gid: b for b in blocks}
    already = {k for _b, _m, k in cands}       # 已是候选的键（这个块本来就是标题？）
    recs: list[dict] = []
    for gid_s, rec in added.items():
        try:
            gid = int(str(gid_s).strip())
        except (TypeError, ValueError):
            recs.append({"gid": gid_s, "status": "跳过", "reason": "键不是块号"})
            continue
        b = by_gid.get(gid)
        if b is None:
            recs.append({"gid": gid, "status": "跳过", "reason": "块不在当前书里"})
            continue
        title = (rec.get("title") or "").strip()
        if not title:
            recs.append({"gid": gid, "status": "跳过", "reason": "标题为空"})
            continue
        key = f"{ADD_PREFIX}{gid}"
        synth = Block(gid=gid, shard=b.shard, local=b.local, page_idx=b.page_idx,
                      type="text", text=title, text_level=1)
        cands.append((synth, "chapter", key))
        recs.append({"gid": gid, "key": key, "title": title, "status": "已加",
                     "level": int(rec.get("level") or 1),
                     "offset": max(0, int(rec.get("offset") or 0)),
                     "shard": b.shard, "page_idx": b.page_idx,
                     "on_heading": str(gid) in already})
    cands.sort(key=lambda x: doc_order(x[0].gid, x[2]))
    return cands, recs


def crosscheck_with_toc(tree: list[Node], toc: dict) -> dict:
    if not toc.get("found"):
        return {"available": False, "note": "未在正文中定位到印刷目录条目"}
    norm = lambda s: re.sub(r"[\s·．.…\-—_、，,：:（）()\*]+", "", s or "")
    # 部分标记也算目录条目（它们没有页码，但同样是目录列出的一级结构）
    chapters = [e for e in toc["entries"] if e.get("kind") in ("chapter", "part")]
    toc_titles = [norm(e["title"]) for e in chapters if len(norm(e["title"])) >= 4]

    def flat(nodes, lvl=1):
        for n in nodes:
            yield n, lvl
            yield from flat(n.children, lvl + 1)

    matched, missing, mismatches, used = [], [], [], set()
    for n, lvl in flat(tree):
        if lvl > 2 or not n.title:
            continue
        key = norm(n.title)
        hit = None
        for i, tt in enumerate(toc_titles):
            if i in used or not key:
                continue
            if min(len(key), len(tt)) >= 4 and (key in tt or tt in key):
                hit = i
                break
        if hit is not None:
            used.add(hit)
            toc_title = chapters[hit]["title"]
            rec = {"nid": n.nid, "title": n.title, "toc_title": toc_title,
                   "from_toc": "from_toc" in n.flags}
            matched.append(rec)
            if clean_title(n.title) != clean_title(toc_title):
                # 只报告、不替换：书内目录是权威，但改标题属内容变更，交人工定夺
                mismatches.append(rec)
        else:
            missing.append({"nid": n.nid, "title": n.title, "level": lvl,
                            "from_toc": "from_toc" in n.flags})
    unmatched = [chapters[i] for i in range(len(toc_titles)) if i not in used]
    denom = len(matched) + len(missing)
    return {
        "available": True,
        "toc_pages": toc.get("pages", []),
        "toc_entry_count": len(chapters),
        "heading_count": denom,
        "matched": len(matched),
        # 两个数要分开看，别合成一个「匹配率」：
        #   toc_coverage —— 书内目录里列的条目，树里找到了多少（**这是主要的验收指标**）
        #   match_rate   —— 我们的 L1/L2 标题里，有多少能被目录背书
        # 后者天然偏低且无害：目录常常只列到章，而 L1/L2 里混着附录内部标题、
        # 自造的「前置」节点等。把它当验收指标会把好结果显示成坏结果
        # （实测某学位论文 toc_coverage 95%，match_rate 却只有 22%）。
        "toc_coverage": round(len(matched) / len(chapters), 3) if chapters else 0.0,
        "match_rate": round(len(matched) / denom, 3) if denom else 0.0,
        "headings_not_in_toc": missing[:40],
        "toc_entries_not_detected": unmatched[:40],
    }


# ---------------------------------------------------------------- 主流程

def build_tree(source_id: str, blocks: list[Block], doc_title: str = "",
               calib=None, manual_levels: dict | None = None,
               manual_deleted: dict | None = None,
               manual_added: dict | None = None) -> tuple[list[Node], dict]:
    """构建目录树。

    calib 为 None 时只做规则推断；传入 Calibration 时启用 R5（用书内目录补漏掉的章）。
    manual_levels / manual_deleted / manual_added 非空时，在建树收尾处按人工核定重排
    （见 reflow）。
    """
    # 该源的层级是否来自**权威标签**（EPUB 的 h1..h6，见 core/epub.py）。
    # 一旦是，下面三处判定要跟着换口径：R2 前置区、R5 目录补章、plain 的定档。
    # 逐块取标记而不是看 project.json 的 mode：mode 是登记信息，标记长在块上，
    # 混合源（理论上）也不会串。PDF 源恒为 False，整条路径逐位不变。
    trusted = any(b.level_trusted for b in blocks)

    toc_region = detect_toc_region(blocks)
    skip_pages = set()
    if toc_region.get("found"):
        for p in toc_region["pages"]:
            skip_pages.add((toc_region["shard"], p))
    # R2 前置区：PDF 里第一个标题之前是封面/书名页/版权页，那些标题是书名和机构名。
    # EPUB 不是这个排法 —— 它的书内目录（nav）常排在序/前言**之前**，照 R2 一刀切
    # 会把「序」「前言」这些真内容一起切掉。标签权威源不做 R2。
    front_zone = ((toc_region["shard"], toc_region["heading_page"])
                  if toc_region.get("found") and not trusted else None)

    running = detect_running_texts(blocks)
    cands, dropped = collect_headings(blocks, skip_pages=skip_pages,
                                      front_zone=front_zone, running=running,
                                      trusted=trusted)
    printed_toc = extract_printed_toc(blocks, toc_region)
    # 顺序：先接人工新增，再跑 R5 目录补章。反过来的话，R5 的 matched() 看不到
    # 人工补的断点 —— 人工按目录补了一个「带副题的章标题原文」，R5 又按目录的
    # 简写再合成一个（实测：同 gid 长出 from_toc + manual 两个同档节点）。
    # 先接人工新增后，其标题就在 existing 里，目录条目能匹配上，不再重复补。
    cands, added_recs = apply_manual_added(cands, blocks, manual_added)
    if trusted:
        # R5 目录补章：标题已经是出版方标的，再拿印刷目录去「补」只会在正确的层级上
        # 叠一层猜测（补出来的合成节点还会借 gid、进 needs_review）。跳过补章；
        # 但交叉校验照做 —— crosscheck 是**核对**，不是**改写**。
        toc_added = []
    else:
        cands, toc_added = repair_from_toc(cands, blocks, printed_toc, calib)

    # 「自动级数基准」＝ 一棵**不含人工新增**的树里那些条目的级数。
    # 为什么要单独长一遍：人工新增的节点在下面那趟里也参与层级栈（marker=chapter），
    # 会把紧随其后的节点按 nid 深度往上推一层 —— 实测本书在 gid 36 处补一个断点，
    # 后面的 gid 38 就从 L1 掉成了 L2。而 reflow 对「人没给值」的条目正是沿用这个数
    # （见 reflow 的 want），基准一旦被自己的新增污染，就变成「我只补了一条标题，
    # 旁边十条的级数自己变了」。
    # 递归跑一遍纯自动建树最直白：它就是「没做过任何人工编辑时看到的目录」。
    # 只有真的存在人工新增时才多花这一趟（本机 415 节点，代价可忽略）。
    base_levels: dict[str, int] = {}
    if manual_added:
        _auto_roots, _ = build_tree(source_id, blocks, doc_title=doc_title, calib=calib)
        for _r in _auto_roots:
            for _n in walk_nodes(_r):
                base_levels[node_key(_n)] = _n.level

    roots: list[Node] = []
    stack: list[Node] = []

    # 第一个标题之前的块（封面/版权/目录前的页）挂到隐式前置节点
    first_gid = min((b.gid for b, _, _ in cands), default=len(blocks))
    front_node = None
    if first_gid > 0:
        front_node = Node(title="前置（封面·书名页·版权页）", marker="front", rank=5,
                          key=str(blocks[0].gid) if blocks else "0",
                          gid_start=0, gid_end=first_gid - 1,
                          shard=blocks[0].shard if blocks else "", page_idx=0)
        roots.append(front_node)

    # R7 用得到的料：印刷目录里列过的标题（归一化后比对），
    # 以及「哪些 marker 是同章级的硬边界」。
    toc_titles = {_norm_title(e.get("title", "")) for e in printed_toc.get("entries", [])}
    CHAPTER_RANKED = ("part", "chapter", "backmatter")
    # 「已经进入后置件区」必须用独立状态记，不能靠栈上找祖先：
    # 后置件之间是兄弟（参考文献/后记/附录平级），一旦压入下一个兄弟，
    # 前一个就被弹栈，用栈找会找不到自己正身处其中。
    in_backmatter = False

    # 主键唯一性兜底：合成节点的键理论上各不相同，但人工核定全靠这个键，
    # 一旦重复就是「改一条动两条」的静默错误 —— 宁可当场加后缀并记档，
    # 也不能让它悄悄进树。（真实块键=str(gid)，gid 本身就是唯一的。）
    seen_keys: set[str] = set()
    collisions: list[dict] = []
    for i, (b, marker, k) in enumerate(cands):
        if k in seen_keys:
            k2 = f"{k}#{i}"
            collisions.append({"gid": b.gid, "key": k, "renamed": k2,
                               "title": clean_title(b.text)[:60]})
            cands[i] = (b, marker, k2)
            k = k2
        seen_keys.add(k)

    # 人工新增的标题：断点在该块**内部**的字符位移（0 ＝ 块首，是绝大多数情况）
    added_offset = {r["key"]: int(r.get("offset") or 0)
                    for r in added_recs if r.get("status") == "已加" and r.get("key")}

    for i, (b, marker, _key) in enumerate(cands):
        title = clean_title(b.text)

        if marker == "plain":
            if b.level_trusted and b.text_level:
                # 标签权威源：标签说第几层就是第几层，不猜。
                # 步长取 10、起点取 RANK["chapter"]，于是 rank 与「嵌套深度」一一对应
                # （h1→20，h2→30，h3→40…），assign_ids 从父链点出来的 level 正好是
                # 标签的层数。若改成「与紧随其后的编号节同级」，这条 h3 就会被拉成 h2。
                rank = RANK["chapter"] + (max(1, int(b.text_level)) - 1) * 10
            else:
                # R3 之外的无编号标题：与紧随其后的编号节同级
                nxt_rank = None
                for b2, m2, _k2 in cands[i + 1:]:
                    if m2 in NUMBERED_MARKERS:
                        nxt_rank = RANK[m2]
                        break
                    if m2 in CHAPTER_RANKED:
                        break
                rank = nxt_rank if nxt_rank else (stack[-1].rank + 5 if stack else RANK["chapter"])
        elif marker in ("opening", "closing"):
            # R3：引言/结语是无编号标题，应与本章内第一层编号节（「第X节」或「一、」）同级，
            # 不能当它们的父节点。先看后面紧跟着的编号节属于哪一档，据此对齐；
            # 后面没有编号节（如章末「结语」）就退回「一、」那一档。
            chapter_rank = next((n.rank for n in reversed(stack) if n.rank <= RANK["chapter"]),
                                RANK["chapter"])
            nxt_rank = None
            for b2, m2, _k2 in cands[i + 1:]:
                if m2 in NUMBERED_MARKERS:
                    nxt_rank = RANK[m2]
                    break
                if m2 in CHAPTER_RANKED or m2 in ("opening", "closing"):
                    break
            rank = nxt_rank if nxt_rank else chapter_rank + (RANK["subsec"] - RANK["chapter"])
        else:
            rank = RANK[marker]

        if marker == "backmatter":
            in_backmatter = True

        # R7：进入后置件区之后，无编号的章级标题不再是章，而是后置件内部的一节。
        # 例：学位论文附录是《伦敦宪章》中英文全文，里面 PREAMBLE / OBJECTIVES /
        # Principle 1..6 全被 MinerU 标成 level 1，若不收拢，附录一项就炸出 29 个「章」。
        # 例外：印刷目录里列过的标题仍按目录给的层级走——目录是权威。
        if (in_backmatter and rank == RANK["chapter"] and marker == "chapter"
                and not _has_numbering(b.text.strip())
                and _norm_title(title) not in toc_titles):
            rank = RANK["sec"]

        while stack and stack[-1].rank >= rank:
            stack.pop()

        parent_id = stack[-1].nid if stack else ""
        # 「这条是我从印刷目录补出来的」必须看**键**，不能看 gid：合成节点的 gid 是
        # 借来的，用 gid 判会把同页那个真实块一起标成 from_toc
        # （《认识论引论》的「第一章」就曾被误标一次）。人工新增的标题也不来自目录，
        # 单独标 manual，别混进 from_toc。
        if is_added_key(_key):
            flags = ["manual"]
        elif is_synth_key(_key):
            flags = ["from_toc", "needs_review"]
        else:
            flags = []
        node = Node(title=title, marker=marker, rank=rank, key=_key,
                    gid_start=b.gid, gid_end=b.gid,
                    shard=b.shard, page_idx=b.page_idx, parent=parent_id, flags=flags,
                    offset=added_offset.get(_key, 0))
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        # R4：目录是终端节点，不压栈，后续标题不得嵌套进它
        if marker != "toc":
            stack.append(node)

    assign_ids(roots)
    # 人工新增的标题，级数也交给 reflow —— 与手动定级走同一条路（键是 add:<gid>）。
    # 用 setdefault：界面上改过这个键的级数时以 levels 为准，added 里那份只是初值。
    eff_levels = dict(manual_levels or {})
    for rec in added_recs:
        if rec.get("status") == "已加":
            eff_levels.setdefault(rec["key"], max(1, min(9, int(rec.get("level") or 1))))
    if eff_levels or manual_deleted:
        roots, stats = reflow(roots, eff_levels, blocks, front_node,
                              deleted=manual_deleted, base_levels=base_levels)
    else:
        stats = attach_content(roots, blocks, front_node)
    cross = crosscheck_with_toc(roots, printed_toc)

    outline = {
        "source_id": source_id,
        "doc_title": doc_title,
        "generated_at": now_iso(),
        "level_stats": stats,
        "node_total": sum(s["nodes"] for s in stats.values()),
        "toc_region": toc_region,
        "printed_toc": printed_toc,
        "toc_repair": toc_added,
        "toc_crosscheck": cross,
        "dropped_headings": dropped,
        "running_texts": sorted(running),
        "unclassified_types": unclassified_types(blocks),
        # 层级是从哪儿来的：tag = 采纳出版方的 h1..h6（EPUB），numbering = 中文编号推断（PDF）。
        # 写进产物，是为了让「这本书的层级为什么长这样」可回查，不用去猜代码走了哪条支。
        "level_source": "tag" if trusted else "numbering",
        "manual_level_count": len([k for k in (manual_levels or {}) if k]),
        "manual_deleted_count": len(manual_deleted or {}),
        "manual_added": added_recs,
        "manual_added_count": len([r for r in added_recs if r.get("status") == "已加"]),
        "tree": [n.to_dict() for n in roots],
    }
    return roots, outline


def _want_level(n: Node, levels: dict, base_levels: dict | None) -> int:
    """这条条目最终该是第几级：人给过就用人的，否则用「自动级数基准」。"""
    k = node_key(n)
    if k in levels:
        return int(levels[k])
    base = (base_levels or {}).get(k)
    return max(1, int(base if base else (n.level or 1)))


def reflow(roots: list[Node], levels: dict[str, int], blocks: list[Block] | None = None,
           front_node: Node | None = None,
           deleted: dict | set | None = None,
           base_levels: dict[str, int] | None = None) -> tuple[list[Node], dict]:
    """按人工指定的层级**重排**目录树，返回 (roots, level_stats)。

    为什么不是「直接改 node.level」：
      level 是 `assign_ids()` 从 nid 点数**派生**的，父子关系其实由建树时的 rank
      单调性决定。改一个节点的 level 而不重排，等于只改了显示的数字、树还是原样。
      所以这里按文档序取出全部节点，用人给的值（没给的沿用自动值）重走一遍
      「层级栈」算法，再重新挂 children、重算 nid 与内容归属。

    键用 gid_start（块序号）而不是 nid：nid 是重排的产物，改一次就全变；
    gid_start 指向 MinerU 原始块，跨重跑稳定 —— 人工成果才留得住。

    **级数就是人给的那个数，不按树深度改写。**
    这里曾经把 `n.level` 留给 assign_ids() 按 nid 点数派生，结果是：你只把一条
    从 L5 提到 L3，紧随其后的邻居会从 L5 掉到 L4（前面抽掉了一层，后面整串跟着上浮）。
    人只动了一条，屏幕上十几条的级数一起变 —— 而他明确要求「我的指定是优先的」。
    父子关系仍由上面的层级栈定（parent ＝ 前面最近一个级数更小的标题），
    但那是**结构**，不再反过来改写任何一条自己的级数。
    自动路径上 depth 本来就恒等于 level（本书 415/415），所以这里在无人干预时是恒等变换。

    deleted —— 人工判为「识别错了」的条目（gid_start 集合，或 deleted_for() 的字典）。
      被剔除的节点**不参与**层级栈，于是它后面的节点自然回到它父级名下：
      子条上提一级、正文归给上一个还开着的节点 —— 这正是「只删这一条」想要的，
      不需要额外搬运子树。

    blocks 必须传：重排会把 n_blocks / n_chars 清零（attach_content 是累加语义），
    不在这里重算，调用方就会拿到一棵字符数全为 0 的树。

    base_levels —— 「自动级数基准」：key → 纯自动树里的级数（build_tree 现算现给）。
      人没给值的条目沿用**它**，而不是手上这棵树的 n.level。区别在于手上这棵树可能
      已经含了人工新增的节点，那些节点在中间那趟里改动过邻居的 nid 深度。
      传 None 时退回 n.level（老行为，只有直接调 reflow 的测试会走到）。
    """
    gone = {str(k) for k in (deleted or {})}
    if not levels and not gone:
        # 没有人工定级、也没删过东西 = 无事发生。**不要**在这里顺手调 attach_content：
        # 它是累加语义，调用方（build_tree）已经算过一次，再算就翻倍。
        return roots, {}

    flat: list[Node] = []
    for r in roots:
        flat.extend(walk_nodes(r))
    if gone:
        # front_node 不允许被删：它是合成的兜底节点，承接「第一个标题之前」的全部块
        # （封面/书名页/版权页）。把它剔除会让这些块无主，导出时凭空少一截正文。
        flat = [n for n in flat if n is front_node or node_key(n) not in gone]
    if not flat:
        return [], {}
    # 同一 gid 上真实块必须排在合成块之前（合成块借了它的 gid）。
    flat.sort(key=lambda n: doc_order(n.gid_start, node_key(n)))

    want = {id(n): _want_level(n, levels, base_levels) for n in flat}

    new_roots: list[Node] = []
    stack: list[tuple[int, Node]] = []
    for n in flat:
        n.children = []
        n.gid_end = n.gid_start
        n.n_blocks = 0
        n.n_chars = 0
        n.flags = [f for f in n.flags if f != "empty"]

        lv = want[id(n)]
        while stack and stack[-1][0] >= lv:
            stack.pop()
        if stack:
            stack[-1][1].children.append(n)
        else:
            new_roots.append(n)
        stack.append((lv, n))

    assign_ids(new_roots)
    # 级数按**人给的**来（没给的沿用自动值）。树的父子关系上面已经用层级栈定好了，
    # 不能让 assign_ids 拿 nid 深度反过来改写 level —— 那就是「只动一条、邻居跟着变」
    # 的根源。必须放在 attach_content 之前：level_stats 是按 n.level 统计的。
    for n in flat:
        n.level = want[id(n)]
    stats = attach_content(new_roots, blocks, front_node) if blocks is not None else {}
    return new_roots, stats


def assign_ids(roots: list[Node], prefix: str = "") -> None:
    """编 nid 并派生出 level / parent。

    parent 必须在这里回填：建树那一趟里 `stack[-1].nid` 还全是空串
    （nid 是本函数才编的），所以循环里赋的 parent 一律是 ""。以前没人读它，
    就这样一路写进了 outline.json —— 每个节点的 parent 都是空，纯误导。
    """
    for i, n in enumerate(roots, 1):
        n.nid = f"{prefix}{i}"
        n.level = n.nid.count(".") + 1
        n.parent = prefix[:-1] if prefix else ""
        assign_ids(n.children, n.nid + ".")


def walk_nodes(node: Node):
    yield node
    for c in node.children:
        yield from walk_nodes(c)


def ancestors_of(by_id: dict[str, Node], node: Node) -> list[Node]:
    out = [node]
    parts = node.nid.split(".")
    for k in range(1, len(parts)):
        anc = by_id.get(".".join(parts[:k]))
        if anc:
            out.append(anc)
    return out


def _fix_end(node: Node) -> int:
    """父节点覆盖范围必须包住全部后代。"""
    end = max(node.gid_start, node.gid_end)
    for c in node.children:
        end = max(end, _fix_end(c))
    node.gid_end = end
    return end


def _split_pieces(b: Block, breakpoints: dict, head: Node | None
                  ) -> list[tuple[int, int, Node | None]]:
    """把一个块按其**内部断点**（offset > 0）切成若干段，返回 [(起, 止, 归属节点)]。

    head ＝ 块首那一段的归属（调用方算好，见 attach_content）。
    绝大多数块没有内部断点，整块一段归 head（返回值恒为长度 1 的列表，与改造前的
    行为逐字一致）。只有 MinerU 把标题和正文并进同一个段落、人又在段落中间立了
    断点时，这里才会真的切出两段以上。
    """
    ns = breakpoints.get(b.gid) or []
    text = b.text or b.table_body or ""
    n = len(text)
    marks: list[tuple[int, Node | None]] = [(0, head)] if head is not None else []
    # 同位置的多个断点（offset 相同）只算一个：后一个不产生新段，直接丢掉
    for c in sorted((x for x in ns if int(x.offset or 0) > 0),
                    key=lambda x: int(x.offset)):
        st = min(max(0, int(c.offset)), n)
        if marks and st <= marks[-1][0]:
            continue
        marks.append((st, c))
    if not marks:
        return [(0, n, None)]
    out = []
    for i, (st, node) in enumerate(marks):
        en = marks[i + 1][0] if i + 1 < len(marks) else n
        if en > st:
            out.append((st, en, node))
    return out


def attach_content(roots: list[Node], blocks: list[Block],
                   front_node: Node | None = None) -> dict:
    """把块归到「当前最深打开节点」，据此回填每个节点的 gid 覆盖范围与统计。"""
    flat: list[Node] = []
    for r in roots:
        flat.extend(walk_nodes(r))
    by_id = {n.nid: n for n in flat}

    # 块 → 落在它里面的断点（人工新增的标题可能切进段落内部）。
    breakpoints: dict[int, list[Node]] = {}
    for n in flat:
        if n is front_node or n.gid_start < 0:
            continue
        breakpoints.setdefault(n.gid_start, []).append(n)
    for g, ns in breakpoints.items():
        ns.sort(key=lambda x: (int(x.offset or 0), doc_order(x.gid_start, node_key(x))))

    owner: dict[int, Node] = {}
    # 进入这个块**之前**的归属者。块内断点（offset>0）把段落切开时，块首那一截
    # 仍属前一个节点 —— 它才是当时开着的那个。
    prev_owner: dict[int, Node | None] = {}
    cur: Node | None = front_node
    for b in blocks:
        before = cur
        if b.gid in breakpoints:
            # 同一 gid 上可能有多个节点（真实块 + 目录补章 / 人工新增）。
            # 块级归属给**最后一个** —— 与改造前一致：两个节点抢同一个块时，
            # 树里靠后的那个接管。真正的切分在字符统计里按 offset 做。
            cur = breakpoints[b.gid][-1]
        owner[b.gid] = cur  # type: ignore[assignment]
        prev_owner[b.gid] = before

    # 覆盖范围（含后代）
    for b in blocks:
        o = owner.get(b.gid)
        if o is None:
            continue
        for x in ancestors_of(by_id, o):
            if x.gid_start < 0 or b.gid < x.gid_start:
                x.gid_start = b.gid
            if b.gid > x.gid_end:
                x.gid_end = b.gid

    # 字符/块统计（含后代），版面噪声与空块不计
    for b in blocks:
        if not b.is_content or is_noise_text(b.text):
            continue
        text = b.text or b.table_body or ""
        if not text.strip():
            continue
        ns = breakpoints.get(b.gid) or []
        if any(int(x.offset or 0) == 0 for x in ns):
            head: Node | None = owner.get(b.gid)      # 有人从块首立断点 → 块首归它
        else:
            head = prev_owner.get(b.gid)              # 否则块首仍属进入这块前的节点
        # 块数只记一次（给块首的归属者）—— 把一个段切成两半不该让 Σ n_blocks 凭空变大
        if head is not None:
            for x in ancestors_of(by_id, head):
                x.n_blocks += 1
        # 字数按断点切开分配。每段恰好归一个节点，所以 Σ n_chars 仍等于全文字数。
        for st, en, o in _split_pieces(b, breakpoints, head):
            if o is None or en <= st:
                continue
            for x in ancestors_of(by_id, o):
                x.n_chars += (en - st)

    for n in flat:
        if n.n_blocks == 0 and not n.children:
            n.flags.append("empty")

    # 层级统计（给「切到第几层」用）
    stats: dict[str, dict] = {}
    for n in flat:
        key = str(n.level)
        s = stats.setdefault(key, {"nodes": 0, "files_if_split": 0, "chars": 0,
                                   "min_chars": None, "max_chars": 0, "empty": 0})
        s["nodes"] += 1
        s["chars"] += n.n_chars
        s["min_chars"] = n.n_chars if s["min_chars"] is None else min(s["min_chars"], n.n_chars)
        s["max_chars"] = max(s["max_chars"], n.n_chars)
        if n.n_blocks == 0:
            s["empty"] += 1
    for key, s in stats.items():
        s["files_if_split"] = s["nodes"]
        s["avg_chars"] = int(s["chars"] / s["nodes"]) if s["nodes"] else 0
    return stats


def write_outline(wd: Path, outline: dict) -> Path:
    return write_json(Path(wd) / "outline.json", outline)


def load_outline(wd: Path) -> dict:
    d = read_json(Path(wd) / "outline.json", None)
    if not d:
        raise FileNotFoundError(f"{wd} 下没有 outline.json —— 先跑 outline")
    return d


def outline_report(outline: dict) -> str:
    L = [f"# 目录索引：{outline.get('doc_title') or outline.get('source_id')}", "",
         f"- 生成：{outline['generated_at']}",
         f"- 节点总数：{outline['node_total']}",
         f"- 层级来源：{'出版方标签 h1..h6（权威）' if outline.get('level_source') == 'tag' else '中文编号推断'}", ""]

    tr = outline.get("toc_region", {})
    if tr.get("found"):
        pt = outline.get("printed_toc", {})
        L += [f"- 书内印刷目录：{tr['shard']} 物理页 {tr['page_range']}，"
              f"抽出 {len(pt.get('entries', []))} 条目录项", ""]

    L += ["## 层级统计（选切分深度用）", "",
          "| 层级 | 节点数 | 若切到本层=文件数 | 总字数 | 平均字数 | 最小 | 最大 | 空节点 |",
          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for k in sorted(outline["level_stats"], key=int):
        s = outline["level_stats"][k]
        L.append(f"| L{k} | {s['nodes']} | **{s['files_if_split']}** | {s['chars']:,} "
                 f"| {s['avg_chars']:,} | {s['min_chars']} | {s['max_chars']:,} | {s['empty']} |")
    L.append("")

    cc = outline.get("toc_crosscheck", {})
    if cc.get("available"):
        L += ["## 与书内印刷目录交叉校验", "",
              f"- 目录页：{cc.get('toc_pages')}（{cc['toc_entry_count']} 条目录项）",
              f"- 目录覆盖率 **{cc['toc_coverage']:.0%}**（目录 {cc['toc_entry_count']} 条中，"
              f"树里找到 {cc['matched']} 条）",
              f"- 本级标题 {cc['heading_count']} 个，其中 {cc['matched']} 个有目录背书"
              f"（{cc['match_rate']:.0%}）",
              "",
              "> 目录覆盖率是主要验收指标。后者天然偏低且无害：目录常常只列到章，",
              "> 而本级标题里混着附录内部标题、自造的「前置」节点等。", ""]
        if cc.get("headings_not_in_toc"):
            L.append("- 检出但目录未列（可能是页眉噪声，或目录只列到章）：")
            for h in cc["headings_not_in_toc"][:12]:
                L.append(f"    - L{h['level']} `{h['nid']}` {h['title'][:50]}")
            L.append("")
        if cc.get("toc_entries_not_detected"):
            L.append(f"- 目录有但未检出（{len(cc['toc_entries_not_detected'])} 条，前 12）：")
            for t in cc["toc_entries_not_detected"][:12]:
                L.append(f"    - {t['title'][:60]}（目录页 {t['printed']}）")
            L.append("")
        if cc.get("title_mismatches"):
            L.append(f"- 标题与目录写法不一致（{len(cc['title_mismatches'])} 处，未自动替换）：")
            for m in cc["title_mismatches"][:12]:
                L.append(f"    - `{m['nid']}` 检出「{m['title'][:44]}」／目录「{m['toc_title'][:44]}」")
            L.append("")
    else:
        L += [f"> 印刷目录交叉校验：{cc.get('note', '不可用')}", ""]

    L += ["## 目录树", ""]

    def walk(nodes: list[dict], depth: int = 0):
        for n in nodes:
            flag = ("  ⚠️" + ",".join(n["flags"])) if n.get("flags") else ""
            L.append(f"{'    ' * depth}- `{n['nid']}` **{n['title']}** "
                     f"〔{n['marker']}｜{n['shard']}p{n['page_idx']}｜{n['n_chars']:,}字"
                     f"｜{n['n_blocks']}块〕{flag}")
            walk(n.get("children", []), depth + 1)

    walk(outline["tree"])
    L.append("")

    if outline.get("dropped_headings"):
        L += ["## 被丢弃的候选标题", "",
              "| 分片 | 物理页 | 文本 | 原因 |", "| --- | --- | --- | --- |"]
        for d in outline["dropped_headings"][:80]:
            L.append(f"| {d['shard']} | {d['page_idx']} | {d['text'][:50]} | {d['reason']} |")
        L.append(f"\n（共 {len(outline['dropped_headings'])} 条）")
    return "\n".join(L)
