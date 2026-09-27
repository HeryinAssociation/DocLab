# -*- coding: utf-8 -*-
"""现代档案：manual levels 明细 + 前言块在 classify/collect 阶段的命运。只读。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
wd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

me = json.loads((wd / "manual_edits.json").read_text(encoding="utf-8"))
print("=== levels 明细 ===")
for k, v in me.get("levels", {}).items():
    print(f"  {k} -> L{v}")
print("\n=== added 明细 ===")
for k, v in me.get("added", {}).items():
    print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:120]}")

# 前言块为什么没进树：跑 classify/collect_headings 看它落在哪
from core.project import iter_blocks, load_shards  # noqa: E402
from core import outline  # noqa: E402

shards = load_shards(wd)
blocks = list(iter_blocks(shards))
by = {b.gid: b for b in blocks}

b42 = by[42]
print("\n=== gid=42 前言块 ===")
print("  type:", b42.type, " level_hint:", getattr(b42, "level", None),
      " text:", repr((b42.text or "").strip()[:40]))
print("  page_idx:", b42.page_idx, " shard:", b42.shard)

# 前置区边界（R2）：第一编在 gid=64；R2 会把 gid<64 的标题都关掉？
# 看看 classify 给 gid 42 的结果
res = outline.collect_headings(blocks)
cands, dropped = res if isinstance(res, tuple) else (res, [])
hit = [c for c in cands if getattr(c[0], "gid", None) == 42]
print("\n=== collect_headings 里 gid=42 ===")
print("  命中:", json.dumps([{"cls": c[1], "gid": c[0].gid} for c in hit], ensure_ascii=False)[:300]
      if hit else "未命中（被过滤）")

dr_hit = [d for d in dropped if d.get("gid") == 42]
print("  dropped 里:", json.dumps(dr_hit, ensure_ascii=False)[:300] if dr_hit else "无")

# R2 前置区边界确认：树里第一个正节点 gid=64，第一编前的标题候选有哪些
print("\n=== gid<64 的标题候选（collect_headings 全量） ===")
for item in cands:
    blk = item[0]
    if blk.gid < 64:
        print(f"  gid={blk.gid} p{blk.page_idx} cls={item[1]} {str(getattr(blk, 'text', ''))[:40]!r}")
