# -*- coding: utf-8 -*-
"""列出四本书导出产物，确认落盘。"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
OUT = os.path.join(BASE, "out")
BOOKS = [
    ("魂系", "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"),
    ("现代档案", "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"),
    ("丁华东", "档案与社会记忆研究_丁华东.b8a64398d9"),
    ("胡鸿杰", "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"),
]
res = {}
for tag, sid in BOOKS:
    d = os.path.join(OUT, sid)
    info = {"exists": os.path.isdir(d), "subdirs": [], "files": 0, "chars": 0}
    if info["exists"]:
        for sub in os.listdir(d):
            sp = os.path.join(d, sub)
            if os.path.isdir(sp):
                md = [f for f in os.listdir(sp) if f.endswith(".md")]
                tot = 0
                for f in md:
                    tot += len(open(os.path.join(sp, f), encoding="utf-8").read())
                info["subdirs"].append({"name": sub, "md": len(md), "chars": tot})
    res[tag] = info
print(json.dumps(res, ensure_ascii=False, indent=2))
