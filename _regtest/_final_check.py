# -*- coding: utf-8 -*-
"""终验：①基线树形 ②基线重导出+verify ③四本书 audit 复跑 ④单元测试。"""
import os
import sys
import json
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
DL = os.path.join(BASE, "doclab.py")
WD = os.path.join(BASE, "_work", "中国数字人文发展报告")

out = {}

# ① 基线树形：艺术学科只应有一个 L2
oc = json.load(open(os.path.join(WD, "outline.json"), encoding="utf-8"))
flat = []
def w(ns):
    for n in ns:
        flat.append(n); w(n.get("children") or [])
w(oc["tree"])
out["base_nodes"] = oc["node_total"]
out["base_art_l2"] = [n["title"] for n in flat
                      if n.get("level") == 2 and "艺术学科" in (n.get("title") or "")]
out["base_l2_count"] = len([n for n in flat if n.get("level") == 2])
# 4.2 落点（应为 57）
for n in flat:
    if n.get("nid") == "4.2":
        out["loc_4_2"] = n.get("loc")

# ② 基线重导出 + verify
r = subprocess.run([PY, DL, "export", "中国数字人文发展报告", "-d", "2"],
                   cwd=BASE, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
out["export_rc"] = r.returncode
out["export_tail"] = (r.stdout or "")[-400:]
r = subprocess.run([PY, DL, "verify", "中国数字人文发展报告", "-d", "2"],
                   cwd=BASE, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
out["verify_rc"] = r.returncode
out["verify_verdict"] = None
for line in (r.stdout or "").splitlines():
    if "总判定" in line:
        out["verify_verdict"] = line.strip()

print(json.dumps(out, ensure_ascii=False, indent=2))
