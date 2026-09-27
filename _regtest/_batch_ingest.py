"""并行入库：一本 PDF 一个进程，同时推进。

为什么这么点火：
  - Bash 的 PATH 缺 coreutils（`tail`/`dirname` 都找不到），管道命令等于没跑；
  - PowerShell 的 `>`/`Out-File` 会按控制台代码页解码 python 的 UTF-8 输出 —— 中文会花。
所以日志一律**由 python 自己写盘**（句柄直接开成 utf-8），shell 只负责起进程、
进程只往 stdout 打纯 ASCII 进度。跑完写 `_summary.json`。

幂等：doclab 的 ingest 认 `_work/<sid>/ingest_state.json`，重跑只会补没就绪的分片，
已下的 zip / 已解压的工程不会重做。
"""
from __future__ import annotations

import glob
import json
import os
import pathlib
import subprocess
import sys
import time

DOC = pathlib.Path(__file__).resolve().parent.parent          # doclab/
PY = sys.executable
BK = pathlib.Path(r"C:\Users\Zhaoshuochen\Desktop\书籍")
LOG = DOC / "_regtest" / "_ingest_logs"
LOG.mkdir(parents=True, exist_ok=True)

JOBS = [
    ("dinghuadong", BK / "档案与社会记忆研究_丁华东.pdf"),
]
_hh = glob.glob(str(BK / "中国档案学的理念与模式*.pdf"))
JOBS.append(("huhongjie", pathlib.Path(_hh[0]) if _hh else BK / "__missing__"))

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
# 刻意**不动** http_proxy：MinerU 的 CDN 大文件必须走代理，
# 绕开会卡死在整数 MiB 边界（实测 2 MiB / 6 MiB）。

procs: dict[str, tuple[subprocess.Popen, object]] = {}
for tag, pdf in JOBS:
    if not pdf.exists():
        print(f"[skip] {tag}: missing {pdf.name}")
        continue
    fh = open(LOG / f"{tag}.log", "w", encoding="utf-8", buffering=1)
    p = subprocess.Popen([PY, str(DOC / "doclab.py"), "ingest", str(pdf)],
                         cwd=str(DOC), stdout=fh, stderr=subprocess.STDOUT, env=env)
    procs[tag] = (p, fh)
    print(f"[launch] {tag} pid={p.pid}", flush=True)
    print(f"         {pdf.name[:60]}", flush=True)

t0 = time.time()
while True:
    alive = sorted(t for t, (p, _) in procs.items() if p.poll() is None)
    if not alive:
        break
    time.sleep(20)
    print(f"[wait] {int(time.time() - t0)}s alive={alive}", flush=True)

res: dict = {}
for tag, (p, fh) in procs.items():
    fh.close()
    res[tag] = p.returncode
    print(f"[exit] {tag} rc={p.returncode}", flush=True)
(LOG / "_summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print("SUMMARY " + json.dumps(res), flush=True)
