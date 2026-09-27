"""人工核定的主键必须是**唯一**的 —— 否则「改一条」会连带改到另一条。

出事的场景（景晔报的「关了联动，相邻条目还是跟着动」）：
  《认识论引论》里印刷目录有「第一章」，正文里 MinerU 没识别出来，于是
  `repair_from_toc` 补了一个**合成节点**。补章要插在目录标注的那一页开头，
  所以合成块借用了「该页第一个真实块」的 gid —— 而那一页第一个块恰好就是
  「第一章」自己的 gid。两条节点的 gid_start 都是 19，而人工核定当时按
  gid 存键 → manual_edits["19"] 一改，两条一起变。

修法：Node 加 `key`。
  真实块   → str(gid)（**与历史数据逐字一致，老 manual_edits 免迁移**）
  合成节点 → "toc:<shard>:<gid>:<标题>"（唯一）

这个测试钉住三件事：
  1. 构建出来的树里主键唯一；
  2. 改任一条的键，另一条的级数**一个字节都不动**；
  3. 老数据（纯数字键）仍然命中它本来那条 —— 不能因为修 bug 把人已有的核定弄丢。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                                  # noqa: E402
from core.manual import norm_key                                  # noqa: E402
from core.outline import build_tree, is_synth_key, node_key, walk_nodes   # noqa: E402
from core.pagecal import calibrate                                 # noqa: E402
from core.project import iter_blocks, load_shards                  # noqa: E402
from core.server import _deleted_row, _flatten_tree                # noqa: E402

SID = "认识论引论-夏甄陶.2b04297f3c"      # 有合成补章、且与真实块撞 gid 的那一本
FAILS = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def load_ctx(sid):
    wd = work_dir(sid)
    pj = json.loads((wd / "project.json").read_text(encoding="utf-8"))
    blocks = iter_blocks(load_shards(wd))
    shards = load_shards(wd)
    return wd, pj, blocks, shards, calibrate(sid, blocks,
                                             [s.to_dict() for s in shards], anchors={})


def levels_of(sid, ctx, levels, deleted=None):
    _, pj, blocks, _shards, calib = ctx
    roots, _ = build_tree(sid, blocks, doc_title=pj.get("doc_title", ""), calib=calib,
                          manual_levels=levels or {}, manual_deleted=deleted or {})
    return {node_key(n): n.level for r in roots for n in walk_nodes(r)}


def main() -> int:
    wd = work_dir(SID)
    if not (wd / "outline.json").is_file():
        print(f"  跳过：本机没有 {SID} 的产物")
        return 0
    ctx = load_ctx(SID)

    print("\n== 主键唯一 ==")
    roots, oc = build_tree(SID, ctx[2], doc_title=ctx[1].get("doc_title", ""),
                           calib=ctx[4], manual_levels={}, manual_deleted={})
    nodes = [n for r in roots for n in walk_nodes(r)]
    keys = [node_key(n) for n in nodes]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    check("树上没有重复主键", not dup, f"重复 {dup[:5]}" if dup else f"{len(keys)} 条")

    # 找出「同 gid 但主键不同」的那一对 —— 这正是出事的地方
    by_gid = {}
    for n in nodes:
        by_gid.setdefault(n.gid_start, []).append(n)
    pairs = {g: ns for g, ns in by_gid.items() if len(ns) > 1}
    check("本机这本书确实存在同 gid 的一对（回归样本有效）", bool(pairs),
          f"gid={sorted(pairs)[:4]}" if pairs else "样本失效，需换一本书验")

    if not pairs:
        print("\n无法继续验证键隔离：没有同 gid 的样本")
        return 1 if FAILS else 0

    g = sorted(pairs)[0]
    a, b = pairs[g][0], pairs[g][1]
    check("同 gid 两条的 key 不同", node_key(a) != node_key(b),
          f"gid={g}: {node_key(a)!r} / {node_key(b)!r}")
    synth = [n for n in (a, b) if is_synth_key(node_key(n))]
    real = [n for n in (a, b) if not is_synth_key(node_key(n))]
    check("其中恰有一条是目录补出的合成节点", len(synth) == 1 and len(real) == 1,
          f"合成={[node_key(n) for n in synth]} 真实={[node_key(n) for n in real]}")
    check("真实块的 key 就是 str(gid)（老数据据此免迁移）",
          real and node_key(real[0]) == str(real[0].gid_start))

    print("\n== 改一条，另一条一个字节都不动 ==")
    base = levels_of(SID, ctx, {})
    ka, kb = node_key(a), node_key(b)
    lv_a0, lv_b0 = base[ka], base[kb]

    after_a = levels_of(SID, ctx, {ka: 3})
    moved_a = [k for k in base if base[k] != after_a[k]]
    check(f"只改 {ka!r} → 只有它变", moved_a == [ka],
          f"变了 {len(moved_a)} 条：{moved_a[:4]}")
    check("另一条级数未动", after_a[kb] == lv_b0, f"{lv_b0} → {after_a[kb]}")

    after_b = levels_of(SID, ctx, {kb: 3})
    moved_b = [k for k in base if base[k] != after_b[k]]
    check(f"只改 {kb!r} → 只有它变", moved_b == [kb],
          f"变了 {len(moved_b)} 条：{moved_b[:4]}")
    check("另一条级数未动", after_b[ka] == lv_a0, f"{lv_a0} → {after_b[ka]}")

    print("\n== 主键规范化（写法不同不等于两条） ==")
    check("int 与数字串归一", norm_key(19) == norm_key("19") == norm_key("019"))
    check("负号数字串仍归一", norm_key("-3") == "-3")
    check("合成键原样保留", norm_key(kb) == kb if is_synth_key(kb) else True)
    try:
        norm_key("")
        check("空键被拒", False)
    except ValueError:
        check("空键被拒", True)
    try:
        norm_key(True)
        check("布尔被拒（True 会变成 '1' 撞上真实块）", False)
    except ValueError:
        check("布尔被拒（True 会变成 '1' 撞上真实块）", True)

    print("\n== 界面的键（_flatten_tree）与后端同源 ==")
    flat = _flatten_tree([r.to_dict() for r in roots], [])
    fkeys = [n["key"] for n in flat]
    check("_flatten_tree 每条都带 key", all(fkeys) and len(fkeys) == len(nodes))
    check("_flatten_tree 的 key 无重复", len(set(fkeys)) == len(fkeys))
    check("同 gid 的两条在界面数据里也是两条",
          sum(1 for n in flat if n["gid"] == g) == 2)
    row = next(n for n in flat if n["key"] == kb)
    check("已删行插回原位时用 gid 排序，不解析字符串键",
          _deleted_row(kb, {"gid": g, "title": "x"})["gid"] == g)
    check("老记录没存 gid 时，能从数字键退化解析",
          _deleted_row(str(g), {"title": "x"})["gid"] == g)

    print("\n== 真实数据不被改动 ==")
    before = (wd / "manual_edits.json")
    sha = None
    if before.is_file():
        import hashlib
        sha = hashlib.sha256(before.read_bytes()).hexdigest()
        d = json.loads(before.read_text(encoding="utf-8"))
        print(f"  manual_edits.json: {len(d.get('levels', {}))} 处定级 · "
              f"{len(d.get('deleted', {}))} 条删除 · sha256={sha[:12]}…")
    check("本测试没有写过 manual_edits.json（只读）",
          (not before.is_file()) or
          hashlib.sha256(before.read_bytes()).hexdigest() == sha)

    print("\n" + ("全通过：一条标题一把钥匙，改谁就只动谁"
                  if not FAILS else f"{len(FAILS)} 项失败：{FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
