# -*- coding: utf-8 -*-
"""跑核心单测套件，结果逐条落文件（Bash 的 tail/管道在这台机器上不可靠）。"""
import os
import sys
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
OUT = os.path.join(BASE, "_regtest", "_out", "_tests.txt")
TESTS = ["test_key", "test_manual", "test_add", "test_chars", "test_pagemap",
         "test_multisel", "test_audit", "test_fixplan", "test_shards",
         "test_download", "test_epub"]

lines = []
for t in TESTS:
    p = os.path.join(BASE, "tests", f"{t}.py")
    if not os.path.isfile(p):
        lines.append(f"=== {t}: <missing>")
        continue
    r = subprocess.run([PY, p], cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    body = (r.stdout or "") + (r.stderr or "")
    tail = [ln for ln in body.strip().split("\n") if ln.strip()][-4:]
    lines.append(f"=== {t}: rc={r.returncode}")
    for ln in tail:
        lines.append("      " + ln[:200])
open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
