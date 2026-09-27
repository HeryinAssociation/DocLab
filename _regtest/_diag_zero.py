# -*- coding: utf-8 -*-
"""零回归核对：当前人工核定状态下重建的树，与既有 outline.json 是否逐条一致。只读。"""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import manual
from core.config import work_dir
from core.outline import build_tree, node_key, walk_nodes, write_outline
from core.pagecal import calibrate, load_calibration
from core.project import iter_blocks, load_shards

SID = "中国数字人文发展报告"
wd = work_dir(SID)
shards = load_shards(wd)
blocks = iter_blocks(shards)
calib = calibrate(SID, blocks, [s.to_dict() for s in shards],
                  anchors=(manual.anchors_for(wd) if hasattr(manual, "anchors_for") else {}))
lv = manual.levels_for(wd)
dl = manual.deleted_for(wd)
ad = manual.added_for(wd)
print("人工：levels", len(lv), "deleted", len(dl), "added", len(ad))

roots, oc = build_tree(SID, blocks, doc_title="中国数字人文发展报告", calib=calib,
                       manual_levels=lv, manual_deleted=dl, manual_added=ad)
new = {node_key(n): (n.level, n.title, n.n_chars, n.n_blocks, n.marker, n.gid_start, n.gid_end)
       for r in roots for n in walk_nodes(r)}

old_oc = json.loads((wd / "outline.json").read_text(encoding="utf-8"))
old = {}
def walko(ns):
    for n in ns:
        old[n.get("key") or str(n["gid_start"])] = (
            n["level"], n["title"], n["n_chars"], n["n_blocks"], n["marker"],
            n["gid_start"], n["gid_end"])
        walko(n.get("children") or [])
walko(old_oc["tree"])

print("旧节点数", len(old), " 新节点数", len(new))
only_old = sorted(set(old) - set(new))
only_new = sorted(set(new) - set(old))
print("只在旧:", only_old[:10])
print("只在新:", only_new[:10])
diff = [(k, old[k], new[k]) for k in old if k in new and old[k] != new[k]]
print("字段不同的条目:", len(diff))
for d in diff[:10]:
    print("   ", d)
print("level_stats 旧:", {k: v["nodes"] for k, v in sorted(old_oc["level_stats"].items(), key=lambda x: int(x[0]))})
print("level_stats 新:", {k: v["nodes"] for k, v in sorted(oc["level_stats"].items(), key=lambda x: int(x[0]))})
print("node_total 旧", old_oc["node_total"], "新", oc["node_total"])
print("manual_added_count 新", oc["manual_added_count"])
