"""看合成的 EPUB 到底建出什么树、什么层级统计 —— 用来把 test_epub 的断言写准。

顺带检查一个疑点：clean_title() 是不是把「第一章 …」这类标题改写了。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from core.config import load_config, out_dir, work_dir          # noqa: E402
from core.ingest import ingest_local                            # noqa: E402
from core.outline import build_tree, walk_nodes                 # noqa: E402
from core.pagecal import calibrate                              # noqa: E402
from core.project import iter_blocks, load_shards               # noqa: E402
import test_epub as T                                           # noqa: E402

OUT = ROOT / "_regtest" / "_diag_epub.txt"
L: list[str] = []

epub = T.build_epub(T.TMP / "diag.epub")
pj = ingest_local(epub, load_config(), log=lambda m: None)
sid = pj["source_id"]
wd = work_dir(sid)
shards = load_shards(wd)
blocks = iter_blocks(shards)

L.append(f"sid={sid}")
L.append("--- 块序列 ---")
for b in blocks:
    L.append(f"  gid={b.gid:>3} unit={b.page_idx} type={b.type:<14} lvl={b.text_level} "
             f"trusted={b.level_trusted} | {(b.text or b.table_body or b.img_path)[:52]!r}")

roots, oc = build_tree(sid, blocks, doc_title="自测样书")
L.append("\n--- 树 ---")


def walk(ns, d=0):
    for n in ns:
        L.append(f"  {'    ' * d}nid={n.nid:<6} L{n.level} marker={n.marker:<8} "
                 f"rank={n.rank:<3} key={n.key:<10} {n.title!r} "
                 f"[{n.n_chars}字/{n.n_blocks}块]")
        walk(n.children, d + 1)


walk(roots)
L.append(f"\nlevel_source={oc.get('level_source')}  node_total={oc.get('node_total')}")
L.append(f"level_stats={ {k: v['files_if_split'] for k, v in sorted(oc['level_stats'].items())} }")
L.append(f"level_stats 全量={oc['level_stats']}")
L.append(f"\ndropped={[(d['reason'], d['text'][:26]) for d in oc['dropped_headings']]}")

calib = calibrate(sid, blocks, [s.to_dict() for s in shards], anchors={})
L.append(f"\ncalib verdict={calib.verdict} locator={calib.locator_type} segs={len(calib.segments)}")

T.OUT_DIR = out_dir(sid)
OUT.write_text("\n".join(L), encoding="utf-8")
print("\n".join(L[:6]))
print(f"→ {OUT}")
