
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
