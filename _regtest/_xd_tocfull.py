# -*- coding: utf-8 -*-
"""现代档案：取 gid=60 全文（印刷目录），逐行解析，与树 L1/L2 对照。只读。"""
import json
import re
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
toc_text = by[60].text or ""
print("=== gid=60 全文 ===")
print(toc_text)
print()

oc = json.loads((wd / "outline.json").read_text(encoding="utf-8"))
flat = []
def walk(ns, d):
    for n in ns:
        flat.append((d, n))
        walk(n.get("children") or [], d + 1)
walk(oc["tree"], 1)

def norm(s):
    return re.sub(r"[\s·．.…\-—_、，,：:（）()\d]+", "", s or "")

# 解析目录行：标题 + 括号页码
lines = [l.strip() for l in toc_text.splitlines() if l.strip()]
toc_entries = []
pend = ""
for l in lines:
    m = re.search(r"[.．·…\-—–\s]*[（(]\s*(\d{1,4})\s*[)）]\s*$", l)
    if m:
        title = re.sub(r"[.．·…\-—–\s]*[（(]\s*\d{1,4}\s*[)）]\s*$", "", l).strip()
        # 折行：标题断成两行（前一行没页码）
        full = (pend + title) if pend else title
        toc_entries.append((full, int(m.group(1))))
        pend = ""
    else:
        # 没页码的行：可能是折行的前半，也可能是无页码条目
        pend = pend + l if pend else l
        if len(pend) > 60:  # 太长不像折行前半，丢弃防串
            pend = ""
print(f"=== 解析出 {len(toc_entries)} 条带页码目录条目 ===")
for t, p in toc_entries:
    print(f"  p{p:>3} | {t}")

print("\n=== 与树 L1/L2 对照（树标题 ⊆ 目录标题 或 目录 ⊆ 树，按归一化） ===")
tree_nodes = [(d, n) for d, n in flat if n.get("level") in (1, 2) and n.get("marker") != "front"]
used = set()
for d, n in tree_nodes:
    nt = norm(n.get("title") or "")
    hit = None
    for i, (t, p) in enumerate(toc_entries):
        if i in used:
            continue
        et = norm(t)
        if nt and et and (nt in et or et in nt):
            hit = (i, t, p)
            break
    if hit:
        used.add(hit[0])
        print(f"  ✓ L{n['level']} {n['nid']} {n['title'][:34]!r:36} ← 目录「{hit[1][:30]}」p{hit[2]}")
    else:
        print(f"  ？L{n['level']} {n['nid']} {n['title'][:34]!r:36} ← 目录无此条")
print("\n=== 目录有、树里没有的 ===")
for i, (t, p) in enumerate(toc_entries):
    if i not in used:
        print(f"  p{p:>3} | {t}")
