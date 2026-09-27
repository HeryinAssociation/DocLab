"""新入库两本的下游管线：pagecal → outline → audit，各一本一条链。

顺序不能反：outline 的 R5「用书内印刷目录补齐 MinerU 漏标的章」要靠校准结果才触发，
所以 pagecal 必须早于 outline（与 doclab.py run 的顺序一致）。
日志由 python 写盘；只往 stdout 打 ASCII。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

DOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DOC))
PY = sys.executable
LOG = DOC / "_regtest" / "_out"
LOG.mkdir(parents=True, exist_ok=True)
env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

from core.config import resolve_source, list_sources          # noqa: E402

JOBS = [("dinghuadong", "档案与社会记忆研究"),
        ("huhongjie", "中国档案学的理念与模式")]

lines = []
for tag, key in JOBS:
    sid = resolve_source(key)
    lines.append(f"{tag}\t{sid}")
    for step in ("pagecal", "outline", "audit"):
        with open(LOG / f"{tag}_{step}.log", "w", encoding="utf-8") as fh:
            rc = subprocess.run([PY, str(DOC / "doclab.py"), step, sid],
                                cwd=str(DOC), stdout=fh, stderr=subprocess.STDOUT,
                                env=env).returncode
        print(f"{tag}/{step} rc={rc}", flush=True)
        if rc not in (0, 2):        # audit 有高危时返回 2，不算失败
            lines.append(f"  ! {step} rc={rc}")
            break

(LOG / "_new_sources.txt").write_text("\n".join(lines), encoding="utf-8")
print("sources: " + repr([s for s in list_sources() if s in "\n".join(lines)]))
print("DONE", flush=True)
