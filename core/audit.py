"""目录校核：把「人肉通读几百条目录去找错」压成「复核 N 条待定项」。

定位（与相邻两层的关系，别搞混）：
  - `outline.py` 负责**建**目录树：确定性规则，不调 Agent。
  - `verify.py` 负责**导出产物**能不能用：页锚覆盖、内容无损、页码一致。
  - 本模块负责**目录树本身对不对**：结构、编号、页码、与书内印刷目录的对照。
    它只读 outline.json / page_calibration.json / manual_edits.json，不改任何产物。

为什么要有这一层：
  目录里的错误分两类。一类是纯机械的 —— 同父节点下「第五章」出现两次、父节点页码
  比子节点还靠后、印刷目录说第 57 页而树里落在第 59 页。这类**机器比人强**：400 条
  里差 2 页这种系统性偏移，人逐条看是看不出来的，因为它不"扎眼"，但一算就露。
  另一类是语义的 —— 一二三四五里少了三，是**真的漏了一章**还是**作者本来就没写
  第三章**；一条「引言」该挂 L3 还是并在章下。这类机器判不了，得去正文里找证据。
  所以本模块只做第一类，并为第二类**备好证据窗口**（见 finding 的 `probe`），
  交给 Agent 判定，人工只复核 Agent 的结论。

`probe` 是这一层对外的关键契约：凡是"疑似缺一条标题"的发现，都必须附上
  - `expect`：期望补出来的标题长什么样（编号由哪来）
  - `gid_range` 或 `physical`：去**哪些块**里找
  - `printed` / `source`：线索来自目录页码还是相邻节点
绝不替人决定补不补 —— 只把搜索范围缩到几十块，让判定有据可依。

输出 `_work/<sid>/目录校核.json`（机器/AI 消费）与 `目录校核.md`（人读）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .pagecal import load_calibration
from .util import now_iso, read_json, write_json, write_text

# ---------------------------------------------------------------- 编号识别
# 与 outline.py 的 RANK 档位同源：这些档位各自独立计数，**不跨档位比较**。
# 「第一部分」下面跟「一、」再跟「（一）」再跟「1.」是四套互不相干的序号，
# 混在一张表里数连续性只会满屏假报。
ZH = "一二三四五六七八九十百零"
PART_RE = re.compile(r"^第\s*([" + ZH + r"\d]{1,3})\s*(?:部分|编|篇)")
CHAP_RE = re.compile(r"^第\s*([" + ZH + r"\d]{1,3})\s*(?:章|回|讲)")
SEC_RE = re.compile(r"^第\s*([" + ZH + r"\d]{1,3})\s*(?:节|小节)")
DUN_RE = re.compile(r"^([" + ZH + r"]{1,3})\s*、")
PAREN_ZH_RE = re.compile(r"^[（(]\s*([" + ZH + r"]{1,3})\s*[）)]")
PAREN_AR_RE = re.compile(r"^[（(]\s*(\d{1,2})\s*[）)]")
ARABIC_RE = re.compile(r"^(\d{1,2})\s*[.．、]")

# 档位 → 人类可读的「下一号怎么写」，probe.expect 用
TIER_HINT = {
    "part": "第{n}部分", "chapter": "第{n}章", "sec": "第{n}节",
    "dun": "{n}、", "paren_zh": "（{n}）", "paren_ar": "（{n}）", "arabic": "{n}. ",
}

_M = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
      "六": 6, "七": 7, "八": 8, "九": 9}


def zh2int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _M.get(s[1], 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return _M.get(a, 0) * 10 + (_M.get(b, 0) if b else 0)
    return _M.get(s) if len(s) == 1 else None


def numbering(title: str) -> tuple[str, int] | None:
    """从标题取「档位 + 序号」。取不到返回 None（无编号标题，如「引言」「结语」）。"""
    t = (title or "").strip()
    for tier, rx in (("part", PART_RE), ("chapter", CHAP_RE), ("sec", SEC_RE)):
        m = rx.match(t)
        if m:
            n = zh2int(m.group(1))
            if n is not None:
                return tier, n
    for tier, rx in (("dun", DUN_RE), ("paren_zh", PAREN_ZH_RE),
                     ("paren_ar", PAREN_AR_RE), ("arabic", ARABIC_RE)):
        m = rx.match(t)
        if m:
            n = zh2int(m.group(1))
            if n is not None:
                return tier, n
    return None


def int2zh(n: int) -> str:
    """1→一、11→十一、21→二十一。编号回写成人看得懂的写法。"""
    zh = "零一二三四五六七八九"
    if n < 10:
        return zh[n]
    if n < 20:
        return "十" + (zh[n - 10] if n > 10 else "")
    return zh[n // 10] + "十" + (zh[n % 10] if n % 10 else "")


def numbering_suffix(tier: str, n: int) -> str:
    """把序号写回原样式，供 probe.expect 与人读的发现标题用。

    档位决定用汉字还是阿拉伯数字 —— 「二、」和「2、」在书里是两种排版，
    probe 让 Agent 去正文里搜的时候，写错了就等于给了个搜不到的关键词。
    """
    if tier in ("part", "chapter", "sec", "dun", "paren_zh"):
        return TIER_HINT[tier].format(n=int2zh(n))
    return TIER_HINT[tier].format(n=n)


# ---------------------------------------------------------------- 定位符
_LEADER = re.compile(r"[.．·…\s]{2,}\d{0,4}\s*$")
_PUNCT = re.compile(r"[\s·・:：,，.。;；\-—–_()（）\[\]【】\"'“”‘’!！?？…/\\]+")


def parse_locator(loc):
    """'57' → (0, 57)；'front-9' → (1, 9)；None → (2, None)。

    前置页与正文页是**两套独立的页码序列**（序言用 1、2、3…，正文重新从 1 起），
    所以跨这两者比大小是无意义的 —— 必须分开比，跨区间的相邻条目单列成一类发现。
    """
    if isinstance(loc, str) and loc.startswith("front-"):
        tail = loc[6:].split("-")[0]
        return (1, int(tail)) if tail.isdigit() else (1, None)
    if loc is None or loc == "":
        return (2, None)
    try:
        return (0, int(loc))
    except (TypeError, ValueError):
        return (2, None)


def _depth_of(nid: str) -> int:
    """nid（"4.1.3"）的层级深度。用来判断一条发现是否落在关注深度以内。"""
    return len([x for x in str(nid or "").split(".") if x])


def _manual_added_gids(oc) -> set:
    """人工补过标题断点的块 gid 集合。

    outline.json 的 `manual_added` 是**对象列表**（每项带 gid），不是按 gid 索引的
    字典 —— 直接 `set(dict)` 会得到一串 `str` 化的整条记录，判定永远不命中。
    """
    out = set()
    for a in ((oc or {}).get("manual_added") or []):
        if isinstance(a, dict):
            if a.get("gid") is not None:
                out.add(str(a["gid"]))
        else:
            k = str(a).split(":")[-1]
            if k.isdigit():
                out.add(k)
    return out


def norm_title(t: str) -> str:
    """比对用标题归一化。

    比 outline.crosscheck 的写法多一步：**先剥尾部点线与页码**。书内目录被 OCR 成
    「第五章 文件生命周期理论的产生与盛行… …95」时，点线页码会跟着标题一起进来，
    不剥掉就整条匹配不上，于是「目录里 24 条，树里一条都没对上」—— 看着像目录建
    错了，其实是取目录那一步没洗干净。
    """
    s = str(t or "").strip()
    for _ in range(3):
        s2 = _LEADER.sub("", s)
        if s2 == s:
            break
        s = s2
    return _PUNCT.sub("", s)


def toc_entry_is_noisy(t: str) -> bool:
    """目录条目里还留着一串点线或尾部页码 —— 说明抽取没洗干净，不是标题本身长这样。

    点线要按「两段以上、中间可隔空格」判：OCR 常把一行点线吐成 `… …`（两个单点，
    中间一个空格），照 `{3,}` 连着数会漏掉，于是这些条目整批匹配不上，coverage
    看着像目录烂了，其实目录是好的。尾部页码可选 —— 有的条目只漏了点线没漏页码。
    """
    return bool(re.search(r"(?:[.．·…]\s*){2,}\d*\s*$", str(t or "")))


# ---------------------------------------------------------------- 检查

class Audit:
    """一次校核。构造后 findings 就是清单。"""

    def __init__(self, source_id: str, wd: Path, depth: int = 3):
        self.source_id = source_id
        self.wd = Path(wd)
        self.depth = max(1, int(depth))
        self.findings: list[dict] = []
        self._n = 0

    # ---- 收集

    def _add(self, check: str, severity: str, title: str, detail: str = "",
             scope: str = "", nids: list | None = None, keys: list | None = None,
             evidence: dict | None = None, probe: dict | None = None) -> None:
        # 「只需要到 L3」：比关注深度更深的条目，严重度降一档 —— 它们不影响导出，
        # 但也不能删掉，否则「L4 里有一坨重复标题」这种事就没人看见了。
        if severity == "high" and scope and _depth_of(scope) > self.depth:
            severity = "warn"
        self._n += 1
        self.findings.append({
            "id": f"{check.split('_')[0].upper()}-{self._n:03d}",
            "check": check, "severity": severity, "scope": scope,
            "nids": nids or [], "keys": keys or [],
            "title": title, "detail": detail,
            "evidence": evidence or {}, "probe": probe,
        })

    # ---- 主流程

    def run(self) -> dict:
        oc = read_json(self.wd / "outline.json", None)
        if not oc:
            raise FileNotFoundError("还没有目录索引 —— 先跑 outline")
        try:
            calib = load_calibration(self.wd)
        except FileNotFoundError:
            calib = None
        self.oc, self.calib = oc, calib
        self.flat = []
        self._flatten(oc.get("tree") or [], None, 1)
        self.by_nid = {r["nid"]: r for r in self.flat}
        if calib is not None:
            for r in self.flat:
                r["loc"] = (calib.locator(r["shard"], r["page_idx"])
                            if r["shard"] and r["page_idx"] is not None else None)
        else:
            for r in self.flat:
                r["loc"] = None
        for r in self.flat:
            r["zone"], r["pnum"] = parse_locator(r["loc"])

        # 必须先算：页码类的检查要靠它把「一串症状」归到「一个根因」上。
        self._opages: dict[str, set] = {}
        self.outliers = self._obs_outliers() if calib is not None else {}

        self._check_structure()
        self._check_numbering()
        self._check_pages()
        self._check_toc()
        self._check_dropped()
        self._check_calibration()
        return self._report()

    def _flatten(self, nodes: list, parent: str | None, depth: int) -> None:
        for i, n in enumerate(nodes):
            rec = {
                "nid": n.get("nid", ""), "level": n.get("level", 1),
                "title": n.get("title", ""), "marker": n.get("marker", ""),
                "key": n.get("key") or str(n.get("gid_start")),
                "gid": n.get("gid_start"), "shard": n.get("shard", ""),
                "page_idx": n.get("page_idx", 0), "chars": n.get("n_chars", 0),
                "blocks": n.get("n_blocks", 0), "flags": n.get("flags", []),
                "parent": parent, "depth": depth, "seq": i,
            }
            self.flat.append(rec)
            self._flatten(n.get("children") or [], rec["nid"], depth + 1)

    def _kids(self) -> dict:
        out: dict = {}
        for r in self.flat:
            out.setdefault(r["parent"], []).append(r)
        return out

    # ---- 单页离群观测（页码类问题的**根因**）

    _OUTLIER_WINDOW = 4

    def _obs_outliers(self) -> dict:
        """找出「单页页码与两侧整段序列只差一两位」的页 —— 页码类问题的根因。

        为什么要单列一类：一处 OCR 误读会把一条本来连续的页码序列劈成三段
        （前段／这一页／后段），于是「区间重叠」「接续不上」「页码倒退」「极短区间」
        成串报出来 —— 30 多页的书能报出 40 个 🔴，看着像目录烂了，其实根因只有一个。

        判据（**不含任何猜测**，全部可复核）：
          ① 该区间只覆盖 1–2 页、不是人工锚点；
          ② 它的 offset 与本片**主 offset**（观测最多者）不同；
          ③ 窗口内前后都能找到主 offset 的区间 —— 也就是「它夹在一条连续序列中间」。
        一旦③成立而它的印刷值又不落在两侧之间，页码序列就**不可能同时成立**
        （实测：314 → 815 → 316），所以这不是"不同说法"，是其中必有错。
        本方法只**指出根因并给出两侧的应有值**，不替人决定改哪一个。
        """
        out: dict = {}
        segs_all = list(self.calib.segments)
        for shard in {s.shard for s in segs_all}:
            ss = sorted([s for s in segs_all if s.shard == shard],
                        key=lambda s: s.page_idx_start)
            if len(ss) < 3:
                continue
            tally: dict[int, list[int]] = {}
            for s in ss:
                if s.confidence == "manual":
                    continue
                t = tally.setdefault(s.offset, [0, 0])
                t[0] += s.n_obs
                t[1] += s.span
            if not tally:
                continue
            dom = max(tally.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
            for i, s in enumerate(ss):
                if s.span > 2 or s.confidence == "manual" or s.offset == dom:
                    continue
                prev = next((x for x in reversed(ss[:i])
                             if x.offset == dom
                             and x.page_idx_end >= s.page_idx_start - self._OUTLIER_WINDOW),
                            None)
                nxt = next((x for x in ss[i + 1:]
                            if x.offset == dom
                            and x.page_idx_start <= s.page_idx_end + self._OUTLIER_WINDOW),
                           None)
                if prev is None or nxt is None:
                    continue
                out.setdefault(shard, []).append({
                    "page_idx_start": s.page_idx_start,
                    "page_idx_end": s.page_idx_end,
                    "observed": s.printed_start,
                    "expected": s.page_idx_start + dom,
                    "shard_offset": dom,
                    "segment_offset": s.offset,
                    "n_obs": s.n_obs,
                    "confidence": s.confidence,
                    "before": {"page_idx_end": prev.page_idx_end,
                               "printed_end": prev.printed_end, "offset": prev.offset},
                    "after": {"page_idx_start": nxt.page_idx_start,
                              "printed_start": nxt.printed_start, "offset": nxt.offset},
                })
        return out

    def _outlier_pages(self, shard: str) -> set:
        got = self._opages.get(shard)
        if got is None:
            got = {p for o in self.outliers.get(shard, [])
                   for p in range(o["page_idx_start"], o["page_idx_end"] + 1)}
            self._opages[shard] = got
        return got

    def _is_outlier_page(self, shard: str, page_idx) -> bool:
        return page_idx is not None and int(page_idx) in self._outlier_pages(shard)

    def _seg_is_outlier(self, seg: dict) -> bool:
        """区间（dict 形态）是否整段落在离群页上。"""
        sh = seg.get("shard")
        if not sh or seg.get("page_idx_start") is None:
            return False
        return all(self._is_outlier_page(sh, p)
                   for p in range(int(seg["page_idx_start"]),
                                  int(seg.get("page_idx_end",
                                              seg["page_idx_start"])) + 1))

    # ---- 结构

    def _check_structure(self) -> None:
        # A 层级跳变：父→子跨了不止一级。跨级不算错，但十有八九是自动定级把某条
        # 无编号标题（"本章注释""附录"）扔到了继承栈的深处 —— 值得逐条看一眼。
        for r in self.flat:
            p = self.by_nid.get(r["parent"]) if r["parent"] else None
            if p and r["level"] - p["level"] > 1:
                self._add("level_jump", "warn",
                          f"L{p['level']} → L{r['level']} 跨 {r['level']-p['level']-1} 级",
                          f"{p['title']} ▸ {r['title']}",
                          scope=r["nid"], nids=[p["nid"], r["nid"]],
                          keys=[p["key"], r["key"]],
                          evidence={"parent_level": p["level"], "level": r["level"],
                                    "marker": r["marker"], "flags": r["flags"]})

        # 同父下标题重复：OCR 把同一章认成两条时会发生（两条都叫「第五章」），
        # 导出时就是两个内容重叠的文件。归一化后字符串相同的才报。
        for pid, ks in self._kids().items():
            seen: dict[str, dict] = {}
            for r in ks:
                k = norm_title(r["title"])
                if not k:
                    continue
                if k in seen:
                    self._add("duplicate_title", "high", "同层出现重复标题",
                              f"「{r['title']}」与 {seen[k]['nid']} 同父同级",
                              scope=r["nid"], nids=[seen[k]["nid"], r["nid"]],
                              keys=[seen[k]["key"], r["key"]],
                              evidence={"parent": pid, "title": r["title"]})
                else:
                    seen[k] = r

    # ---- 编号

    def _check_numbering(self) -> None:
        """同父同档的序号连续性 —— 这是「第一章、第二章、第四章」那类漏章的第一道筛。

        只在**同一父节点**内、**同一档位**内比。父节点换了序号重新起算（每编下都从
        「第一章」开始），混着比会满屏假报。
        """
        for pid, ks in self._kids().items():
            last: dict[str, dict] = {}
            for r in ks:
                got = numbering(r["title"])
                if not got:
                    continue
                tier, n = got
                prev = last.get(tier)
                if prev is not None:
                    pn = prev["n"]
                    if n == pn:
                        self._add("numbering_repeat", "high", "同档位序号重复",
                                  f"「{numbering_suffix(tier, n)}」出现两次："
                                  f"{prev['node']['title']} ▸ {r['title']}",
                                  scope=r["nid"],
                                  nids=[prev["node"]["nid"], r["nid"]],
                                  keys=[prev["node"]["key"], r["key"]],
                                  evidence={"tier": tier, "n": n, "parent": pid})
                    elif n > pn + 1:
                        gap = n - pn - 1
                        expect = numbering_suffix(tier, pn + 1)
                        # 章/节级的编号跳号是硬信号 —— 书里就是漏了那一章。
                        # 阿拉伯数字的列表编号则多半是「决议条款 / 清单条目」，
                        # 编号常与正文连写，跳号未必意味着漏标题：降一档，
                        # 但保留 probe 取证窗口（现代档案的 2.→8.、27.→30. 就是这类）。
                        sev = "warn" if tier == "arabic" else "high"
                        self._add(
                            "numbering_gap", sev,
                            f"{numbering_suffix(tier, pn)} → {numbering_suffix(tier, n)}："
                            f"缺 {gap} 条（{expect} … {numbering_suffix(tier, n-1)}）",
                            f"{prev['node']['title']} ▸ {r['title']}",
                            scope=r["nid"], nids=[prev["node"]["nid"], r["nid"]],
                            keys=[prev["node"]["key"], r["key"]],
                            evidence={"tier": tier, "prev_n": pn, "n": n, "gap": gap,
                                      "parent": pid},
                            probe={
                                "kind": "gap_between",
                                "expect": [numbering_suffix(tier, x)
                                           for x in range(pn + 1, n)],
                                "gid_range": [prev["node"]["gid"], r["gid"]],
                                "after_nid": prev["node"]["nid"],
                                "before_nid": r["nid"],
                                "hint": "在 gid_range 区间内的正文块里搜这些编号原样"
                                        "（OCR 常把编号与标题之间的空格吃掉，"
                                        "建议按「第」+数字/汉字模糊搜）",
                            })
                    elif n < pn:
                        # 序号**回退**（5. → 1.、11. → 1.）与「缺号」是两件事。
                        # 译文集/论文集里每篇文章自带一套「1. 2. 3.」，同一父节点下
                        # 成串出现回退是**常态**；把它们判成高危，一本文集能刷出十几条
                        # 红字，真正该看的缺号反而被淹掉。所以：回退单列一类、降到 warn，
                        # 并把判定权交回人 —— 是换了篇，还是漏了标题，只有正文说得清。
                        self._add(
                            "numbering_restart", "warn",
                            f"{numbering_suffix(tier, pn)} → {numbering_suffix(tier, n)}："
                            f"序号重新起算",
                            f"{prev['node']['title']} ▸ {r['title']}",
                            scope=r["nid"], nids=[prev["node"]["nid"], r["nid"]],
                            keys=[prev["node"]["key"], r["key"]],
                            evidence={"tier": tier, "prev_n": pn, "n": n,
                                      "parent": pid},
                            probe={
                                "kind": "restart_between",
                                "gid_range": [prev["node"]["gid"], r["gid"]],
                                "after_nid": prev["node"]["nid"],
                                "before_nid": r["nid"],
                                "hint": "先看这两条之间是不是换了一篇文章／一节（译文集"
                                        "里每篇自带编号，重新起算属正常）。若中间没有"
                                        "分篇标志，则可能漏了标题，或 OCR 把编号认错"
                                        "（本书实测有「3.」被认成「8.」，3 与 8 混）。",
                            })
                last[tier] = {"n": n, "node": r}

    # ---- 页码

    def _check_pages(self) -> None:
        kids = self._kids()
        for pid, ks in kids.items():
            prev = None
            for r in ks:
                if prev is not None and r["pnum"] is not None and prev["pnum"] is not None:
                    # 这两类都属于「页码类症状」。若其中一端落在**单页离群观测**上，
                    # 根因已经被 page_obs_outlier 单独点出来了，再逐条报一遍只会
                    # 把报告灌满 —— 同一件事说 30 遍，读者会以为有 30 个错。
                    hit_outlier = (self._is_outlier_page(r["shard"], r["page_idx"])
                                   or self._is_outlier_page(prev["shard"], prev["page_idx"]))
                    if r["zone"] != prev["zone"]:
                        # 相邻两条跨了编号区间。多半不是书的错，而是校准把
                        # 「前置/正文」的分界划错了，或者人工锚点把 offset 顶偏了。
                        if not hit_outlier:
                            self._add("page_zone_crossing", "warn",
                                      "相邻条目的页码跨了前置/正文两套编号",
                                      f"{prev['loc']} ▸ {r['loc']}：{r['title']}",
                                      scope=r["nid"], nids=[prev["nid"], r["nid"]],
                                      keys=[prev["key"], r["key"]],
                                      evidence={"prev_loc": prev["loc"], "loc": r["loc"],
                                                "prev_page_idx": prev["page_idx"],
                                                "page_idx": r["page_idx"], "parent": pid})
                    elif r["pnum"] < prev["pnum"] and not hit_outlier:
                        self._add("page_decreasing", "high", "页码倒退",
                                  f"{prev['loc']} ▸ {r['loc']}："
                                  f"{prev['title']} ▸ {r['title']}",
                                  scope=r["nid"], nids=[prev["nid"], r["nid"]],
                                  keys=[prev["key"], r["key"]],
                                  evidence={"prev_loc": prev["loc"], "loc": r["loc"],
                                            "prev_page_idx": prev["page_idx"],
                                            "page_idx": r["page_idx"],
                                            "delta": r["pnum"] - prev["pnum"]})
                if r["pnum"] is not None:
                    prev = r

        for r in self.flat:
            p = self.by_nid.get(r["parent"]) if r["parent"] else None
            if (p and p["pnum"] is not None and r["pnum"] is not None
                    and r["zone"] == p["zone"] and r["pnum"] < p["pnum"]):
                self._add("page_parent_after_child", "warn", "父节点页码比子节点靠后",
                          f"父 {p['nid']} {p['loc']} ▸ 子 {r['nid']} {r['loc']}",
                          scope=r["nid"], nids=[p["nid"], r["nid"]],
                          keys=[p["key"], r["key"]],
                          evidence={"parent_loc": p["loc"], "loc": r["loc"]})

        # L1/L2 落在前置编号区间：序/前言确实在那里，但如果**章级**标题也在里面，
        # 基本可以断定是前置/正文分界或人工锚点偏了（本书实测就是偏 2 页）。
        bad = [r for r in self.flat if r["level"] <= 2 and r["zone"] == 1]
        for r in bad:
            self._add("top_node_in_front_zone", "info",
                      "L1/L2 落在前置编号区间",
                      f"定位符 {r['loc']}｜{r['marker']}｜{r['title']}",
                      scope=r["nid"], nids=[r["nid"]], keys=[r["key"]],
                      evidence={"loc": r["loc"], "marker": r["marker"]})

    # ---- 印刷目录

    def _check_toc(self) -> None:
        toc = self.oc.get("printed_toc") or {}
        cc = self.oc.get("toc_crosscheck") or {}
        if not toc.get("found"):
            self._add("toc_unavailable", "warn", "没在正文里定位到书内印刷目录",
                      cc.get("note", ""),
                      evidence={"toc_region": self.oc.get("toc_region", {})})
            return
        entries = [e for e in toc.get("entries", [])
                   if e.get("kind") in ("chapter", "part")]

        # 目录是按页序排的，页号应当单调不减。某条明显小于前一条，说明**那一行的
        # 页码被 OCR 认错了**（丁华东「224」读成「24」，夹在 216 与 231 之间）。
        # 拿这种页号去比树里的页锚，只会造一条假红灯 —— 先在本条上盖个戳，
        # 后面按「页码不可信」降档处理。
        prev_p = 0
        for e in entries:
            p = e.get("printed")
            if not p:
                continue
            if int(p) < prev_p - 3:
                e["_ocr_suspect"] = True
            else:
                prev_p = int(p)

        if not entries:
            self._add("toc_unavailable", "warn", "印刷目录在，但一条可用条目都没抽出来",
                      "抽取规则只认「标题+页码」的行，点线/OCR 噪声多时会全灭",
                      evidence={"raw_entries": len(toc.get("entries", []))})
            return

        # 先报抽取质量：条目里还挂着点线页码，说明是抽取没洗，不是书里这么写的。
        noisy = [e for e in entries if toc_entry_is_noisy(e.get("title", ""))]
        for e in noisy:
            self._add("toc_entry_noisy", "info", "目录条目残留点线/页码",
                      str(e.get("title"))[:80],
                      evidence={"raw": e.get("title"), "printed": e.get("printed")})

        # 用**更严的清洗**重做一遍匹配。outline 那套归一化不剥尾部的点线页码，
        # 于是 OCR 质量差一点的目录会整批对不上，coverage 看着像 0.17 的烂目录，
        # 其实目录本身是好的。这里把两套结果都报出来，别让人误判是目录建错了。
        used: set[int] = set()
        matched: list[tuple[dict, dict]] = []
        for r in self.flat:
            if not r["title"]:
                continue
            key = norm_title(r["title"])
            if len(key) < 2:
                continue
            for i, e in enumerate(entries):
                if i in used:
                    continue
                ek = norm_title(e.get("title", ""))
                if not ek:
                    continue
                # 短标题（「后记」「结束语」「绪论」，2–3 字）只认完全相等：放宽成包含
                # 匹配会让「附录」误配一堆条目。但门槛**不能是 4** —— 那会让树里明明
                # 有的短标题一律判成「树里没有」，报出一串假漏条（丁华东的「后记」、
                # 魂系的「结束语」都栽在这上面）。
                hit = ((key == ek) if min(len(key), len(ek)) < 4
                       else (key in ek or ek in key))
                if hit:
                    used.add(i)
                    matched.append((r, e))
                    break

        by_zone: dict[int, list[int]] = {}
        for r, e in matched:
            pw, pnum = parse_locator(r["loc"])
            if pw != 0 or pnum is None or not e.get("printed"):
                continue
            delta = pnum - int(e["printed"])
            by_zone.setdefault(delta, []).append(r["nid"])
            if delta == 0:
                continue
            # 「目录页号 ≠ 树里页锚」有几种性质完全不同的原因，别一律红：
            #   · 目录那一行的页号被 OCR 认错（「224」读成「24」）→ 拿它比必假红；
            #   · 小差值（≤2）→ 目录标的多半是该章**正文起始页**，标题却印在前一页
            #     的页眉下，是排版常态；
            #   · 中等差值（3–20）→ 单条待查（skill 的口径也是「先看是不是系统性
            #     偏移的余波，确属单条再单查」）；
            #   · 大差值（>20）→ 目录页号或定位确实出了错。
            if e.get("_ocr_suspect"):
                sev, note = "warn", "；该条页号违反目录自身的递增序，疑为 OCR 误读"
            elif abs(delta) <= 2:
                sev, note = "info", "；小差值，多半是标题页与该章正文起始页的排版差"
            elif abs(delta) <= 20:
                sev, note = "warn", "；单条待查（先排除系统性偏移）"
            else:
                sev, note = "high", "；差值过大，多半是目录页号或定位有误"
            self._add("toc_page_mismatch", sev,
                      f"页序差 {delta:+d} 页（目录 {e['printed']} ／ 树里 {pnum}）",
                      f"{r['nid']} {r['title'][:50]}{note}",
                      scope=r["nid"], nids=[r["nid"]], keys=[r["key"]],
                      evidence={"toc_title": e.get("title"),
                                "toc_printed": e["printed"], "printed": pnum,
                                "loc": r["loc"], "ocr_suspect": bool(e.get("_ocr_suspect"))})

        # 系统性偏移才是重点：一条差 2 页是那条有问题，二十条齐刷刷差 2 页
        # 就是**校准的 offset 偏了**，改一处就够。分开说，别让人去改二十处。
        dom = [(d, v) for d, v in sorted(by_zone.items(), key=lambda x: -len(x[1]))
               if d != 0 and len(v) >= 3]
        if dom:
            d, v = dom[0]
            self._add("toc_page_offset_shift", "high",
                      f"整批章节齐刷刷偏 {d:+d} 页（{len(v)} 条）",
                      "同一区间内多条同时偏同一个值 —— 是**校准 offset 偏了**，"
                      "不是这些标题各自定位错了。优先查人工锚点与前置/正文分界。",
                      evidence={"delta": d, "count": len(v), "nids": v[:20]})

        miss = [e for i, e in enumerate(entries) if i not in used]
        for e in miss:
            pw, pnum = parse_locator(None)
            probe = None
            printed = e.get("printed")
            if printed and self.calib is not None:
                try:
                    phys = self.calib.physical_of(int(printed))
                except Exception:                                 # noqa: BLE001
                    phys = None
                if phys:
                    shard, pidx = phys
                    probe = {"kind": "search_page",
                             "expect": [norm_title(e.get("title", "")) or e.get("title")],
                             "physical": {"shard": shard, "page_idx": pidx},
                             "window_pages": 3, "printed": int(printed),
                             "hint": "目录说它在纸书这一页；到该物理页前后几页的正文块里"
                                     "搜标题原文（点线/空格/全半角都可能被 OCR 改动）"}
                else:
                    probe = {"kind": "search_anywhere",
                             "expect": [e.get("title")],
                             "printed": int(printed),
                             "hint": "目录给了页码，但该页码映射不到任何物理页 —— "
                                     "先看校准区间表是否覆盖这一页"}
            et = str(e.get("title") or "")
            tagged = bool(numbering(et)) or bool(
                re.match(r"^第\s*[一二三四五六七八九十百零\d]{1,3}\s*[部分篇编]", et))
            if not printed or not tagged:
                # 目录行被 OCR 折成两截时，第二截既不带编号、又常常捞不到页码 ——
                # 「树里没有」对它们不成立：它根本不是一条能独立定位的条目。
                # 报 high 只会把真漏条淹掉（魂系目录里「发展中遭受冲击，文件生命周期
                # 理论产生与盛行」就是「第二部分…」那条的下一行）。
                self._add("toc_entry_fragment", "warn",
                          "目录条目自身残缺（无编号或无页码），无法独立定位",
                          et[:60],
                          evidence={"toc_entry": e}, probe=probe)
            else:
                self._add("toc_entry_undetected", "high",
                          f"书内目录列了、树里没有：{et[:44]}",
                          f"kind={e.get('kind')} printed={e.get('printed')}",
                          evidence={"toc_entry": e}, probe=probe)

        self.toc_stat = {
            "entries": len(entries), "matched": len(matched),
            "undetected": len(miss), "noisy": len(noisy),
            "audit_coverage": round(len(matched) / len(entries), 3) if entries else 0.0,
            "outline_reported_coverage": cc.get("toc_coverage"),
            "delta_histogram": {str(d): len(v) for d, v in sorted(by_zone.items())},
        }

    # ---- 被丢弃的候选

    def _check_dropped(self) -> None:
        """复核「丢弃表」。

        标题被规则扔掉是常态，但规则扔错的代价很大：那条标题从此既不在树里、
        也没人知道它存在（导出 md 里它就是一段普通正文）。所以凡是被丢弃的
        候选，只要**文本自带编号**（第一X章 / 一、 / （一） / 1.），就值得设一道
        闸门 —— 自带编号的短行极少是正文，误杀的嫌疑最大。

        比自带编号更硬的证据是**印刷目录背书**：书内目录里明明列了这一条，而它
        被我们的规则扔了。这种不是「可疑」，是「确凿漏了一条」——升到必处理。
        实测本书就是这样漏掉一个带副题（——是否存在一个"…"？）的章标题：
        原标题末尾有问号，被「带句末标点」这条规则判成不像标题；而目录里只有
        去掉副题的短名，于是树里最后长出来的是**目录的简写**，不是书上的原文。
        """
        toc_titles = []
        for e in (self.oc.get("printed_toc") or {}).get("entries", []):
            k = norm_title(e.get("title", ""))
            if len(k) >= 4:
                toc_titles.append((k, e))

        # 已经被人工补成标题的丢弃项，不再是漏项。少了这一步，校核会一直红着
        # 催一条**已经修好**的条目 —— 人补了断点、重跑一遍，红还在，于是没人再
        # 信这个闸门。键就是 `add:<gid>`，gid 直接对得上。
        added_gids = _manual_added_gids(self.oc)

        for d in self.oc.get("dropped_headings") or []:
            if str(d.get("gid")) in added_gids:
                continue
            text = str(d.get("text") or "")
            head = text.split("\n")[0]
            nk = norm_title(text)
            reason = str(d.get("reason") or "")
            backed = next((e for k, e in toc_titles
                           if len(nk) >= 4 and (k in nk or nk in k)), None)
            # 「目录背书」不等于「这是标题」：章首导读段常把章标题原样复述进去
            # （『第一章"…"部分，主要对…』），一匹配就报「真漏了」是误报。
            # 标题有长度上限；另外目录页自己的行被 R1 丢弃属于**正确丢弃**。
            if (backed and len(text) <= 60 and "目录页" not in reason):
                self._add("dropped_toc_backed", "high",
                          "书内目录列了、却被规则丢弃（标题真的漏了）",
                          f"[{reason}] {head[:62]}",
                          evidence={"shard": d.get("shard"),
                                    "page_idx": d.get("page_idx"),
                                    "reason": reason, "text": text,
                                    "toc_title": backed.get("title"),
                                    "toc_printed": backed.get("printed")},
                          probe={"kind": "dropped_candidate",
                                 "physical": {"shard": d.get("shard"),
                                              "page_idx": d.get("page_idx")},
                                 "expect": [head[:60] or str(backed.get("title"))],
                                 "printed": backed.get("printed"),
                                 "hint": "到该物理页的正文块里取**原文**当标题"
                                         "（不要用目录的简写顶替，目录常常省掉副题）；"
                                         "确认后走「新增标题断点」，不要改正文"})
            elif numbering(head) and len(text) <= 60:
                self._add("dropped_numbered_heading", "warn",
                          "被丢弃的候选自带编号，疑似真标题",
                          f"[{reason}] {text[:62]}",
                          evidence={"shard": d.get("shard"),
                                    "page_idx": d.get("page_idx"),
                                    "reason": reason, "text": text},
                          probe={"kind": "dropped_candidate",
                                 "physical": {"shard": d.get("shard"),
                                              "page_idx": d.get("page_idx")},
                                 "expect": [head[:40]],
                                 "hint": "到该物理页的正文块里核对：若这行确实独立成段、"
                                         "且下文接着讲它，就是被误杀的标题"})
            elif backed:
                self._add("dropped_toc_echo", "info",
                          "被丢弃的是复述了目录标题的正文段（不是标题）",
                          f"[{reason}] {text[:80]}",
                          evidence={"shard": d.get("shard"),
                                    "page_idx": d.get("page_idx"),
                                    "reason": reason, "text": text,
                                    "matched_toc": backed.get("title")})
            elif reason.startswith("不像标题"):
                self._add("dropped_long_candidate", "info",
                          "被丢弃：不像标题（过长或带句末标点）",
                          head[:80],
                          evidence={"shard": d.get("shard"),
                                    "page_idx": d.get("page_idx"),
                                    "text": text})

    # ---- 校准区间

    def _check_calibration(self) -> None:
        if self.calib is None:
            self._add("calib_missing", "high", "没有页码校准结果",
                      "先跑 pagecal，否则页码类检查全部无从谈起")
            return
        c = self.calib
        segs = list(c.segments)

        # 先把根因摆出来（每片一条），后面的症状就不必逐条重复了。
        for shard, items in sorted(self.outliers.items()):
            pages = sorted({o["page_idx_start"] for o in items})
            dom = items[0]["shard_offset"]
            detail = "；".join(
                f"物理 {o['page_idx_start']}：观测 {o['observed']}"
                f"（两侧序列按 offset {dom:+d} 应为 {o['expected']}）"
                for o in items)
            self._add("page_obs_outlier", "high",
                      f"{len(items)} 处单页页码与整段序列冲突（{shard}）",
                      f"本片主 offset {dom:+d}；{detail}",
                      scope=shard,
                      evidence={"shard": shard, "dom_offset": dom,
                                "pages": pages, "items": items},
                      probe={"kind": "page_obs",
                             "physical": {"shard": shard, "page_idx": pages[0]},
                             "expect": [str(o["expected"]) for o in items],
                             "observed": [o["observed"] for o in items],
                             "hint": "这些页上的页码块只有一个数字，且与前后整段序列只差"
                                     "一两位（315 认成 815 / 238 认成 288）。按现映射会读成"
                                     "「314 → 815 → 316」这种真书不可能出现的序列。先到该"
                                     "物理页核对原文；确认是误读后，用**人工锚点**在该页钉死"
                                     "对应关系，别去改区间表。"})

        for s in segs:
            span = s.page_idx_end - s.page_idx_start + 1
            # 单页孤段、且 offset 与邻段不同 —— 典型是那一页上出现了别的数字被当成
            # 页码观测（书里印着「304」的正文页会被读成页码）。它会让区间表看起来
            # 页码跳来跳去，实际只是观测噪声。
            # 已经判定为「夹在连续序列中的离群页」的，根因已由 page_obs_outlier 报出，
            # 这里不再重复计数。
            if span <= 2 and not self._seg_is_outlier(s.to_dict()):
                self._add("calib_short_segment", "warn",
                          "极短区间（1–2 页且独立 offset）",
                          f"{s.shard} 物理 {s.page_idx_start}-{s.page_idx_end} "
                          f"→ 印刷 {s.printed_start}-{s.printed_end}（{s.offset:+d}）",
                          evidence=s.to_dict())
        # 同一分片内印刷区间重叠 = 同一页号被映射到两处，必有一处错。
        # **但只在同一套编号体系内比**：前置页（front）与正文页（body）各有自己的
        # 页序（前置印 1、2、3…，正文也从 1 开始），跨着比大小本来就无意义 ——
        # 与 page_zone_crossing 是同一个道理。前置区内部的轻微重叠降为提示：那几页的
        # 数字大多是从版权页/目录页上读来的杂数，且 physical_of() 优先取 body 段，
        # 不影响正文定位；真要让页锚出错，得是 body 段撞车（或 front 标签撞车，
        # 那有 page_locator_collision 兜着）。
        for shard in {s.shard for s in segs}:
            ss = sorted([s for s in segs if s.shard == shard],
                        key=lambda x: x.page_idx_start)
            for a, b in zip(ss, ss[1:]):
                if a.kind != b.kind:
                    continue                # 前置/正文两套页序，数字重叠是允许的
                if b.printed_start > a.printed_end:
                    continue
                if self._seg_is_outlier(a.to_dict()) or self._seg_is_outlier(b.to_dict()):
                    continue        # 离群页造成的重叠，根因已单列
                self._add("calib_segment_overlap",
                          "warn" if a.kind == "front" else "high",
                          "同一分片内印刷页码范围重叠",
                          f"{shard}[{a.kind}]：{a.page_idx_start}-{a.page_idx_end}"
                          f"→{a.printed_start}-{a.printed_end} 与 "
                          f"{b.page_idx_start}-{b.page_idx_end}"
                          f"→{b.printed_start}-{b.printed_end}",
                          evidence={"a": a.to_dict(), "b": b.to_dict()})
        for chk in (c.continuity or {}).get("checks", []):
            if not chk.get("ok") and not self._chk_hits_outlier(chk):
                self._add("calib_continuity", "high", "跨分片页序接续不上",
                          f"{chk.get('from')} → {chk.get('to')}：期望 {chk.get('expected')}，"
                          f"实际 {chk.get('next_printed')}（{chk.get('note', '')}）",
                          evidence=chk)
        for g in c.gaps or []:
            self._add("calib_declared_gap", "info", "已声明的页码断点（不外推）",
                      f"{g.get('shard')} 物理页 {g.get('after_page_idx')}"
                      f"→{g.get('before_page_idx')} 之间漏 {g.get('n_unmapped_pages')} 页",
                      evidence=g)

        # 定位符撞车：两个物理页拿到同一个页锚 —— 导出后 `<!-- p=N -->` 是歧义的，
        # 读者按锚回查会翻到另一页。这比「印刷区间数字重叠」更贴近实际危害：
        # 前置页与正文页各有自己的编号序列，数字重叠是**允许**的（前缀不同），
        # 但**标签**撞车不允许。
        for shard, n in sorted(c.shard_pages.items()):
            seen: dict[str, list[int]] = {}
            for p in range(0, int(n or 0)):
                loc = c.locator(shard, p)
                if loc:
                    seen.setdefault(loc, []).append(p)
            for loc, pages in sorted(seen.items()):
                if len(pages) < 2:
                    continue
                zone, _ = parse_locator(loc)
                self._add("page_locator_collision", "warn" if zone == 1 else "high",
                          f"{len(pages)} 个物理页共用定位符 `{loc}`",
                          f"{shard} 物理页 {pages}",
                          scope=shard,
                          evidence={"shard": shard, "locator": loc, "pages": pages,
                                    "zone": zone})

    def _chk_hits_outlier(self, chk: dict) -> bool:
        """接续检查的 from/to 写成 `P2:126`（分片:物理页）——只要一端是离群页就归到根因。"""
        for k in ("from", "to"):
            shard, _, page = str(chk.get(k, "")).partition(":")
            if page.isdigit() and self._is_outlier_page(shard, int(page)):
                return True
        return False

    # ---- 出报告

    def _report(self) -> dict:
        order = {"high": 0, "warn": 1, "info": 2}
        self.findings.sort(key=lambda f: (order.get(f["severity"], 9), f["id"]))
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f["check"]] = counts.get(f["check"], 0) + 1
        sev = {"high": 0, "warn": 0, "info": 0}
        for f in self.findings:
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
        calib_ctx = None
        if self.calib is not None:
            calib_ctx = {
                "verdict": self.calib.verdict,
                "locator_type": self.calib.locator_type,
                "reason": self.calib.reason,
                "shards": self.calib.shards,
                "shard_pages": self.calib.shard_pages,
                "segments": [s.to_dict() for s in self.calib.segments],
                "continuity": self.calib.continuity,
            }
        levels: dict[str, int] = {}
        over = 0
        for r in self.flat:
            levels[str(r["level"])] = levels.get(str(r["level"]), 0) + 1
            if r["level"] > self.depth:
                over += 1
        rep = {
            "source_id": self.source_id,
            "generated_at": now_iso(),
            "focus_depth": self.depth,
            "outline_node_total": self.oc.get("node_total", len(self.flat)),
            "manual": read_json(self.wd / "manual_edits.json", {}) or {},
            "level_histogram": levels,
            "nodes_below_focus_depth": over,
            "verdict": ("fail" if sev["high"] else ("warn" if sev["warn"] else "pass")),
            "severity": sev,
            "counts": counts,
            "calibration": calib_ctx,
            "toc": getattr(self, "toc_stat", {"entries": 0}),
            "findings": self.findings,
            "outline_flat": [
                {"nid": r["nid"], "level": r["level"], "title": r["title"],
                 "marker": r["marker"], "key": r["key"], "gid": r["gid"],
                 "loc": r["loc"], "chars": r["chars"], "blocks": r["blocks"],
                 "flags": r["flags"]}
                for r in self.flat if r["level"] <= max(self.depth, 3)],
        }
        write_json(self.wd / "目录校核.json", rep)
        write_text(self.wd / "目录校核.md", self._markdown(rep))
        return rep

    def _markdown(self, rep: dict) -> str:
        icon = {"high": "🔴", "warn": "🟡", "info": "⚪"}
        L = [f"# 目录校核：{rep['source_id']}", "",
             f"- 生成：{rep['generated_at']}",
             f"- 节点 {rep['outline_node_total']}｜关注深度 L{rep['focus_depth']}"
             f"（其下另有 {rep['nodes_below_focus_depth']} 个节点）",
             f"- 判定：**{rep['verdict']}** ｜ 🔴 {rep['severity']['high']} "
             f"🟡 {rep['severity']['warn']} ⚪ {rep['severity']['info']}",
             "",
             "> 本报告只陈述**事实**，不替人决定怎么改。🔴 必须处理；🟡 建议看一眼；",
             "> ⚪ 只是把现状摊开。凡涉及「疑似缺标题」的条目都带 `probe`（搜索窗口），",
             "> 供 Agent 去正文里取证，而不是凭标题猜。", ""]
        cal = rep.get("calibration")
        if cal:
            L += ["## 校准现状", "",
                  f"- 判定 `{cal['verdict']}`／`{cal['locator_type']}`：{cal['reason']}", ""]
        toc = rep.get("toc") or {}
        if toc.get("entries"):
            L.append(f"- 书内目录 {toc['entries']} 条：校核匹配 {toc.get('matched')}、"
                     f"树里没有 {toc.get('undetected')}、条目含点线噪声 {toc.get('noisy')}"
                     f"（覆盖率 {toc.get('audit_coverage')}；"
                     f"建树时自报 {toc.get('outline_reported_coverage')}）")
            hist = {k: v for k, v in (toc.get("delta_histogram") or {}).items() if k != "0"}
            if hist:
                L.append(f"- 页序偏差分布（目录 vs 树）：{hist}")
        if cal:
            L += ["", "| 分片 | 类型 | 物理页 | 印刷页 | offset | 观测 | 置信 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
            for s in cal["segments"]:
                L.append(f"| {s['shard']} | {s.get('kind')} | "
                         f"{s['page_idx_start']}–{s['page_idx_end']} | "
                         f"{s['printed_start']}–{s['printed_end']} | {s['offset']:+d} | "
                         f"{s.get('n_obs')} | {s.get('confidence')} |")

        groups = [
            ("🔴 必须处理", [f for f in rep["findings"] if f["severity"] == "high"]),
            ("🟡 建议核对", [f for f in rep["findings"] if f["severity"] == "warn"]),
            ("⚪ 现状记录", [f for f in rep["findings"] if f["severity"] == "info"]),
        ]
        for head, items in groups:
            L += ["", f"## {head}（{len(items)}）"]
            if not items:
                L.append("- ✅ 无")
                continue
            for f in items:
                L.append(f"- **{f['id']}** `{f['check']}`"
                         + (f" `{f['scope']}`" if f["scope"] else "") + f"　{f['title']}")
                if f["detail"]:
                    L.append(f"    - {f['detail']}")
                if f.get("probe"):
                    p = f["probe"]
                    if p.get("gid_range"):
                        L.append(f"    - 搜索窗口：gid {p['gid_range'][0]}–{p['gid_range'][1]}"
                                 f"（{p.get('after_nid')} → {p.get('before_nid')}）")
                    if p.get("physical"):
                        L.append(f"    - 搜索窗口：{p['physical'].get('shard')} "
                                 f"物理页 {p['physical'].get('page_idx')}"
                                 + (f"±{p.get('window_pages')}" if p.get("window_pages") else ""))
                    if p.get("expect"):
                        L.append(f"    - 期望补出的标题：{'／'.join(map(str, p['expect']))}")
                    if p.get("hint"):
                        L.append(f"    - {p['hint']}")

        L += ["", f"## 目录全景（L1–L{rep['focus_depth']}）", ""]
        for r in rep["outline_flat"]:
            if r["level"] <= rep["focus_depth"]:
                L.append(f"- {'　'*(r['level']-1)}`{r['nid']}` {r['loc'] or '—'}"
                         f" {r['title'][:60]}")
        L.append("")
        return "\n".join(L)


def run(source_id: str, wd: Path, depth: int = 3) -> dict:
    return Audit(source_id, Path(wd), depth).run()


def audit_report_md(rep: dict) -> str:
    """给 CLI 用的简短摘要（完整报告已落盘）。"""
    L = [f"目录校核：{rep['source_id']}",
         f"  节点 {rep['outline_node_total']}｜判定 {rep['verdict']}"
         f"｜🔴 {rep['severity']['high']} 🟡 {rep['severity']['warn']} "
         f"⚪ {rep['severity']['info']}"]
    for check, n in sorted(rep["counts"].items(), key=lambda x: -x[1]):
        L.append(f"  {check:<28} {n}")
    return "\n".join(L)
