# -*- coding: utf-8 -*-
"""批量取证：把「疑似缺章/标题残缺」的区块区间原文摊开，一次落盘。

用法：python _regtest/_peek.py > _regtest/_out/_peek.txt
"""
import os
import sys
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
DL = os.path.join(BASE, "doclab.py")

HX = "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"
XD = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
DH = "档案与社会记忆研究_丁华东.b8a64398d9"
HH = "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"

JOBS = [
    ("魂系 第六章标题区(985-996)", HX, ["--gid", "985-996"]),
    ("魂系 第二部分/第四章区(480-496)", HX, ["--gid", "480-496"]),
    ("魂系 第七章区(1225-1245)", HX, ["--gid", "1225-1245"]),
    ("魂系 第五章合成节点区(740-752)", HX, ["--gid", "740-752"]),
    ("魂系 结束语(目录说 P2 p42≈印刷227)", HX, ["--q", "结束语"]),
    ("现代档案 2.3 列表(370-385)", XD, ["--gid", "370-385"]),
    ("现代档案 4.2 列表(933-946)", XD, ["--gid", "933-946"]),
    ("现代档案 13.1 列表(3418-3432)", XD, ["--gid", "3418-3432"]),
    ("现代档案 13.1 列表(3436-3448)", XD, ["--gid", "3436-3448"]),
    ("现代档案 13.2 列表(3555-3580)", XD, ["--gid", "3555-3580"]),
    ("丁华东 第一章区(228-248)", DH, ["--gid", "228-248"]),
    ("胡鸿杰 第一章区(345-362)", HH, ["--gid", "345-362"]),
    ("胡鸿杰 找第二章(351-730)", HH, ["--q", "第二章", "--gid", "351-730"]),
    ("胡鸿杰 第五章区(1030-1050)", HH, ["--gid", "1030-1050"]),
]

out = []
for label, sid, argv in JOBS:
    out.append("=" * 78)
    out.append(f"### {label}")
    r = subprocess.run([PY, DL, "grep", sid, *argv], cwd=BASE,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    body = (r.stdout or "") + (r.stderr or "")
    # 去掉 stderr 里的环境噪声行
    keep = [ln for ln in body.split("\n")
            if ln.strip() and "shell-runtime-bash-env" not in ln]
    out.extend(keep[:60])
    out.append("")

open(os.path.join(BASE, "_regtest", "_out", "_peek.txt"), "w",
     encoding="utf-8").write("\n".join(out))
print("written", len(out), "lines")
