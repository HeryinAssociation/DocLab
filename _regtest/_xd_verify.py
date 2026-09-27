# -*- coding: utf-8 -*-
"""核验现代档案三处修复在最终树里的形态 + 余下 warn 是否可解释。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))

flat = []
def walk(ns, d):
    for n in ns:
        n["_d"] = d
        flat.append(n)
        walk(n.get("children") or [], d + 1)
walk(oc["tree"], 1)

print("=== node_total:", oc["node_total"])

print("\n=== ① 前言（应 L1，key=add:42）===")
for n in flat:
    if n.get("gid") == 42 or "前言" in str(n.get("title") or "").replace(" ", ""):
        print(f"  d{n['_d']} L{n.get('level')} nid={n.get('nid')} key={n.get('key')} gid={n.get('gid')} "
              f"flags={n.get('flags')} chars={n.get('own_chars')} title={n.get('title')!r}")

print("\n=== ② 第九编（应 L1 全题，一条）===")
for n in flat:
    if "第九编" in str(n.get("title") or ""):
        print(f"  d{n['_d']} L{n.get('level')} nid={n.get('nid')} key={n.get('key')} gid={n.get('gid')} "
              f"flags={n.get('flags')} chars={n.get('own_chars')} title={n.get('title')!r}")

print("\n=== ③ 档案与文化（应 L2，第九编下，key=add:2508）===")
for n in flat:
    if "档案与文化" in str(n.get("title") or ""):
        print(f"  d{n['_d']} L{n.get('level')} nid={n.get('nid')} key={n.get('key')} gid={n.get('gid')} "
              f"flags={n.get('flags')} chars={n.get('own_chars')} title={n.get('title')!r}")

print("\n=== L1 全列表（应 14 个：前言 + 第一~十二编 + ?）===")
for n in flat:
    if n.get("level") == 1:
        print(f"  nid={n.get('nid')} L{n.get('level')} key={n.get('key')} gid={n.get('gid')} "
              f"p={n.get('printed') if n.get('printed') is not None else n.get('page_idx')} "
              f"chars={n.get('own_chars')} {n.get('title')!r}")

print("\n=== 第九编下的 L2（应 4 章）===")
for n in flat:
    if n.get("level") == 1 and "第九编" in str(n.get("title") or ""):
        for c in (n.get("children") or []):
            print(f"    nid={c.get('nid')} L{c.get('level')} gid={c.get('gid')} chars={c.get('own_chars')} {c.get('title')!r}")

print("\n=== 余下 high？===")
aud = json.loads((WD / "目录校核.json").read_text(encoding="utf-8"))
print("verdict:", aud.get("verdict"))
hs = [f for f in aud.get("findings", []) if f.get("severity") == "high"]
print("high 数:", len(hs))
for f in hs:
    print("  ", json.dumps(f, ensure_ascii=False)[:400])

print("\n=== numbering_gap 两条（章级还是条目级）===")
for f in aud.get("findings", []):
    if f.get("type") == "numbering_gap":
        print("  ", json.dumps(f, ensure_ascii=False)[:400])

print("\n=== dropped_numbered_heading 三条 ===")
for f in aud.get("findings", []):
    if f.get("type") == "dropped_numbered_heading":
        print("  ", json.dumps(f, ensure_ascii=False)[:400])
