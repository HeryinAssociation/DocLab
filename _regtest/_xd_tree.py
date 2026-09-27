# -*- coding: utf-8 -*-
"""现代档案：全树查 前言/结束语；2.3 子结构；audit 的 front_zone 检查口径。只读。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
wd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

oc = json.loads((wd / "outline.json").read_text(encoding="utf-8"))

flat = []
def walk(ns, d, path):
    for n in ns:
        flat.append((d, n, path))
        walk(n.get("children") or [], d + 1, path + [n.get("title")])
walk(oc["tree"], 1, [])

print("=== 全树含「前言 / 结束语 / 后记」的节点 ===")
for d, n, path in flat:
    t = n.get("title") or ""
    if any(k in t for k in ("前言", "结束语", "后记", "序")):
        print(f"  L{d} key={n.get('key')} gid={n.get('gid_start')} {t[:40]}")

print("\n=== 2.3「档案，发展及国家主权」子结构 ===")
for d, n, path in flat:
    if n.get("gid_start") == 361:
        for c in n.get("children") or []:
            print(f"  L{d+1} key={c.get('key')} gid={c.get('gid_start')} {c.get('title')[:40]}")
        break

print("\n=== 2.1「档案馆的责任」子结构（对照文章内部节样式） ===")
for d, n, path in flat:
    if n.get("gid_start") == 65:
        ch = n.get("children") or []
        print(f"  子节点数: {len(ch)}")
        for c in ch[:10]:
            print(f"  L{d+1} key={c.get('key')} gid={c.get('gid_start')} {c.get('title')[:40]}")
        break

print("\n=== manual_edits.json 现有内容（levels/added/deleted 计数） ===")
me = json.loads((wd / "manual_edits.json").read_text(encoding="utf-8"))
for k, v in me.items():
    if isinstance(v, dict):
        print(f"  {k}: {len(v)} 条")
    else:
        print(f"  {k}: {v}")
