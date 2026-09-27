# -*- coding: utf-8 -*-
"""解析修复后，对五本书重跑 pagecal → outline → audit（逐本串行，互不干扰）。

每本一张日志写到 _regtest/_rerun/<tag>.log（PowerShell/Bash 的 stdout 不可信，
一律落文件）。写完 _rerun/_summary.json。
"""
import os
import sys
import json
import time
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
DL = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\doclab.py"
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
LOGDIR = os.path.join(BASE, "_regtest", "_rerun")
os.makedirs(LOGDIR, exist_ok=True)

BOOKS = [
    ("中国数字人文发展报告", "中国数字人文发展报告"),
    ("魂系", "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"),
    ("现代档案", "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"),
    ("丁华东", "档案与社会记忆研究_丁华东.b8a64398d9"),
    ("胡鸿杰", "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"),
]


def run(sid, *argv):
    return subprocess.run([PY, DL, *argv, sid], cwd=BASE,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def main():
    summary = {}
    for tag, sid in BOOKS:
        log = []
        t0 = time.time()
        for step in ("pagecal", "outline", "audit"):
            r = run(sid, step)
            log.append(f"### {step} rc={r.returncode}")
            log.append((r.stdout or "").strip()[-4000:])
            if r.stderr:
                log.append("### stderr\n" + (r.stderr or "").strip()[-1500:])
            if r.returncode not in (0, 2):
                log.append(f"!! {step} 失败，停止本本")
                break
        txt = "\n".join(log)
        open(os.path.join(LOGDIR, f"{tag}.log"), "w", encoding="utf-8").write(txt)
        # 抓校核结论
        try:
            rep = json.load(open(os.path.join(BASE, "_work", sid, "目录校核.json"),
                                 encoding="utf-8"))
            summary[tag] = {"verdict": rep["verdict"], "severity": rep["severity"],
                            "nodes": rep.get("outline_node_total"),
                            "elapsed": round(time.time() - t0, 1)}
        except Exception as e:                                     # noqa: BLE001
            summary[tag] = {"error": f"{type(e).__name__}: {e}"}
        print(f"[{tag}] {json.dumps(summary[tag], ensure_ascii=False)}", flush=True)
    json.dump(summary, open(os.path.join(LOGDIR, "_summary.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=2)
    print("DONE")


main()
