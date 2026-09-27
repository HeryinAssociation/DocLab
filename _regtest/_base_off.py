# -*- coding: utf-8 -*-
"""P1 正文区 offset：自动结果 vs 现锚点(20↔10) vs 候选(20↔8)。"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
sys.path.insert(0, BASE)
SID = "中国数字人文发展报告"

import doclab as D                                                # noqa: E402
from core.pagecal import calibrate                               # noqa: E402

wd, shards, blocks = D.load_blocks(SID)
smeta = [s.to_dict() for s in shards]
print("blocks:", len(blocks), "shards:", [getattr(s, "shard", getattr(s, "id", "?")) for s in shards])

def show(tag, anchors):
    cal = calibrate(SID, blocks, smeta, anchors=anchors)
    print(f"\n===== {tag} =====")
    print("locator_type:", cal.locator_type, "| reason:", cal.reason[:120])
    for s in cal.segments:
        print(f"  {s.shard} {s.kind:5s} idx {s.page_idx_start}-{s.page_idx_end} "
              f"→ printed {s.printed_start}-{s.printed_end} offset {s.offset:+d} "
              f"obs={s.n_obs} cov={s.coverage}")
    # 关键节点落点
    for idx in (20, 69, 97, 128, 160, 185, 199):
        print(f"    P1 idx {idx} → {cal.locator('P1', idx)}")

show("A 自动（无锚点）", None)
show("B 现锚点 5↔1, 20↔10", {"P1": [{"page_idx": 5, "printed": 1},
                                    {"page_idx": 20, "printed": 10}]})
show("C 候选锚点 5↔1, 20↔8", {"P1": [{"page_idx": 5, "printed": 1},
                                    {"page_idx": 20, "printed": 8}]})
show("D 候选锚点 5↔1, 15↔3", {"P1": [{"page_idx": 5, "printed": 1},
                                    {"page_idx": 15, "printed": 3}]})
