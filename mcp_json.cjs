'use strict';
/**
 * 从 CLI 输出里抠出 JSON。带 --json 时 stdout 末尾就是纯 JSON。
 *
 * 不能只用 `^[[{]/m` 找第一个匹配：CLI 的日志行本身就常以 `[export]`、`[epub]`
 * 开头，先匹配到日志就会把后面的 JSON 一起交给 JSON.parse，整次调用被误判失败。
 * 从末尾往前找第一个能解析成 JSON 的起点，日志里那些 `[...]` 行自然被跳过。
 */
function parseJson(out) {
  const s = String(out || '').trim();
  if (!s) return null;
  try { return JSON.parse(s); } catch (_) {}
  const lines = s.split(/\r?\n/);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const t = lines[i].trim();
    if (!t.startsWith('{') && !t.startsWith('[')) continue;
    try { return JSON.parse(lines.slice(i).join('\n')); } catch (_) {}
  }
  return null;
}

module.exports = { parseJson };
