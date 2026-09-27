"""跑编译 + 校核测试 + 两本重跑校核，输出一律由 python 写 UTF-8 文件。"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

DOC = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
LOG = DOC / "_regtest" / "_out"
LOG.mkdir(parents=True, exist_ok=True)
env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

JOBS = [
    ("compile", [PY, "-m", "py_compile", "core/audit.py", "core/pagecal.py", "doclab.py"]),
    ("test_audit", [PY, "tests/test_audit.py"]),
    ("audit_hunxi", [PY, "doclab.py", "audit", "魂系"]),
    ("audit_xiandai", [PY, "doclab.py", "audit", "现代档案"]),
]
for name, cmd in JOBS:
    with open(LOG / f"{name}.log", "w", encoding="utf-8") as fh:
        rc = subprocess.run(cmd, cwd=str(DOC), stdout=fh,
                            stderr=subprocess.STDOUT, env=env).returncode
    print(f"{name} rc={rc}", flush=True)
