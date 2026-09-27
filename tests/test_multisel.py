"""多选 + 批量定级 + 目录视图开关：真的跑一遍 renderTreeList，看它在 DOM 上写出了什么。

为什么不用「抽一个函数出来单测」：多选的关键行为全在**渲染**里 ——
勾上的行要变底色、批量条要出现并显示正确条数、被勾的那几条要能在页面上被认出来。
只测 setLevel 的算术，测不出「界面上到底给了人什么反馈」。
「藏到第 N 级 / 藏已删」同理，判据就是清单里有没有那一行。

做法：用 node 的 vm 把 index.html 的 <script> 在**假 DOM** 里跑起来
（去掉末尾的 boot()，避免它去请求服务器），然后设好 EDIT / SEL 调 renderTreeList()，
直接读回 innerHTML 断言。不发网络、不写盘。
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
FAILS = []

PROBE_JS = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
let src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
// 末尾的 boot() 会去请求本地服务 —— 本测试只关心中文的渲染逻辑，去掉它。
src = src.replace(/boot\(\)\.catch\([\s\S]*?\);\s*$/, '');
// 脚本里 EDIT / SEL 是 let / const，不会挂到 sandbox 上，追加一段导出桩。
src += `
;const __ev = (o) => Object.assign(
  {shiftKey: false, ctrlKey: false, metaKey: false, target: {closest: () => null}}, o || {});
globalThis.__t = {
  render: renderTreeList,
  reflow: reflowPreview,
  setEdit: (e) => { EDIT = e; },
  curSel: () => [...SEL],
  selClear: () => { SEL.clear(); SEL_ANCHOR = null; },
  selAdd: (k) => SEL.add(k),
  click: (i, mods) => rowClick(__ev(mods), i),
  all: toggleSelAll,
  bump: bumpSel,
  clear: clearSel,
  setDepth: (k) => setVDepth(k),
  setShowDel: (k) => setShowDel(k),
  visible: () => VISIBLE.slice(),
};
`;

const els = {};
function el(id){
  if (!els[id]) els[id] = {
    id, value: '', innerHTML: '', textContent: '', className: '', title: '',
    scrollTop: 0, style: {}, display: '',
    classList: { toggle(){}, add(){}, remove(){}, contains(){ return false; } },
    addEventListener(){}, closest(){ return null; }, focus(){}, blur(){},
  };
  return els[id];
}
const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  document: {
    getElementById: el,
    querySelectorAll: () => [],
    addEventListener(){},
    createElement: () => el('tmp' + Math.random()),
  },
  localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
  fetch: async () => ({ ok: true, json: async () => ({}), text: async () => '' }),
  confirm: () => false,
  alert(){},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const T = sandbox.__t;
// 三条普通条目 + 一对**同 gid 不同 key**（复刻「第一章」/「一认识论的对象」）
T.setEdit({
  node_total: 6, levels: {},
  nodes: [
    { key: '1', gid: 1, level: 1, title: '第一章', own_chars: 10, chars: 100 },
    { key: '19', gid: 19, level: 1, title: '第一章正文', own_chars: 20, chars: 200 },
    { key: 'toc:P1:19:一认识论的对象', gid: 19, level: 1,
      title: '一认识论的对象', own_chars: 30, chars: 300 },
    { key: '2', gid: 2, level: 2, title: '第二章', own_chars: 40, chars: 400 },
    { key: '3', gid: 3, level: 2, title: '第三章', own_chars: 50, chars: 500 },
  ],
});

const out = {};
function snap(tag){
  T.render();
  out[tag] = {
    list: el('treeList').innerHTML,
    batchDisplay: el('batchBar').style.display,
    batchN: el('batchN').textContent,
    batchKb: el('batchKb').innerHTML,
    selBtn: el('selBtn').textContent,
    selBtnCls: el('selBtn').className,
    meta: el('treeMeta').textContent,
    sel: T.curSel().sort(),
    visible: T.visible(),
  };
}

T.selClear();
snap('未选择');
// Ctrl 点第 2 行（key=19）与第 3 行（合成键）—— 单击是「只选这条」，加选要按住 Ctrl
T.click(1, { ctrlKey: true });
T.click(2, { ctrlKey: true });
snap('选了两条同 gid 的');
// Shift 范围选：从第 0 行连到第 4 行
T.selClear(); T.click(0); T.click(4, { shiftKey: true });
snap('Shift 范围选');
// 全选 / 取消全选
T.selClear(); T.all();
snap('全选');
T.all();
snap('再点一次=取消全选');
// 清除
T.click(0); T.clear();
snap('清除选择');

// 视图：藏层级 / 藏已删 —— 藏起来的行不能影响选择
T.setEdit({ node_total: 4, levels: {}, nodes: [
  { key: 'A', gid: 1, level: 1, title: '第一章', own_chars: 1, chars: 10 },
  { key: 'B', gid: 2, level: 2, title: '第一节', own_chars: 1, chars: 10 },
  { key: 'C', gid: 3, level: 3, title: '一、深处', own_chars: 1, chars: 10 },
  { key: 'D', gid: 4, level: 1, title: '第二章', own_chars: 1, chars: 10, deleted: true },
]});
T.selClear(); T.setDepth(0); T.setShowDel(true); T.render();
const vAll = { vis: T.visible(), list: el('treeList').innerHTML };
T.setDepth(2);
const vDeep = { vis: T.visible(), list: el('treeList').innerHTML,
                meta: el('vdMeta').textContent };
T.setShowDel(false);
const vNoDel = { vis: T.visible(), list: el('treeList').innerHTML,
                 meta: el('vdMeta').textContent };
T.selClear(); T.all();
const vSel = T.curSel().sort();
T.setDepth(0); T.setShowDel(true); T.render();
out['视图'] = { all: vAll, deep: vDeep, nod: vNoDel, sel: vSel };

console.log(JSON.stringify(out));
"""


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    tmp = ROOT / "_regtest"
    tmp.mkdir(exist_ok=True)
    js = tmp / "_multisel_probe.js"
    js.write_text(PROBE_JS, encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(ROOT / "ui" / "index.html")],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print("node 跑失败：\n" + r.stderr[-1500:])
        return 1
    o = json.loads(r.stdout)

    print("\n== 未选择时的界面 ==")
    a = o["未选择"]
    check("每行都有勾选框（只是状态灯，点行才是选）",
          a["list"].count('type="checkbox"') == 5,
          f'找到 {a["list"].count(chr(34) + "checkbox" + chr(34))} 个')
    check("没有勾中的行", "selrow" not in a["list"])
    check("批量条默认不显示", a["batchDisplay"] in ("none", ""), repr(a["batchDisplay"]))
    check("默认不勾任何条目（初始层级就是 MinerU 的）", a["sel"] == [])
    check("按钮写「全选」", a["selBtn"] == "全选", a["selBtn"])

    print("\n== 勾选：同 gid 的两条各自独立 ==")
    b = o["选了两条同 gid 的"]
    check("选中集合里有 2 条", len(b["sel"]) == 2, str(b["sel"]))
    check("两条的 key 不同（不会互相带出）",
          b["sel"] == ["19", "toc:P1:19:一认识论的对象"], str(b["sel"]))
    check("页面上标出了 2 行 selrow", b["list"].count("selrow") == 2,
          f'出现 {b["list"].count("selrow")} 次')
    check("批量条出现了", b["batchDisplay"] == "flex", repr(b["batchDisplay"]))
    check("批量条写明已选 2 条", b["batchN"] == "已选 2 条", b["batchN"])
    check("批量条上有 1..6 六个统一设级按钮",
          all(f">{k}<" in b["batchKb"] for k in range(1, 7)))

    print("\n== Shift 范围选 ==")
    c = o["Shift 范围选"]
    check("选中第 0..4 行共 5 条", len(c["sel"]) == 5, str(c["sel"]))
    check("其中包含合成节点那条", "toc:P1:19:一认识论的对象" in c["sel"])

    print("\n== 全选 / 取消全选 / 清除 ==")
    d = o["全选"]
    check("全选后 5 条都在", len(d["sel"]) == 5, str(d["sel"]))
    check("按钮变成「取消全选」", d["selBtn"] == "取消全选", d["selBtn"])
    e = o["再点一次=取消全选"]
    check("再点一次清空", e["sel"] == [], str(e["sel"]))
    f = o["清除选择"]
    check("清除选择后批量条消失", f["batchDisplay"] in ("none", ""), repr(f["batchDisplay"]))
    check("清除选择后没有 selrow", f["list"].count("selrow") == 0)

    print("\n== 列表里不该再有「联动上下级」 ==")
    check("界面文案已无联动开关",
          "联动" not in o["未选择"]["list"] and "联动上下级" not in o["未选择"]["meta"])

    print("\n== 视图开关：藏层级 / 藏已删 ==")
    v = o["视图"]
    check("默认四条全在（含已删那条）", len(v["all"]["vis"]) == 4, str(v["all"]["vis"]))
    check("藏到 L2 后 L3 的行消失",
          len(v["deep"]["vis"]) == 3 and "深处" not in v["deep"]["list"],
          f'{v["deep"]["vis"]} / 清单里还有没有「深处」：{"深处" in v["deep"]["list"]}')
    check("提示写明藏了几条、且声明只是不显示",
          "L3 及更深 1 条" in v["deep"]["meta"] and "目录不动" in v["deep"]["meta"],
          v["deep"]["meta"])
    check("藏已删后那一条不见",
          len(v["nod"]["vis"]) == 2 and "第二章" not in v["nod"]["list"],
          f'{v["nod"]["vis"]} / {v["nod"]["meta"]}')
    check("藏起来的行不会被「全选」碰到", v["sel"] == ["A", "B"], str(v["sel"]))
    check("点行就能选，弹窗按钮之外不必瞄准小方框",
          'onclick="rowClick(event,' in o["未选择"]["list"])

    print("\n" + ("全通过：勾谁就改谁，界面把这件事说清楚了"
                  if not FAILS else f"{len(FAILS)} 项失败：{FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
