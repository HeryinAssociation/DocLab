# -*- coding: utf-8 -*-
"""看 level_jump 详情、第九编下各节点 level 与 nid、空壳节点是否产生空文件。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID
oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))
aud = json.loads((WD / "目录校核.json").read_text(encoding="utf-8"))

print("=== level_jump 详情 ===")
for f in aud.get("findings", []):
    if f.get("type") == "level_jump":
        print(json.dumps(f, ensure_ascii=False, indent=1))

print("\n=== 第八编尾 ~ 第十编头 的平铺文档序（含 level/nid/key/n_chars）===")
flat = []
def walk(ns, d):
    for n in ns:
        flat.append((d, n))
        walk(n.get("children") or [], d + 1)
walk(oc["tree"], 1)
on = False
for d, n in flat:
    t = str(n.get("title") or "")
    if "第九编" in t:
        on = True
    if "第十编" in t:
        on = False
    if on and (n.get("nid") or "").startswith("11"):
        print(f"  L{n.get('level')} nid={n.get('nid')} key={n.get('key')} start={n.get('gid_start')} "
              f"nch={n.get('n_chars')} flags={n.get('flags')} {t!r}")

print("\n=== 空节点在导出的落盘情况 ===")
outd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\out") / SID
if outd.exists():
    for lvl in sorted(outd.iterdir()):
        if not lvl.is_dir():
            continue
        files = sorted(lvl.glob("*.md"))
        empties = [f for f in files if len(f.read_text(encoding="utf-8").strip()) < 40]
        print(f"  {lvl.name}: {len(files)} 个 md，其中疑似空文件 {len(empties)}")
        for f in empties:
            print(f"     - {f.name} ({len(f.read_text(encoding='utf-8'))} chars)")
    # 找含「档案与文化」的文件
    print("\n  --- 含「档案与文化」的导出文件 ---")
    for f in outd.rglob("*.md"):
        if "档案与文化" in f.name:
            print(f"     L{f.parent.name}: {f.name}  {len(f.read_text(encoding='utf-8'))} chars")
else:
    print("  out 目录不存在")
