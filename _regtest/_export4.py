# -*- coding: utf-8 -*-
"""四本书导出 L2 + verify（四道闸门），逐本落日志。"""
import os
import sys
import json
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
DL = os.path.join(BASE, "doclab.py")
LOG = os.path.join(BASE, "_regtest", "_rerun")
os.makedirs(LOG, exist_ok=True)

BOOKS = [
    ("魂系", "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"),
    ("现代档案", "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"),
    ("丁华东", "档案与社会记忆研究_丁华东.b8a64398d9"),
    ("胡鸿杰", "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"),
]

summary = {}
for tag, sid in BOOKS:
    log = []
    for step in (["export", sid, "-d", "2"], ["verify", sid, "-d", "2"]):
        r = subprocess.run([PY, DL, *step], cwd=BASE, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        log.append(f"### {step[0]} rc={r.returncode}")
        log.append((r.stdout or "")[-3000:])
        if r.stderr:
            log.append("### stderr\n" + (r.stderr or "")[-800:])
    open(os.path.join(LOG, f"exp_{tag}.log"), "w", encoding="utf-8").write("\n".join(log))
    # 抓 verify 判定
    try:
        rep = json.load(open(os.path.join(BASE, "_work", sid, "校验报告.json"),
                             encoding="utf-8"))
        summary[tag] = {"verdict": rep.get("verdict"),
                        "gates": {k: (v.get("ok") if isinstance(v, dict) else v)
                                  for k, v in (rep.get("gates") or {}).items()}}
    except Exception as e:                                         # noqa: BLE001
        summary[tag] = {"err": f"{type(e).__name__}: {e}"}
    print(f"[{tag}] {json.dumps(summary[tag], ensure_ascii=False)}", flush=True)

json.dump(summary, open(os.path.join(LOG, "_exp_summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("DONE")
