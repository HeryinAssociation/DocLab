
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
