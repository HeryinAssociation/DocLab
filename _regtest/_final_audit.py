# -*- coding: utf-8 -*-
"""四本书 + 基线 最终 audit 判定汇总（先跑 audit 再读 目录校核.json）。"""
import os
import sys
import json
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
DL = os.path.join(BASE, "doclab.py")

BOOKS = [
    ("基线-数字人文", "中国数字人文发展报告"),
    ("魂系", "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"),
    ("现代档案", "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"),
    ("丁华东", "档案与社会记忆研究_丁华东.b8a64398d9"),
    ("胡鸿杰", "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"),
]

out = {}
for tag, sid in BOOKS:
    subprocess.run([PY, DL, "audit", sid], cwd=BASE, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
    p = os.path.join(BASE, "_work", sid, "目录校核.json")
    if not os.path.exists(p):
        out[tag] = {"err": "no 目录校核.json"}
        continue
    rep = json.load(open(p, encoding="utf-8"))
    by_check = {}
    for it in rep.get("findings", []):
        if it.get("severity") == "high":
            by_check[it.get("check")] = by_check.get(it.get("check"), 0) + 1
    out[tag] = {
        "verdict": rep.get("verdict"),
        "sev": rep.get("severity"),
        "nodes": rep.get("outline_node_total"),
        "high_by_check": by_check,
    }

print(json.dumps(out, ensure_ascii=False, indent=2))
