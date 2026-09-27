
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const m = src.match(/function pageMap\(\)\{[\s\S]*?\n\}/);
if (!m) { console.error('没找到 pageMap —— 界面重构了？同步改这个测试'); process.exit(2); }
global.EDIT = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const pageMap = eval('(' + m[0] + ')');
const pm = pageMap();
const out = {};
for (const k of Object.keys(pm)) {
  out[k] = {};
  // 比 printed 之外还要比 kind：前置页(front)的编号不是正文页码，
  // 界面若把它同化成正文数字，人校对时对的就是错的对象。
  for (const [i, v] of Object.entries(pm[k])) out[k][i] = [v.printed, v.kind];
}
console.log(JSON.stringify(out));
