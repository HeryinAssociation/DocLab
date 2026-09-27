
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
