
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
let src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
src = src.replace(/boot\(\)\.catch\([\s\S]*?\);\s*$/, '');
src += `
;globalThis.__t = {
  renderBlocks: renderBlockList,
  renderTree: renderTreeList,
  load: loadBlocks,
  pick: pickBlock,
  pickLv: pickLv,
  confirm: confirmAdd,
  setEdit: (e) => { EDIT = e; },
  blk: () => BLK,
  pickGid: () => BLK_PICK,
  title: () => BLK_TITLE,
  lv: () => BLK_PICK_LV,
};
`;

const CALLS = [];
const BLOCKS = { total: 3, rows: [
  { gid: 31, shard: 'P1', page_idx: 6, printed: 'front-3', type: 'text',
    text: '第三节 数字人文的范式演进\n正文从这里开始，讲的是范式。', full_len: 300,
    is_heading: false, in_tree: false, level: null, added: false, deleted: false },
  { gid: 74, shard: 'P1', page_idx: 20, printed: '5', type: 'text',
    text: '二 认识论的历史', full_len: 9,
    is_heading: true, in_tree: true, level: 2, added: false, deleted: false },
  { gid: 88, shard: 'P1', page_idx: 30, printed: '18', type: 'text',
    text: '刚刚补过的那一条', full_len: 8,
    is_heading: false, in_tree: true, level: 3, added: true, deleted: false },
]};

const els = {};
function el(id){
  if (!els[id]) els[id] = {
    id, value: '', innerHTML: '', textContent: '', className: '', title: '',
    scrollTop: 0, style: {}, display: '',
    classList: { toggle(){}, add(){}, remove(){}, contains(){ return false; } },
    addEventListener(){}, closest(){ return null; }, focus(){}, select(){}, blur(){},
  };
  return els[id];
}
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  document: {
    getElementById: el,
    querySelectorAll: () => [],
    addEventListener(){},
    createElement: () => el('tmp' + Math.random()),
  },
  localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
  confirm: () => true,
  alert(){},
  fetch: async (path, opts) => {
    CALLS.push({ path, body: opts && opts.body ? JSON.parse(opts.body) : null });
    if (path.indexOf('/api/blocks') === 0)
      return { ok: true, json: async () => ({ ok: true, ...BLOCKS }) };
    if (path.indexOf('/api/edit') === 0)
      return { ok: true, json: async () => ({
        ok: true, source_id: 'S', node_total: 3, levels: {}, anchors: {}, added: {},
        nodes: [
          { key: '31', gid: 31, level: 1, title: '原有条一', own_chars: 10, chars: 100 },
          { key: 'add:31', gid: 31, level: 2, title: '第三节 数字人文的范式演进',
            own_chars: 0, chars: 0, pending_add: true, flags: ['manual'] },
          { key: '74', gid: 74, level: 2, title: '原有条二', own_chars: 20, chars: 200 },
        ] }) };
    return { ok: true, json: async () => ({ ok: true }) };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const T = sandbox.__t;

(async () => {
  const out = {};
  T.setEdit({ node_total: 3, levels: {}, added: {}, nodes: [] });
  el('blkQ').value = '范式';
  await T.load(0);
  T.renderBlocks();
  out['列表'] = {
    list: el('blkList').innerHTML,
    meta: el('blkMeta').textContent,
    page: el('blkPageBar').style.display,
    total: el('blkTotalTip').textContent,
  };

  // 选中第二块（已是标题，也允许再加断点）→ 看编辑条
  el('blkQ').value = ''; await T.load(0);
  T.pick(31);
  T.renderBlocks();
  out['点设为标题'] = {
    host: el('blkAddHost').innerHTML,
    title: T.title(),
    lv: T.lv(),
    pick: T.pickGid(),
  };

  // 改成第 3 级，再填一个自定义标题，确认提交
  el('addTitle').value = '第三节 数字人文的范式演进（修正）';
  el('addOff').value = '12';
  T.pickLv(3);
  out['改级后'] = { host: el('blkAddHost').innerHTML, title: T.title(), lv: T.lv() };
  el('addTitle').value = '第三节 数字人文的范式演进（修正）';
  el('addOff').value = '12';
  await T.confirm();
  out['提交'] = { calls: CALLS.slice(), host: el('blkAddHost').innerHTML };

  // 目录列表：待重算的新增行
  T.setEdit({ node_total: 3, levels: {}, added: {},
    nodes: [
      { key: '31', gid: 31, level: 1, title: '原有条一', own_chars: 10, chars: 100 },
      { key: 'add:31', gid: 31, level: 2, title: '第三节 数字人文的范式演进',
        own_chars: 0, chars: 0, pending_add: true, flags: ['manual'] },
    ] });
  T.renderTree();
  out['目录列表'] = { list: el('treeList').innerHTML };

  globalThis.__out = out;
})();
setTimeout(() => console.log(JSON.stringify(globalThis.__out || {})), 400);
