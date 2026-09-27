# -*- coding: utf-8 -*-
"""各源块类型普查。"""
import io, sys, collections
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from core.config import list_sources, work_dir
from core.project import load_shards, iter_blocks

OUT = io.open(r"C:\Users\Zhaoshuochen\Desktop\_tmp_types.txt", "w", encoding="utf-8", newline="\n")
for sid in list_sources():
    wd = work_dir(sid)
    try:
        blocks = iter_blocks(load_shards(wd))
    except Exception as e:
        OUT.write("%s: %s\n" % (sid, e))
        continue
    c = collections.Counter(b.type for b in blocks)
    ctl = collections.Counter((b.type, b.text_level) for b in blocks if b.type == "text")
    OUT.write("=== %s  (%d blocks)\n" % (sid, len(blocks)))
    OUT.write("   types: %s\n" % dict(c))
    OUT.write("   text_level 分布: %s\n" % dict(collections.Counter(l for _, l in ctl)))
    pf = [b for b in blocks if b.type == "page_footnote"]
    OUT.write("   page_footnote: %d 条  例: %s\n" % (len(pf), [b.text[:40] for b in pf[:6]]))
OUT.close()
print("ok")
