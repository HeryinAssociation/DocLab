# -*- coding: utf-8 -*-
"""查 11.3 子树与原 key=2508 节点的子孙，决定是否需要清理空壳。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID
oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))

# 节点字段样例
def first(ns):
    return ns[0] if ns else None
print("=== 节点字段名 ===")
n0 = first(oc["tree"])
print(sorted(n0.keys()))
print(json.dumps({k: v for k, v in n0.items() if k != "children"}, ensure_ascii=False))

def find(ns, pred):
    for n in ns:
        if pred(n):
            return n
        r = find(n.get("children") or [], pred)
        if r:
            return r
    return None

def dump(n, d=0, maxd=4):
    if d > maxd:
        return
    print("  " * d + f"- L{n.get('level')} nid={n.get('nid')} key={n.get('key')} gid={n.get('gid')} "
          f"start={n.get('gid_start')} flags={n.get('flags')} nch={n.get('n_chars')} "
          f"own={n.get('own_chars')} gc={len(n.get('children') or [])} {n.get('title')!r}")
    for c in (n.get("children") or []):
        dump(c, d + 1, maxd)

print("\n=== 第九编 11 号节点整棵（L1=11）===")
n11 = None
for n in oc["tree"]:
    if n.get("nid") == "11":
        n11 = n
if n11:
    dump(n11, 0, 3)

print("\n=== 原 2508 节点（非 add:）在哪 ===")
def walk(ns, d):
    for n in ns:
        if str(n.get("key")) == "2508":
            print(f"  d{d} L{n.get('level')} nid={n.get('nid')} key={n.get('key')} gid={n.get('gid')} "
                  f"start={n.get('gid_start')} flags={n.get('flags')} n_chars={n.get('n_chars')} "
                  f"nch_children={len(n.get('children') or [])} title={n.get('title')!r}")
            for c in (n.get("children") or []):
                print(f"      child L{c.get('level')} nid={c.get('nid')} key={c.get('key')} "
                      f"n_chars={c.get('n_chars')} {c.get('title')!r}")
        walk(n.get("children") or [], d + 1)
walk(oc["tree"], 1)

print("\n=== manual_edits 里 2508 / 2361 / 42 相关 ===")
me = json.loads((WD / "manual_edits.json").read_text(encoding="utf-8"))
for sec in ("levels", "anchors", "deleted", "added"):
    v = me.get(sec)
    if isinstance(v, dict):
        for k in v:
            if str(k) in ("2508", "add:2508", "2361", "add:2361", "42", "add:42") or "2508" in str(k):
                print(f"  [{sec}] {k} = {json.dumps(v[k], ensure_ascii=False)[:300]}")
    elif isinstance(v, list):
        for it in v:
            print(f"  [{sec}] {json.dumps(it, ensure_ascii=False)[:200]}")
