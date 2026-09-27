"""人工核定层：层级重排 + 页码锚点。

这一层是工作台的核心（景晔：「工作台的意义就在于你程序识别的目录、识别的页码
有误了之后，我们人类可以调整」），所以它的语义要钉死：

  层级：改一个节点的级，它的子树跟着走；**级数就是你给的那个数**，层级栈只用来
        定父子关系（谁是正文范围），不再反过来改写任何一条的级数。
  锚点：只影响**它自己及其之后**，第一个锚点之前的自动结果原样保留；
        两个锚点 offset 不同就自然断开，绝不替人插值。
  存储：人工结果单独落 manual_edits.json，重跑 pagecal/outline 时覆盖自动值；
        键是 Node.key（真实块＝str(gid)，合成节点＝"toc:…"），见 tests/test_key.py。

不发网络、不碰 MinerU，读的是本机已有的 _work 产物。
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import manual                                       # noqa: E402
from core.config import work_dir                              # noqa: E402
from core.outline import build_tree, walk_nodes               # noqa: E402
from core.pagecal import apply_anchors, calibrate, load_calibration   # noqa: E402
from core.project import iter_blocks, load_shards             # noqa: E402

SID = "中国数字人文发展报告"
NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
FAILS = []

# 界面上的层级重排必须和后端 reflow 算出同样的深度。
# 两份实现漂移的话，会出现「界面缩进显示 2 级、写进 md 的目录却是 3 级」这种
# 不报错但很难查的错 —— 和 pageMap 那套是同一个理由。
EXTRACT_REFLOW_JS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const m = src.match(/function reflowPreview\(\)\{[\s\S]*?\n\}/);
if (!m) { console.error('没找到 reflowPreview —— 界面重构了？同步改这个测试'); process.exit(2); }
global.EDIT = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const reflowPreview = eval('(' + m[0] + ')');
console.log(JSON.stringify(reflowPreview().depth));
"""


def frontend_depth(nodes: list, levels: dict) -> dict:
    tmp = ROOT / "_regtest"
    tmp.mkdir(exist_ok=True)
    js, payload = tmp / "_reflow_probe.js", tmp / "_reflow_edit.json"
    js.write_text(EXTRACT_REFLOW_JS, encoding="utf-8")
    payload.write_text(json.dumps({"nodes": nodes, "levels": levels}, ensure_ascii=False),
                       encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(ROOT / "ui" / "index.html"), str(payload)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"node 跑失败：{r.stderr}")
    return json.loads(r.stdout)


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def flat(roots):
    return [n for r in roots for n in walk_nodes(r)]


def main() -> int:
    wd = work_dir(SID)
    shards = load_shards(wd)
    blocks = iter_blocks(shards)
    calib0 = load_calibration(wd)

    print("== 层级重排 ==")
    r0, _ = build_tree(SID, blocks, doc_title="t", calib=calib0)
    f0 = flat(r0)
    top0 = sum(n.n_chars for n in r0)

    lv2 = next(n for n in f0 if n.level == 2)
    r1, _ = build_tree(SID, blocks, doc_title="t", calib=calib0,
                       manual_levels={str(lv2.gid_start): 1})
    f1 = flat(r1)
    n1 = next(n for n in f1 if n.gid_start == lv2.gid_start)
    check("升一级：节点总数不变（只是重挂，不增删）", len(f0) == len(f1))
    check("升一级：顶层 +1", len(r1) == len(r0) + 1, f"{len(r0)} → {len(r1)}")
    # 提升会把**紧随其后的同级节点**并入其下 —— 这是层级栈的必然结果，不是 bug：
    # 树结构由「层级序列」唯一决定，改了序列中间某一项，后面跟着重新归位。
    # 所以要断的是「原来的子树仍在它下面」，不是「子树原样不变」。
    old_kids = {c.gid_start for c in lv2.children}
    new_sub = {x.gid_start for x in walk_nodes(n1)}
    check("升一级：原子树仍在其下", old_kids <= new_sub, f"{len(old_kids)} 个原子节点")
    check("升一级：紧随其后的同级被并入（层级栈语义）",
          len(n1.children) >= len(lv2.children),
          f"子节点 {len(lv2.children)} → {len(n1.children)}")
    check("升一级：顶层内容总量守恒", sum(n.n_chars for n in r1) == top0)
    check("升一级：被改节点变成第 1 级", n1.level == 1)
    # 内容范围只会重划「被改节点」和「它的原父节点」这两个
    m0 = {n.gid_start: (n.gid_end, n.n_chars) for n in f0}
    m1 = {n.gid_start: (n.gid_end, n.n_chars) for n in f1}
    diff = {g for g in m0 if m0[g] != m1.get(g)}
    check("升一级：内容范围只重划 2 个节点", len(diff) == 2, f"实际 {len(diff)}")

    top_second = r0[1]
    r2, _ = build_tree(SID, blocks, doc_title="t", calib=calib0,
                       manual_levels={str(top_second.gid_start): 2})
    check("降一级：顶层 -1", len(r2) == len(r0) - 1, f"{len(r0)} → {len(r2)}")

    # 跳级：**不再压平**。你给几级就是几级 —— 人工指定是权威。
    # 「实际深度」是从 nid 点数派生的，它反映结构，不该反过来改写你给的数。
    deep = next(n for n in f0 if n.level == 1)
    r3, _ = build_tree(SID, blocks, doc_title="t", calib=calib0,
                       manual_levels={str(deep.gid_start): 3})
    n3 = next(n for n in flat(r3) if n.gid_start == deep.gid_start)
    check("跳级不再被压平：设为第 3 级就是第 3 级", n3.level == 3,
          f"设为 3 → 得到 L{n3.level}")

    # ★ 景晔报过两次的那条：改一条，**邻居的级数不许跟着变**。
    #   以前 level 由 assign_ids 按 nid 点数派生 —— 把前面一条提到更浅的级，
    #   后面整串的深度跟着上浮（实测 2490 个组合里 881 个会带动邻居）。
    #   现在 level 恒等于「人给的 ?? 自动值」，别的条目一律不动。
    lv0 = {n.gid_start: n.level for n in f0}
    moved = [(n.gid_start, lv0[n.gid_start], n.level) for n in flat(r3)
             if n.gid_start != deep.gid_start and n.level != lv0[n.gid_start]]
    check("改一条不带动邻居的级数", not moved,
          f"{len(moved)} 个邻居被改写，前 5: {moved[:5]}")

    print("\n== 条目删除 ==")
    # 顺带钉住 parent 字段：建树那趟赋的是空串（nid 那时还没编），必须由 assign_ids 回填。
    # 这字段以前没人读，所以能一直错着 —— 但它是写进 outline.json 的，别处会当真。
    check("顶层 parent 为空、非顶层有父",
          all(n.parent == "" for n in r0)
          and all(n.parent for n in f0 if n.level > 1))
    # 删掉一个中间层节点：它自己消失，子条上提一级挂到它的父级，正文一字不丢。
    # 这是景晔选的语义（「只删本条·子条上提」），必须钉死：删错了条目不能连带丢内容。
    victim = next(n for n in f0 if n.level >= 2 and n.children)
    parent0 = next(n for n in f0 if n.nid == victim.parent)
    r4, _ = build_tree(SID, blocks, doc_title="t", calib=calib0,
                       manual_deleted={str(victim.gid_start): {"title": victim.title}})
    f4 = flat(r4)
    check("删除：节点数 -1", len(f4) == len(f0) - 1, f"{len(f0)} → {len(f4)}")
    check("删除：该条目已不在树里",
          all(n.gid_start != victim.gid_start for n in f4))
    p4 = next(n for n in f4 if n.gid_start == parent0.gid_start)
    sub4 = {x.gid_start for x in walk_nodes(p4)}
    check("删除：原子条上提到父级名下",
          {c.gid_start for c in victim.children} <= sub4,
          f"原 {len(victim.children)} 个子条")
    check("删除：原子条成为父级的**直接**子节点（不是只留在子树里）",
          {c.gid_start for c in victim.children} <= {c.gid_start for c in p4.children})
    check("删除：正文一字不丢（顶层字数守恒）",
          sum(n.n_chars for n in r4) == top0,
          f"{top0} → {sum(n.n_chars for n in r4)}")
    # 父级的 n_chars 是**含后代累计**的，所以删掉中间节点后它的总数不该变：
    # 那些字本来就通过 victim 计进过它，现在只是改成直接归它。变的只有归属路径。
    check("删除：父级覆盖范围不变",
          (p4.gid_start, p4.gid_end) == (parent0.gid_start, parent0.gid_end),
          f"{parent0.gid_start}-{parent0.gid_end} → {p4.gid_start}-{p4.gid_end}")

    # 只删、不定级 —— 删除必须能单独生效（reflow 的早退条件不能只认 levels）
    r5, _ = build_tree(SID, blocks, doc_title="t", calib=calib0,
                       manual_deleted={str(victim.gid_start): {}})
    check("删除：无人工定级也能生效", len(flat(r5)) == len(f0) - 1)

    print("\n== 页码锚点 ==")
    # 基准必须用**干净自动校准**，不能拿 load_calibration()：后者是落盘那一份，
    # 操作者在界面上存过的锚点已经烘进去了。用它当基准，测的就不是 apply_anchors
    # 的规则，而是「他今天存过什么」—— 他一按「应用并重算」，这套断言就无端变红。
    def auto_calib():
        return calibrate(SID, blocks, [s.to_dict() for s in shards], anchors={})

    c = auto_calib()
    before = [(s.shard, s.kind, s.page_idx_start, s.page_idx_end, s.offset) for s in c.segments]
    apply_anchors(c, {"P1": [{"page_idx": 100, "printed": 90}]})
    segs = [s for s in c.segments if s.shard == "P1"]
    check("锚点之后用人工 offset", c.locator("P1", 100) == "90")
    check("锚点之后逐页递加", c.locator("P1", 150) == "140" and c.locator("P1", 199) == "189")
    check("锚点之前保留自动结果（不整段作废）",
          c.locator("P1", 50) == "38" and c.locator("P1", 15) == "3")
    check("前置页仍标 front，不冒充正文页码", c.locator("P1", 5) == "front-1")
    check("人工段标 confidence=manual",
          any(s.confidence == "manual" for s in segs))
    check("跨分片不受影响", c.locator("P2", 0) == "188")
    check("人工锚点把定位能力提到 paginated", c.locator_type == "paginated")

    c2 = auto_calib()
    apply_anchors(c2, {"P2": [{"page_idx": 0, "printed": 200},
                              {"page_idx": 50, "printed": 255}]})
    check("两锚点各自递加、不插值",
          c2.locator("P2", 49) == "249" and c2.locator("P2", 50) == "255"
          and c2.locator("P2", 199) == "404")

    c3 = auto_calib()
    apply_anchors(c3, {})
    check("零锚点 = 完全不动", [(s.shard, s.kind, s.page_idx_start, s.page_idx_end, s.offset)
                               for s in c3.segments] == before)

    print("\n== 界面重排 vs 后端重排 ==")
    # 键用 Node.key：真实块 = str(gid)，从印刷目录补出的合成节点 = "toc:…"。
    # 从前一律用 str(gid_start)，而合成节点的 gid 是借的 —— 它会和同页那个真实块
    # 撞在同一个数字上，人工改一条连带改到另一条（见 tests/test_key.py）。
    def K(n):
        return n.key or str(n.gid_start)

    nodes_flat = [{"key": K(n), "gid": n.gid_start, "level": n.level, "title": n.title}
                  for n in f0]
    cases_lv = {
        "升一级": {K(lv2): 1},
        "降一级": {K(r0[1]): 2},
        "跳级": {K(r0[0]): 3},
        "多点混合": {K(lv2): 1, K(r0[2]): 2},
    }
    for name, lv in cases_lv.items():
        fe = frontend_depth(nodes_flat, lv)
        rk, _ = build_tree(SID, blocks, doc_title="t", calib=calib0, manual_levels=lv)
        be = {K(n): n.level for r in rk for n in walk_nodes(r)}
        bad = [g for g in set(fe) | set(be) if fe.get(g) != be.get(g)]
        check(f"界面/后端一致 — {name}", not bad,
              f"{len(bad)} 处不一致，前 4: {bad[:4]}" if bad else "")

    # 删除也要两端一致：界面把被删条目标成 deleted 后排除出层级栈，
    # 后端在 reflow 里过滤 —— 两边若不同步，删除后的缩进会整体错位。
    nodes_del = [dict(x) for x in nodes_flat]
    for x in nodes_del:
        if x["key"] == K(victim):
            x["deleted"] = True
    fe = frontend_depth(nodes_del, {})
    rk, _ = build_tree(SID, blocks, doc_title="t", calib=calib0,
                       manual_deleted={K(victim): {}})
    be = {K(n): n.level for r in rk for n in walk_nodes(r)}
    bad = [g for g in set(fe) | set(be) if fe.get(g) != be.get(g)]
    check("界面/后端一致 — 删除一条", not bad,
          f"{len(bad)} 处不一致，前 4: {bad[:4]}" if bad else "")
    check("界面/后端一致 — 被删条目两边都消失",
          K(victim) not in fe and K(victim) not in be)

    print("\n== 存储 ==")
    tmp = ROOT / "_regtest" / "manual_probe"
    tmp.mkdir(parents=True, exist_ok=True)
    manual.set_level(tmp, 82, 2)
    manual.set_anchor(tmp, "P1", 100, 90)
    d = manual.load(tmp)
    check("定级落盘", d["levels"].get("82") == 2)
    check("锚点落盘", d["anchors"]["P1"][0]["printed"] == 90)
    try:
        manual.set_level(tmp, 1, 99)
        check("非法层级被拒", False)
    except ValueError:
        check("非法层级被拒", True)

    manual.set_deleted(tmp, 82, {"title": "页眉残留", "marker": "chapter", "chars": 12})
    d = manual.load(tmp)
    check("删除落盘（带快照，供「已删」列表与恢复用）",
          d["deleted"]["82"]["title"] == "页眉残留")
    check("删掉的条目不再保留定级", d["levels"].get("82") is None)
    check("summary 认删除数", manual.summary(tmp)["deleted"] == 1)
    manual.set_level(tmp, 82, 3)
    manual.set_deleted(tmp, 82, {"title": "页眉残留"})
    check("对已删条目再定级后重删，定级仍被清掉",
          manual.load(tmp)["levels"].get("82") is None)
    manual.clear_deleted(tmp, 82)
    check("恢复后从删除表移除", "82" not in manual.load(tmp)["deleted"])

    manual.clear_level(tmp, 82)
    manual.clear_anchor(tmp, "P1", 100)
    d = manual.load(tmp)
    check("撤销后回到空", not d["levels"] and not d["anchors"] and not d["deleted"])
    # 收尾：不留痕迹
    (tmp / "manual_edits.json").unlink(missing_ok=True)
    tmp.rmdir()

    print("\n" + ("全通过" if not FAILS else f"{len(FAILS)} 项失败：{FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
