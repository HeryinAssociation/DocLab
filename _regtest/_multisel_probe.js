
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
