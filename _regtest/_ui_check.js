
let BOOT = null, CUR = null, EDIT = null, DETAIL = null;
let DEPTH = 2, TAB = 'tree', DIRTY = false, BUSY = false;
let PAGE_ROWS = [], PAGE_BY_SHARD = {};
/* 选中集合：存条目的**主键 key**，不是 gid。
   为什么不拿 gid 当键：从印刷目录补出来的合成节点借用了「该页第一个真实块」的 gid，
   于是《认识论引论》的「第一章」(真实) 与「一认识论的对象」(合成) 共用 gid=19 ——
   用 gid 做键，点一条会带出另一条，正是景晔发现的那个毛病。
   后端 manual_edits.json 同样按 key 存，两边是同一把钥匙。 */
const SEL = new Set();
let SEL_ANCHOR = null;    // 上一次点选的**行下标**，Shift 范围选用
let VISIBLE = [];         // 上一次渲染出来的行下标（过滤后），供「全选可见」用

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = (n) => (n ?? 0).toLocaleString('en-US');

/* ---------------------------------------------------------------- 日志 */
function log(msg){
  const el = $('log');
  el.textContent += '\n' + msg;
  el.scrollTop = el.scrollHeight;
  peek(msg);
}
function setLog(msg){ $('log').textContent = msg; peek(msg); }
function peek(msg){
  const last = String(msg).split('\n').filter(Boolean).pop() || '';
  $('logPeek').textContent = last.slice(0, 200);
}
/* 三档高度：默认压扁 96px → 放大 → 折叠 30px → 回默认。
   注意别在启动时调它 —— 初始就该是压扁档，调一次等于开局先放大。 */
let LOGH = 'def';
function cycleLog(){
  LOGH = LOGH === 'def' ? 'big' : (LOGH === 'big' ? 'min' : 'def');
  const w = $('logwrap');
  w.classList.toggle('min', LOGH === 'min');
  w.classList.toggle('big', LOGH === 'big');
  document.documentElement.style.setProperty('--logh',
    LOGH === 'min' ? '30px' : (LOGH === 'big' ? '46vh' : '96px'));
  $('logTg').textContent = LOGH === 'def' ? '放大' : (LOGH === 'big' ? '收起' : '展开');
}

/* ---------------------------------------------------------------- 通信 */
async function api(path, opts){
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({ error: 'bad json' }));
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const post = (path, body) => api(path, {method: 'POST',
  headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

async function poll(jid){
  for (;;){
    await new Promise(r => setTimeout(r, 700));
    const j = await api('/api/job?id=' + encodeURIComponent(jid));
    if (j.log && j.log.length) setLog(j.log.join('\n'));
    if (j.status !== 'running' && j.status !== 'pending') return j;
  }
}

/* ---------------------------------------------------------------- 引导 */
async function boot(){
  BOOT = await api('/api/bootstrap');
  const c = BOOT.config;
  $('cfgChip').textContent = `MinerU ${c.token_present ? '已鉴权' : '未配 token'} · ${c.shard_pages} 页/片`;
  if ($('shardPages') && !$('shardPages').dataset.touched) $('shardPages').value = c.shard_pages;
  if ($('shardMode') && !$('shardMode').dataset.touched && c.shard_mode) $('shardMode').value = c.shard_mode;
  syncShardUI();
  renderSources();
  renderPending();
  loadMineruDirs();
  if (!CUR && BOOT.sources.length) await selectSource(BOOT.sources[0].source_id);
  if (!CUR) showNoSource();
  // 允许 ?tab=page 直接落在页码对照上
  const t = new URLSearchParams(location.search).get('tab');
  if (t === 'page' || t === 'tree' || t === 'blk') setTab(t);
}

function showNoSource(){
  $('pNoSrc').style.display = 'block';
  $('pEdit').style.display = 'none';
  $('pNoOutline').style.display = 'none';
  $('pIng').style.display = 'none';
  $('pOut').style.display = 'none';
  $('hDoc').textContent = '校对台';
}

function renderSources(){
  $('srcCount').textContent = BOOT.sources.length + ' 本';
  if (!BOOT.sources.length){
    $('srcList').innerHTML = '<span class="hint">还没有源。左边「新增源」登记或 OCR 一本。</span>';
    return;
  }
  $('srcList').innerHTML = BOOT.sources.map(s => {
    const d = (k) => `<span class="dot ${s.has[k] ? 'on' : ''}"></span>`;
    return `<div class="src ${s.source_id === CUR ? 'on' : ''}"
                 onclick="selectSource('${esc(s.source_id)}')">
      <button class="del" title="移出工作台（可恢复）"
              onclick="event.stopPropagation();doDelete('${esc(s.source_id)}','${esc(s.title)}')">移除</button>
      <div class="t">${esc(s.title)}</div>
      <div class="m">
        <span>${s.pages}页/${s.shards.length}片</span>
        <span>${d('pagecal')}页码 ${d('outline')}目录 ${d('export')}导出</span>
        ${s.verify ? `<span class="tag ${esc(s.verify)}">${esc(s.verify)}</span>` : ''}
      </div></div>`;
  }).join('');
}

function shardChips(shards){
  return (shards || []).map(s =>
    `<span class="shd ${esc(s.state || '')}" title="${esc(s.err || '')}">
       <i class="sd"></i>${esc(s.tag)} · ${esc(STATE_CN[s.state] || s.state || '')}</span>`).join('');
}
const STATE_CN = {pending: '待提交', uploaded: '已上传', ocr_done: 'OCR完成',
                  unzipped: '已就绪', failed: '下载失败', ocr_failed: 'OCR失败'};

function renderPending(){
  const p = BOOT.pending || [];
  $('pPend').style.display = p.length ? 'block' : 'none';
  $('pendCount').textContent = p.length ? p.length + ' 个' : '';
  $('pendList').innerHTML = p.map(r => `
    <div style="margin-bottom:9px">
      <div style="font-size:12.5px;font-weight:500">${esc(r.title || r.source_id)}</div>
      <div style="margin-top:3px">${shardChips(r.shards)}</div>
      <div class="row" style="margin-top:4px">
        <button class="sm" onclick="doResume('${esc(r.source_id)}')">续跑缺片</button>
        <button class="sm" onclick="doDelete('${esc(r.source_id)}','${esc(r.title || r.source_id)}')">放弃</button>
      </div>
    </div>`).join('');
}

/* ---------------------------------------------------------------- 选源 */
async function selectSource(id){
  CUR = id;
  DIRTY = false; showDirty();
  // 换书：正文块视图整份作废（块的 gid 只对当前这本书成立），编辑条也要收起
  BLK = {rows: [], total: 0, offset: 0, limit: 60, q: '', loaded: false};
  BLK_PICK = null;
  if ($('blkQ')) $('blkQ').value = '';
  if ($('blkAddHost')) $('blkAddHost').innerHTML = '';
  renderSources();
  const row = (BOOT.sources || []).find(s => s.source_id === id);
  $('hDoc').textContent = row ? row.title : id;
  $('pNoSrc').style.display = 'none';

  DETAIL = await api('/api/source?id=' + encodeURIComponent(id));
  renderIngest();
  renderOut();

  if (!DETAIL.outline || (!DETAIL.outline.node_total
                          && !DETAIL.outline.manual_deleted_count)){
    $('pNoOutline').style.display = 'block';
    $('pEdit').style.display = 'none';
    return;
  }
  $('pNoOutline').style.display = 'none';
  $('pEdit').style.display = 'block';
  await loadEdit();
}

function renderIngest(){
  const p = DETAIL.pending_ingest;
  $('pIng').style.display = p ? 'block' : 'none';
  if (!p) return;
  $('ingMeta').textContent = `${p.shards.filter(s => s.state === 'unzipped').length}/${p.shards.length} 片就绪`;
  $('ingBody').innerHTML = shardChips(p.shards) +
    `<div class="row" style="margin-top:6px"><button class="sm"
       onclick="doResume('${esc(p.source_id)}')">续跑缺片（已就绪的不会重做）</button></div>`;
}

/* ---------------------------------------------------------------- 校对台 */
async function loadEdit(){
  EDIT = await api('/api/edit?source=' + encodeURIComponent(CUR));
  EDIT.added = EDIT.added || {};
  // 换源/重算后，原来勾的那批 key 属于上一棵树，必须清掉 —— 留着的话
  // 批量条会显示「已选 3 条」，而那 3 条在新树里根本不是你现在看到的东西。
  SEL.clear(); SEL_ANCHOR = null;
  renderTreeList();
  renderPageList();
  showDirty();
}
function setTab(t){
  TAB = t;
  $('tabBtnTree').classList.toggle('on', t === 'tree');
  $('tabBtnPage').classList.toggle('on', t === 'page');
  $('tabBtnBlk').classList.toggle('on', t === 'blk');
  $('tabTree').style.display = t === 'tree' ? 'block' : 'none';
  $('tabPage').style.display = t === 'page' ? 'block' : 'none';
  $('tabBlk').style.display = t === 'blk' ? 'block' : 'none';
  // 正文块懒加载：4000 块不必在选源时就拉一遍
  if (t === 'blk' && !BLK.loaded) loadBlocks(0);
}

/* ---- 正文块：在 MinerU 漏掉标题的地方补一个断点 ----
   「补漏掉的标题」的正路在这里，而不是去改导出的 md：md 是**产物**，每次
   「应用并重算」全量重生成，手改的会被冲掉；而且目录树在 outline.json 里，
   文件是按树的节点切的 —— 手加一个 `##` 不改变树，页码、字数、归属全是错的。
   所以断点只能在工具里立：落在**文档流**的某个位置上（＝某个块的某个字符），
   原文一字不动，人工层叠在它上面。 */
let BLK = {rows: [], total: 0, offset: 0, limit: 60, q: '', loaded: false};
let BLK_PICK = null;      // 正在填标题的那一块（gid），null ＝ 没在编辑
let BLK_PICK_LV = 2;      // 编辑条里选中的层级
let BLK_TITLE = '';       // 编辑条里的标题文本（oninput 实时同步，重渲染才不丢）
let BLK_OFF = 0;          // 断点在块内的字符位移

async function loadBlocks(offset){
  const q = ($('blkQ').value || '').trim();
  if (offset !== undefined && offset !== null) BLK.offset = Math.max(0, offset | 0);
  $('blkMeta').textContent = '读取中…';
  try {
    const r = await api('/api/blocks?source=' + encodeURIComponent(CUR)
        + '&q=' + encodeURIComponent(q)
        + '&offset=' + BLK.offset + '&limit=' + BLK.limit);
    BLK.rows = r.rows || []; BLK.total = r.total || 0; BLK.q = q;
    BLK.loaded = true;
    renderBlockList();
  } catch (e){
    $('blkMeta').textContent = '';
    log('❌ 读正文块失败：' + e.message);
  }
}
function blkClearQ(){ $('blkQ').value = ''; loadBlocks(0); }
function blkPage(d){
  const next = BLK.offset + d * BLK.limit;
  if (next < 0 || next >= BLK.total) return;
  loadBlocks(next);
  $('blkList').scrollTop = 0;
}
/* 把命中片段标出来。先在**原文**上定位再逐段转义，别在转义后的串上找 ——
   搜索词里带 & < 这类字符时会错位。 */
function hl(text, ql){
  const t = String(text || '');
  if (!ql) return esc(t);
  const i = t.toLowerCase().indexOf(ql);
  if (i < 0) return esc(t);
  return esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + ql.length)) + '</mark>'
       + esc(t.slice(i + ql.length));
}

function renderBlockList(){
  const rows = BLK.rows || [], q = BLK.q || '', ql = q.toLowerCase();
  $('blkMeta').textContent = BLK.loaded
    ? `${num(BLK.total)} 块` + (q ? `命中「${q}」` : '') : '';
  if (!rows.length){
    $('blkList').innerHTML = `<div class="hint" style="padding:10px">`
      + (q ? '没有含这句话的块。换个更短的片段再搜 —— MinerU 可能把标题和上下文并进了同一段。'
           : '这本书没有可显示的正文块。') + '</div>';
    $('blkPageBar').style.display = 'none';
    return;
  }
  $('blkList').innerHTML = rows.map(b => {
    const tags = [];
    if (b.in_tree) tags.push(`<span class="tag2 hd">已是标题${b.level ? ' L' + b.level : ''}</span>`);
    else if (b.is_heading) tags.push('<span class="tag2 hd">MinerU 标为标题</span>');
    if (b.added) tags.push('<span class="tag2 add">已新增</span>');
    const long = (b.full_len || 0) > (b.text || '').length;
    const btn = b.added
      ? `<button class="sm" onclick="dropAdded(${b.gid})"
                title="撤销在这一块上新增的标题。正文原文一字不动">撤销</button>`
      : `<button class="sm" onclick="pickBlock(${b.gid})"
                title="把这一块立成一个标题断点。原文仍留在正文里">设为标题</button>`;
    return `<div class="blk${b.in_tree ? ' in-tree' : ''}" id="bk_${b.gid}">
      <span class="bp">${b.printed ? 'p' + esc(b.printed) : '—'}<br>#${b.gid}</span>
      <span class="bt"><span class="tx" onclick="this.classList.toggle('open')"
            title="点击展开/收起全文">${hl(b.text, ql)}${long ? ' …' : ''}</span></span>
      <span class="ba">${tags.join('')}${btn}</span>
    </div>`;
  }).join('');
  const from = BLK.total ? BLK.offset + 1 : 0;
  const to = BLK.offset + rows.length;
  $('blkPageBar').style.display = BLK.total > BLK.limit ? 'flex' : 'none';
  $('blkPageInfo').textContent = `${from}–${to}`;
  $('blkTotalTip').textContent = `共 ${num(BLK.total)} 块`;
}

function pickBlock(gid){
  const b = (BLK.rows || []).find(x => x.gid === gid);
  if (!b) return;
  BLK_PICK = gid;
  // 标题文本预填**这一块的第一行** —— 漏掉的标题多半就长这样。可以在框里改。
  const first = String(b.text || '').split('\n')[0].trim();
  BLK_TITLE = first.length > 90 ? first.slice(0, 90) : first;
  BLK_OFF = 0;
  // 层级默认沿用上一块（上一行）的级数：补漏标题常常是连着补一串同级的，
  // 每次都从 2 开始点一遍太烦。
  const i = BLK.rows.indexOf(b);
  BLK_PICK_LV = b.level || (i > 0 ? (BLK.rows[i - 1].level || 2) : 2);
  renderAddBox();
  const inp = $('addTitle');
  if (inp){ inp.focus(); inp.select(); }
}
function pickLv(k){
  syncAddBox(); BLK_PICK_LV = k; renderAddBox();
}
function syncAddBox(){
  if ($('addTitle')) BLK_TITLE = $('addTitle').value;
  if ($('addOff')) BLK_OFF = Math.max(0, parseInt($('addOff').value || '0', 10) || 0);
}
function cancelAdd(){ BLK_PICK = null; renderAddBox(); }
function renderAddBox(){
  const host = $('blkAddHost');
  if (BLK_PICK === null){ host.innerHTML = ''; return; }
  const b = (BLK.rows || []).find(x => x.gid === BLK_PICK);
  if (!b){ BLK_PICK = null; host.innerHTML = ''; return; }
  const kb = [1, 2, 3, 4, 5, 6].map(k =>
    `<b class="${k === BLK_PICK_LV ? 'on' : ''}" title="定成第 ${k} 级"
        onclick="pickLv(${k})">${k}</b>`).join('');
  host.innerHTML = `<div class="addbox">
    <div class="blkref">块 #${b.gid} · ${b.printed ? 'p' + esc(b.printed) : '页码未定'} ·
      ${esc(String(b.text || '').slice(0, 150))}${(b.full_len || 0) > 150 ? ' …' : ''}</div>
    <div class="row"><span class="hint">标题文本</span>
      <input type="text" id="addTitle" value="${esc(BLK_TITLE)}"></div>
    <div class="row"><span class="hint">定为第</span><span class="kb2">${kb}</span>
      <span class="hint">级</span>
      <span class="hint" style="margin-left:14px">从块内第</span>
      <input type="number" id="addOff" min="0" value="${BLK_OFF}"
             title="断点在这个块**内部**的字符位置。0 ＝ 从块首断开（绝大多数情况）；只有 MinerU 把标题和紧随的正文并进同一段时才需要改它">
      <span class="hint">字断开</span></div>
    <div class="row"><button class="primary sm" onclick="confirmAdd()">确认新增</button>
      <button class="sm" onclick="cancelAdd()">取消</button>
      <span class="hint">正文一字不动；点「应用并重算」后进目录</span></div>
  </div>`;
}

async function confirmAdd(){
  if (BLK_PICK === null) return;
  syncAddBox();
  const gid = BLK_PICK, title = (BLK_TITLE || '').trim(), off = BLK_OFF;
  if (!title){ log('先给这个断点填一个标题文本'); return; }
  try {
    await post('/api/edit/add', {source: CUR, gid, title, level: BLK_PICK_LV, offset: off});
    EDIT.added[String(gid)] = {title, level: BLK_PICK_LV, offset: off};
    BLK_PICK = null;
    markDirty();
    log(`[新增] 块 #${gid}${off ? `（从块内第 ${off} 字断开）` : ''} → 第 ${BLK_PICK_LV} 级`
        + `「${title}」。正文原文不动；点「应用并重算」后进目录`);
    await loadEdit();          // 目录列表要立刻看得见它，不能等应用之后才出现
    renderBlockList();
    setTab('tree');
  } catch (e){ log('❌ 新增没存上：' + e.message); }
}

async function dropAdded(gid){
  const rec = (EDIT.added || {})[String(gid)];
  const t = rec ? rec.title : `块 #${gid}`;
  if (!confirm(`撤销新增的标题「${t}」？\n\n正文一字不动，只是不再有这个断点。`)) return;
  try {
    await post('/api/edit/add-clear', {source: CUR, gid});
    delete EDIT.added[String(gid)];
    delete EDIT.levels['add:' + gid];
    markDirty();
    log(`[撤销] 块 #${gid} 的人工标题已撤。点「应用并重算」后目录恢复原样`);
    await loadEdit();
    renderBlockList();
  } catch (e){ log('❌ 撤销失败：' + e.message); }
}
function showDirty(){
  $('dirtyTip').style.display = DIRTY ? 'inline-block' : 'none';
  $('btnApply').classList.toggle('primary', DIRTY);
}
function markDirty(){ DIRTY = true; showDirty(); }

/* ---- 目录层级 ---- */
/* 每一条的级数 ＝ **你给的那个数**（没指定过就是自动识别的那个数）。
   这里曾经用「层级栈」现算深度，那是个坑：把 A 从 L5 提到 L3，A 前面被抽掉一层，
   紧随其后的邻居就从 L5 浮到 L4 —— 你只动了一条，屏幕上十几条的级数一起变。
   他为此找过两次（"关了联动，另一个相邻标题同步变化了"）。
   人工指定是权威，**谁也不许改写别人那一行的数字**。
   父子关系（谁是谁的正文范围）仍按「前面最近一个级数更小的标题」归，
   那是结构、是导出时算归属用的，不参与显示。 */
function reflowPreview(){
  const lv = {}, depth = {};
  const alive = EDIT.nodes.filter(n => !n.deleted);
  for (const n of alive){
    lv[n.key] = EDIT.levels[n.key] || n.level || 1;
    depth[n.key] = lv[n.key];
  }

  /* 区间字数：这一条 → 「下一个同级或更高级标题」之前的全部正文，
     等于它在新树里整棵子树（含自身）的**自有正文**之和。
     必须拿 own_chars 累加：n_chars 是含后代的，照新树重加会把子树算两遍。
     被删条目名下的正文并进它前面最近的存活条目 —— 后端重建树时这些块
     找不到标题可挂，就归给前一个节点，界面得跟它对齐，不然应用前后数字对不上。 */
  const own = {};
  let carry = 0, lastAlive = null;
  for (const n of EDIT.nodes){
    if (n.deleted){ carry += n.own_chars || 0; continue; }
    own[n.key] = (n.own_chars || 0) + carry;
    carry = 0; lastAlive = n.key;
  }
  if (carry && lastAlive !== null) own[lastAlive] += carry;  // 已删的排在最后

  const ic = {};
  for (let i = 0; i < alive.length; i++){
    const g = alive[i].key, d0 = depth[g];
    let s = own[g] || 0;
    for (let k = i + 1; k < alive.length; k++){
      if (depth[alive[k].key] > d0) s += own[alive[k].key] || 0;
      else break;
    }
    ic[g] = s;
  }
  return {lv, depth, ic};
}

/* ---- 多选批量定级 ----
   取消「联动上下级」开关：它按 MinerU 的自动层级去猜「哪些是一批」，猜错就带着
   不该改的条目一起动。改为**完全按你的选择来** —— 初始层级仍按 MinerU 识别，
   你对哪几条下手，就只有哪几条变。 */
const keyOf = (i) => { const n = (EDIT.nodes || [])[i]; return n ? n.key : null; };
const titleOfKey = (k) => {
  const n = (EDIT.nodes || []).find(x => x.key === k);
  return n ? n.title : k;
};
/* 当前生效层级：人工定级优先，否则 MinerU 自动值 */
function curLevel(key){
  const n = (EDIT.nodes || []).find(x => x.key === key);
  return EDIT.levels[key] || (n ? (n.level || 1) : 1);
}

function toggleSelAt(i, shift){
  const key = keyOf(i);
  if (!key) return;
  if (shift && SEL_ANCHOR !== null && SEL_ANCHOR !== i){
    // Shift 范围选：按**可见行序**取一段（过滤视图下就是选屏幕上这一段）
    const a = Math.min(SEL_ANCHOR, i), b = Math.max(SEL_ANCHOR, i);
    for (let j = a; j <= b; j++){
      const k = keyOf(j);
      if (k && !(EDIT.nodes[j] || {}).deleted) SEL.add(k);
    }
  } else {
    if (SEL.has(key)) SEL.delete(key); else SEL.add(key);
    SEL_ANCHOR = i;
  }
  renderTreeList();
}

function clearSel(){ SEL.clear(); SEL_ANCHOR = null; renderTreeList(); }

function toggleSelAll(){
  const vis = VISIBLE.length ? VISIBLE : (EDIT.nodes || []).map((_, i) => i);
  const alive = vis.filter(i => !(EDIT.nodes[i] || {}).deleted);
  const allIn = alive.length > 0 && alive.every(i => SEL.has(EDIT.nodes[i].key));
  if (allIn){ alive.forEach(i => SEL.delete(EDIT.nodes[i].key)); }
  else { alive.forEach(i => SEL.add(EDIT.nodes[i].key)); }
  renderTreeList();
}

/* 把一组条目统一设为第 k 级。点单行的数字＝只改这一行；点选中集合里某行的数字＝
   改整个选中集合。 */
async function applyLevels(keys, k){
  const pairs = {};
  for (const key of keys) pairs[key] = k;
  Object.assign(EDIT.levels, pairs);
  markDirty(); renderTreeList();
  const names = keys.slice(0, 3).map(titleOfKey);
  log(`[定级] ${keys.length} 条 → 第 ${k} 级：${names.join('、')}`
      + (keys.length > 3 ? ` 等 ${keys.length} 条` : '')
      + '。点「应用并重算」后落盘');
  try {
    await post('/api/edit/levels', {source: CUR, pairs});
  } catch (e){ log('❌ 定级没存上：' + e.message); }
}

/* 选中项整体升降一级（提一级＝层级数字减 1，数字越小越靠上） */
async function bumpSel(delta){
  const keys = [...SEL];
  if (!keys.length){ log('先勾选要调整的条目'); return; }
  const pairs = {};
  let hitEdge = 0;
  for (const key of keys){
    const want = curLevel(key) + delta;
    const cl = Math.min(9, Math.max(1, want));
    if (cl !== want) hitEdge++;
    pairs[key] = cl;
  }
  Object.assign(EDIT.levels, pairs);
  markDirty(); renderTreeList();
  log(`[定级] ${keys.length} 条整体${delta < 0 ? '提' : '降'}一级`
      + (hitEdge ? `（其中 ${hitEdge} 条已到顶/到底，未再移动）` : '')
      + '。点「应用并重算」后落盘');
  try {
    await post('/api/edit/levels', {source: CUR, pairs});
  } catch (e){ log('❌ 定级没存上：' + e.message); }
}

function renderTreeList(){
  const keep = $('treeList').scrollTop;
  const q = ($('treeQ').value || '').trim();
  const nodes = EDIT.nodes || [];
  const manual = EDIT.levels || {};
  const nDel = nodes.filter(n => n.deleted).length;
  const {depth, ic} = reflowPreview();   // 提前算：下面 meta 的层级分布也要用它
  // 分布必须按**预览后**的层级数，不能拿 outline.json 的 level_stats ——
  // 你把一条从 L2 提到 L1，那张旧表还是 L2 的口径，同一屏里两套数字打架。
  const distMap = {};
  for (const n of nodes) if (!n.deleted) distMap[depth[n.key]] = (distMap[depth[n.key]] || 0) + 1;
  const dist = Object.keys(distMap).sort((a, b) => a - b)
    .map(k => `L${k}=${distMap[k]}`).join(' · ');
  $('treeMeta').textContent = `${EDIT.node_total} 节点`
    + (Object.keys(manual).length ? ` · 人工定级 ${Object.keys(manual).length} 处` : '')
    + (nDel ? ` · 已删 ${nDel} 条` : '')
    + (dist ? ` · ${dist}` : '');
  // 选中集合里可能残留已经不存在的 key（换源、重算之后）—— 先剔除再统计，
  // 否则批量条上写着「已选 3 条」，屏幕上一条勾都没有。
  const live = new Set(nodes.filter(n => !n.deleted).map(n => n.key));
  for (const k of [...SEL]) if (!live.has(k)) SEL.delete(k);
  const selN = SEL.size;

  $('batchBar').style.display = selN ? 'flex' : 'none';
  if (selN){
    $('batchN').textContent = `已选 ${selN} 条`;
    $('batchKb').innerHTML = [1, 2, 3, 4, 5, 6].map(k =>
      `<b title="把选中的 ${selN} 条都定为第 ${k} 级" onclick="batchLevel(${k})">${k}</b>`
    ).join('');
  }
  $('btnRestoreAll').style.display = nDel > 1 ? 'inline-block' : 'none';

  const vis = [];
  const html = nodes.map((n, i) => {
    if (q && !n.title.includes(q)) return '';
    vis.push(i);
    if (n.deleted){
      const was = n.level ? `原 L${n.level} · ` : '';
      return `<div class="trow gone" id="tr_${i}">
        <span class="tt">${esc(n.title)}<i class="mm">已删</i></span>
        <span class="meta">${was}${num(n.chars)}字</span>
        <button class="sm" onclick="restoreNode(${i})">恢复</button>
      </div>`;
    }
    const d = depth[n.key] || 1;
    const mine = EDIT.levels[n.key];
    const on = SEL.has(n.key);
    // 高亮就是你给的那个数（没给过就是自动识别的那个数）。级数不再被层级栈
    // 改写，也不会因为选了别的条目而变化 —— 点了几级就几级亮，永远。
    const shown = d;
    const kb = [1, 2, 3, 4, 5, 6].map(k =>
      `<b class="${k === shown ? 'on' : ''}" title="定为第 ${k} 级"
          onclick="setLevel(${i},${k})">${k}</b>`).join('');
    const mm = mine ? '<i class="mm">人工</i>' : '';
    // 刚补的标题，还没点「应用并重算」—— outline.json 是上一次重算的产物，
    // 里面还没有这一条。标出来，免得人以为字数/位置已经是最终值了。
    const pend = n.pending_add ? '<i class="mm">新增</i>' : '';
    // 字数跟**新层级**实时走：你把一条提到第 1 级，它这一节的范围就从
    // 「原来那几段」变成「一直到下一个同级标题之前」，字数必须当场跟着变，
    // 否则你没法拿它判断切出来的文件多大。与已生效的值不同就标一下。
    const c = ic[n.key] != null ? ic[n.key] : n.chars;
    const cDelta = n.pending_add
      ? `<span title="还没有点「应用并重算」—— 目录和正文文件都还没重算，这一条暂时没有字数">待重算</span>`
      : ((c !== n.chars)
          ? `<i class="mm numchg" title="按你改后的层级重算：${num(n.chars)} → ${num(c)} 字（点「应用并重算」后落盘）">${num(c)}字</i>`
          : `<span>${num(c)}字</span>`);
    // 合成的「前置」节点不给删：它承接第一个标题之前的全部块（封面/书名页/版权页），
    // 剔掉会让这些块无主，导出时凭空少一截正文。
    const del = n.marker === 'front' ? ''
      : (n.pending_add || String(n.key).startsWith('add:')
          ? `<span class="x" title="撤销这条新增（正文一字不动，只是不再有这个断点）"
                   onclick="delNode(${i})">×</span>`
          : `<span class="x" title="删掉这个条目（下级上提一级，正文不丢）"
                   onclick="delNode(${i})">×</span>`);
    return `<div class="trow lv${Math.min(d, 3)}${on ? ' selrow' : ''}${n.pending_add ? ' pending' : ''}"
                 id="tr_${i}" style="padding-left:${10 + (d - 1) * 16}px">
      <input type="checkbox" class="ck" ${on ? 'checked' : ''}
             title="勾选后可批量定级（按住 Shift 可连选一段）"
             onclick="toggleSelAt(${i}, event.shiftKey)">
      <span class="tt">${esc(n.title)}${mm}${pend}</span>
      <span class="meta">L${d} · ${cDelta}</span>
      <span class="kb">${kb}</span>${del}
    </div>`;
  }).join('');
  VISIBLE = vis;
  const nVis = vis.filter(i => !(nodes[i] || {}).deleted).length;
  $('selBtn').style.display = nVis ? 'inline-block' : 'none';
  const allIn = nVis > 0 && vis.filter(i => !(nodes[i] || {}).deleted)
                               .every(i => SEL.has(nodes[i].key));
  $('selBtn').className = 'sm' + (allIn ? ' lk on' : '');
  $('selBtn').textContent = allIn ? '取消全选' : '全选';
  $('selBtn').title = '把当前列表里（过滤后）的条目全部加入选择，再统一定级。'
    + '默认不动任何东西 —— 初始层级就是 MinerU 识别出来的那个';
  $('treeList').innerHTML = html || '<div class="hint" style="padding:9px">没有匹配的标题。</div>';
  $('treeList').scrollTop = keep;
}

/* 点某一行的数字按钮。
   规则（取代原来的「联动上下级」开关）：
     · 这一行**在选中集合里**且选了不止一条 → 作用于整个选中集合；
     · 否则 → 只改这一行，并把它设为唯一选中。
   这样「只动一条」是默认行为，要批量就先勾选 —— 完全按你的选择来，
   不做任何「自动识别为同批」的猜测。 */
async function setLevel(i, k){
  const n = (EDIT.nodes || [])[i];
  if (!n) return;
  let keys;
  if (SEL.size > 1 && SEL.has(n.key)) keys = [...SEL];
  else { SEL.clear(); SEL.add(n.key); keys = [n.key]; }
  await applyLevels(keys, k);
}

/* 批量条上的数字：把选中集合全部定为第 k 级 */
async function batchLevel(k){
  const keys = [...SEL];
  if (!keys.length){ log('先在左边勾选要调整的条目'); return; }
  await applyLevels(keys, k);
}

/* ---- 条目删除 ---- */
async function delNode(i){
  const n = (EDIT.nodes || [])[i];
  if (!n || n.deleted) return;
  // 人工新增的条目：删掉 ＝ **撤销这次新增**，不是「MinerU 认错、被我剔掉」。
  // 后端 manual.set_deleted 对 add: 键也是这么处理的（不写进 deleted 档案）。
  // 两边必须一致，否则「已删除」列表里会混进一条从来没被识别出来过的标题。
  if (n.pending_add || String(n.key).startsWith('add:')){
    await dropAdded(n.gid);
    return;
  }
  n.deleted = true;                       // 本地先行，立刻退出层级栈
  delete EDIT.levels[n.key];              // 后端也会一并清掉这一条的定级
  SEL.delete(n.key);                      // 删掉的条目不该还留在选择里
  markDirty(); renderTreeList();
  try {
    await post('/api/edit/delete', {source: CUR, key: n.key, gid: n.gid, node: {
      title: n.title, level: n.level, marker: n.marker, shard: n.shard,
      page_idx: n.page_idx, chars: n.chars, own_chars: n.own_chars,
      blocks: n.blocks, gid: n.gid}});
    log(`[删除] 「${n.title}」—— 下级上提一级，正文不丢。点「应用并重算」后进目录`);
  } catch (e){
    n.deleted = false; renderTreeList();
    log('❌ 删除没存上：' + e.message);
  }
}

async function restoreNode(i){
  const n = (EDIT.nodes || [])[i];
  if (!n) return;
  try {
    await post('/api/edit/delete-clear', {source: CUR, key: n.key, gid: n.gid});
    n.deleted = false;
    markDirty(); renderTreeList();
    log('[恢复] 条目已回到目录树。点「应用并重算」后进目录');
  } catch (e){ log('❌ 恢复失败：' + e.message); }
}

async function restoreAllDeletes(){
  const nDel = (EDIT.nodes || []).filter(n => n.deleted).length;
  if (!nDel || !confirm(`恢复全部 ${nDel} 条被删条目？`)) return;
  try {
    await post('/api/edit/deletes-clear', {source: CUR});
    EDIT.nodes.forEach(n => { if (n.deleted) n.deleted = false; });
    markDirty(); renderTreeList();
    log('[恢复] 全部条目已回到目录树。点「应用并重算」后进目录');
  } catch (e){ log('❌ 恢复失败：' + e.message); }
}

/* ---- 页码对照 ----
   前端复刻后端 pagecal.apply_anchors 的规则，这样敲一格就能立刻看到后面整段跟着递加，
   不必每敲一下都等一次往返。规则：人工锚点只影响它自己及其之后；
   第一个锚点之前的页沿用机器识别的区间，不替人外推。 */
function pageMap(){
  const out = {};
  for (const shard of (EDIT.shard_order || [])){
    const total = (EDIT.shard_pages || {})[shard] || 0;
    const segs = EDIT.auto_segments[shard] || [];
    // 前置页的编号是它自己的（front-N），不是正文页码。界面上必须能分辨，
    // 否则「序言第 1 页」会被当成「正文第 1 页」—— 校对时就对错了对象。
    const kindAt = (i) => {
      for (const s of segs) if (i >= s.start && i <= s.end) return s.kind || 'body';
      return 'body';
    };
    const m = {};
    for (const s of segs){
      for (let i = s.start; i <= Math.min(s.end, total - 1); i++)
        m[i] = {printed: i + s.offset, src: 'auto', kind: s.kind || 'body'};
    }
    const anchors = (EDIT.anchors[shard] || []).slice()
      .sort((a, b) => a.page_idx - b.page_idx);
    if (anchors.length){
      const first = anchors[0].page_idx;
      for (let i = first; i < total; i++) delete m[i];
      anchors.forEach((a, k) => {
        const end = (k + 1 < anchors.length) ? anchors[k + 1].page_idx - 1 : total - 1;
        const off = a.printed - a.page_idx;
        const kind = kindAt(a.page_idx);
        for (let i = a.page_idx; i <= Math.min(end, total - 1); i++)
          m[i] = {printed: i + off, src: 'manual', kind: kind};
      });
    }
    out[shard] = m;
  }
  return out;
}
/* 页码格的样式与标记：渲染和局部刷新共用一份，避免两处漂移 */
function pvClass(v, seen, isA){
  if (isA) return 'anch';
  if (v && v.kind === 'front') return 'front';       // 前置页自有编号，不是正文页码
  if (seen !== undefined) return 'obs';              // 机器真读到过
  return v ? (v.src === 'manual' ? 'man' : 'auto') : 'none';
}
function pvMark(v, seen, isA){
  if (isA) return '锚';
  if (v && v.kind === 'front') return '前置';
  if (seen !== undefined) return '识别';
  return v ? '推算' : '';
}

function renderPageList(){
  const pm = pageMap();
  PAGE_ROWS = []; PAGE_BY_SHARD = {};
  const parts = [];
  let prev = null, prevGap = false, prevFront = false;
  for (const shard of (EDIT.shard_order || [])){
    const total = (EDIT.shard_pages || {})[shard] || 0;
    const base = (EDIT.shard_offsets || {})[shard] || 0;
    const obs = (EDIT.observations || {})[shard] || {};
    const anch = {};
    (EDIT.anchors[shard] || []).forEach(a => { anch[a.page_idx] = a.printed; });
    for (let i = 0; i < total; i++){
      const pdf = base + i + 1;
      const v = pm[shard][i];
      const seen = obs[String(i)];
      const isA = anch[i] !== undefined;
      const gap = (!v && !isA);
      const isFront = !!(v && v.kind === 'front');
      const cls = pvClass(v, seen, isA);
      const mk = pvMark(v, seen, isA);
      // 说明只在真有信息时出现，且同一件事只说一次：
      // 早先每行都写「机器读到 N」、每行都写「前置页自有编号」，纯占地方。
      let note = '';
      if (shard !== prev) note = `分片 ${shard} 起 · PDF 第 ${pdf} 页`;
      else if (isA) note = '人工指定 → 其后逐页递加';
      else if (isFront && !prevFront) note = '前置页自有编号，不是正文页码';
      else if (gap && !prevGap) note = '以下无页码（机器没读到）';
      parts.push(`<div class="pgrow ${shard !== prev ? 'sep' : ''}" data-shard="${esc(shard)}" data-i="${i}" data-pdf="${pdf}">
        <span class="pdf">${pdf}</span>
        <input class="pv ${cls}" value="${v ? v.printed : ''}" inputmode="numeric"
               title="${isFront ? '前置页第 ' + v.printed + ' 页' : ''}"
               placeholder="${gap ? '—' : ''}">
        <span class="mk">${mk}</span>
        <span class="note">${esc(note)}</span></div>`);
      prev = shard; prevGap = gap; prevFront = isFront;
    }
  }
  $('pageList').innerHTML = parts.join('');
  $('pageList').querySelectorAll('.pgrow').forEach(el => {
    const rec = {shard: el.dataset.shard, i: +el.dataset.i, pdf: +el.dataset.pdf, el: el};
    PAGE_ROWS.push(rec);
    (PAGE_BY_SHARD[rec.shard] = PAGE_BY_SHARD[rec.shard] || []).push(rec);
  });
  renderPageListMeta();
}

function refreshPages(shard, fromIdx){
  const pm = pageMap()[shard] || {};
  const obs = (EDIT.observations || {})[shard] || {};
  const anch = {};
  (EDIT.anchors[shard] || []).forEach(a => { anch[a.page_idx] = a.printed; });
  for (const r of (PAGE_BY_SHARD[shard] || [])){
    if (r.i < fromIdx) continue;
    const v = pm[r.i];
    const inp = r.el.querySelector('input.pv');
    const seen = obs[String(r.i)];
    const isA = anch[r.i] !== undefined;
    inp.value = v ? v.printed : '';
    inp.className = 'pv ' + pvClass(v, seen, isA);
    r.el.querySelector('.mk').textContent = pvMark(v, seen, isA);
  }
}

async function commitPage(inp){
  const row = inp.closest('.pgrow');
  const shard = row.dataset.shard, i = +row.dataset.i;
  const raw = inp.value.trim();
  const val = raw === '' ? null : parseInt(raw, 10);
  if (val !== null && (isNaN(val) || val < 1)){
    log('❌ 印刷页码要是正整数：' + raw);
    renderPageList();
    return;
  }
  try {
    if (val === null){
      await post('/api/edit/anchor-clear', {source: CUR, shard: shard, page_idx: i});
      const arr = EDIT.anchors[shard] || [];
      EDIT.anchors[shard] = arr.filter(a => a.page_idx !== i);
      log(`[锚点] 清除 ${shard} p${i + 1}`);
    } else {
      await post('/api/edit/anchor', {source: CUR, shard: shard, page_idx: i, printed: val});
      const arr = (EDIT.anchors[shard] || []).filter(a => a.page_idx !== i);
      arr.push({page_idx: i, printed: val});
      arr.sort((a, b) => a.page_idx - b.page_idx);
      EDIT.anchors[shard] = arr;
      log(`[锚点] ${shard} PDF 第 ${+row.dataset.pdf} 页 = 印刷第 ${val} 页 → 其后逐页递加`);
    }
    markDirty();
    refreshPages(shard, 0);
    renderPageListMeta();
  } catch (e){ log('❌ 锚点没存上：' + e.message); }
}

function renderPageListMeta(){
  const nA = Object.values(EDIT.anchors || {}).reduce((a, v) => a + v.length, 0);
  const nObs = Object.values(EDIT.observations || {}).reduce((a, o) => a + Object.keys(o).length, 0);
  $('pageMeta').textContent = `机器读到页码 ${nObs} 页`
    + (nA ? ` · 人工锚点 ${nA} 处` : '') + ` · locator=${EDIT.calib.locator_type || '—'}`;
}

function jumpPdf(){
  const n = parseInt($('jumpPdf').value, 10);
  if (isNaN(n)) return;
  const r = PAGE_ROWS.find(x => x.pdf === n);
  if (r) r.el.scrollIntoView({block: 'center'});
  else log(`❌ 没有 PDF 第 ${n} 页（全书 ${PAGE_ROWS.length} 页）`);
}

async function clearAnchors(){
  if (!confirm('清空这本书的全部人工锚点？页码会回到机器识别的结果。')) return;
  await post('/api/edit/anchors-clear', {source: CUR});
  Object.keys(EDIT.anchors || {}).forEach(k => { EDIT.anchors[k] = []; });
  markDirty(); renderPageList();
  log('[锚点] 已全部清空');
}

/* ---- 应用 ---- */
async function applyEdits(){
  if (BUSY) return;
  BUSY = true; $('btnApply').disabled = true;
  setLog('[apply] 按人工核定重算页码与目录…');
  try {
    const r = await post('/api/apply', {source: CUR});
    const j = await poll(r.job);
    if (j.status === 'done'){
      DIRTY = false; showDirty();
      await selectSource(CUR);
      log('[apply] 完成 —— 目录和页码已按你的核定重算');
    } else {
      log('❌ ' + (j.error || '重算失败'));
    }
  } catch (e){ log('❌ ' + e.message); }
  BUSY = false; $('btnApply').disabled = false;
}

/* ---------------------------------------------------------------- 阶段 */
async function runStage(kind){
  const body = {source: CUR, depth: DEPTH, footnotes: ($('fnMode') || {}).value || 'page-end'};
  try {
    const r = await post('/api/' + kind, body);
    const j = await poll(r.job);
    if (j.status === 'done'){
      await selectSource(CUR);
    } else log('❌ ' + (j.error || '失败'));
  } catch (e){ log('❌ ' + e.message); }
}

/* ---------------------------------------------------------------- 产物 */
function renderOut(){
  $('pOut').style.display = 'block';
  const d = DETAIL, oc = d.outline || {}, pc = d.page_calibration || {}, vf = d.verify || {};
  const stats = oc.level_stats || {};
  const exports = d.exports || [];
  const body = [];

  body.push(`<div class="row" style="margin-bottom:9px">
    <span class="hint">目录</span>
    <b class="mono">${oc.node_total || 0} 节点</b>
    <span class="hint">· 页码 <b class="mono">${esc(pc.verdict || '未校准')}</b>
      <span class="mono">${esc(pc.locator_type || '')}</span></span>
    ${vf.verdict ? `<span class="tag ${esc(vf.verdict)}">${esc(vf.verdict)}</span>` : ''}
  </div>`);

  const keys = Object.keys(stats).sort((a, b) => a - b);
  body.push('<div class="hint" style="margin-bottom:5px">按第几层导出（数字＝切出的文件数）</div><div class="dep">');
  keys.forEach(k => {
    body.push(`<div class="o ${+k === DEPTH ? 'on' : ''}" onclick="DEPTH=${k};renderOut()">
      <b>${stats[k].nodes}</b><span>第 ${k} 层</span></div>`);
  });
  body.push('</div>');

  body.push(`<div class="row" style="margin-top:10px">
    <label class="hint">脚注 <select id="fnMode" style="width:auto">
      <option value="page-end">页末</option><option value="inline">随文</option>
      <option value="drop">丢弃</option></select></label>
    <button class="primary sm" onclick="runStage('export')">导出 L${DEPTH}</button>
    <button class="sm" onclick="runStage('verify')">校验</button>
    <button class="sm" onclick="dl()">下载 zip</button>
  </div>`);

  if (exports.length){
    body.push('<table style="margin-top:10px"><tr><th>深度</th><th class="num">文件</th>'
      + '<th class="num">字数</th><th class="num">图</th><th></th></tr>');
    exports.forEach(e => {
      const s = e.stats || {};
      body.push(`<tr><td class="mono">${esc(e.depth)}</td><td class="num">${s.files || 0}</td>
        <td class="num">${num(s.chars)}</td><td class="num">${s.images_copied || 0}</td>
        <td><button class="sm" onclick="DEPTH=${parseInt(e.depth.replace('L',''),10)};renderOut()">选</button></td></tr>`);
    });
    body.push('</table>');
  } else {
    body.push('<p class="hint" style="margin:9px 0 0">还没导出过。</p>');
  }

  const gates = (vf.gates || []);
  if (gates.length){
    body.push('<div style="margin-top:11px">');
    gates.forEach(g => body.push(
      `<div class="g ${esc(g.status)}"><b>${esc(g.id)}</b> ${esc(g.title || '')}
        <span class="tag ${esc(g.status)}">${esc(g.status)}</span>
        <div class="hint">${esc(g.note || '')}</div></div>`));
    body.push('</div>');
  }
  $('outBody').innerHTML = body.join('');
}
function dl(){ window.location = `/api/download?id=${encodeURIComponent(CUR)}&depth=${DEPTH}`; }

/* ---------------------------------------------------------------- 新增源 */
async function loadMineruDirs(){
  try {
    const q = ($('mdQuery').value || '').trim();
    const r = await api('/api/mineru_dirs?q=' + encodeURIComponent(q));
    $('mdList').innerHTML = r.dirs.length ? r.dirs.map(d =>
      `<div class="mdrow ${d.registered ? 'done' : ''}" onclick="pickDir(this)"
            data-dir="${esc(d.dir)}">
         <span class="nm">${esc(d.name)}</span>
         <span class="pg">${d.pages}p · ${num(d.blocks)}块</span>
         <span class="hint">${d.registered ? '已登记' : ''}</span></div>`).join('')
      : '<div class="hint" style="padding:9px">没有匹配的工程。</div>';
  } catch (e){ $('mdList').innerHTML = '<div class="hint" style="padding:9px">读不到：' + esc(e.message) + '</div>'; }
}
function pickDir(el){
  if (!el.dataset.picked){
    el.dataset.picked = '1'; el.classList.add('sel');
    const cur = ($('adoptDirs').value || '').trim();
    $('adoptDirs').value = cur ? cur + ';' + el.dataset.dir : el.dataset.dir;
  } else {
    delete el.dataset.picked; el.classList.remove('sel');
    $('adoptDirs').value = ($('adoptDirs').value || '').split(';')
      .filter(x => x.trim() && x.trim() !== el.dataset.dir).join(';');
  }
}
function deriveTitle(name){
  return String(name).replace(/\.pdf$/i, '').replace(/[-_]\s*\d{4}\s*[-_].*$/, '').trim();
}

async function doAdopt(){
  const dirs = ($('adoptDirs').value || '').split(';').map(s => s.trim()).filter(Boolean);
  if (!dirs.length) return log('❌ 先选一个或多个 MinerU 工程目录');
  try {
    const r = await post('/api/adopt', {dirs: dirs, title: ($('ingestTitle').value || '').trim()});
    const j = await poll(r.job);
    if (j.status === 'done'){
      $('adoptDirs').value = '';
      CUR = j.result.source_id; await boot(); await selectSource(CUR);
    } else log('❌ ' + (j.error || '登记失败'));
  } catch (e){ log('❌ ' + e.message); }
}

async function doIngest(){
  const f = ($('ingestFile').value || '').trim();
  if (!f) return log('❌ 请填 PDF 绝对路径');
  setLog('[ingest] 提交中…');
  try {
    const r = await post('/api/ingest', {file: f, title: ($('ingestTitle').value || '').trim(),
      shard_mode: $('shardMode').value, shard_pages: parseInt($('shardPages').value, 10) || null});
    const j = await poll(r.job);
    if (j.status === 'done'){ CUR = j.result.source_id; await boot(); await selectSource(CUR); }
    else { await boot(); log('⚠️ OCR 没跑完，左栏「未完成」里可以续跑'); }
  } catch (e){ log('❌ ' + e.message); }
}

async function doResume(sid){
  try {
    const r = await post('/api/resume', {source: sid});
    const j = await poll(r.job);
    if (j.status === 'done'){ await boot(); if (CUR) await selectSource(CUR); }
    else log('❌ ' + (j.error || '续跑失败'));
  } catch (e){ log('❌ ' + e.message); }
}

async function doDelete(sid, title){
  if (!confirm(`把《${title}》移出工作台？\n\n` +
               `· 只移走 doclab 自己的产物（目录/页码/导出），进 _trash 可恢复\n` +
               `· 原始 PDF 不动\n· MinerU 本机工程目录不动\n\n` +
               `OCR 重跑要烧配额和几十分钟，别误点。`)) return;
  try {
    const r = await post('/api/delete', {source: sid});
    const j = await poll(r.job);
    if (j.status === 'done'){
      if (CUR === sid){ CUR = null; EDIT = null; }
      await boot();
    } else log('❌ ' + (j.error || '移除失败'));
  } catch (e){ log('❌ ' + e.message); }
}

function syncShardUI(){
  const m = $('shardMode').value;
  $('shardTip').textContent = m === 'mineru'
    ? '整本原 PDF 按片重复上传、每片带页范围，由 MinerU 选页。代价＝上传流量 × 片数。'
    : '本机 pypdf 先切成分片再上传（需装 pypdf）。';
}

/* ---------------------------------------------------------------- 页码格事件 */
$('pageList').addEventListener('change', e => {
  const inp = e.target.closest('input.pv');
  if (inp && !BUSY) commitPage(inp);
});
$('pageList').addEventListener('keydown', e => {
  if (e.target.closest('input.pv') && e.key === 'Enter'){ e.preventDefault(); e.target.blur(); }
});

boot().catch(e => { setLog('❌ 启动失败：' + e.message); });
