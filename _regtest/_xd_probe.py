# -*- coding: utf-8 -*-
"""现代档案 L2 核对取证：印刷目录条目、丢弃标题、书末内容。只读不写。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
wd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

oc = json.loads((wd / "outline.json").read_text(encoding="utf-8"))

print("=== 印刷目录条目（printed_toc） ===")
pt = (oc.get("printed_toc") or {})
for e in pt.get("entries", []):
    print(f"  p{e.get('page')} | {e.get('title')}")

print("\n=== dropped_headings（被丢弃的疑似标题，章级尺度） ===")
for d in oc.get("dropped_headings") or []:
    t = str(d.get("text") or "")
    if len(t) >= 6:  # 章级标题通常较长
        print(f"  gid={d.get('gid')} p{d.get('page_idx')} {d.get('shard')} | {t[:60]}")

print("\n=== 顶层平铺（核对nid连续性） ===")
flat = []
def walk(ns, depth):
    for n in ns:
        flat.append((depth, n))
        walk(n.get("children") or [], depth + 1)
walk(oc["tree"], 1)
l1 = [n for d, n in flat if n.get("level") == 1]
for n in l1:
    print(f"  L1 {n.get('nid')} {n.get('title')[:30]}  gid={n.get('gid_start')}")
print("node_total:", oc.get("node_total"))

# 书末：最后 L1（第十二编）的最后 L2 的区间之后还有多少块
from core.project import iter_blocks, load_shards  # noqa: E402
shards = load_shards(wd)
blocks = list(iter_blocks(shards))
last = max((n for d, n in flat if n.get("level") == 2), key=lambda n: int(n.get("gid_start") or 0))
gs = int(last.get("gid_start") or 0)
ge = gs + int(last.get("n_blocks") or 0)
print(f"\n最后 L2 节点：{last.get('title')[:30]} gid {gs}..{ge}（总块数 {len(blocks)}）")
print("=== 书末剩余块（gid > 最后节点区间，最多看 60 块） ===")
for b in blocks:
    if b.gid > ge:
        t = (b.text or "").strip().replace("\n", " ")
        if t:
            mark = "[h]" if getattr(b, "is_heading", False) else "   "
            print(f"  gid={b.gid} {b.shard} p{b.page_idx} {mark} {t[:50]}")
