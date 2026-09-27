"""把诊断脚本（_diag_epub.py 等）临时登记出来的合成源清掉，别留在工作台里。

只清 `diag.` / 自测前缀开头的源；真实登记的源一律不碰（打印出来让人自己看）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import WORK_DIR, OUT_DIR, list_sources      # noqa: E402

PREFIXES = ("diag.", "doclab-epub-selftest", "_zz_")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    for sid in list_sources():
        if not sid.startswith(PREFIXES):
            print(f"  留着（真实源）：{sid}")
            continue
        for d in (WORK_DIR / sid, OUT_DIR / sid):
            if d.exists():
                try:
                    shutil.rmtree(d)
                    print(f"  清掉 {d}")
                except BaseException as e:                      # noqa: BLE001
                    print(f"  ⚠️ 清不掉 {d}：{type(e).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
