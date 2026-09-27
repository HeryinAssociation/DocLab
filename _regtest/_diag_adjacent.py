"""诊断：关闭联动时，改一条的层级，究竟有哪些**别的**条目跟着变？

穷举 415 条 × 6 级，用界面自己的 reflowPreview 算 depth，对比基准。
只读，不写任何状态。
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                      # noqa: E402
from core.outline import build_tree                   # noqa: E402
from core.pagecal import calibrate                    # noqa: E402
from core.project import iter_blocks, load_shards     # noqa: E402
from core.server import _flatten_tree                 # noqa: E402

SID = "中国数字人文发展报告"
NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"

JS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const m = src.match(/function reflowPreview\(\)\{[\s\S]*?\n\}/);
if (!m) { console.error('no reflowPreview'); process.exit(2); }
const reflowPreview = eval('(' + m[0] + ')');
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
function snap(levels){
  global.EDIT = { nodes: payload.nodes, levels: levels, level_stats: {} };
  const r = reflowPreview();
  return r;
}
const base = snap({});
const out = [];
for (const gid of payload.targets){
  for (let k = 1; k <= 6; k++){
    const lv = {}; lv[String(gid)] = k;
    const r = snap(lv);
    const moved = [];
    for (const n of payload.nodes){
      if (n.deleted) continue;
      const a = base.depth[n.gid], b = r.depth[n.gid];
      if (b !== a) moved.push([n.gid, a, b]);
    }
    out.push({ gid: gid, k: k, self: [base.depth[gid], r.depth[gid]], moved: moved });
  }
}
console.log(JSON.stringify({ base_depth: base.depth, rows: out }));
"""


def main():
    wd = work_dir(SID)
    tmp = ROOT / "_regtest"
    tmp.mkdir(exist_ok=True)
    pj = json.loads((wd / "project.json").read_text(encoding="utf-8"))
    title = pj.get("doc_title", "")

    blocks = iter_blocks(load_shards(wd))
    calib = calibrate(SID, blocks,
                      [s.to_dict() for s in load_shards(wd)], anchors={})
    tree, _ = build_tree(SID, blocks, doc_title=title, calib=calib)
    nodes = _flatten_tree([t.to_dict() for t in tree], [])
    by_gid = {n["gid"]: n for n in nodes}
    print(f"基准树 {len(nodes)} 节点")

    payload = {"nodes": nodes, "targets": [n["gid"] for n in nodes]}
    pay = tmp / "_adj_payload.json"
    pay.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    js = tmp / "_adj_probe.js"
    js.write_text(JS, encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(ROOT / "ui" / "index.html"), str(pay)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    res = json.loads(r.stdout)

    rows = res["rows"]
    base_depth = {int(k): v for k, v in res["base_depth"].items()}

    # ① 自动层级本身是不是严格嵌套？（depth == 自己的 level）
    mism = [(n["gid"], n["level"], base_depth[n["gid"]], n["title"][:18])
            for n in nodes if base_depth[n["gid"]] != n["level"]]
    print(f"\n① 无任何人工干预时，depth ≠ 自身 level 的条目：{len(mism)} / {len(nodes)}")
    for g, lv0, d0, t in mism[:8]:
        print(f"     gid={g} level={lv0} depth={d0} 「{t}」")

    # ② 邻居是不是变到**和我点的一模一样**的级数
    same_k = [x for x in rows
              if any(m[0] != x["gid"] and m[2] == x["k"] for m in x["moved"])]
    print(f"\n② 邻居被带到「和我点的一样的级数」的组合：{len(same_k)} / {len(rows)}")

    # ③ 一个具体例子的前后排布
    def show(gid, k):
        x = next(r for r in rows if r["gid"] == gid and r["k"] == k)
        print(f"\n③ 例：点 gid={gid}「{by_gid[gid]['title'][:16]}」的「{k}」——")
        i = next(i for i, n in enumerate(nodes) if n["gid"] == gid)
        for n in nodes[max(0, i - 3):i + 5]:
            d0 = base_depth[n["gid"]]
            d1 = next((m[2] for m in x["moved"] if m[0] == n["gid"]), d0)
            flag = "  ← 你点的" if n["gid"] == gid else ("  ← 邻居（变了）" if d1 != d0 else "")
            print(f"     gid={n['gid']:>4} 原L{n['level']}  L{d0}" +
                  (f"→L{d1}" if d1 != d0 else "   ") +
                  f"  「{n['title'][:22]}」{flag}")
    show(133, 3)
    show(307, 2)

    with_moved = [x for x in rows if any(m[0] != x["gid"] for m in x["moved"])]
    print(f"\n共 {len(rows)} 个「点某条设某级」组合，"
          f"其中 {len(with_moved)} 个会让**别的**条目层级变动 "
          f"({len(with_moved) * 100.0 / len(rows):.1f}%)")

    # 归纳：让邻居变动的组合有什么共同点
    kind = {}
    samples = {}
    for x in rows:
        others = [m for m in x["moved"] if m[0] != x["gid"]]
        if not others:
            continue
        me = by_gid[x["gid"]]
        key = (me["level"], x["k"], len(others))
        kind[key] = kind.get(key, 0) + 1
        samples.setdefault(key, x)

    print("\n共同点分布（原层级, 你点的级, 被带动的邻居条数）→ 出现次数")
    for key in sorted(kind, key=lambda t: -kind[t])[:14]:
        lv0, k, n_other = key
        x = samples[key]
        me = by_gid[x["gid"]]
        first = None
        for m in x["moved"]:
            if m[0] != x["gid"]:
                first = (m, by_gid[m[0]]["title"][:16], by_gid[m[0]]["level"])
                break
        print(f"  原L{lv0} 点{k} 带动{n_other}条 × {kind[key]:>4}  "
              f"| 例: gid={x['gid']}「{me['title'][:14]}」自己 "
              f"L{x['self'][0]}→L{x['self'][1]}；邻居 gid={first[0][0]}"
              f"「{first[1]}」(原L{first[2]}) L{first[0][1]}→L{first[0][2]}")

    # 反向：邻居**没有**被带动的情况占比（同样原层级、同样点法）
    print("\n同一「原层级+点击级」下，邻居有没有被带动的分布：")
    agg = {}
    for x in rows:
        me = by_gid[x["gid"]]
        others = [m for m in x["moved"] if m[0] != x["gid"]]
        k2 = (me["level"], x["k"])
        a, b = agg.get(k2, (0, 0))
        agg[k2] = (a + (1 if others else 0), b + 1)
    for k2 in sorted(agg):
        hit, tot = agg[k2]
        print(f"  原L{k2[0]} 点{k2[1]}: 带动邻居 {hit}/{tot}")


if __name__ == "__main__":
    raise SystemExit(main())
