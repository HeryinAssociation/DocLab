# -*- coding: utf-8 -*-
"""定论核对：印刷目录（编级）页码 vs 树里 L1 定位符；并抽验第九编 4 章的定位符。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID
ROOT = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
sys.path.insert(0, str(ROOT))
from core import pagecal  # noqa: E402

oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))
cal = pagecal.load_calibration(WD) if hasattr(pagecal, "load_calibration") else None

print("=== 印刷目录条目（含页码）===")
pt = (oc.get("printed_toc") or {}).get("entries") or []
for e in pt:
    print(f"  p={e.get('printed')!r:>6} lvl={e.get('level')} {e.get('title')!r}")

print("\n=== 树里 L1 的定位符 ===")
def loc_of(n):
    try:
        return cal.locator(n.get("shard"), int(n.get("page_idx") or 0))
    except Exception as ex:                       # noqa: BLE001
        return f"<err {ex}>"
for n in oc["tree"]:
    print(f"  L{n.get('level')} shard={n.get('shard')} page_idx={n.get('page_idx')} "
          f"loc={loc_of(n)} key={n.get('key')} {n.get('title')!r}")
    if n.get("level") == 1 and "第九编" in str(n.get("title") or ""):
        for c in n.get("children") or []:
            print(f"      L{c.get('level')} shard={c.get('shard')} page_idx={c.get('page_idx')} "
                  f"loc={loc_of(c)} key={c.get('key')} {c.get('title')!r}")
