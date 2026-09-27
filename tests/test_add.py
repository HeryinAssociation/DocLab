"""人工新增标题（补 MinerU 漏掉的标题）＝ 在文档流上立一个断点。

这一层要钉死的事：

  语义：断点落在**某个块的某个字符位置**上（offset＝块内位移，0 就是块首）。
        MinerU 的块就是段落（实测两本书里含换行的块只有 1 个和 5 个），
        所以 offset 绝大多数就是 0；留着它是为了「标题和正文被并进同一段」时能切进去。
  正文：**一字不动**。这块原文照旧留在正文里，新增的标题在导出时以 `# 标题` 出现
        （景晔选的口径：两处并存）。
  守恒：一个块的字数只归一个节点。块内断点把段落切开时，两半各归一边，
        Σ own_chars 仍等于全书字数。
  撤销：删掉一条人工新增 ≠ 「剔除识别错的条目」，是**撤销这次新增**，
        所以不写进 deleted 档案（否则「已删除」里会混进一条从来没被识别出来过的标题）。

不发网络；建树/导出读本机已有产物，写盘只写系统临时目录。
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import manual                                       # noqa: E402
from core.config import work_dir                              # noqa: E402
from core.exporter import export, frontier                    # noqa: E402
from core.outline import build_tree, node_key, walk_nodes     # noqa: E402
from core.pagecal import calibrate                            # noqa: E402
from core.project import NOISE_TYPES, iter_blocks, is_noise_text, load_shards  # noqa: E402

SID = "中国数字人文发展报告"
# 临时目录放到系统 temp，不落在工作区里。原因有两条：
#   1) 工作区带批量删除保护（一次删太多文件会被拦），清理一失败整个用例就判红 ——
#      而那是环境策略，不是产品逻辑。断言全过了却因为删不掉临时目录报 FAIL，会教人
#      忽略红灯。
#   2) 临时产物不该在仓库里堆着，跑一轮留一堆目录。
TMP = pathlib.Path(tempfile.gettempdir()) / "_doclab_test_add"
NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
FAILS = []


def rm(path: pathlib.Path) -> None:
    """清临时目录。删不掉只提示，绝不让用例红 —— 见 TMP 处的说明。"""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except BaseException as e:                                  # noqa: BLE001
        print(f"    （清不掉 {path}：{type(e).__name__}，跳过）")

UI_JS = r"""
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
  setBoot: (b) => { BOOT = b; },
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
  // BOOT 必须先给：loadBlocks 开头就是 hasCap('blocks')，而 hasCap 读 BOOT.caps。
  // 不给的话它直接早退，整个「正文块」视图一个字都不渲染 —— 探针会全红，却看着像
  // 产品坏了。这条桩之前一直缺，是因为用例在那之前就被别的事打断了，从没跑到这儿。
  T.setBoot({caps: ['edit', 'edit_add', 'blocks', 'epub'],
             code_stamp: 'test', pid: 1, sources: []});
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
"""


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def run_ui():
    js = TMP / "_add_probe.js"
    js.write_text(UI_JS, encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(ROOT / "ui" / "index.html")],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print("node 跑失败：\n" + r.stderr[-1800:])
        return None
    return json.loads(r.stdout)


def main() -> int:
    TMP.mkdir(exist_ok=True)

    # ---------------------------------------------------------- 存储层（临时目录）
    print("== 存储层 ==")
    pwd = TMP / "add_probe"
    rm(pwd)
    pwd.mkdir(parents=True)
    manual.set_added(pwd, 31, "第三节 数字人文", 3, 0)
    d = manual.load(pwd)
    check("新增写进 added 段", d["added"].get("31", {}).get("title") == "第三节 数字人文")
    check("层级、位移一起存下",
          d["added"]["31"]["level"] == 3 and d["added"]["31"]["offset"] == 0)
    check("summary 里有 added 计数", manual.summary(pwd)["added"] == 1)
    manual.set_added(pwd, 31, "改过的标题", 2, 5)
    check("同一块再写一次＝改这一条（不会堆两条）",
          manual.summary(pwd)["added"] == 1
          and manual.load(pwd)["added"]["31"]["offset"] == 5)
    manual.clear_added(pwd, 31)
    check("撤销后 added 为空", manual.summary(pwd)["added"] == 0)
    # 删一条人工新增 ＝ 撤销新增，不写 deleted 档案
    manual.set_added(pwd, 31, "第三节", 2, 0)
    manual.set_deleted(pwd, "add:31", {"title": "第三节"})
    d2 = manual.load(pwd)
    check("删掉人工新增＝撤销（added 已清）", "31" not in d2["added"], str(d2["added"]))
    check("删掉人工新增**不**写 deleted 档案", "add:31" not in d2["deleted"],
          str(d2["deleted"]))
    check("撤销时也清掉它的定级", "add:31" not in d2["levels"])
    manual.set_added(pwd, 31, "第三节", 2, 0)
    manual.set_level(pwd, "add:31", 4)
    manual.clear_all(pwd)
    check("clear_all 也清 added", manual.summary(pwd)["added"] == 0)

    # ---------------------------------------------------------- 建树（真实数据，只读）
    print("\n== 建树：断点落进树里 ==")
    wd = work_dir(SID)
    shards = load_shards(wd)
    blocks = iter_blocks(shards)
    calib = calibrate(SID, blocks, [s.to_dict() for s in shards], anchors={})
    bmap = {b.gid: b for b in blocks}

    def btext(b):
        return b.text or b.table_body or ""

    # 「全书字数」＝过噪声/非空过滤后的正文块总长。
    # 不能拿 `if b.is_content` 直接相加 —— 那会把页眉页脚与纯空白块也算进去
    # （本书 403694 vs 400310），而 attach_content 是不计这些的。
    # 这个数同时等于 outline.json 里 L1 各条 n_chars 相加（已逐条验证）。
    total = sum(len(btext(b)) for b in blocks
                if b.is_content and not is_noise_text(b.text) and btext(b).strip())

    def run(added=None, levels=None, deleted=None):
        roots, oc = build_tree(SID, blocks, doc_title="t", calib=calib,
                               manual_levels=levels or {}, manual_deleted=deleted or {},
                               manual_added=added or {})
        ns = [n for r in roots for n in walk_nodes(r)]
        return ns, oc

    def own_sum(ns):
        return sum(n.n_chars - sum(c.n_chars for c in n.children) for n in ns)

    n0, o0 = run()
    in_tree = {n.gid_start for n in n0}
    # 断点落点必须在**真正文**里：跳过前置区（封面/书名页/目录页，那些块归「前置」节点）、
    # 跳过已经立过标题的块（在那些块上再立一个就是重复）、跳过带换行的块
    # （导出时一个块会摊成多行，首行断言就没法比）。
    front_end = max(n.gid_end for n in n0 if n.marker == "front")
    cover = {}
    for n in n0:
        if n.marker == "front":
            continue
        for g in range(n.gid_start, n.gid_end + 1):
            cover.setdefault(g, n)
    prefix_count = Counter(btext(b)[:24] for b in blocks if btext(b).strip())
    body = next(b for b in blocks
                if b.type == "text" and b.gid not in in_tree
                and b.gid > front_end and len(btext(b)) >= 150
                and "\n" not in btext(b) and b.gid in cover
                and prefix_count[btext(b)[:24]] == 1)
    g = body.gid
    check("基线：Σ自有字数＝全书字数", own_sum(n0) == total, f"{own_sum(n0)} / {total}")
    _r0, _ = build_tree(SID, blocks, doc_title="t", calib=calib)
    _l1 = sum(x.n_chars for x in _r0)
    check("基线：Σ自有字数＝L1 各条相加（另一条路算出来要一样）",
          _l1 == total, f"{_l1} / {total}")
    check("旧产物没有 manual_added 字段时也照跑",
          o0.get("manual_added") == [] and o0.get("manual_added_count") == 0)
    print(f"       （断点落点：gid {g}，{body.shard} p{body.page_idx}，{len(btext(body))} 字）")

    nA, oA = run({str(g): {"title": "第三节 测试断点", "level": 2, "offset": 0}})
    addA = next((n for n in nA if node_key(n) == f"add:{g}"), None)
    check("新增节点出现在树里", addA is not None)
    check("主键是 add:<gid>（不与真实块撞车）",
          addA is not None and node_key(addA) == f"add:{g}")
    check("层级＝人工给的那一级", addA is not None and addA.level == 2,
          f"L{addA.level if addA else '—'}")
    check("标记为 manual（不是 from_toc）",
          addA is not None and addA.flags == ["manual"], str(addA.flags if addA else None))
    check("标题文本就是人填的", addA is not None and addA.title == "第三节 测试断点")
    check("节点数 +1", len(nA) == len(n0) + 1, f"{len(n0)} → {len(nA)}")
    check("Σ自有字数仍守恒（块只归一个节点）", own_sum(nA) == total,
          f"{own_sum(nA)} / {total}")
    check("别的条目级数一律不动",
          all(n.level == next(m.level for m in n0 if node_key(m) == node_key(n))
              for n in nA if not node_key(n).startswith("add:")),
          "有邻居被改写")
    check("outline 里记下了这次新增",
          oA.get("manual_added_count") == 1
          and oA["manual_added"][0]["status"] == "已加")

    # 块内位移：断点切进段落内部，前半仍归前一个节点
    nB, _ = run({str(g): {"title": "第三节 测试断点", "level": 2, "offset": 30}})
    addB = next(n for n in nB if node_key(n) == f"add:{g}")
    prevA = next((n for n in nA if not node_key(n).startswith("add:")
                  and n.gid_start <= g <= n.gid_end), None)
    prevB = next((n for n in nB if not node_key(n).startswith("add:")
                  and n.gid_start <= g <= n.gid_end), None)

    def own(n):
        return n.n_chars - sum(c.n_chars for c in n.children)

    check("位移>0 时新节点少拿前 30 字",
          own(addB) == own(addA) - 30, f"{own(addA)} → {own(addB)}")
    check("位移>0 时前一个节点多拿那 30 字",
          prevB is not None and prevA is not None and own(prevB) == own(prevA) + 30,
          f"{own(prevA) if prevA else '—'} → {own(prevB) if prevB else '—'}")
    check("切分之后 Σ 仍守恒", own_sum(nB) == total, f"{own_sum(nB)} / {total}")
    check("offset 落在节点上（导出要用）", addB.offset == 30, str(addB.offset))

    # 删掉这条新增 → 完全回到基线
    nC, _ = run({str(g): {"title": "第三节", "level": 2, "offset": 0}},
                deleted={f"add:{g}": {"title": "第三节"}})
    check("撤销新增后节点数回到基线", len(nC) == len(n0), f"{len(nC)} / {len(n0)}")
    check("撤销后 Σ 仍守恒", own_sum(nC) == total)
    check("撤销后树里没有残留的 add 节点",
          not any(node_key(n).startswith("add:") for n in nC))

    # 人在界面上改过级数 → levels 优先于 added 里的初值
    nD, _ = run({str(g): {"title": "第三节", "level": 2, "offset": 0}},
                levels={f"add:{g}": 3})
    addD = next(n for n in nD if node_key(n) == f"add:{g}")
    check("人工改过级数时以 levels 为准（added 里只是初值）", addD.level == 3,
          f"L{addD.level}")

    # ---------------------------------------------------------- 导出（临时目录）
    print("\n== 导出：断点切开正文 ==")
    for tag, off in (("off0", 0), ("off30", 30)):
        d = TMP / f"add_exp_{tag}"
        rm(d)
        # 造两个「上一轮留下的」文件：一个是上一轮切出来的正文（这一轮不再产生），
        # 一个是校验报告（不是正文，任何情况都不该被动）。
        d.mkdir(parents=True, exist_ok=True)
        (d / "999_上一轮切出来的.md").write_text("# 幽灵\n", encoding="utf-8")
        (d / "校验报告.md").write_text("# 校验\n", encoding="utf-8")
        _ns, _oc, tree = (lambda r, o: (None, None, [n.to_dict() for n in r]))(
            *build_tree(SID, blocks, doc_title="t", calib=calib,
                        manual_levels={}, manual_deleted={},
                        manual_added={str(g): {"title": "第三节 测试断点", "level": 2,
                                               "offset": off}}))
        meta = export(SID, wd, d, blocks, calib,
                      {"tree": tree, "doc_title": "t", "source_id": SID}, 2,
                      copy_images=False, shards_meta=[s.to_dict() for s in shards])
        check(f"offset={off}：清掉上一轮留下的正文文件（不留幽灵）",
              not (d / "999_上一轮切出来的.md").exists()
              and meta["stats"]["stale_removed"] == 1,
              str(meta["stats"].get("stale_removed")))
        check(f"offset={off}：不碰校验报告.md / 00-目录.md",
              (d / "校验报告.md").exists() and (d / "00-目录.md").exists())
        files = sorted(p for p in d.glob("*.md") if p.name != "00-目录.md")
        hit = [p for p in files if "第三节 测试断点" in p.read_text(encoding="utf-8")[:600]]
        check(f"offset={off}：新增标题有自己的文件", len(hit) == 1,
              f"{len(hit)} 个")
        if not hit:
            continue
        txt = hit[0].read_text(encoding="utf-8")
        lines = txt.split("\n")
        head = next(i for i, x in enumerate(lines) if x.startswith("<!-- p="))
        segs = [x for x in lines[head:] if x.strip() and not x.startswith("<!--")]
        # 比首**行**而不是首 20 字：块是多行时，一个块会在 md 里摊成好几行。
        # 这里挑的块保证单行，所以两者等价 —— 但断言写成「首行」才对得上导出行为。
        want_head = btext(body)[off:].split("\n")[0].strip()
        check(f"offset={off}：断点后首段＝原文第 {off} 字起",
              bool(segs) and segs[0].strip().startswith(want_head[:20]),
              (segs[0][:24] if segs else "（空）"))
        prev = files[files.index(hit[0]) - 1]
        ptxt = prev.read_text(encoding="utf-8")
        if off == 0:
            check("offset=0：前一个文件不再含这块原文",
                  btext(body)[:24] not in ptxt)
        else:
            check("offset>0：前一个文件保留了段落前半截",
                  btext(body)[:off] in ptxt, f"前 {off} 字")
            check("offset>0：前一个文件不含后半截",
                  btext(body)[off:off + 20] not in ptxt)
        check("整段原文在全书里只出现一次（切分不重复、不丢字）",
              sum(p.read_text(encoding="utf-8").count(btext(body)[:24])
                  for p in files) == 1)

    # ---------------------------------------------------------- 取块算法 A/B
    print("\n== 取块算法 A/B（无人工断点时必须与改造前逐块一致）==")
    oc0 = json.loads((wd / "outline.json").read_text(encoding="utf-8"))
    bad = 0
    for depth in (1, 2, 3):
        nodes = frontier(oc0["tree"], depth)
        if not nodes:
            continue
        marks = sorted({(int(n["gid_start"]), int(n.get("offset") or 0)) for n in nodes})
        nxt = {m: (marks[i + 1] if i + 1 < len(marks) else None)
               for i, m in enumerate(marks)}
        bg = {b.gid: b for b in blocks}
        for n in nodes:
            g0, g1 = int(n["gid_start"]), int(n["gid_end"])
            lo = int(n.get("offset") or 0)
            stop = nxt.get((g0, lo))
            old = [x for x in range(g0, g1 + 1) if x in bg]
            hi = g1
            if stop is not None and stop[1] > 0 and stop[0] > g1:
                hi = stop[0]
            new = []
            for x in range(g0, hi + 1):
                if x not in bg:
                    continue
                if stop is not None and x == stop[0]:
                    if stop[1] == 0:
                        break
                    new.append(x)
                    break
                new.append(x)
            if old != new:
                bad += 1
    check("无人工断点时取块与旧算法完全一致（零回归）", bad == 0, f"{bad} 处不同")

    # ---------------------------------------------------------- 界面（假 DOM）
    print("\n== 界面：正文块视图 ==")
    o = run_ui()
    if not o:
        FAILS.append("界面探针没跑起来")
    else:
        lst = o["列表"]
        check("搜索时把命中的片段标出来", "<mark>" in lst["list"])
        # 三块里「已新增」那块给的是「撤销」，另外两块给「设为标题」。
        # 已经立过标题的块**也**给「设为标题」（旁边另有一个「已是标题 L2」标签）——
        # 这是有意的：与其让人点了没反应，不如让他加了再删。所以这里只排除「已新增」。
        check("不是「已新增」的块都给了「设为标题」按钮",
              lst["list"].count("设为标题") == 2,
              f'{lst["list"].count("设为标题")} 个')
        check("已在树里的块也照样能给「设为标题」（不再设死路）",
              "pickBlock(74)" in lst["list"])
        # mock 里 #74（MinerU 认出来的）与 #88（人工补过的）都在树里，各挂一个标签
        check("已在树里的块标出「已是标题」", lst["list"].count("已是标题") == 2,
              f'{lst["list"].count("已是标题")} 个')
        check("已新增的块改成「撤销」按钮", lst["list"].count(">撤销<") == 1)
        check("标出页码与块号", "pfront-3" in lst["list"] and "#31" in lst["list"])
        check("顶部写明命中块数", "块命中" in lst["meta"], lst["meta"])

        p = o["点设为标题"]
        check("点「设为标题」后就地开出编辑条", "addTitle" in p["host"])
        check("标题文本预填这一块的第一行", p["title"] == "第三节 数字人文的范式演进",
              p["title"])
        check("层级有默认值（沿用上一块）", isinstance(p["lv"], int) and 1 <= p["lv"] <= 6,
              str(p["lv"]))
        check("编辑条里有「从块内第 N 字断开」", 'id="addOff"' in p["host"])

        c = o["改级后"]
        check("点第 3 级后高亮跟着走", c["lv"] == 3, str(c["lv"]))
        check("改级不会清掉已填的标题文本", c["title"] == "第三节 数字人文的范式演进（修正）",
              c["title"])
        check("编辑条上第 3 级被标成选中",
              c["host"].count('class="on"') == 1)

        s = o["提交"]
        add = [x for x in s["calls"] if x["path"].endswith("/api/edit/add")]
        check("确认提交打到 /api/edit/add", len(add) == 1, str([x["path"] for x in s["calls"]]))
        if add:
            bd = add[0]["body"]
            check("提交带上了 gid / 标题 / 层级 / 块内位移",
                  bd.get("gid") == 31 and bd.get("title") == "第三节 数字人文的范式演进（修正）"
                  and bd.get("level") == 3 and bd.get("offset") == 12, json.dumps(bd, ensure_ascii=False))
        check("提交后编辑条收起", s["host"] == "")

        tl = o["目录列表"]["list"]
        check("目录列表里出现待重算的新增行", "新增" in tl and "待重算" in tl)
        # 不能直接找子串 "trow pending"：模板里 class 是 "trow lv2 pending"，
        # 中间夹着层级类名，字符串搜索必然找不到。
        m = re.search(r'class="trow[^"]*\bpending\b', tl)
        check("新增行有特殊底色", m is not None, m.group(0) if m else tl[:120])
        check("新增行的 × 是撤销（不是删除）", "撤销这条新增" in tl)

    rm(TMP)                                   # 临时产物不留在系统里
    print("\n" + ("全通过：漏掉的标题能在正文里定位、立断点，正文一字不动"
                  if not FAILS else f"{len(FAILS)} 项失败：{FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
