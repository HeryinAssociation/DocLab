#!/usr/bin/env python
"""原型 v2：目录校核的确定性部分 —— 直接产出报告文件（避免 shell 编码问题）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pagecal import load_calibration        # noqa: E402
from core.util import read_json                  # noqa: E402

ZH = "一二三四五六七八九十百零"
PART_RE = re.compile(r"^第\s*([" + ZH + r"\d]{1,3})\s*(?:部分|编|篇)")
CHAP_RE = re.compile(r"^第\s*([" + ZH + r"\d]{1,3})\s*(?:章|回|讲)")
SEC_RE = re.compile(r"^第\s*([" + ZH + r"\d]{1,3})\s*(?:节|小节)")
ZH_DUN_RE = re.compile(r"^([" + ZH + r"]{1,3})\s*、")
PAREN_ZH_RE = re.compile(r"^[（(]\s*([" + ZH + r"]{1,3})\s*[）)]")
ARABIC_RE = re.compile(r"^(\d{1,2})\s*[.．、]")
PAREN_ARABIC_RE = re.compile(r"^[（(]\s*(\d{1,2})\s*[）)]")

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


def seq_of(title: str) -> tuple[str, int] | None:
    t = (title or "").strip()
    for tag, rx in (("part", PART_RE), ("chapter", CHAP_RE), ("sec", SEC_RE)):
        m = rx.match(t)
        if m:
            n = zh2int(m.group(1))
            if n is not None:
                return (tag, n)
    m = ZH_DUN_RE.match(t)
    if m:
        n = zh2int(m.group(1))
        if n is not None:
            return ("dun", n)
    m = PAREN_ZH_RE.match(t)
    if m:
        n = zh2int(m.group(1))
        if n is not None:
            return ("paren_zh", n)
    m = PAREN_ARABIC_RE.match(t)
    if m:
        return ("paren_ar", int(m.group(1)))
    m = ARABIC_RE.match(t)
    if m:
        return ("arabic", int(m.group(1)))
    return None


def parse_loc(loc):
    """'3' → (0,3)；'front-9' → (1,9)；None → (2,None)。kind 用于分组比较。"""
    if not loc:
        return (2, None)
    if isinstance(loc, str) and loc.startswith("front-"):
        return (1, int(loc[6:]))
    try:
        return (0, int(loc))
    except (TypeError, ValueError):
        return (2, None)


def flatten(tree, parent=None, depth=1, out=None):
    if out is None:
        out = []
    for n in tree:
        rec = {"nid": n.get("nid"), "level": n.get("level"), "title": n.get("title", ""),
               "marker": n.get("marker", ""), "gid": n.get("gid_start"),
               "key": n.get("key") or str(n.get("gid_start")),
               "shard": n.get("shard"), "page_idx": n.get("page_idx"),
               "chars": n.get("n_chars", 0), "blocks": n.get("n_blocks", 0),
               "flags": n.get("flags", []), "depth": depth, "parent": parent}
        out.append(rec)
        flatten(n.get("children") or [], rec["nid"], depth + 1, out)
    return out


def one(sid_dir: Path, L: list) -> None:
    oc = read_json(sid_dir / "outline.json", {}) or {}
    calib = load_calibration(sid_dir)
    flat = flatten(oc.get("tree") or [])
    by_nid = {r["nid"]: r for r in flat}
    for r in flat:
        r["loc"] = calib.locator(r["shard"], r["page_idx"]) if r["shard"] else None
        r["pkind"], r["pnum"] = parse_loc(r["loc"])

    L.append(f"\n\n# {sid_dir.name}")
    L.append(f"- 节点 {len(flat)}｜校准 {calib.verdict}/{calib.locator_type}｜"
             f"分片 {calib.shards}")
    L.append(f"- 区间表：" + "；".join(
        f"{s.shard}/{s.kind} 物理{s.page_idx_start}-{s.page_idx_end}→"
        f"印刷{s.printed_start}-{s.printed_end}(off{s.offset:+d})" for s in calib.segments))

    # ---------- A 层级 ----------
    A = []
    for r in flat:
        p = by_nid.get(r["parent"])
        if p and r["level"] - p["level"] > 1:
            A.append(f"{r['nid']} L{p['level']}→L{r['level']} {p['title'][:26]} ▸ {r['title'][:34]}")

    # ---------- B 编号连续性（同父同档） ----------
    kids: dict = {}
    for r in flat:
        kids.setdefault(r["parent"], []).append(r)
    B = []
    for pid, ks in kids.items():
        last: dict[str, int] = {}
        for r in ks:
            got = seq_of(r["title"])
            if not got:
                continue
            tag, n = got
            if tag in last and n != last[tag] + 1:
                B.append(f"[父 {pid}] {tag} {last[tag]}→{n}（期望 {last[tag]+1}） {r['title'][:56]}")
            last[tag] = n

    # ---------- C 页码（只在同一 kind 内比；跨 kind 单列） ----------
    C, Ccross = [], []
    for pid, ks in kids.items():
        prev = None
        for r in ks:
            if prev is not None and r["pnum"] is not None and prev["pnum"] is not None:
                if r["pkind"] != prev["pkind"]:
                    Ccross.append(f"{prev['nid']}({prev['loc']}) → {r['nid']}({r['loc']}) "
                                  f"{r['title'][:34]}")
                elif r["pnum"] <= prev["pnum"]:
                    C.append(f"{prev['nid']} {prev['loc']} → {r['nid']} {r['loc']}  "
                             f"{prev['title'][:22]} ▸ {r['title'][:26]}")
            if r["pnum"] is not None:
                prev = r

    # ---------- D 父子页码倒挂（同 kind） ----------
    D = []
    for r in flat:
        p = by_nid.get(r["parent"])
        if (p and p["pnum"] is not None and r["pnum"] is not None
                and p["pkind"] == r["pkind"] and r["pnum"] < p["pnum"]):
            D.append(f"父 {p['nid']}({p['loc']}) > 子 {r['nid']}({r['loc']}) {r['title'][:40]}")

    # ---------- E 跨区间落点（前置编号/正文编号混用） ----------
    E = [f"{r['nid']} L{r['level']} loc={r['loc']} {r['marker']} {r['title'][:44]}"
         for r in flat if r["level"] <= 2 and r["pkind"] == 1]

    # ---------- F 印刷目录 ----------
    cc = oc.get("toc_crosscheck", {})
    F = [f"覆盖 {cc.get('toc_coverage')} 匹配率 {cc.get('match_rate')} "
         f"目录 {cc.get('toc_entry_count')} 条",
         "印刷目录条目："]
    for e in (oc.get("printed_toc") or {}).get("entries", []):
        F.append(f"  - [{e.get('kind')}] p{e.get('printed')} {str(e.get('title'))[:56]}")
    F.append("目录有、树里没有：")
    for e in cc.get("toc_entries_not_detected", []):
        F.append(f"  - {e}")
    F.append("树里有、目录没有：")
    for e in cc.get("headings_not_in_toc", [])[:14]:
        F.append(f"  - {e.get('nid')} L{e.get('level')} {e['title'][:46]}")
    F.append("toc_repair：")
    for a in oc.get("toc_repair", []):
        F.append(f"  - {a.get('status')} p{a.get('printed')} {str(a.get('title'))[:44]}"
                 f" — {a.get('reason', '')}")

    # ---------- G 丢弃候选 ----------
    dd = oc.get("dropped_headings") or []
    G = [f"{d.get('shard')}p{d.get('page_idx')} [{d.get('reason')}] {str(d.get('text'))[:76]}"
         for d in dd]

    # ---------- H 空/极小节点 ----------
    H = [f"{r['nid']} L{r['level']} chars={r['chars']} blocks={r['blocks']} {r['title'][:44]}"
         for r in flat if r["chars"] == 0 or (r["chars"] < 100 and r["level"] <= 3)]

    def blk(tag, title, items, note=""):
        L.append(f"\n## {tag} {title}　（{len(items)}）{note}")
        L.extend(f"- {x}" for x in items) if items else L.append("- ✅ 无")

    blk("A", "层级跳变（父→子 跨级）", A)
    blk("B", "同父同档编号不连续", B)
    blk("C", "页码非递增（同 kind）", C)
    blk("C2", "相邻条目跨了编号区间（front↔body）", Ccross)
    blk("D", "父子页码倒挂（同 kind）", D)
    blk("E", "L1/L2 落在前置编号区间", E)
    blk("F", "印刷目录交叉", F)
    blk("G", "被丢弃的候选标题（复核误杀）", G)
    blk("H", "空/极小节点", H)

    L.append("\n## I L1/L2 全景")
    for r in flat:
        if r["level"] <= 2:
            L.append(f"- {'  '*(r['level']-1)}L{r['level']} {r['nid']:<9} {str(r['loc']):<9}"
                     f" {r['marker']:<9} {r['title'][:60]}")


if __name__ == "__main__":
    R = []
    which = sys.argv[1:]
    dirs = ([ROOT / "_work" / w for w in which] if which
            else [p for p in (ROOT / "_work").iterdir()
                  if p.is_dir() and (p / "outline.json").exists()])
    for d in dirs:
        try:
            one(d, R)
        except Exception as e:                                    # noqa: BLE001
            R.append(f"\n\n# {d.name}\n- ❌ {type(e).__name__}: {e}")
    out = ROOT / "_regtest" / "_audit_v2.md"
    out.write_text("\n".join(R), encoding="utf-8")
    print("WROTE", out)
