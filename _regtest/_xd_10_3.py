# -*- coding: utf-8 -*-
"""现代档案：10.3 子结构；全树找 gid 2508；9编标题两块在树里的位置。只读。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
wd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

oc = json.loads((wd / "outline.json").read_text(encoding="utf-8"))
flat = []
def walk(ns, d):
    for n in ns:
        flat.append((d, n))
        walk(n.get("children") or [], d + 1)
walk(oc["tree"], 1)

print("=== 全树找 gid_start 2508 / 2362 / 42 ===")
for d, n in flat:
    if n.get("gid_start") in (2508, 2362, 42):
        print(f"  找到 L{d} nid={n.get('nid')} key={n.get('key')} gid={n.get('gid_start')} {n.get('title')!r}")

print("\n=== 10.3「档案与公共关系」子结构 ===")
for d, n in flat:
    if n.get("gid_start") == 2477 and n.get("level") == 2:
        ch = n.get("children") or []
        print(f"  子节点数: {len(ch)}")
        for c in ch:
            print(f"  L{c.get('level')} nid={c.get('nid')} key={c.get('key')} gid={c.get('gid_start')} n_blocks={c.get('n_blocks')} {c.get('title')[:44]!r}")
        break

print("\n=== dropped_headings 全量（不限长度） ===")
for d_ in oc.get("dropped_headings") or []:
    print(f"  gid={d_.get('gid')} p{d_.get('page_idx')} {d_.get('shard')} | reason={d_.get('reason')} | {str(d_.get('text'))[:44]!r}")
