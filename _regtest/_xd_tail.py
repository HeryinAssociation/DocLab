# -*- coding: utf-8 -*-
"""现代档案：树尾部核查——最后节点区间、trailing 块归属、导出 md 尾部。只读。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
wd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

oc = json.loads((wd / "outline.json").read_text(encoding="utf-8"))

flat = []
def walk(ns, d):
    for n in ns:
        flat.append((d, n))
        walk(n.get("children") or [], d + 1)
walk(oc["tree"], 1)

print("=== 树尾 12 个节点 ===")
for d, n in flat[-12:]:
    print(f"  L{d} {n.get('title')[:38]} gid_start={n.get('gid_start')} n_blocks={n.get('n_blocks')} own_chars={n.get('own_chars')} key={n.get('key')}")

# 最后一个节点的覆盖终点
last_n = flat[-1][1]
gs = int(last_n.get("gid_start") or 0)
nb = int(last_n.get("n_blocks") or 0)
print(f"\n最后节点终点（按 n_blocks）: gid {gs}..{gs+nb-1}")

from core.project import iter_blocks, load_shards  # noqa: E402
shards = load_shards(wd)
blocks = list(iter_blocks(shards))
print(f"总块数: {len(blocks)}")

# 检查 trailing: 最后节点 gid_start+n_blocks 之后还有多少块、其中多少是内容块
content_after = 0
for b in blocks:
    if b.gid >= gs + nb:
        t = (b.text or "").strip()
        if t:
            content_after += 1
print(f"区间外剩余块: {sum(1 for b in blocks if b.gid >= gs+nb)}，其中非空文本块 {content_after}")

# outline.json 里有没有 trailing/coverage 字段
for k in ("coverage", "trailing", "tail_blocks", "unassigned", "content_blocks", "noise_blocks"):
    if k in oc:
        print(f"outline[{k}] =", oc[k])

# 导出 md 尾部
out_dir = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\out") / SID / "L2"
mds = sorted(out_dir.glob("*.md"))
print(f"\nL2 导出文件数: {len(mds)}")
if mds:
    tail = mds[-1].read_text(encoding="utf-8")
    print(f"最后文件: {mds[-1].name}  长度 {len(tail)} 字符")
    print("---- 尾部 400 字 ----")
    print(tail[-400:])
