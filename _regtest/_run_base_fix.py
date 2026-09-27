# -*- coding: utf-8 -*-
"""基线书：演练修订单 → 应用 → pagecal → outline → audit，逐段落日志。"""
import os
import sys
import json
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
DL = os.path.join(BASE, "doclab.py")
SID = "中国数字人文发展报告"
PLAN = os.path.join(BASE, "_regtest", "_plans", "基线-数字人文.json")

log = []


def run(step, tag):
    r = subprocess.run([PY, DL, *step], cwd=BASE, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    log.append(f"\n########## {tag}  rc={r.returncode} ##########")
    log.append((r.stdout or "")[-2500:])
    if r.stderr:
        log.append("[stderr] " + (r.stderr or "")[-600:])
    return r


run(["fix", SID, "--plan", PLAN], "fix 演练")
run(["fix", SID, "--plan", PLAN, "--apply"], "fix 应用")
run(["pagecal", SID], "pagecal")
run(["outline", SID], "outline")
run(["audit", SID], "audit")

open(os.path.join(BASE, "_regtest", "_out", "_run_base_fix.txt"), "w",
     encoding="utf-8").write("\n".join(log))

# 汇总
rep = json.load(open(os.path.join(BASE, "_work", SID, "目录校核.json"),
                     encoding="utf-8"))
oc = json.load(open(os.path.join(BASE, "_work", SID, "outline.json"),
                    encoding="utf-8"))
print("verdict:", rep["verdict"], rep["severity"], "nodes:", oc["node_total"])
for f in rep["findings"]:
    if f["severity"] == "high":
        print("  HIGH", f["id"], f["check"], "|", f["detail"][:90])
