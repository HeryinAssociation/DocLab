#!/usr/bin/env python
"""原型：目录校核的**确定性**部分能查出什么。

只读 outline.json / page_calibration.json / project.json，不改任何产物。
目的：在动手写 core/audit.py 之前，先看真实数据上哪些检查有效、哪些是噪声。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pagecal import load_calibration        # noqa: E402
from core.util import read_json                  # noqa: E402

ZH = "一二三四五六七八九十百零1234567890"
PART_RE = re.compile(r"^第\s*([" + ZH + r"]{1,3})\s*(?:部分|编|篇)")
CHAP_RE = re.compile(r"^第\s*([" + ZH + r"]{1,3})\s*(?:章|回|讲)")
SEC_RE = re.compile(r"^第\s*([" + ZH + r"]{1,3})\s*(?:节|小节)")
ZH_DUN_RE = re.compile(r"^([" + ZH + r"]{1,3})\s*、")
PAREN_ZH_RE = re.compile(r"^[（(]\s*([" + ZH + r"]{1,3})\s*[）)]")
ARABIC_RE = re.compile(r"^(\d{1,2})\s*[.．、]")
PAREN_ARABIC_RE = re.compile(r"^[（(]\s*(\d{1,2})\s*[）)]")


def zh2int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    m = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
         "六": 6, "七": 7, "八": 8, "九": 9}
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + m.get(s[1], 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return m.get(a, 0) * 10 + (m.get(b, 0) if b else 0)
    if len(s) == 1:
        return m.get(s)
    return None


def seq_of(title: str, marker: str) -> tuple[str, int] | None:
    """从标题文本里取出「编号档位 + 序号」。取不到返回 None。"""
    t = title.strip()
    for tag, rx in (("part", PART_RE), ("chapter", CHAP_RE), ("sec", SEC_RE)):
        m = rx.match(t)
        if m:
            n = zh2int(m.group(1))
            return (tag, n) if n is not None else None
    m = ZH_DUN_RE.match(t)
    if m:
        n = zh2int(m.group(1))
        if n is not None:
            return ("subsec", n)
    m = PAREN_ZH_RE.match(t)
    if m:
        n = zh2int(m.group(1))
        if n is not None:
            return ("subsubsec", n)
    m = PAREN_ARABIC_RE.match(t)
    if m:
        return ("subitem", int(m.group(1)))
    m = ARABIC_RE.match(t)
    if m:
        return ("item", int(m.group(1)))
    return None


def flatten(tree, parent=None, depth=1, out=None, path=""):
    if out is None:
        out = []
    for n in tree:
        rec = {
            "nid": n.get("nid"), "level": n.get("level"), "title": n.get("title", ""),
            "marker": n.get("marker", ""), "gid": n.get("gid_start"),
            "key": n.get("key") or str(n.get("gid_start")),
            "shard": n.get("shard"), "page_idx": n.get("page_idx"),
            "chars": n.get("n_chars", 0), "blocks": n.get("n_blocks", 0),
            "flags": n.get("flags", []), "depth": depth, "parent": parent,
            "path": (path + "/" + n.get("title", "")[:12]) if path else n.get("title", "")[:12],
        }
        out.append(rec)
        flatten(n.get("children") or [], rec["nid"], depth + 1, out, rec["path"])
    return out


def audit(sid_dir: Path) -> None:
    oc = read_json(sid_dir / "outline.json", {}) or {}
    calib = load_calibration(sid_dir)
    flat = flatten(oc.get("tree") or [])
    print("=" * 78)
    print(f"# {sid_dir.name}   节点 {len(flat)}")
    print("  outline.json 顶层键：", sorted(oc.keys()))
    print(f"  校准：{calib.verdict}/{calib.locator_type}")

    # ---- printed page per node
    for r in flat:
        r["printed"] = None
        if r["shard"] and r["page_idx"] is not None:
            try:
                r["printed"] = calib.locator(r["shard"], r["page_idx"])
            except Exception:                                    # noqa: BLE001
                pass

    # ---- 1. 层级：跳级
    by_nid = {r["nid"]: r for r in flat}
    print("\n## 1 层级跳变（父→子 跨了不只一级）")
    bad = 0
    for r in flat:
        p = by_nid.get(r["parent"])
        if p and r["level"] - p["level"] > 1:
            bad += 1
            print(f"   {r['nid']} L{p['level']}→L{r['level']}  {p['title'][:24]} > {r['title'][:34]}")
    print(f"   共 {bad}")

    # ---- 2. 同父内部：编号连续性 + 顺序倒挂
    print("\n## 2 同父内部编号连续性 / 同一档位序号跳号")
    kids: dict = {}
    for r in flat:
        kids.setdefault(r["parent"], []).append(r)
    issues = 0
    for pid, ks in kids.items():
        last: dict[str, int] = {}
        for r in ks:
            got = seq_of(r["title"], r["marker"])
            if not got:
                continue
            tag, n = got
            if tag in last:
                if n != last[tag] + 1:
                    issues += 1
                    print(f"   [{pid}] {tag} 期望 {last[tag]+1} 实际 {n}  {r['title'][:50]}")
                elif n <= last[tag]:
                    issues += 1
            last[tag] = n
    print(f"   共 {issues}")

    # ---- 3. 页码单调性（同父序号递增但页码不递增）
    print("\n## 3 页码倒挂（同父下后一条的印刷页 <= 前一条）")
    bad = 0
    for pid, ks in kids.items():
        prev = None
        for r in ks:
            if prev is not None and r["printed"] and prev["printed"]:
                if r["printed"] <= prev["printed"]:
                    bad += 1
                    print(f"   {prev['nid']} p{prev['printed']} → {r['nid']} p{r['printed']}"
                          f"  {prev['title'][:26]} | {r['title'][:30]}")
            if r["printed"]:
                prev = r
    print(f"   共 {bad}")

    # ---- 4. 父节点页码应 <= 子节点
    print("\n## 4 父子页码倒挂")
    bad = 0
    for r in flat:
        p = by_nid.get(r["parent"])
        if p and p["printed"] and r["printed"] and r["printed"] < p["printed"]:
            bad += 1
            print(f"   父 {p['nid']} p{p['printed']} > 子 {r['nid']} p{r['printed']}  {r['title'][:40]}")
    print(f"   共 {bad}")

    # ---- 5. L1/L2 全景
    print("\n## 5 L1 / L2 全景（人看层级用）")
    for r in flat:
        if r["level"] <= 2:
            print(f"   {'  '*(r['level']-1)}L{r['level']} {r['nid']:<10} p{str(r['printed']):<5}"
                  f" {r['marker']:<10} {r['title'][:56]}")

    # ---- 6. 印刷目录交叉
    cc = oc.get("toc_crosscheck", {})
    print("\n## 6 印刷目录交叉校验")
    print(f"   {json.dumps({k: v for k, v in cc.items() if not isinstance(v, list)}, ensure_ascii=False)}")
    for e in cc.get("toc_entries_not_detected", [])[:20]:
        print(f"   目录有、树里没有：{e}")
    for e in cc.get("headings_not_in_toc", [])[:10]:
        print(f"   树里有、目录没有：{e['nid']} L{e.get('level')} {e['title'][:44]}")
    tr = oc.get("toc_repair", [])
    print(f"   toc_repair {len(tr)} 条：")
    for a in tr[:20]:
        print(f"     {a.get('status')} {str(a.get('title'))[:40]} p{a.get('printed')} {a.get('reason','')}")

    # ---- 7. 丢弃候选（复核误杀）
    dd = oc.get("dropped") or []
    print(f"\n## 7 被丢弃的候选标题（{len(dd)}）— 复核误杀")
    for d in dd:
        print(f"   {d.get('shard')}p{d.get('page_idx')} [{d.get('reason')}] {str(d.get('text'))[:70]}")

    # ---- 8. 字幕区间：L1 覆盖
    print("\n## 8 L1 字数/块数覆盖")
    tot = sum(r["chars"] for r in flat if r["level"] == 1)
    print(f"   L1 Σn_chars={tot:,}  全树根 Σ={sum(r['chars'] for r in flat if r['parent'] in ('', None)):,}")


if __name__ == "__main__":
    which = sys.argv[1:] or ["中国数字人文发展报告"]
    for w in which:
        audit(ROOT / "_work" / w)
