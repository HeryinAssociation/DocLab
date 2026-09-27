/* 用无头 Chromium（Playwright 已经装在盘上的那个）驱动一次真实交互，截两张图：
     1) 默认「全部」—— 看得见所有层级、已删条目也在
     2) 点「显示到 2」+ 关掉「显示已删」—— L3 及更深、已删条目都藏起来
   走 CDP（--remote-debugging-port），不装任何东西。
   跑法：node _regtest/_shot_view.js <url> <outdir>
*/
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CHROME = 'C:\\Users\\Zhaoshuochen\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe';
const DBG = 9333;
const URL = process.argv[2] || 'http://127.0.0.1:8801/';
const OUTDIR = process.argv[3] || path.join(__dirname, 'shots');
const PROFILE = path.join(process.env.TEMP || '.', 'doclab-cdp-profile');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function targetWs(){
  for (let i = 0; i < 80; i++){
    try {
      const r = await fetch(`http://127.0.0.1:${DBG}/json/list`);
      const list = await r.json();
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch (e){ /* 还没起来 */ }
    await sleep(250);
  }
  throw new Error('CDP 目标没出现');
}

function makeSend(ws){
  let id = 0;
  const pend = new Map();
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pend.has(m.id)){
      const {res, rej} = pend.get(m.id);
      pend.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  });
  return (method, params = {}) => new Promise((res, rej) => {
    const i = ++id;
    pend.set(i, {res, rej});
    ws.send(JSON.stringify({id: i, method, params}));
  });
}

async function shot(send, name){
  const {data} = await send('Page.captureScreenshot', {format: 'png'});
  const f = path.join(OUTDIR, name);
  fs.writeFileSync(f, Buffer.from(data, 'base64'));
  return {f, bytes: fs.statSync(f).size};
}

(async () => {
  fs.mkdirSync(OUTDIR, {recursive: true});
  fs.rmSync(PROFILE, {recursive: true, force: true});
  const chrome = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-proxy-server',
    '--hide-scrollbars', `--remote-debugging-port=${DBG}`,
    `--user-data-dir=${PROFILE}`, '--window-size=1560,1040', URL,
  ], {stdio: 'ignore'});

  const out = {};
  try {
    const ws = new WebSocket(await targetWs());
    await new Promise((r) => ws.addEventListener('open', r, {once: true}));
    const send = makeSend(ws);
    await send('Page.enable');
    await send('Runtime.enable');
    await sleep(3500);                       // 等它把源列表和目录拉回来

    const ev = async (expr) => {
      const r = await send('Runtime.evaluate',
                           {expression: expr, returnByValue: true, awaitPromise: true});
      return r.result ? r.result.value : undefined;
    };

    out.sources = await ev('(BOOT && BOOT.sources ? BOOT.sources.length : -1)');
    out.nodes = await ev('(EDIT && EDIT.nodes ? EDIT.nodes.length : -1)');
    out.viewBar = await ev('document.getElementById("vdKb").innerText.replace(/\\n/g, " ")');
    out.showDelChecked = await ev('document.getElementById("showDelCk").checked');
    out.rowsAll = await ev('document.querySelectorAll("#treeList .trow").length');
    out.shotAll = await shot(send, 'ui_view_all_20260922.png');

    // 真的去点「显示到 2」，再关掉「显示已删」—— 走的正是界面上的按钮
    await ev(`[...document.querySelectorAll("#vdKb b")].find(b => b.textContent === "2").click()`);
    await ev('document.getElementById("showDelCk").click()');
    await sleep(600);
    out.viewAfter = await ev('(function(){const o={};'
      + 'o.depth=document.querySelector("#vdKb b.on").textContent;'
      + 'o.showDel=document.getElementById("showDelCk").checked;'
      + 'o.rows=document.querySelectorAll("#treeList .trow").length;'
      + 'o.meta=document.getElementById("vdMeta").textContent;'
      + 'o.hasL3=[...document.querySelectorAll("#treeList .trow .meta")]'
      + '.some(e => /^L[3-9]/.test(e.textContent));return o;})()');
    out.storage = await ev('localStorage.getItem("doclab.view")');
    out.shotHidden = await shot(send, 'ui_view_d2_20260922.png');

    ws.close();
  } catch (e){
    out.error = String(e && e.stack || e);
  } finally {
    chrome.kill();
  }
  console.log(JSON.stringify(out, null, 2));
})();
