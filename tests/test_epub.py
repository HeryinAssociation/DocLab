"""EPUB 入库链路自测：合成一本小书，走 ingest → outline → pagecal → export → verify。

为什么合成而不是拿真书：真 EPUB 动辄几十万字且随时会换，回归没有基准。这本合成书
把每条判定各放一处 —— 标签层级（h1/h2/h3）、跨文档断句、页下注、原版边码、图、表、
引文块、附录 —— 所以断言的是「规则有没有生效」，不是「某本书现在长什么样」。
zip 里所有条目都用**固定时间戳**，否则每次跑 sha256 都不一样，source_id 每次都变，
工作目录会一轮轮堆下去。

最重要的一条断言是**不许伪造页码**：EPUB 没有纸书页码，导出里只允许出现
`p=pdf-N unmapped` 与 `p=unmapped` 两种锚，出现任何「p=数字」即判失败。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_config, out_dir, work_dir              # noqa: E402
from core.exporter import export                                    # noqa: E402
from core.ingest import ingest_epub, ingest_local, remove_source    # noqa: E402
from core.outline import build_tree, walk_nodes                     # noqa: E402
from core.pagecal import calibrate                                  # noqa: E402
from core.project import iter_blocks, load_shards                   # noqa: E402
from core.verify import run_all                                     # noqa: E402

NAME = "doclab-epub-selftest"
# 合成样本放在系统 temp：工作区对删除有配额，样本要反复重建（每次先删旧的），
# 放工作区里会跟配额较劲。产品自己的存储（_work / out）仍在工作区，那是必须的。
TMP = Path(tempfile.gettempdir()) / "_doclab_epub_selftest"
FAILS: list[str] = []

# 1×1 PNG。真伪不重要 —— 全链路只搬运字节、不解码。
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d5b1a0000000049454e44ae426082")

DOCS: dict[str, str] = {
    # 0 封面：书名用普通 p，**不用 h 标签**，检验「第一个标题之前的块归前置节点」
    "cover.xhtml": '<div class="cover"><p class="bt">doclab EPUB 自测样书</p>'
                   '<p>合成样本，仅供回归</p></div>',
    # 1 书内目录：h1「目录」落在目录页上，按 R1 整页排除；条目是链接列表，不是标题候选
    "nav.xhtml": '<h1>目录</h1><ul>'
                 '<li><a href="ch01a.xhtml">第一章 数字人文的兴起</a></li>'
                 '<li><a href="ch02.xhtml">第二章 记忆的空间转向</a></li>'
                 '<li><a href="ch03.xhtml">第三章 数字记忆的空间生成</a></li></ul>',
    # 2 第一章前半：含 h2、原版边码、页下注，结尾**被切在下一个文档中间**
    "ch01a.xhtml":
        '<h1>第一章 数字人文的兴起</h1>'
        '<p>数字人文并不是一个全新的领域，它的前身可以追溯到人文计算。</p>'
        '<h2>第一节 概念溯源</h2>'
        '<p>人文计算一词最早出现在二十世纪中叶。</p>'
        '<p>F7</p>'
        '<p>① 参见王军：《从人文计算到可视化》，《数字人文》2020年第1期。</p>'
        '<p>这一段的结尾被切在下一个文档里，句子没有写完，</p>',
    # 3 第一章后半：**没有标题**，直接接上一段 —— 检验跨文档归并
    "ch01b.xhtml": '<p>的下半句应当与上一段合并成完整的一句。</p>'
                   '<p>顺带一提，这一段是为了测跨文档归并。</p>',
    # 4 第二章：引文块 + 表格
    "ch02.xhtml":
        '<h1>第二章 记忆的空间转向</h1>'
        '<p>空间不是容器，而是社会关系的产物。</p>'
        '<h2>第一节 列斐伏尔</h2>'
        '<blockquote><p>（社会）空间是（社会）产物。</p></blockquote>'
        '<table><tr><th>年份</th><th>事件</th></tr>'
        '<tr><td>1974</td><td>空间的生产出版</td></tr></table>',
    # 5 第三章：三级标签 + 图片
    "ch03.xhtml":
        '<h1>第三章 数字记忆的空间生成</h1>'
        '<h2>第一节 三重空间</h2>'
        '<h3>一、空间实践</h3><p>空间实践对应感知的空间。</p>'
        '<img src="images/fig2.png" alt="三重空间示意"/>'
        '<h3>二、空间表征</h3><p>空间表征对应构想的空间。</p>',
    # 6 附录：h1 → 仍是 L1（标签权威，不因 BACK_RE 被收拢）
    "back.xhtml": '<h1>附录一 术语对照</h1><p>数字人文 digital humanities</p>',
}


def check(name: str, ok: bool, extra: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAILS.append(name)


def build_epub(dest: Path) -> Path:
    """合成 EPUB。所有条目固定时间戳，保证 sha256 稳定。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ids = {fn: f"id{i}" for i, fn in enumerate(DOCS)}
    ids["nav.xhtml"] = "idnav"
    manifest = "\n".join(
        f'<item id="{ids[fn]}" href="{fn}" media-type="application/xhtml+xml"/>'
        for fn in DOCS)
    spine = "\n".join(f'<itemref idref="{ids[fn]}"/>' for fn in DOCS)
    opf = (f'<?xml version="1.0" encoding="utf-8"?>'
           f'<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="i">'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:title>doclab EPUB 自测样书</dc:title><dc:creator>砚</dc:creator>'
           f'<dc:identifier id="i">urn:uuid:doclab-epub-selftest</dc:identifier>'
           f'</metadata><manifest>{manifest}'
           f'<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
           f'<item id="f2" href="images/fig2.png" media-type="image/png"/>'
           f'</manifest><spine toc="ncx">{spine}</spine></package>')
    navmap = "".join(
        f'<navPoint id="p{i}"><navLabel><text>{t}</text></navLabel>'
        f'<content src="{f}"/></navPoint>'
        for i, (t, f) in enumerate([("第一章 数字人文的兴起", "ch01a.xhtml"),
                                    ("第二章 记忆的空间转向", "ch02.xhtml"),
                                    ("第三章 数字记忆的空间生成", "ch03.xhtml")]))
    ncx = ('<?xml version="1.0" encoding="utf-8"?>'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           '<docTitle><text>doclab EPUB 自测样书</text></docTitle>'
           f'<navMap>{navmap}</navMap></ncx>')
    container = ('<?xml version="1.0"?>'
                 '<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')

    date = (2020, 1, 1, 0, 0, 0)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        def put(name: str, data: bytes, compress=zipfile.ZIP_DEFLATED) -> None:
            zi = zipfile.ZipInfo(name, date_time=date)
            zi.compress_type = compress
            zi.external_attr = 0o600 << 16
            z.writestr(zi, data)

        put("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        put("META-INF/container.xml", container.encode("utf-8"))
        put("toc.ncx", ncx.encode("utf-8"))
        put("content.opf", opf.encode("utf-8"))
        for fn, body in DOCS.items():
            put(fn, ('<?xml version="1.0" encoding="utf-8"?>'
                     '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                     f'<title>{fn}</title></head><body>{body}</body></html>'
                     ).encode("utf-8"))
        put("images/fig2.png", PNG)
    return dest


ANCHOR_RE = re.compile(r"^<!--\s*p=([^>]*?)\s*-->\s*$", re.M)
LEGAL_ANCHOR = re.compile(r"^(?:pdf-\d+ unmapped|unmapped)$")


def titles(roots) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in roots:
        for n in walk_nodes(r):
            out[n.title] = n.level
    return out


def main() -> int:
    cfg = load_config()
    epub = build_epub(TMP / f"{NAME}.epub")

    print("== 入库：EPUB 本机解析 ==")
    pj = ingest_local(epub, cfg, log=lambda m: None)
    sid = pj["source_id"]
    wd = work_dir(sid)
    check("source_id 由书名派生（不是临时名）", sid.startswith(NAME), sid)
    check("mode = epub-local", pj.get("mode") == "epub-local", str(pj.get("mode")))
    check("单分片 P1", len(pj["shards"]) == 1 and pj["shards"][0]["tag"] == "P1")
    check("脚注默认档 = inline（EPUB 的注本来就在正文里）",
          (pj.get("defaults") or {}).get("footnotes") == "inline",
          str(pj.get("defaults")))
    ep = pj.get("epub", {})
    check("7 个阅读顺序单元", ep.get("epub_units") == len(DOCS), str(ep.get("epub_units")))
    check("抽出页下注 ≥1", (ep.get("epub_notes") or 0) >= 1, str(ep.get("epub_notes")))
    check("抽出原版边码 ≥1", (ep.get("epub_edges") or 0) >= 1, str(ep.get("epub_edges")))
    check("抽出图片 1 张", ep.get("epub_images") == 1, str(ep.get("epub_images")))
    check("抽出表格块（表头／分隔／数据行各一块）",
          (ep.get("epub_tables") or 0) >= 3, str(ep.get("epub_tables")))
    check("层级基准＝h1", ep.get("epub_level_base") == 1, str(ep.get("epub_level_base")))
    check("没有丢图", not ep.get("epub_images_missing"), str(ep.get("epub_images_missing")))
    print("\n== 幂等：同一份 EPUB 再入库不重跑 ==")
    pj2 = ingest_local(epub, cfg, log=lambda m: None)
    check("复用同一个 source_id", pj2["source_id"] == sid)

    print("\n== 拒绝非 PDF/EPUB ==")
    bad = TMP / "x.txt"
    bad.write_text("hi", encoding="utf-8")
    try:
        ingest_local(bad, cfg, log=lambda m: None)
        check("非 PDF/EPUB 抛 NotImplementedError", False, "没抛")
    except NotImplementedError as e:
        check("非 PDF/EPUB 抛 NotImplementedError", True, str(e)[:44])
    # 入口自己也要拦：不然一份 .txt 会被丢进 zipfile，报出来的是一句看不懂的 BadZipFile
    try:
        ingest_epub(bad, cfg, log=lambda m: None)
        check("ingest_epub 也校验扩展名", False, "没抛")
    except NotImplementedError:
        check("ingest_epub 也校验扩展名", True)
    except Exception as e:                              # noqa: BLE001
        check("ingest_epub 也校验扩展名", False, f"抛的是 {type(e).__name__}")

    print("\n== 块层 ==")
    shards = load_shards(wd)
    blocks = iter_blocks(shards)
    heads = [b for b in blocks if b.type == "text" and b.text_level]
    check("标题块都标了 level_trusted", heads and all(b.level_trusted for b in heads),
          f"{len(heads)} 个标题")
    check("非标题块一律 level_trusted=False",
          all(not b.level_trusted for b in blocks if b not in heads))
    check("没有任何 page_number 块（EPUB 拿不到纸书页码）",
          not any(b.type == "page_number" for b in blocks))
    edge = [b for b in blocks if "`F7`" in b.text]
    check("原版边码按原文照抄进正文（`F7`）", len(edge) == 1, str([b.text for b in edge]))
    notes = [b for b in blocks if b.type == "page_footnote"]
    check("页下注是 page_footnote 块（不是噪声）",
          len(notes) == 1 and notes[0].text.startswith("①"),
          str([n.text[:20] for n in notes]))
    merged = [b for b in blocks if "句子没有写完" in b.text and "的下半句" in b.text]
    check("跨文档断句已归并成一段", len(merged) == 1,
          (merged[0].text[-30:] if merged else "没找到"))
    imgs = [b for b in blocks if b.type == "image"]
    check("图片块指向 images/", len(imgs) == 1 and imgs[0].img_path.startswith("images/"),
          str([b.img_path for b in imgs]))
    if imgs:
        real = (Path(shards[0].images_dir) / Path(imgs[0].img_path).name)
        check("图片字节真的落进工程 images/", real.is_file(), str(real.name))

    print("\n== 目录树：标签层级被采纳 ==")
    roots, oc = build_tree(sid, blocks, doc_title="自测样书")
    check("level_source = tag", oc.get("level_source") == "tag", str(oc.get("level_source")))
    check("trusted 源不做目录补章", oc.get("toc_repair") == [],
          str(oc.get("toc_repair"))[:60])
    reasons = {d["reason"] for d in oc.get("dropped_headings", [])}
    check("没有条目因 R2 前置区被丢（EPUB 的序/前言在目录页之前）",
          not any(r.startswith("R2") for r in reasons), str(sorted(reasons)))
    check("没有条目因 R9 图表页被丢（EPUB 的「页」是一整章）",
          not any(r.startswith("R9") for r in reasons))
    tl = titles(roots)
    # ⚠️ 标题里的空格已被清洗规则吃掉（「第一章 数字人文」→「第一章数字人文」）。
    # 这是 epub-to-markdown 原脚本的行为，本节沿用不另造一套 —— 用户已用它转过四本书。
    # 要改成「标题保留原空格」是另一个决定，先记在这里，别在测试里悄悄改口径。
    check("第一章 → L1", tl.get("第一章数字人文的兴起") == 1,
          str(tl.get("第一章数字人文的兴起")))
    check("第一节 → L2", tl.get("第一节概念溯源") == 2, str(tl.get("第一节概念溯源")))
    check("一、空间实践 → L3（三级标签不被压平）",
          tl.get("一、空间实践") == 3, str(tl.get("一、空间实践")))
    check("第二章 → L1（不因上一章的三级而漂移）",
          tl.get("第二章记忆的空间转向") == 1, str(tl.get("第二章记忆的空间转向")))
    check("附录一 → L1（标签权威，不被 BACK_RE 收拢）",
          tl.get("附录一术语对照") == 1, str(tl.get("附录一术语对照")))
    check("节点总数 10（前置 + 4 个 L1 + 3 个 L2 + 2 个 L3）",
          oc["node_total"] == 10, str(oc["node_total"]))
    check("层级统计：L1=5 L2=3 L3=2",
          {k: v["nodes"] for k, v in oc["level_stats"].items()} == {"1": 5, "2": 3, "3": 2},
          str({k: v["nodes"] for k, v in oc["level_stats"].items()}))
    check("第一个标题之前的块归前置节点",
          any(n.title.startswith("前置") for r in roots for n in walk_nodes(r)))

    print("\n== 页码：不许伪造 ==")
    calib = calibrate(sid, blocks, [s.to_dict() for s in shards], anchors={})
    check("判定 degraded", calib.verdict == "degraded", calib.verdict)
    check("locator_type = section_only", calib.locator_type == "section_only",
          calib.locator_type)
    check("没有观测到任何印刷页码",
          not [o for o in calib.obs if getattr(o, "source", "") == "page_number"])

    print("\n== 导出 ==")
    od = out_dir(sid) / "L2"
    if od.exists():
        try:
            shutil.rmtree(od)
        except BaseException as e:                              # noqa: BLE001
            print(f"    （上一轮的 {od} 没清掉：{type(e).__name__}，导出会覆盖）")
    # 脚注档按产品那条路取，而不是在测试里写死：CLI / 界面都是
    # 「人没选 → 用源自己登记的默认档」（doclab.fn_mode / server.act_export）。
    import doclab as cli                                        # noqa: PLC0415
    fm = cli.fn_mode(wd, None)
    check("脚注默认档取源自己的口径（EPUB→inline）", fm == "inline", fm)
    meta = export(sid, wd, od, blocks, calib,
                  {"tree": [n.to_dict() for n in roots], "doc_title": "自测样书",
                   "source_id": sid}, 2,
                  copy_images=True, footnote_mode=fm,
                  shards_meta=[s.to_dict() for s in shards])
    check("脚注档＝随文", meta["footnote_mode"] == "inline", str(meta["footnote_mode"]))
    check("定位类型＝section_only", meta["locator_type"] == "section_only",
          str(meta["locator_type"]))
    md = sorted(p for p in od.glob("*.md") if p.name not in ("00-目录.md", "校验报告.md"))
    check("按第 2 层切出文件", len(md) == meta["stats"]["files"],
          f"{len(md)} 个 / files={meta['stats']['files']}")
    # 5 个前端节点：前置、附录一（两个 L1 叶子）＋ 三章各自的 L2「第一节」。
    # 注意这**不等于** level_stats["2"]["files_if_split"]（那是该层的节点数 3）——
    # 那个标签写的是「若切到本层=文件数」，实际值不是 frontier 数，详见本轮交回说明。
    check("切分边界 = frontier(2)：2 个 L1 叶子 + 3 个 L2", len(md) == 5, f"{len(md)} 个")

    # 硬约束：任何一条锚都不许冒称纸书页码
    all_anchors: list[str] = []
    for p in md:
        all_anchors += [m.group(1) for m in ANCHOR_RE.finditer(p.read_text(encoding="utf-8"))]
    illegal = [a for a in all_anchors if not LEGAL_ANCHOR.match(a)]
    check("锚一律是 pdf-N unmapped / unmapped（无任何纸书页码）",
          bool(all_anchors) and not illegal,
          f"{len(all_anchors)} 条锚，非法 {illegal[:3]}")

    txt_all = {p.name: p.read_text(encoding="utf-8") for p in md}
    check("页下注随文出现（<!-- footnote -->）",
          any("<!-- footnote -->" in t and "①" in t for t in txt_all.values()))
    check("边码在正文里逐字保留", any("`F7`" in t for t in txt_all.values()))
    check("引文块保留 markdown 引用（> ）",
          any("\n> （社会）空间是（社会）产物。" in t for t in txt_all.values()))
    check("表格落成 markdown 表",
          any("年份 | 事件" in t and "---|---" in t for t in txt_all.values()))
    img_dir = od / "images"
    check("图片链接指向 images/，且图片真的搬过来了",
          any("](images/" in t for t in txt_all.values())
          and img_dir.is_dir() and bool(list(img_dir.glob("*.png"))),
          f"images/ 下 {len(list(img_dir.glob('*.png')))} 张")
    check("正文一字不改：跨文档归并后的整句在文件里",
          any("句子没有写完，的下半句应当与上一段合并成完整的一句。" in t
              for t in txt_all.values()))

    print("\n== 三道闸门 ==")
    rep = run_all(wd, od, blocks, calib)
    g = {x["id"]: x for x in rep["gates"]}
    check("G1 覆盖率：降级后按规范放行（warn）", g["G1"]["status"] == "warn",
          g["G1"]["note"])
    check("G2 内容无损：全部源块可逐字回查", g["G2"]["status"] == "pass",
          g["G2"]["note"])
    check("G4 入库硬条件：section_only 满足替代条件",
          g["G4"]["status"] == "pass", g["G4"]["note"])
    check("总判定不 fail", rep["verdict"] in ("pass", "warn"), rep["verdict"])

    print("\n== 归位：把本次合成源移出工作台 ==")
    # 走产品自己的「移出工作台」（移动，不是删除）—— 顺带把这条路径也测了。
    box = None
    try:
        box = remove_source(sid, log=lambda m: None).get("trash")
    except BaseException as e:                                  # noqa: BLE001
        print(f"    （移出工作台失败：{type(e).__name__}：{str(e)[:80]}）")
    check("合成源已移出工作台（_work 与 out 都不再有它）",
          not wd.exists() and not out_dir(sid).exists())
    if box:                                                     # 回收站那份顺手清掉
        try:
            shutil.rmtree(box)
        except BaseException as e:                              # noqa: BLE001
            print(f"    （回收站 {box} 没清掉：{type(e).__name__}，留着不影响结果）")
    check("既有源一个没动",
          (work_dir("中国数字人文发展报告") / "project.json").exists())

    print("\n" + ("全通过：EPUB 走的是本机解析，层级来自标签，页码一律不伪造"
                  if not FAILS else f"{len(FAILS)} 项失败：{FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
