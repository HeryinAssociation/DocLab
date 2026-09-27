# -*- coding: utf-8 -*-
"""普查：MinerU 标了 text_level（标题）却没进树的块。这是「漏标题」的最强召回信号。"""
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
oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))

# 树里所有节点的 gid_start（= 断点所在块）
tree_gids = set()
def walk(ns):
    for n in ns:
        if n.get("gid_start") is not None:
            tree_gids.add(int(n["gid_start"]))
        if n.get("gid") is not None:
            tree_gids.add(int(n["gid"]))
        walk(n.get("children") or [])
walk(oc["tree"])

# 人工新增的断点 gid
me = json.loads((WD / "manual_edits.json").read_text(encoding="utf-8"))
added = {int(k) for k in (me.get("added") or {})}

print("=== MinerU 标了标题级别、但树里没有的块 ===")
miss = []
for b in blocks:
    if b.text_level is None:
        continue
    if b.gid in tree_gids or b.gid in added:
        continue
    miss.append(b)

print(f"共 {len(miss)} 条\n")
from collections import Counter
c = Counter(b.text_level for b in miss)
print("按 text_level:", dict(c), "\n")
for b in miss:
    txt = str(b.text or "").replace("\n", " ")
    print(f"  gid={b.gid} {b.shard} p{b.page_idx} lvl={b.text_level} len={len(txt)} {txt[:100]!r}")

print("\n=== 对照：MinerU 标级别的块总数 / 其中进了树的 ===")
tot = sum(1 for b in blocks if b.text_level is not None)
print(f"  标级别的块 {tot}，进树 {tot - len(miss)}，未进树 {len(miss)}")
