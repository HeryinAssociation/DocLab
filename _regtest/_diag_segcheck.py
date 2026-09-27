"""取块算法的 A/B：证明「无人工断点」时新算法与旧算法逐块等价。

旧：seg = [g for g in range(g0, g1+1) if g in by_gid]
新：seg 一直取到「下一个断点之前」，按 (gid, offset) 截断

只要所有 offset 都是 0、且相邻导出节点的 gid 区间严丝合缝，两者就完全一致。
这里用两本书的全量真实数据把它验到底。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                      # noqa: E402
from core.exporter import frontier                    # noqa: E402
from core.outline import load_outline                 # noqa: E402
from core.project import iter_blocks, load_shards      # noqa: E402

SIDS = ["中国数字人文发展报告", "认识论引论-夏甄陶.2b04297f3c"]
FAIL = 0

for SID in SIDS:
    wd = work_dir(SID)
    blocks = iter_blocks(load_shards(wd))
    by_gid = {b.gid: b for b in blocks}
    oc = load_outline(wd)

    for depth in (1, 2, 3, 0):
        nodes = frontier(oc["tree"], depth)
        if not nodes:
            continue
        marks = sorted({(int(n["gid_start"]), int(n.get("offset") or 0)) for n in nodes})
        nxt = {m: (marks[i + 1] if i + 1 < len(marks) else None)
               for i, m in enumerate(marks)}
        bad, gap = [], []
        for n in nodes:
            g0, g1 = int(n["gid_start"]), int(n["gid_end"])
            lo = int(n.get("offset") or 0)
            stop = nxt.get((g0, lo))
            old = [g for g in range(g0, g1 + 1) if g in by_gid]
            hi = g1
            if stop is not None and stop[1] > 0 and stop[0] > g1:
                hi = stop[0]          # 只有块内断点才扩右端（与 exporter 同一规则）
            new = []
            for g in range(g0, hi + 1):
                if g not in by_gid:
                    continue
                if stop is not None and g == stop[0]:
                    if stop[1] == 0:
                        break
                    new.append(g)
                    break
                new.append(g)
            if old != new:
                bad.append((n.get("title", "")[:20], old[:6], new[:6]))
            if stop is not None and stop[0] != g1 + 1:
                gap.append((n.get("title", "")[:20], g1, stop[0]))
        print(f"{SID} L{depth}: {len(nodes)} 个导出节点，"
              f"取块不一致 {len(bad)} 个，区间不衔接 {len(gap)} 个")
        for t, o, nw in bad[:3]:
            print(f"    不一致 「{t}」 旧{o} 新{nw}")
        for t, g1, s0 in gap[:3]:
            print(f"    不衔接 「{t}」 g1={g1} 下一断点={s0}")
        FAIL += len(bad)
print("\n" + ("取块算法 A/B 完全等价：无人工断点时导出逐块一致"
              if not FAIL else f"{FAIL} 处不一致 —— 有回归"))
