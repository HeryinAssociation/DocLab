# -*- coding: utf-8 -*-
"""基线书取证：①"中国艺术学科…"节点在不在树里 ②§4.x 偏移分布。"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
SID = "中国数字人文发展报告"
WD = os.path.join(BASE, "_work", SID)

outline = json.load(open(os.path.join(WD, "outline.json"), encoding="utf-8"))
rep = json.load(open(os.path.join(WD, "目录校核.json"), encoding="utf-8"))

flat = []
def walk(nodes, depth):
    for n in nodes:
        n["_d"] = depth
        flat.append(n)
        walk(n.get("children") or [], depth + 1)
walk(outline.get("tree") or [], 1)

print("=== 含「中国艺术学科」/「数字艺术史」的节点 ===")
for n in flat:
    t = n.get("title") or ""
    if "艺术学科" in t or "数字艺术史" in t or "艺术" in t and n.get("_d") <= 3:
        print(f"d{n['_d']} nid={n.get('nid')} L{n.get('level')} key={n.get('key')} gid={n.get('gid')} page={n.get('page_idx')} loc={n.get('loc')} flags={n.get('flags')} title={t!r}")

print("\n=== 顶层 L1/L2 ===")
for n in flat:
    if n.get("level") in (1, 2):
        print(f"L{n.get('level')} nid={n.get('nid')} key={n.get('key')} gid={n.get('gid')} shard={n.get('shard')} page={n.get('page_idx')} page_end={n.get('page_idx_end')} title={n.get('title')!r} flags={n.get('flags')}")

print("\n=== printed_toc entries ===")
for e in (outline.get("printed_toc") or {}).get("entries", []):
    print(json.dumps(e, ensure_ascii=False))

print("\n=== audit delta histogram ===")
print(json.dumps(rep.get("toc_page_check") or rep.get("toc"), ensure_ascii=False))
for f in rep["findings"]:
    if f["check"] in ("toc_page_mismatch", "toc_page_offset_shift"):
        print(f["check"], f["severity"], "|", f["detail"], "|", json.dumps(f.get("evidence"), ensure_ascii=False))
