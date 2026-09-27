
let BOOT = null, CUR = null, EDIT = null, DETAIL = null;
let DEPTH = 2, TAB = 'tree', DIRTY = false, BUSY = false;
let PAGE_ROWS = [], PAGE_BY_SHARD = {};

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
  if (t === 'page' || t === 'tree') setTab(t);
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
  renderSources();
  const row = (BOOT.sources || []).find(s => s.source_id === id);
  $('hDoc').textContent = row ? row.title : id;
  $('pNoSrc').style.display = 'none';

  DETAIL = await api('/api/source?id=' + encodeURIComponent(id));
  renderIngest();
  renderOut();

  if (!DETAIL.outline || !DETAIL.outline.node_total){
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
  renderTreeList();
  renderPageList();
  showDirty();
}
function setTab(t){
  TAB = t;
  $('tabBtnTree').classList.toggle('on', t === 'tree');
  $('tabBtnPage').classList.toggle('on', t === 'page');
  $('tabTree').style.display = t === 'tree' ? 'block' : 'none';
  $('tabPage').style.display = t === 'page' ? 'block' : 'none';
}
function showDirty(){
  $('dirtyTip').style.display = DIRTY ? 'inline-block' : 'none';
  $('btnApply').classList.toggle('primary', DIRTY);
}
function markDirty(){ DIRTY = true; showDirty(); }

/* ---- 目录层级 ---- */
/* 用人工层级先算出**实际深度**（与后端 outline.reflow 同一套层级栈）。
   必须实时算：把 L2 的某节点提到 L1 后，紧跟在它后面的同级节点会被它并进去 ——
   这是层级栈的必然结果，不是 bug。不让操作者当场看见，他就会在点「应用」之后
   对着一个没预料到的结构发呆。 */
function reflowPreview(){
  const lv = {}, depth = {};
  for (const n of EDIT.nodes) lv[n.gid] = EDIT.levels[String(n.gid)] || n.level;
  const stack = [];
  for (const n of EDIT.nodes){
    const L = lv[n.gid];
    while (stack.length && stack[stack.length - 1] >= L) stack.pop();
    depth[n.gid] = stack.length + 1;
    stack.push(L);
  }
  return {lv, depth};
}

function renderTreeList(){
  const keep = $('treeList').scrollTop;
  const q = ($('treeQ').value || '').trim();
  const nodes = EDIT.nodes || [];
  const stat = EDIT.level_stats || {};
  const manual = EDIT.levels || {};
  const dist = Object.keys(stat).sort((a, b) => a - b)
    .map(k => `L${k}=${stat[k].nodes}`).join(' · ');
  $('treeMeta').textContent = `${EDIT.node_total} 节点`
    + (Object.keys(manual).length ? ` · 人工改过 ${Object.keys(manual).length} 处` : '')
    + (dist ? ` · ${dist}` : '');

  const {lv, depth} = reflowPreview();
  const html = nodes.map(n => {
    if (q && !n.title.includes(q)) return '';
    const set = lv[n.gid], d = depth[n.gid];
    const kb = [1, 2, 3, 4, 5, 6].map(k =>
      `<b class="${k === set ? 'on' : ''}" title="定为第 ${k} 级"
          onclick="setLevel(${n.gid},${k})">${k}</b>`).join('');
    const mm = manual[String(n.gid)] ? '<i class="mm">人工</i>' : '';
    // 跳级会被压平（树里深度就是深度），这时把「设了几级 / 实际几级」都摆出来
    const gap = set !== d ? `<i class="mm" title="设为第 ${set} 级，但树里实际落在第 ${d} 级">Δ</i>` : '';
    return `<div class="trow lv${Math.min(d, 3)}" id="tr_${n.gid}"
                 style="padding-left:${10 + (d - 1) * 16}px">
      <span class="tt">${esc(n.title)}${mm}${gap}</span>
      <span class="meta">L${d} · ${num(n.chars)}字</span>
      <span class="kb">${kb}</span>
    </div>`;
  }).join('');
  $('treeList').innerHTML = html || '<div class="hint" style="padding:9px">没有匹配的标题。</div>';
  $('treeList').scrollTop = keep;
}

async function setLevel(gid, lv){
  EDIT.levels[String(gid)] = lv;
  markDirty();
  renderTreeList();          // 立刻重排显示，让人看见这一改的后果
  try {
    await post('/api/edit/level', {source: CUR, gid: gid, level: lv});
  } catch (e){ log('❌ 定级没存上：' + e.message); }
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
