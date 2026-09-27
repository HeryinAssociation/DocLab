#!/usr/bin/env node
/**
 * doclab MCP server —— 薄封装，只做参数转换 + JSON 解析。
 *
 * 纪律（与 JingyeLab tools/mcp/README.md「不实现平行 client」一致）：
 *   所有逻辑都在 doclab.py 里，这里只 spawn 它、把 stdout 的 JSON 转成 MCP 结果。
 *   绝不在本文件里重写页码校准、目录推断、导出规则。
 *
 * 用法（stdio）：
 *   node mcp_server.cjs
 * 环境变量：
 *   DOCLAB_PYTHON  覆盖 Python 解释器路径
 *   DOCLAB_ROOT    覆盖 doclab 目录（默认取本文件所在目录）
 */
'use strict';

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = process.env.DOCLAB_ROOT || __dirname;
const CLI = path.join(ROOT, 'doclab.py');
const PYTHON = process.env.DOCLAB_PYTHON
  || 'C:\\Users\\Zhaoshuochen\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe';

// ------------------------------------------------------------------ 子进程

function runCli(args, timeoutMs = 3600000) {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [CLI, ...args], {
      cwd: ROOT,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      windowsHide: true,
    });
    let out = '', err = '';
    let done = false;
    const timer = setTimeout(() => {
      if (!done) { try { child.kill(); } catch (_) {} }
    }, timeoutMs);
    child.stdout.on('data', (d) => { out += d.toString('utf8'); });
    child.stderr.on('data', (d) => { err += d.toString('utf8'); });
    child.on('close', (code) => {
      done = true; clearTimeout(timer);
      resolve({ code, out, err });
    });
    child.on('error', (e) => {
      done = true; clearTimeout(timer);
      resolve({ code: -1, out, err: String(e) });
    });
  });
}

/** 从 CLI 输出里抠出 JSON。带 --json 时 stdout 末尾就是纯 JSON。 */
function parseJson(out) {
  const s = out.trim();
  if (!s) return null;
  const i = s.search(/^[[{]/m);
  if (i < 0) return null;
  try { return JSON.parse(s.slice(i)); } catch (_) { return null; }
}

async function cliJson(args, timeoutMs) {
  const r = await runCli([...args, '--json'], timeoutMs);
  const j = parseJson(r.out);
  if (!j) {
    throw new Error(`doclab 未返回 JSON（exit ${r.code}）\n${(r.err || r.out).slice(-2000)}`);
  }
  return j;
}

// ------------------------------------------------------------------ 工具定义

const S = (props, required = []) => ({ type: 'object', properties: props, required });

const TOOLS = [
  {
    name: 'doclab_probe',
    description: '自检：MinerU API 是否连通鉴权、token 来源、分片上限、pypdf 是否可用。',
    inputSchema: S({}),
    run: () => cliJson(['probe']),
  },
  {
    name: 'doclab_sources',
    description: '列出已登记的所有源（source_id、页数、分片、已完成到哪个阶段、校验结论）。',
    inputSchema: S({}),
    run: () => cliJson(['sources']),
  },
  {
    name: 'doclab_status',
    description: '看某个源的完整状态：工程信息、目录索引统计、页码校准、校验结论、已导出目录。',
    inputSchema: S({ source: { type: 'string', description: 'source_id（可前缀匹配；只有一个源时可省略）' } }),
    run: (a) => cliJson(['status', ...(a.source ? [a.source] : [])]),
  },
  {
    name: 'doclab_adopt',
    description: '登记本机已有的 MinerU 工程目录为源（不重跑 OCR）。给 dirs 或用 keyword 在 MinerU 输出根下自动找。',
    inputSchema: S({
      dirs: { type: 'array', items: { type: 'string' }, description: 'MinerU 工程目录绝对路径列表' },
      keyword: { type: 'string', description: '按目录名关键词自动查找' },
      source_id: { type: 'string' },
      title: { type: 'string' },
    }),
    run: (a) => cliJson(['adopt',
      ...(a.dirs && a.dirs.length ? ['--dirs', ...a.dirs] : []),
      ...(a.keyword ? ['--keyword', a.keyword] : []),
      ...(a.source_id ? ['--source-id', a.source_id] : []),
      ...(a.title ? ['--title', a.title] : []),
      '--force']),
  },
  {
    name: 'doclab_ingest',
    description: '把 PDF 送进 MinerU API 做 OCR（超 200 页自动分片），落成可解析的工程。耗时较长。',
    inputSchema: S({
      file: { type: 'string', description: 'PDF 绝对路径' },
      title: { type: 'string' },
      shard_pages: { type: 'integer', description: '分片页数上限，默认 200' },
      force: { type: 'boolean', description: '已存在同源时强制重跑' },
    }, ['file']),
    run: (a) => cliJson(['ingest', a.file,
      ...(a.title ? ['--title', a.title] : []),
      ...(a.shard_pages ? ['--shard-pages', String(a.shard_pages)] : []),
      ...(a.force ? ['--force'] : [])], 7200000),
  },
  {
    name: 'doclab_pagecal',
    description: '页码校准：由 MinerU 的 page_number / header 块推出「物理页 → 纸书页码」的分区间映射。'
      + '校准不出会按规范降级 section_only，不猜偏移。返回区间表、断点、跨分片接续结论。',
    inputSchema: S({ source: { type: 'string' } }),
    run: (a) => cliJson(['pagecal', ...(a.source ? [a.source] : [])]),
  },
  {
    name: 'doclab_outline',
    description: '构建目录索引：推断章节层级树，抽出书内印刷目录做交叉校验，'
      + '并用目录补齐 MinerU 漏标的章。返回各层节点数（选切分深度用）、目录补章动作、匹配率。',
    inputSchema: S({ source: { type: 'string' } }),
    run: (a) => cliJson(['outline', ...(a.source ? [a.source] : [])]),
  },
  {
    name: 'doclab_export',
    description: '按深度切分导出：一节一个 markdown，段落级页锚 <!-- p=N -->（纸书页码），'
      + '附 conversion_meta.json / manifest.json / 00-目录.md。只写 md，不碰 Zotero 与证据表。',
    inputSchema: S({
      source: { type: 'string' },
      depth: { type: 'integer', description: '切到第几层；0 = 整本一个文件' },
      footnotes: { type: 'string', enum: ['page-end', 'inline', 'drop'] },
      no_images: { type: 'boolean' },
    }, ['depth']),
    run: (a) => cliJson(['export',
      ...(a.source ? [a.source] : []),
      '--depth', String(a.depth),
      ...(a.footnotes ? ['--footnotes', a.footnotes] : []),
      ...(a.no_images ? ['--no-images'] : [])]),
  },
  {
    name: 'doclab_verify',
    description: '四道闸门校验导出产物：G1 页锚覆盖率 / G2 内容无损回查 / G3 页码一致性 / '
      + 'G4 入库硬条件（有页锚或 locator_type=section_only）。返回每道闸门的 pass/warn/fail。',
    inputSchema: S({ source: { type: 'string' }, depth: { type: 'integer' } }),
    run: (a) => cliJson(['verify', ...(a.source ? [a.source] : []),
      ...(a.depth !== undefined ? ['--depth', String(a.depth)] : [])]),
  },
  {
    name: 'doclab_grep',
    description: '在正文块里全文检索（去空白＋全角折半角后包含匹配）。'
      + '目录核对时用它找疑似漏掉的标题：按标题原文搜，从命中的块拿 gid，'
      + '再在目录 JSON 里新增该条目。输出带 [h] 的是 MinerU 自标标题。',
    inputSchema: S({
      q: { type: 'string', description: '关键词，如「第四章」「结束语」' },
      source: { type: 'string' },
      gid: { type: 'string', description: '只在这些块里搜：36 或 36-124' },
      page: { type: 'string', description: '只取某个定位符的页，如 57 / front-9' },
      limit: { type: 'integer' },
    }, ['q']),
    run: (a) => cliJson(['grep', ...(a.source ? [a.source] : []),
      '--q', a.q,
      ...(a.gid ? ['--gid', a.gid] : []),
      ...(a.page ? ['--page', a.page] : []),
      ...(a.limit ? ['--limit', String(a.limit)] : [])]),
  },
  {
    name: 'doclab_toc_export',
    description: '把当前目录结构导出为 JSON（AI 语义核对的入口）。平铺、文档顺序，'
      + '每条带 key/level/title/gid/页码/字数。depth=核对深度，更深的条目不导出＝核对时忽略。'
      + '同时落盘到工作目录的 目录树.json。',
    inputSchema: S({
      source: { type: 'string' },
      depth: { type: 'integer', description: '核对深度（默认 3；重点核到 L2 就给 2）' },
    }),
    run: (a) => cliJson(['toc-export', ...(a.source ? [a.source] : []),
      '--depth', String(a.depth === undefined ? 3 : a.depth)]),
  },
  {
    name: 'doclab_toc_import',
    description: '把 AI 修改后的目录 JSON 导回：与当前树 diff，自动翻译成人工核定层落盘并重算。'
      + '改法：层级错了改 level；误识别条目整条删掉；缺标题先 doclab_grep 拿 gid 再新增'
      + '（key 留空、必须带 gid+title+level）。标题文本不许手改。'
      + '默认演练返回将要应用的变更清单，apply=true 才落盘。',
    inputSchema: S({
      file: { type: 'string', description: 'AI 修改后的目录 JSON 文件路径' },
      source: { type: 'string' },
      apply: { type: 'boolean', description: '真的落盘并重算（默认只演练）' },
    }, ['file']),
    run: (a) => cliJson(['toc-import', ...(a.source ? [a.source] : []),
      '--file', a.file,
      ...(a.apply ? ['--apply'] : [])]),
  },
  {
    name: 'doclab_run',
    description: '一次跑完 pagecal → outline → export → verify。文献处理环节的一键入口。',
    inputSchema: S({
      source: { type: 'string' },
      depth: { type: 'integer' },
      footnotes: { type: 'string', enum: ['page-end', 'inline', 'drop'] },
    }, ['depth']),
    run: async (a) => {
      const src = a.source ? [a.source] : [];
      const cal = await cliJson(['pagecal', ...src]);
      const out = await cliJson(['outline', ...src]);
      const exp = await cliJson(['export', ...src, '--depth', String(a.depth),
        ...(a.footnotes ? ['--footnotes', a.footnotes] : [])]);
      const ver = await cliJson(['verify', ...src, '--depth', String(a.depth)]);
      return { calibration: cal, outline: out, export: exp, verify: ver };
    },
  },
  {
    name: 'doclab_sections',
    description: '列出某深度的分节清单（序号、标题、纸书页范围、字数、文件名）。'
      + 'Agent 从这里挑要读哪一节。',
    inputSchema: S({ source: { type: 'string' }, depth: { type: 'integer' } }),
    run: (a) => cliJson(['sections', ...(a.source ? [a.source] : []),
      '--depth', String(a.depth === undefined ? 2 : a.depth)]),
  },
  {
    name: 'doclab_read_section',
    description: '读某一节的 markdown 正文（含段落级页锚）。这是 Agent 取用文献内容的主入口；'
      + '引用页码时请直接用正文里的 <!-- p=N --> 锚，不要自己推算。',
    inputSchema: S({
      section: { type: 'string', description: '序号（如 014）或文件名前缀' },
      source: { type: 'string' },
      depth: { type: 'integer' },
      page: { type: 'string', description: '只取某个页锚下的内容，如 287' },
    }, ['section']),
    run: (a) => cliJson(['read', a.section, ...(a.source ? [a.source] : []),
      '--depth', String(a.depth === undefined ? 2 : a.depth),
      ...(a.page ? ['--page', a.page] : [])]),
  },
];

// ------------------------------------------------------------------ JSON-RPC

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

async function handle(req) {
  const { id, method, params } = req;
  const reply = (result) => send({ jsonrpc: '2.0', id, result });
  const fail = (code, message) => send({ jsonrpc: '2.0', id, error: { code, message } });

  try {
    if (method === 'initialize') {
      return reply({
        protocolVersion: '2024-11-05',
        capabilities: { tools: {} },
        serverInfo: { name: 'doclab', version: '0.1.0' },
      });
    }
    if (method === 'notifications/initialized' || method === 'initialized') return;
    if (method === 'ping') return reply({});

    if (method === 'tools/list') {
      return reply({
        tools: TOOLS.map((t) => ({
          name: t.name, description: t.description, inputSchema: t.inputSchema,
        })),
      });
    }

    if (method === 'tools/call') {
      const name = params && params.name;
      const args = (params && params.arguments) || {};
      const tool = TOOLS.find((t) => t.name === name);
      if (!tool) return fail(-32602, `未知工具 ${name}`);
      const data = await tool.run(args);
      return reply({
        content: [{ type: 'text', text: JSON.stringify(data, null, 2) }],
        structuredContent: data,
        isError: false,
      });
    }

    return fail(-32601, `不支持的方法 ${method}`);
  } catch (e) {
    return reply({
      content: [{ type: 'text', text: `doclab 执行失败：${e.message}` }],
      isError: true,
    });
  }
}

let buf = '';
let inflight = 0;
let stdinEnded = false;

function maybeExit() {
  if (stdinEnded && inflight === 0) setTimeout(() => process.exit(0), 20);
}

process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buf += chunk;
  let nl;
  while ((nl = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, nl).trim();
    buf = buf.slice(nl + 1);
    if (!line) continue;
    let req;
    try { req = JSON.parse(line); } catch (_) { continue; }
    if (req.id === undefined && !req.method) continue;
    inflight += 1;
    // 工具调用可能跑几分钟，必须等它返回再允许退出，
    // 否则管道式调用（stdin 立刻关闭）会把结果截断。
    Promise.resolve(handle(req)).finally(() => { inflight -= 1; maybeExit(); });
  }
});
process.stdin.on('end', () => { stdinEnded = true; maybeExit(); });
