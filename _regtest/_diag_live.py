"""拿景晔当前真实的 manual_edits 跑一遍界面预览，核对：只改了他点的那几条。"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                      # noqa: E402
from core.manual import load as load_manual           # noqa: E402
from core.outline import build_tree                   # noqa: E402
from core.pagecal import calibrate                    # noqa: E402
from core.project import iter_blocks, load_shards      # noqa: E402
from core.server import _flatten_tree                  # noqa: E402

SID = "中国数字人文发展报告"
NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"

JS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const m = src.match(/function reflowPreview\(\)\{[\s\S]*?\n\}/);
const reflowPreview = eval('(' + m[0] + ')');
const p = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
global.EDIT = { nodes: p.nodes, levels: p.levels, level_stats: {} };
const r = reflowPreview();
console.log(JSON.stringify({ depth: r.depth, ic: r.ic }));
"""


def main():
    wd = work_dir(SID)
    tmp = ROOT / "_regtest"
    pj = json.loads((wd / "project.json").read_text(encoding="utf-8"))
    blocks = iter_blocks(load_shards(wd))
    shards = load_shards(wd)
    calib = calibrate(SID, blocks, [s.to_dict() for s in shards], anchors={})
    _, outline = build_tree(SID, blocks, doc_title=pj.get("doc_title", ""), calib=calib)
    nodes = _flatten_tree([t for t in outline["tree"]], [])

    man = load_manual(wd)
    levels = {str(k): int(v) for k, v in (man.get("levels") or {}).items()}
    print("你现在的核定：", json.dumps(levels, ensure_ascii=False))

    pay = tmp / "_live_payload.json"
    pay.write_text(json.dumps({"nodes": nodes, "levels": levels}, ensure_ascii=False),
                   encoding="utf-8")
    js = tmp / "_live_probe.js"
    js.write_text(JS, encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(ROOT / "ui" / "index.html"), str(pay)],
                       capture_output=True, text=True, encoding="utf-8")
    res = json.loads(r.stdout)

    by_gid = {n["gid"]: n for n in nodes}
    bad = []
    for n in nodes:
        d = res["depth"][str(n["gid"])] if str(n["gid"]) in res["depth"] else res["depth"][n["gid"]]
        want = levels.get(str(n["gid"]), n["level"])
        if d != want:
            bad.append((n["gid"], n["title"][:20], want, d))
    print(f"渲染层级 ≠ 应有层级 的条目：{len(bad)}")
    for b in bad[:10]:
        print("   ", b)

    print("\n80 前后的实际渲染：")
    i0 = next(i for i, n in enumerate(nodes) if n["gid"] == 80)
    for n in nodes[i0 - 2:i0 + 5]:
        g = n["gid"]
        d = res["depth"][g] if g in res["depth"] else res["depth"][str(g)]
        ic = res["ic"][g] if g in res["ic"] else res["ic"][str(g)]
        tag = " ← 人工" if str(g) in levels else ""
        print(f"    gid={g:>5} 自动L{n['level']} → 显示L{d}  {ic:>9,}字  "
              f"「{n['title'][:26]}」{tag}")


if __name__ == "__main__":
    raise SystemExit(main())
