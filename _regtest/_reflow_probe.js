
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const m = src.match(/function reflowPreview\(\)\{[\s\S]*?\n\}/);
if (!m) { console.error('没找到 reflowPreview —— 界面重构了？同步改这个测试'); process.exit(2); }
global.EDIT = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const reflowPreview = eval('(' + m[0] + ')');
console.log(JSON.stringify(reflowPreview().depth));
