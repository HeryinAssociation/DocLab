# -*- coding: utf-8 -*-
"""现代档案：前言 gid=42 与结束语 gid=449 的上下文——判层级。只读。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
from core.project import iter_blocks, load_shards  # noqa: E402

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
wd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID
shards = load_shards(wd)
blocks = list(iter_blocks(shards))
by = {b.gid: b for b in blocks}

def ctx(center, n=6):
    lo, hi = max(0, center - n), min(len(blocks), center + n + 1)
    for b in blocks[lo:hi]:
        t = (b.text or "").strip().replace("\n", " ")
        mark = "[h]" if getattr(b, "is_heading", False) else "   "
        tag = ">>" if b.gid == center else "  "
        print(f"  {tag} gid={b.gid} {b.shard} p{b.page_idx} {mark} {t[:60]}")

print("=== 前言 gid=42 上下文 ===")
ctx(42)
print("\n=== 结束语 gid=449 上下文 ===")
ctx(449)

# 前言正文大概到哪：gid 43 起第一编之前
print("\n=== gid 42..64 之间（前言正文→第一编） ===")
for b in blocks[43:64]:
    t = (b.text or "").strip().replace("\n", " ")
    mark = "[h]" if getattr(b, "is_heading", False) else "   "
    print(f"  gid={b.gid} {b.shard} p{b.page_idx} {mark} {t[:60]}")
