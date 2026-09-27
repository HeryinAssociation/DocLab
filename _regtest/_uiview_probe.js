/* 目录视图开关 + 点行选择的验证台。
   把 ui/index.html 里的 <script> 抠出来，在一个假 DOM 里跑：
     · 视图：显示到第 N 级 / 显示已删 —— 藏起来的行不能影响选择
     · 选择：单击＝只选这条、Ctrl＝加一条、Shift＝连选可见的一段
   跑法：
     node _regtest/_uiview_probe.js ui/index.html
*/
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
let src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
// 末尾的 boot() 会真的去请求本机服务 —— 本验证只关心中文渲染与选择逻辑，去掉它。
src = src.replace(/boot\(\)\.catch\([\s\S]*?\);\s*$/, '');
// 脚本里 EDIT / SEL 是 let / const，不会挂到 sandbox 上，追加一段导出桩。
src += `
;const __ev = (o) => Object.assign(
  {shiftKey: false, ctrlKey: false, metaKey: false, target: {closest: () => null}}, o || {});
globalThis.__t = {
  render: renderTreeList,
  reflow: reflowPreview,
  setEdit: (e) => { EDIT = e; },
  curSel: () => [...SEL].sort(),
  selClear: () => { SEL.clear(); SEL_ANCHOR = null; },
  click: (i, mods) => rowClick(__ev(mods), i),
  all: toggleSelAll,
  clear: clearSel,
  setVDepth, setShowDel, loadView,
  view: () => ({depth: VDEPTH, showDel: SHOW_DEL}),
  visible: () => VISIBLE.slice(),
};
`;

const els = {};
function el(id){
  if (!els[id]) els[id] = {
    id, value: '', innerHTML: '', textContent: '', className: '', title: '',
    scrollTop: 0, style: {}, display: '', checked: false,
    classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }},
    addEventListener(){}, closest(){ return null; }, focus(){}, blur(){},
  };
  return els[id];
}
const LS = {};
const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  document: {
    getElementById: el,
    querySelectorAll: () => [],
    addEventListener(){},
    createElement: () => el('tmp' + Math.random()),
  },
  localStorage: {
    getItem: (k) => (k in LS ? LS[k] : null),
    setItem: (k, v) => { LS[k] = String(v); },
    removeItem: (k) => { delete LS[k]; },
  },
  fetch: async () => ({ok: true, json: async () => ({}), text: async () => ''}),
  confirm: () => false,
  alert(){},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const T = sandbox.__t;
const fails = [];
function ck(name, ok, extra){
  console.log((ok ? '  OK  ' : '  ✗   ') + name + (ok ? '' : '   实际：' + extra));
  if (!ok) fails.push(name);
}
const S = (x) => JSON.stringify(x);

/* 六条：L1/L2/L3/L4 各一条、末尾一条已删 —— 覆盖「藏深层」与「藏已删」两件事 */
function fixture(){
  T.setEdit({
    node_total: 6, levels: {},
    nodes: [
      {key: 'a', gid: 1, level: 1, title: '第一章', own_chars: 10, chars: 100},
      {key: 'b', gid: 2, level: 2, title: '第一节', own_chars: 20, chars: 200},
      {key: 'c', gid: 3, level: 3, title: '一、小标题', own_chars: 30, chars: 300},
      {key: 'd', gid: 4, level: 4, title: '（一）更小', own_chars: 40, chars: 400},
      {key: 'e', gid: 5, level: 2, title: '第二节', own_chars: 50, chars: 500},
      {key: 'f', gid: 6, level: 1, title: '第二章', own_chars: 60, chars: 600, deleted: true},
    ],
  });
  T.selClear();
  T.setVDepth(0); T.setShowDel(true);
  T.setVDepth(0);                       // setVDepth 是「同值即取消」，这里确保落在全部
}

console.log('--- 1. 默认：全都看得见 ---');
fixture();
T.render();
ck('默认六行全在（含已删）', T.visible().length === 6, S(T.visible()));
ck('默认无「藏起」提示', el('vdMeta').textContent === '', el('vdMeta').textContent);
ck('已删行仍在清单里', el('treeList').innerHTML.includes('第二章'), '缺了');

console.log('--- 2. 显示到 L2：L3 及更深全藏 ---');
T.setVDepth(2);
ck('只剩 a/b/e/f 四行', S(T.visible()) === S([0, 1, 4, 5]), S(T.visible()));
ck('L3/L4 行已不在清单', !el('treeList').innerHTML.includes('小标题')
   && !el('treeList').innerHTML.includes('更小'), '还在');
ck('提示写明藏了几条', el('vdMeta').textContent.includes('L3 及更深 2 条'),
   el('vdMeta').textContent);
ck('提示声明只是不显示', el('vdMeta').textContent.includes('目录不动'), el('vdMeta').textContent);
ck('深度数字 2 高亮', /class="on"[^>]*>2</.test(el('vdKb').innerHTML), el('vdKb').innerHTML);

console.log('--- 3. 藏起来的行不能被「全选」碰到 ---');
T.all();
ck('全选只拿到看得见的（不含 c/d）', S(T.curSel()) === S(['a', 'b', 'e']), S(T.curSel()));

console.log('--- 4. Shift 连选：跳过藏起来的行 ---');
fixture(); T.setVDepth(2);
T.click(0);                             // 锚在第一章
T.click(5, {shiftKey: true});           // 连到第 6 行（已删）
ck('连选＝a/b/e（c/d 藏着，f 是已删）', S(T.curSel()) === S(['a', 'b', 'e']), S(T.curSel()));

console.log('--- 5. 藏已删条目 ---');
fixture(); T.setShowDel(false);
ck('已删行消失', S(T.visible()) === S([0, 1, 2, 3, 4]), S(T.visible()));
ck('清单里不再有那一条', !el('treeList').innerHTML.includes('第二章'), '还在');
ck('提示写明藏了已删', el('vdMeta').textContent.includes('已删 1 条'), el('vdMeta').textContent);
ck('勾选框同步为未勾', el('showDelCk').checked === false, String(el('showDelCk').checked));
fixture(); T.setShowDel(true);
ck('再打开就回来', T.visible().length === 6, S(T.visible()));

console.log('--- 6. 选择：单击 / Ctrl / Shift ---');
fixture();
T.click(2);                             // 单击第三条
ck('单击＝只选这一条', S(T.curSel()) === S(['c']), S(T.curSel()));
T.click(0);                             // 单击另一条 → 换掉，不是累加
ck('再单击＝换成那一条', S(T.curSel()) === S(['a']), S(T.curSel()));
T.click(4, {ctrlKey: true});
ck('Ctrl 单击＝加一条', S(T.curSel()) === S(['a', 'e']), S(T.curSel()));
T.click(4, {ctrlKey: true});
ck('再 Ctrl 单击＝减掉这一条', S(T.curSel()) === S(['a']), S(T.curSel()));
T.selClear(); T.click(0);               // 重新起一个干净的锚点再做连选
T.click(2, {shiftKey: true});
ck('Shift＝从锚点连选一段', S(T.curSel()) === S(['a', 'b', 'c']), S(T.curSel()));
T.click(4, {shiftKey: true});
ck('锚点不被 Shift 挪走：仍旧从 0 起算', S(T.curSel()) === S(['a', 'b', 'c', 'd', 'e']),
   S(T.curSel()));
T.selClear();
T.click(0);
T.click(3, {ctrlKey: true});            // 锚点跟着 Ctrl 的那一下走（资源管理器同款）
ck('Ctrl 点过之后，Shift 从刚点的那条起算',
   (T.click(4, {shiftKey: true}), S(T.curSel()) === S(['a', 'd', 'e'])), S(T.curSel()));

console.log('--- 7. 已删行点不动 ---');
fixture();
T.click(5);
ck('点已删行不改变选择', S(T.curSel()) === S([]), S(T.curSel()));

console.log('--- 8. 再点同一个数字＝取消 ---');
fixture();
T.setVDepth(3);
ck('设成 3', T.view().depth === 3, S(T.view()));
T.setVDepth(3);
ck('再点一次 3＝回到全部', T.view().depth === 0, S(T.view()));
T.setVDepth(0);
ck('「全部」高亮', /class="wide on"/.test(el('vdKb').innerHTML), el('vdKb').innerHTML);

console.log('--- 9. 设置记在浏览器里 ---');
fixture();
T.setVDepth(2); T.setShowDel(false);
loadViewFromLS();
function loadViewFromLS(){ T.loadView(); }
ck('刷新后深度还在', T.view().depth === 2, S(T.view()));
ck('刷新后已删仍隐藏', T.view().showDel === false, S(T.view()));
LS['doclab.view'] = '{坏掉的 json';
T.loadView();
ck('存坏了不炸、退回默认', T.view().depth === 0 && T.view().showDel === true, S(T.view()));

console.log(fails.length ? `\n✗ ${fails.length} 项未通过` : '\n✓ 全部通过');
process.exit(fails.length ? 1 : 0);
