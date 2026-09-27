# -*- coding: utf-8 -*-
"""现代档案：gid=2508「档案与文化」上下文（判定它是被 10.3 吞掉的章）＋gid=60 拆行看9编完整行。只读。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
from core.project import iter_blocks, load_shards  # noqa: E402

SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
wd = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID
shards = load_shards(wd)
blocks = list(iter_blocks(shards))

print("=== gid 2504..2515 上下文 ===")
for b in blocks:
    if 2504 <= b.gid <= 2515:
        t = (b.text or "").strip().replace("\n", " ")
        mark = "[h]" if getattr(b, "is_heading", False) else "   "
        print(f"  gid={b.gid} {b.shard} p{b.page_idx} {mark} {t[:64]}")
