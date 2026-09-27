"""探针：确认本机环境对「批量删除」抛的是什么 —— 决定测试清理该怎么写才不被它判红。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

d = Path(tempfile.gettempdir()) / "_doclab_rmtree_probe"
if d.exists():
    try:
        shutil.rmtree(d, ignore_errors=True)
    except BaseException:                                   # noqa: BLE001
        pass
d.mkdir(parents=True, exist_ok=True)
for i in range(60):
    (d / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

sys.stdout.reconfigure(encoding="utf-8")
print("目标文件数：", len(list(d.iterdir())))

# 1) 裸 rmtree
try:
    shutil.rmtree(d)
    print("裸 rmtree：成功")
except BaseException as e:                                  # noqa: BLE001
    print(f"裸 rmtree：抛 {type(e).__module__}.{type(e).__name__}: {str(e)[:200]}")

# 2) ignore_errors=True
try:
    shutil.rmtree(d, ignore_errors=True)
    print("ignore_errors=True：没抛（目录还在？%s）" % d.exists())
except BaseException as e:                                  # noqa: BLE001
    print(f"ignore_errors=True：仍抛 {type(e).__module__}.{type(e).__name__}: {str(e)[:200]}")

# 3) 逐个文件删
try:
    for p in sorted(d.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    print("逐文件删：成功")
except BaseException as e:                                  # noqa: BLE001
    print(f"逐文件删：抛 {type(e).__module__}.{type(e).__name__}: {str(e)[:200]}")
