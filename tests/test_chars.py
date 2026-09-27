"""界面算的「这一节有多少字」必须等于后端应用之后写进目录的字节数。

为什么单独测：
  改级的时候，界面要让人**当场**看见「这一节从多少字变成多少字」——
  这是判断切出来的文件多大的唯一依据。而这个数在界面上是前端算的
  （每点一次都往返后端重跑建树，没法用），后端的 reflow + attach_content
  才是真正落盘的那份。两份一旦漂移，就会出现「界面写着 16 万、
  应用之后是 4 万」这种不报错、只是悄悄错的局面。
  所以这里把 index.html 的 reflowPreview() 抽出来，和后端对同一组
  人工核定跑，要求**逐条相同**。

口径：一条标题的字数 ＝ 它 → 「下一个同级或更高级标题」之前的全部正文，
     也就是它在新树里整棵子树（含自身）的自有正文之和。
     不能拿 n_chars 直接按新树重加 —— n_chars 是含后代的，会重复计算。
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                        # noqa: E402
from core.outline import build_tree                     # noqa: E402
from core.pagecal import calibrate                      # noqa: E402
from core.project import iter_blocks, load_shards       # noqa: E402
from core.server import _flatten_tree                   # noqa: E402

SID = "中国数字人文发展报告"
NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"

EXTRACT_JS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const m = src.match(/function reflowPreview\(\)\{[\s\S]*?\n\}/);
if (!m) { console.error('没找到 reflowPreview —— 界面重构了？同步改这个测试'); process.exit(2); }
const reflowPreview = eval('(' + m[0] + ')');
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = {};
for (const c of payload.cases) {
  global.EDIT = { nodes: payload.nodes, levels: c.levels || {}, level_stats: {} };
  const r = reflowPreview();
  const ic = {}, depth = {};
  /* 键必须是 n.key，不能是 n.gid：目录补出来的合成节点借用了同页真实块的 gid，
     用 gid 会在同一格上互相覆盖。/ui 和后端 manual_edits 都是 key 口径。 */
  for (const n of payload.nodes) { ic[n.key] = r.ic[n.key]; depth[n.key] = r.depth[n.key]; }
  out[c.name] = { ic, depth };
}
console.log(JSON.stringify(out));
"""

# 多选批量定级：完全按用户选择的条目改，不做任何「自动识别为同批」的猜测。
# 同时钉住「相邻条目不许被带动」——这是景晔反复报的那个毛病。
MULTI_JS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function grab(re, name){ const m = src.match(re); if(!m){ console.error('没找到 '+name); process.exit(2);} return m[0]; }
const reflowPreview = eval('(' + grab(/function reflowPreview\(\)\{[\s\S]*?\n\}/, 'reflowPreview') + ')');
const setLevel = eval('(' + grab(/async function setLevel\(i, k\)\{[\s\S]*?\n\}/, 'setLevel') + ')');
const applyLevels = eval('(' + grab(/async function applyLevels\(keys, k\)\{[\s\S]*?\n\}/, 'applyLevels') + ')');
const bumpSel = eval('(' + grab(/async function bumpSel\(delta\)\{[\s\S]*?\n\}/, 'bumpSel') + ')');
const curLevel = eval('(' + grab(/function curLevel\(key\)\{[\s\S]*?\n\}/, 'curLevel') + ')');
let CAP = null;
global.markDirty = () => {}; global.renderTreeList = () => {}; global.log = () => {};
global.titleOfKey = (k) => k;        // 探针里不需要真标题
global.CUR = 'S';
global.post = async (p, b) => { CAP = { p, b }; return { ok: true }; };

/* 1..6 号是普通条目。
   7 / 8 是**同 gid、不同 key** 的一对 —— 复刻《认识论引论》里
   「第一章」(真实, gid=19) 与「一认识论的对象」(合成, gid=19) 的处境：
   用 gid 当键时点 7 会连带改 8，这是已经发生过的数据事故。 */
const NODES = [
  { key: '1', gid: 1, level: 1, title: 'A' },
  { key: '2', gid: 2, level: 2, title: 'B' },
  { key: '3', gid: 3, level: 3, title: 'C' },
  { key: '4', gid: 4, level: 3, title: 'D' },
  { key: '5', gid: 5, level: 2, title: 'E' },
  { key: '6', gid: 6, level: 1, title: 'F' },
  { key: '19', gid: 19, level: 1, title: '第一章' },
  { key: 'toc:P1:19:一认识论的对象', gid: 19, level: 1, title: '一认识论的对象' },
];
(async () => {
  const out = {};
  async function run(name, sel, i, k){
    global.EDIT = { nodes: NODES.map(n => Object.assign({}, n)), levels: {} };
    global.SEL = new Set(sel);
    CAP = null;
    await setLevel(i, k);
    out[name] = { pairs: Object.keys(CAP.b.pairs).sort(), sel: [...SEL].sort() };
  }
  async function bump(name, sel, delta){
    global.EDIT = { nodes: NODES.map(n => Object.assign({}, n)), levels: {} };
    global.SEL = new Set(sel);
    CAP = null;
    await bumpSel(delta);
    out[name] = { pairs: Object.keys(CAP.b.pairs).sort(), vals: CAP.b.pairs };
  }
  await run('没勾选：只改这一条', [], 1, 1);   // 第 1 行 → key '2'
  await run('勾了 3 条，点其中一条 → 只改这 3 条', ['2','3','4'], 2, 1);
  await run('勾了 3 条，点**没勾**的第 5 行 → 只改第 5 条', ['2','3','4'], 4, 1);
  await run('共用 gid 的两条：点真实块不动合成块', [], 6, 2);
  await run('共用 gid 的两条：点合成块不动真实块', [], 7, 3);
  await bump('整体提一级', ['2','3','4'], -1);
  await bump('整体降一级', ['1','6'], 1);
  console.log(JSON.stringify(out));
})();
"""


def build_api_nodes(levels=None, deleted=None, blocks=None, calib=None, title=""):
    """后端真值：跑一遍建树，再按界面拿到的形状摊平。"""
    tree, _ = build_tree(SID, blocks, doc_title=title, calib=calib,
                         manual_levels=levels, manual_deleted=deleted)
    return _flatten_tree([t.to_dict() for t in tree], [])


def run_node(js_text, payload, tmp, out_name):
    js = tmp / out_name
    js.write_text(js_text, encoding="utf-8")
    pay = tmp / (out_name + ".payload.json")
    pay.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(ROOT / "ui" / "index.html"), str(pay)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"node 跑失败：{r.stderr}")
    return r.stdout


def main() -> int:
    wd = work_dir(SID)
    tmp = ROOT / "_regtest"
    tmp.mkdir(exist_ok=True)
    pj = json.loads((wd / "project.json").read_text(encoding="utf-8"))
    title = pj.get("doc_title", "")

    blocks = iter_blocks(load_shards(wd))
    calib = calibrate(SID, blocks,
                      [s.to_dict() for s in load_shards(wd)], anchors={})

    base_nodes = build_api_nodes(blocks=blocks, calib=calib, title=title)
    by_key = {n["key"]: n for n in base_nodes}
    keys = [n["key"] for n in base_nodes]
    assert len(keys) == len(set(keys)), \
        f"主键必须唯一，实测 {len(keys)} 条里有 {len(keys) - len(set(keys))} 个重复"
    print(f"  基准树 {len(base_nodes)} 节点（主键唯一）"
          f"，自有正文字数合计 = {sum(n['own_chars'] for n in base_nodes):,}")

    # 挑几个真实条目做定级场景：最大的那个章级条目 + 它后面第一个下一级条目
    l1 = [n for n in base_nodes if n["level"] == 1]
    l2 = [n for n in base_nodes if n["level"] == 2]
    big = max((n for n in l1 if n["gid"] > 0), key=lambda n: n["chars"])
    mate = next((n for n in l2 if n["gid"] > big["gid"]), None)
    print(f"  拿 key={big['key']}「{big['title'][:18]}」(L1, {big['chars']:,}字)"
          + (f" 与 key={mate['key']}「{mate['title'][:18]}」(L2)" if mate else ""))

    cases = [("无人工定级", {}, None)]
    cases.append((f"把 L2 的 {big['key']} 提为第 1 级", {big["key"]: 1}, None))
    if mate:
        cases.append(("连同它的同级兄弟一起提为第 1 级",
                      {big["key"]: 1, mate["key"]: 1}, None))
    cases.append((f"删掉 {big['key']}", {}, {big["key"]: dict(by_key[big["key"]])}))

    fails = 0
    print("\n== 界面预览 vs 后端落盘 ==")
    for name, levels, deleted in cases:
        nodes = base_nodes if (not levels and not deleted) else \
            build_api_nodes(levels=levels, deleted=deleted, blocks=blocks,
                            calib=calib, title=title)
        payload = {"nodes": nodes,
                   "cases": [{"name": "c", "levels": levels}]}
        fe = json.loads(run_node(EXTRACT_JS, payload, tmp, "_chars_probe.js"))["c"]

        # 后端：按**这次核定**重新建树后，各条的字数就是应用之后的值
        be = {n["key"]: n["chars"] for n in nodes}
        bad = [(k, fe["ic"].get(k), be[k]) for k in be
               if fe["ic"].get(k) != be[k]]

        status = "PASS" if not bad else "FAIL"
        print(f"  {status}  {name}  [{len(nodes)} 条]"
              + (f"  —— {len(bad)} 处不一致，前 3: {bad[:3]}" if bad else ""))
        fails += bool(bad)

    print("\n== 多选批量定级（取代联动开关） ==")
    multi = json.loads(run_node(MULTI_JS, {}, tmp, "_multi_probe.js"))
    expect_pairs = {
        "没勾选：只改这一条": ["2"],
        "勾了 3 条，点其中一条 → 只改这 3 条": ["2", "3", "4"],
        "勾了 3 条，点**没勾**的第 5 行 → 只改第 5 条": ["5"],
        "共用 gid 的两条：点真实块不动合成块": ["19"],
        "共用 gid 的两条：点合成块不动真实块": ["toc:P1:19:一认识论的对象"],
        "整体提一级": ["2", "3", "4"],
        "整体降一级": ["1", "6"],
    }
    for name, want in expect_pairs.items():
        got = (multi.get(name) or {}).get("pairs")
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  → {got}"
              + ("" if ok else f"  （应为 {want}）"))
        fails += not ok

    expect_vals = {
        "整体提一级": {"2": 1, "3": 2, "4": 2},   # 2 本来就是 L2 → 提为 L1
        "整体降一级": {"1": 2, "6": 2},
    }
    for name, want in expect_vals.items():
        got = (multi.get(name) or {}).get("vals")
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {name} 的级数  → {got}"
              + ("" if ok else f"  （应为 {want}）"))
        fails += not ok

    print("\n" + ("全通过：界面上看到的字数，就是点「应用并重算」之后落盘的字数；"
                  "批量定级只动你勾的那些"
                  if not fails else f"{fails} 处不一致 —— 界面在骗人，必须修"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
