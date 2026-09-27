"""人工新增标题断点 —— 后端行为的实机诊断（只读，绝不写盘）。

用法：python _regtest/_diag_added.py [source_id]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                      # noqa: E402
from core.outline import build_tree, node_key, walk_nodes   # noqa: E402
from core.pagecal import calibrate                    # noqa: E402
from core.project import iter_blocks, load_shards      # noqa: E402

SID = sys.argv[1] if len(sys.argv) > 1 else "认识论引论-夏甄陶.2b04297f3c"

wd = work_dir(SID)
pj = json.loads((wd / "project.json").read_text(encoding="utf-8"))
shards = load_shards(wd)
blocks = iter_blocks(shards)
calib = calibrate(SID, blocks, [s.to_dict() for s in shards], anchors={})
bmap = {b.gid: b for b in blocks}
total = sum(len(b.text or b.table_body or "") for b in blocks if b.is_content)


def run(added, levels=None, deleted=None):
    roots, oc = build_tree(SID, blocks, doc_title=pj.get("doc_title", ""), calib=calib,
                           manual_levels=levels or {}, manual_deleted=deleted or {},
                           manual_added=added)
    return [n for r in roots for n in walk_nodes(r)], oc


def own_sum(ns):
    return sum(n.n_chars - sum(c.n_chars for c in n.children) for n in ns)


def show(ns, gids=()):
    for n in ns:
        k = node_key(n)
        if k.startswith("add:") or n.gid_start in gids:
            own = n.n_chars - sum(c.n_chars for c in n.children)
            print(f"   key={k:<36} L{n.level} off={n.offset} "
                  f"gid={n.gid_start}-{n.gid_end} blocks={n.n_blocks} own={own:<7} "
                  f"flags={n.flags} {n.title[:26]}")


n0, o0 = run({})
print(f"源 {SID}")
print(f"基线：节点 {len(n0)}  Σown={own_sum(n0)}  全文={total}  "
      f"一致={'是' if own_sum(n0) == total else '否'}")
print(f"     manual_added={o0.get('manual_added')} count={o0.get('manual_added_count')}")

# 挑一个正文块：不是标题候选、位于某节点区间内部、文本较长
in_tree = {n.gid_start for n in n0}
body = next(b for b in blocks
            if b.type == "text" and b.gid not in in_tree and b.gid > 30
            and len(b.text) > 120)
print(f"\n目标块 gid={body.gid} {body.shard}p{body.page_idx} len={len(body.text)}")
print(f"   原文：{body.text[:80]}")


def owner_before(ns, gid):
    """gid 这个块**之前**归属的那个节点（看它名下块数与区间）。"""
    best = None
    for n in ns:
        if node_key(n).startswith("add:"):
            continue
        if n.gid_start <= gid <= n.gid_end:
            if best is None or n.gid_start > best.gid_start:
                best = n
    return best


print("\n== A. offset=0（块首立断点，最常见）==")
prev0 = owner_before(n0, body.gid)
nsA, ocA = run({str(body.gid): {"title": "测试断点", "level": 2, "offset": 0}})
show(nsA, (body.gid,))
prevA = owner_before(nsA, body.gid - 1)
print(f"   Σown={own_sum(nsA)} (应={total})  节点 {len(n0)}→{len(nsA)}")
print(f"   块 {body.gid} 从「{prev0.title[:16] if prev0 else '—'}」"
      f"(own {prev0.n_chars - sum(c.n_chars for c in prev0.children) if prev0 else 0}) "
      f"转给新增断点")

print("\n== B. offset=30（切进段落内部）==")
prevB = owner_before(n0, body.gid)
nsB, ocB = run({str(body.gid): {"title": "测试断点", "level": 2, "offset": 30}})
show(nsB, (body.gid,))
pb = owner_before(nsB, body.gid)
pb_own = (pb.n_chars - sum(c.n_chars for c in pb.children)) if pb else 0
pb0_own = (prevB.n_chars - sum(c.n_chars for c in prevB.children)) if prevB else 0
print(f"   Σown={own_sum(nsB)} (应={total})")
print(f"   前一个节点「{pb.title[:16] if pb else '—'}」own {pb0_own} → {pb_own}"
      f"（应少 {30} 字，即切在块内第 30 字）")

print("\n== C. 新增后又被删除（应完全回到基线）==")
nsC, _ = run({str(body.gid): {"title": "测试断点", "level": 2, "offset": 0}},
             deleted={f"add:{body.gid}": {"title": "测试断点"}})
print(f"   节点 {len(nsC)} (基线 {len(n0)})  Σown={own_sum(nsC)} (应={total})  "
      f"残留 add 节点={sum(1 for n in nsC if node_key(n).startswith('add:'))}")

print("\n== D. 人工改过级数时以 levels 为准 ==")
nsD, _ = run({str(body.gid): {"title": "测试断点", "level": 2, "offset": 0}},
             levels={f"add:{body.gid}": 3})
addD = next(n for n in nsD if node_key(n) == f"add:{body.gid}")
print(f"   added.level=2 但 levels[add:{body.gid}]=3 → 实际 L{addD.level} "
      f"(应=3)，offset={addD.offset}")

print("\n== E. 级数与邻居无关 ==")
base_lv = {node_key(n): n.level for n in n0}
lvA = {node_key(n): n.level for n in nsA}
moved = [k for k in base_lv if base_lv[k] != lvA.get(k) and not k.startswith("add:")]
print(f"   新增一条之后，别的条目级数被改动：{moved or '无'}")
