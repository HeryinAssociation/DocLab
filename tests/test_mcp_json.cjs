'use strict';
/** CLI 输出里的 JSON 提取：日志行常以 [export]/[epub] 开头，不能被当成 JSON。 */
const assert = require('assert');
const { parseJson } = require('../mcp_json.cjs');

// 1) 纯对象 / 纯数组
assert.deepStrictEqual(parseJson('{"ok":true,"n":1}'), { ok: true, n: 1 });
assert.deepStrictEqual(parseJson('[1,2]'), [1, 2]);

// 2) CLI 日志（以 [ 开头）后面跟格式化 JSON —— 之前正是这里误判失败
const log = [
  'doclab v0.2.0｜砚 · 文献处理工作台',
  '',
  '[epub] source ← out/source/L1',
  '[epub] 书.epub：21 章 / 0 图 / 420 KB',
  '[epub] ⚠️ 缺图，已置为占位：images/x.jpg',
  '{',
  '  "name": "书.epub",',
  '  "chapters": 21,',
  '  "verify": {"ok": true}',
  '}',
  '',
].join('\n');
const got = parseJson(log);
assert.strictEqual(got.name, '书.epub');
assert.strictEqual(got.chapters, 21);
assert.strictEqual(got.verify.ok, true);

// 3) 数组形式的工具输出（如 sources）前面也有日志
const arr = parseJson('doclab v0.2.0\n\n[\n  {"source_id":"a"}\n]');
assert.strictEqual(arr.length, 1);
assert.strictEqual(arr[0].source_id, 'a');

// 4) 日志里出现 `[` 但不是 JSON 起点时继续往前找
assert.deepStrictEqual(parseJson('[not-json]\n[export] 完成\n{"ok":true}'), { ok: true });

// 5) 没有 JSON
assert.strictEqual(parseJson(''), null);
assert.strictEqual(parseJson('只有日志，没有 JSON'), null);
assert.strictEqual(parseJson('{ 坏的 JSON'), null);

// 6) 多个 JSON 块：取最后一个能解析的（CLI 约定 JSON 在末尾）
assert.deepStrictEqual(parseJson('{"old":1}\n[log]\n{"new":2}'), { new: 2 });

console.log('✓ mcp_json parseJson: 6 组用例通过');
