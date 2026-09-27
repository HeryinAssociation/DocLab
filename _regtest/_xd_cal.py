# -*- coding: utf-8 -*-
"""看现代档案的分片与页码校准，判断 front 区异常是否真问题。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

cal = json.loads((WD / "page_calibration.json").read_text(encoding="utf-8"))
print("=== 校准顶层 ===")
print(sorted(cal.keys()))
segs = cal.get("segments") or []
print(f"\n=== segments（{len(segs)} 段）===")
for s in segs:
    print(f"  shard={s.get('shard')} kind={s.get('kind')} off={s.get('offset')} "
          f"pages=[{s.get('start')}..{s.get('end')}] src={s.get('source')} "
          f"obs={s.get('observations') if 'observations' in s else ''}")

print("\n=== 各分片观测（前若干条）===")
for ob in (cal.get("observations") or [])[:40]:
    print("  ", json.dumps(ob, ensure_ascii=False))

print("\n=== header/locator 相关键 ===")
for k, v in cal.items():
    if k not in ("segments", "observations"):
        s = json.dumps(v, ensure_ascii=False)
        print(f"  {k}: {s[:300]}")
