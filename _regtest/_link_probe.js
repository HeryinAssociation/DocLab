
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function grab(re, name){ const m = src.match(re); if(!m){ console.error('没找到 '+name); process.exit(2);} return m[0]; }
/* setLevel 现在会调 reflowPreview 去算「点击之后实际落在第几级」，
   好在你点了一个跳级的数字时报一句明白话。这个探针因此也得把它带进来 ——
   两个函数声明在同一作用域，闭包就能互相看见。只抽 setLevel 会 ReferenceError。 */
const reflowPreview = eval('(' + grab(/function reflowPreview\(\)\{[\s\S]*?\n\}/, 'reflowPreview') + ')');
const setLevel = eval('(' + grab(/async function setLevel\(gid, k\)\{[\s\S]*?\n\}/, 'setLevel') + ')');
let CAP = null;
global.markDirty = () => {}; global.renderTreeList = () => {}; global.log = () => {};
global.CUR = 'S';
global.post = async (p, b) => { CAP = { p, b }; return { ok: true }; };
global.$ = () => ({ textContent:'', className:'', style:{}, title:'', value:'', scrollTop:0 });

/* 台账：1(L1) 2(L2) 3(L3) 4(L3) 5(L2) 6(L1)
   2 有自己的子树(3,4)，它的真兄弟是 5 —— 旧的「扁平列表找下一个」走位
   会一头撞进 3 并当场停下，一条也带不走。 */
const NODES = [
  { gid: 1, level: 1, title: 'A' }, { gid: 2, level: 2, title: 'B' },
  { gid: 3, level: 3, title: 'C' }, { gid: 4, level: 3, title: 'D' },
  { gid: 5, level: 2, title: 'E' }, { gid: 6, level: 1, title: 'F' },
];
(async () => {
  const out = {};
  async function run(name, linked, gid, k, deleted){
    global.EDIT = { nodes: NODES.map(n => Object.assign({}, n)), levels: {} };
    if (deleted) for (const g of deleted) global.EDIT.nodes.find(n => n.gid === g).deleted = true;
    global.LINKED = linked; CAP = null;
    await setLevel(gid, k);
    out[name] = Object.keys(CAP.b.pairs).map(Number).sort((a,b)=>a-b);
  }
  await run('关：只改一条', false, 2, 1);
  await run('开：跳过子树吃到真兄弟', true, 2, 1);        // 期望 2,5
  await run('开：不同层级就停', true, 3, 2);              // 期望 3,4
  await run('开：中间夹着已删条目不中断', true, 2, 1, [3]); // 期望 2,5
  await run('开：隔着整棵子树仍是同批', true, 1, 2);        // 期望 1,6
  console.log(JSON.stringify(out));
})();
