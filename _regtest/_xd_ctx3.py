# -*- coding: utf-8 -*-
"""看三条被丢候选的上下文（前后各 2 块），判断是否真标题。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = ROOT / "_work" / SID
sys.path.insert(0, str(ROOT))
from core.project import iter_blocks, load_shards  # noqa: E402

blocks = iter_blocks(load_shards(WD))
by_gid = {}
for b in blocks:
    by_gid[getattr(b, "gid", None)] = b


def show(center, r=2):
    print(f"\n--- 中心 gid={center} 前后 {r} 块 ---")
    for g in range(center - r, center + r + 1):
        b = by_gid.get(g)
        if b is None:
            print(f"  gid={g}  (无)")
            continue
        txt = str(getattr(b, "text", "") or "").replace("\n", "\\n")
        print(f"  gid={g} p{getattr(b,'page_idx',None)} type={getattr(b,'type',None)} "
              f"lvl={getattr(b,'text_level',None)} len={len(txt)} {txt[:110]!r}")


for g in (164, 1547):
    show(g, 2)

# 另外两条一起看（问句成组出现，看整段的编号序列）
print("\n=== gid 160-170 的编号序列 ===")
for g in range(158, 175):
    b = by_gid.get(g)
    if b is None:
        continue
    txt = str(getattr(b, "text", "") or "").replace("\n", " ")
    print(f"  gid={g} p{getattr(b,'page_idx',None)} lvl={getattr(b,'text_level',None)} {txt[:90]!r}")
