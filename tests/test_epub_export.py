"""EPUB 装订回归：md 目录 → EPUB，结构、内容、自检与往返。

不依赖真实书籍、OCR 或 Zotero。手工造一份与 exporter 产物同形的分节目录，
覆盖页锚、脚注（随文与页末）、引文、表格、图片、层级标题与缺图。
重点不是「某本书长什么样」，而是「同一份 md 装出来的 EPUB 是否结构完整、
内容逐项落地、坏包能被自检挡住」。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.epub import convert_epub                     # noqa: E402
from core.epub_export import build_epub, verify_epub  # noqa: E402
from core.exporter import export                      # noqa: E402
from core.pagecal import Calibration, Segment         # noqa: E402
from core.project import Block                        # noqa: E402
from core.util import write_json                      # noqa: E402

# 1×1 PNG；只搬运字节，不解码，真假不影响断言
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d5b1a0000000049454e44ae426082")

MD1 = """<!-- doclab v0.2.0 | source=selftest | node=1 | depth=2 | locator_type=paginated -->
<!-- source_range: gid 0-10 | shard P1 p0 -->
# 第一章 起点

> 归属：前置 › 第一章

<!-- p=front-3 -->
**导语**：正文里的 *斜体* 与 `F7`。

<!-- p=12 -->
第一段正文，含 <尖括号> 与 AT&T。

> 引文一行。
> 引文二行。

<!-- footnote --> ① 这是随文脚注。
"""

MD2 = """# 第一节 方法

<!-- p=13 -->
方法正文一。

| 年份 | 事件 |
|---|---|
| 1974 | 空间的生产 |

**脚注**（P1 物理页 13）：
- ① 页末脚注甲
- ② 页末脚注乙
"""

MD3 = """# 第二节 材料

<!-- p=14 -->
![图一](images/fig1.png)
*图一*

段落二。
"""

MD4 = """# 附录 索引

<!-- p=pdf-15 unmapped -->
附录正文。
"""


def make_export_dir(root: Path, missing_image: bool = False) -> Path:
    out = root / "L2"
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "images" / "fig1.png").write_bytes(PNG)
    (out / "001_第一章_起点.md").write_text(MD1, encoding="utf-8")
    (out / "002_第一节_方法.md").write_text(MD2, encoding="utf-8")
    (out / "003_第二节_材料.md").write_text(MD3, encoding="utf-8")
    (out / "004_附录_索引.md").write_text(MD4, encoding="utf-8")
    if missing_image:
        (out / "images" / "fig1.png").unlink()
    write_json(out / "conversion_meta.json", {
        "source_file": "测试书", "converted_at": "2026-11-17T01:02:03+08:00",
        "locator_type": "paginated", "footnote_mode": "inline",
    })
    write_json(out / "manifest.json", {"files": [
        {"seq": 1, "path": "001_第一章_起点.md", "title": "第一章 起点", "level": 1,
         "belongs_to": ""},
        {"seq": 2, "path": "002_第一节_方法.md", "title": "第一节 方法", "level": 2,
         "belongs_to": "第一章 起点"},
        {"seq": 3, "path": "003_第二节_材料.md", "title": "第二节 材料", "level": 2,
         "belongs_to": "第一章 起点"},
        {"seq": 4, "path": "004_附录_索引.md", "title": "附录 索引", "level": 1,
         "belongs_to": ""},
    ]})
    return out


def read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class EpubExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="epub-export-")
        self.root = Path(self.tmp.name)
        self.out = make_export_dir(self.root)
        self.epub = self.root / "book.epub"

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kw):
        args = dict(title=None, author="作者甲", language="zh-CN",
                    source_id="selftest", modified="2026-11-17T01:02:03+08:00")
        args.update(kw)
        return build_epub(self.out, dest=self.epub, **args)

    # ---------------------------------------------------------- 结构与元数据
    def test_cover_page_and_opf(self):
        info = self.build(cover={"data": PNG, "media_type": "image/png",
                                 "width": 1, "height": 1, "kind": "test"})
        self.assertTrue(info["verify"]["ok"], info["verify"]["errors"])
        self.assertEqual(info["cover"]["href"], "images/cover.png")
        self.assertEqual(info["cover"]["bytes"], len(PNG))
        self.assertEqual(info["verify"]["stats"]["cover"], "images/cover.png")
        with zipfile.ZipFile(self.epub) as z:
            names = z.namelist()
            self.assertIn("OEBPS/text/cover.xhtml", names)
            self.assertIn("OEBPS/images/cover.png", names)
            self.assertEqual(z.read("OEBPS/images/cover.png"), PNG)
            cover = z.read("OEBPS/text/cover.xhtml").decode("utf-8")
            self.assertIn('src="../images/cover.png"', cover)
            self.assertIn('epub:type="cover"', cover)
            opf = ET.fromstring(z.read("OEBPS/content.opf"))
            manifest = next(e for e in opf if local(e.tag) == "manifest")
            item = next(e for e in manifest
                        if e.get("id") == "cover-image")
            self.assertEqual(item.get("properties"), "cover-image")
            self.assertEqual(item.get("href"), "images/cover.png")
            spine = next(e for e in opf if local(e.tag) == "spine")
            refs = [e.get("idref") for e in spine if local(e.tag) == "itemref"]
            self.assertEqual(refs[0], "cover")
            self.assertEqual(refs[1], "titlepage")
            guide = next(e for e in opf if local(e.tag) == "guide")
            self.assertTrue(any(r.get("type") == "cover" for r in guide))

    def test_no_cover_by_default(self):
        info = self.build()
        self.assertIsNone(info["cover"])
        self.assertIsNone(info["verify"]["stats"]["cover"])
        with zipfile.ZipFile(self.epub) as z:
            self.assertNotIn("OEBPS/text/cover.xhtml", z.namelist())

    def test_package_structure_and_metadata(self):
        info = self.build()
        self.assertTrue(self.epub.is_file())
        self.assertEqual(info["name"], "book.epub")
        self.assertEqual(info["chapters"], 4)
        self.assertEqual(info["images"], 1)
        self.assertEqual(info["nav_entries"], 4)
        self.assertEqual(info["title"], "测试书")
        self.assertTrue(info["identifier"].startswith("urn:uuid:"))
        self.assertTrue(info["verify"]["ok"], info["verify"]["errors"])

        with zipfile.ZipFile(self.epub) as z:
            names = z.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertEqual(z.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.read("mimetype"), b"application/epub+zip")
            self.assertIn("META-INF/container.xml", names)
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = next(e for e in container.iter() if local(e.tag) == "rootfile")
            self.assertEqual(rootfile.get("full-path"), "OEBPS/content.opf")
            opf = ET.fromstring(z.read("OEBPS/content.opf"))
            meta = next(e for e in opf if local(e.tag) == "metadata")
            title = next(e for e in meta if local(e.tag) == "title").text
            creator = next(e for e in meta if local(e.tag) == "creator").text
            lang = next(e for e in meta if local(e.tag) == "language").text
            modified = next(e for e in meta if local(e.tag) == "meta"
                            and e.get("property") == "dcterms:modified").text
            self.assertEqual((title, creator, lang), ("测试书", "作者甲", "zh-CN"))
            self.assertEqual(modified, "2026-11-16T17:02:03Z")
            manifest = next(e for e in opf if local(e.tag) == "manifest")
            items = [e for e in manifest if local(e.tag) == "item"]
            nav = next(e for e in items if (e.get("properties") or "") == "nav")
            self.assertEqual(nav.get("href"), "nav.xhtml")
            spine = next(e for e in opf if local(e.tag) == "spine")
            self.assertEqual(len([e for e in spine if local(e.tag) == "itemref"]), 5)
            self.assertEqual(z.getinfo("OEBPS/images/fig1.png").file_size, len(PNG))

    # ---------------------------------------------------------- 正文转换
    def test_body_conversion(self):
        self.build()
        data = read_zip(self.epub)
        ch1 = data["OEBPS/text/ch001.xhtml"].decode("utf-8")
        self.assertIn("<h1>第一章 起点</h1>", ch1)
        self.assertIn('id="pfront-3"', ch1)
        self.assertIn('id="p12"', ch1)
        self.assertIn('epub:type="pagebreak"', ch1)
        self.assertIn("<em>斜体</em>", ch1)
        self.assertIn("<code>F7</code>", ch1)
        self.assertIn("&lt;尖括号&gt;", ch1)
        self.assertIn("AT&amp;T", ch1)
        self.assertIn("<blockquote><p>引文一行。</p><p>引文二行。</p></blockquote>", ch1)
        self.assertIn('<p class="footnote">① 这是随文脚注。</p>', ch1)
        self.assertIn('<p class="crumbs">归属：前置 › 第一章</p>', ch1)
        # 章节标题只出现一次（md 首个 h1 由外层渲染，不重复）
        self.assertEqual(ch1.count("<h1>"), 1)

        ch2 = data["OEBPS/text/ch002.xhtml"].decode("utf-8")
        self.assertIn("<table><thead><tr><th>年份</th><th>事件</th></tr></thead>", ch2)
        self.assertIn("<td>1974</td><td>空间的生产</td>", ch2)
        self.assertNotIn("---", ch2)                    # 分隔行只用于定列，不落表体
        self.assertIn('<ul class="fn-list">', ch2)
        self.assertIn("页末脚注甲", ch2)

        ch3 = data["OEBPS/text/ch003.xhtml"].decode("utf-8")
        self.assertIn('<figure><img src="../images/fig1.png" alt="图一"/>'
                      '<figcaption>图一</figcaption></figure>', ch3)
        self.assertNotIn("<p><em>图一</em></p>", ch3)   # *图一* 不重复渲染

        ch4 = data["OEBPS/text/ch004.xhtml"].decode("utf-8")
        self.assertIn('id="ppdf-15"', ch4)
        self.assertIn('title="PDF 第 15 页（未映射）"', ch4)

    # ---------------------------------------------------------- 目录顺序
    def test_nav_order_follows_manifest(self):
        self.build()
        nav = read_zip(self.epub)["OEBPS/nav.xhtml"].decode("utf-8")
        titles = re.findall(r"<a href=\"[^\"]+\">([^<]+)</a>", nav)
        self.assertEqual(titles, ["第一章 起点", "第一节 方法", "第二节 材料", "附录 索引"])
        # 层级：两个 L2 嵌在第一章下
        self.assertRegex(nav, r"第一章 起点</a><ol><li><a href=\"text/ch002\.xhtml\">")
        ncx = read_zip(self.epub)["OEBPS/toc.ncx"].decode("utf-8")
        self.assertEqual(ncx.count("<navPoint "), 4)

    # ---------------------------------------------------------- 自检
    def test_verify_detects_broken_package(self):
        self.build()
        self.assertTrue(verify_epub(self.epub, expected_chapters=4)["ok"])

        bad = self.root / "bad.epub"
        with zipfile.ZipFile(self.epub) as zin, zipfile.ZipFile(bad, "w") as zout:
            for item in zin.infolist():
                if item.filename == "OEBPS/images/fig1.png":
                    continue
                zout.writestr(item, zin.read(item.filename))
        report = verify_epub(bad, expected_chapters=4)
        self.assertFalse(report["ok"])
        self.assertTrue(any("图片不存在" in e for e in report["errors"]), report["errors"])

        bad2 = self.root / "bad2.epub"
        with zipfile.ZipFile(bad2, "w") as z:
            info = zipfile.ZipInfo("mimetype", date_time=(2010, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, b"application/epub+zip")
            z.writestr("META-INF/container.xml",
                       ('<?xml version="1.0"?><container version="1.0" '
                        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                        'media-type="application/oebps-package+xml"/></rootfiles>'
                        '</container>').encode("utf-8"))
        report2 = verify_epub(bad2)
        self.assertFalse(report2["ok"])
        self.assertTrue(any("mimetype" in e for e in report2["errors"]), report2["errors"])

    def test_expected_chapter_count_mismatch_fails(self):
        self.build()
        report = verify_epub(self.epub, expected_chapters=5)
        self.assertFalse(report["ok"])
        self.assertTrue(any("章节数不符" in e for e in report["errors"]))

    # ---------------------------------------------------------- 缺图
    def test_missing_image_strict_and_placeholder(self):
        out = make_export_dir(self.root / "missing", missing_image=True)
        with self.assertRaisesRegex(ValueError, "图片没有随导出搬运"):
            build_epub(out, dest=self.root / "strict.epub", title="缺图书",
                       source_id="missing", strict=True)
        info = build_epub(out, dest=self.root / "loose.epub", title="缺图书",
                          source_id="missing")
        self.assertTrue(info["verify"]["ok"])
        self.assertEqual(info["missing_images"], ["images/fig1.png"])
        self.assertEqual(info["images"], 0)
        ch3 = read_zip(self.root / "loose.epub")["OEBPS/text/ch003.xhtml"].decode("utf-8")
        self.assertIn("[图片缺文件：图一]", ch3)
        self.assertNotIn("<img", ch3)

    def test_unsupported_image_format_becomes_placeholder(self):
        out = make_export_dir(self.root / "unsupported")
        (out / "images" / "scan.tiff").write_bytes(b"II*\x00")
        md = out / "003_第二节_材料.md"
        md.write_text(md.read_text(encoding="utf-8").replace(
            "images/fig1.png", "images/scan.tiff"), encoding="utf-8")
        info = build_epub(out, dest=self.root / "unsupported.epub", title="格式测试",
                          source_id="unsupported")
        self.assertTrue(info["verify"]["ok"], info["verify"]["errors"])
        self.assertEqual(info["images"], 0)
        self.assertTrue(any("格式不受 EPUB 支持" in w for w in info["warnings"]))
        ch3 = read_zip(self.root / "unsupported.epub")["OEBPS/text/ch003.xhtml"].decode("utf-8")
        self.assertIn("[图片格式不受支持：图一]", ch3)
        self.assertNotIn("<img", ch3)

    # ---------------------------------------------------------- 原始 HTML 片段
    def test_raw_html_table_and_sup_are_rendered(self):
        out = make_export_dir(self.root / "html")
        md = out / "002_第一节_方法.md"
        text = md.read_text(encoding="utf-8")
        old = "| 年份 | 事件 |\n|---|---|\n| 1974 | 空间的生产 |"
        new = ('<table><tr><td>维度</td><td>内涵</td></tr>'
               '<tr><td colspan="2">合并单元格</td></tr>'
               '<tr><td rowspan=2>载体</td><td>分享集体形象</td></tr></table>\n\n'
               '上标 C<sup>2</sup>DH 与换行甲<br>乙。')
        self.assertIn(old, text)
        md.write_text(text.replace(old, new), encoding="utf-8")
        info = build_epub(out, dest=self.root / "html.epub", title="HTML 片段",
                          source_id="html")
        self.assertTrue(info["verify"]["ok"], info["verify"]["errors"])
        ch2 = read_zip(self.root / "html.epub")["OEBPS/text/ch002.xhtml"].decode("utf-8")
        self.assertNotIn("&lt;table", ch2)
        self.assertIn("<table><thead><tr><th>维度</th><th>内涵</th></tr></thead>", ch2)
        self.assertIn('<td colspan="2">合并单元格</td>', ch2)
        self.assertIn('<td rowspan="2">载体</td>', ch2)
        self.assertIn("上标 C<sup>2</sup>DH 与换行甲<br/>乙。", ch2)
        self.assertIn("</table>", ch2)

    # ---------------------------------------------------------- 可重复
    def test_build_is_reproducible(self):
        a = self.build()
        b = self.build()
        self.assertEqual(a["sha256"], b["sha256"])

    def test_outside_output_dir_never_deletes_other_epubs(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        other = elsewhere / "other.epub"
        other.write_bytes(b"keep me")
        build_epub(self.out, dest=elsewhere / "mine.epub", title="外部输出",
                   source_id="outside")
        self.assertTrue(other.is_file())
        self.assertTrue((elsewhere / "mine.epub").is_file())

    # ---------------------------------------------------------- 往返
    def test_roundtrip_through_epub_reader(self):
        self.build()
        conv = convert_epub(self.epub)
        blocks = conv["blocks"]
        text = "\n".join(b.get("text") or b.get("table_body") or "" for b in blocks)
        self.assertIn("第一段正文", text)
        self.assertIn("方法正文一", text)
        self.assertIn("附录正文", text)
        self.assertIn("1974", text)
        self.assertIn("空间的生产", text)
        headings = [b["text"] for b in blocks if b.get("text_level")]
        for t in ("第一章 起点", "第一节 方法", "第二节 材料"):
            self.assertTrue(any(t.replace(" ", "") in h.replace(" ", "") for h in headings),
                            f"标题缺失：{t} / {headings}")
        self.assertEqual(conv["meta"]["epub_images"], 1)
        self.assertEqual(conv["meta"]["epub_tables"], 3)

    def test_export_dir_without_markdown_fails(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "没有可装订的章节"):
            build_epub(empty, dest=self.root / "empty.epub")

    # ---------------------------------------------------------- 引用编码
    def test_image_href_is_uri_encoded_but_zip_name_is_raw(self):
        out = make_export_dir(self.root / "encoded")
        (out / "images" / "图 一.png").write_bytes(PNG)
        md = out / "003_第二节_材料.md"
        md.write_text(md.read_text(encoding="utf-8").replace(
            "images/fig1.png", "images/图 一.png"), encoding="utf-8")
        epub = self.root / "encoded.epub"
        info = build_epub(out, dest=epub, title="编码测试", source_id="encoded")
        self.assertTrue(info["verify"]["ok"], info["verify"]["errors"])
        data = read_zip(epub)
        self.assertIn("OEBPS/images/图 一.png", data)      # zip 条目名保持原样
        opf = data["OEBPS/content.opf"].decode("utf-8")
        self.assertIn('href="images/%E5%9B%BE%20%E4%B8%80.png"', opf)
        ch3 = data["OEBPS/text/ch003.xhtml"].decode("utf-8")
        self.assertIn('src="../images/%E5%9B%BE%20%E4%B8%80.png"', ch3)

    # ---------------------------------------------------------- 与 exporter 串联
    def test_exporter_builds_epub_from_the_markdown_it_just_wrote(self):
        base = self.root / "integration"
        wd, out, imgs = base / "wd", base / "out", base / "src-images"
        for d in (wd, imgs):
            d.mkdir(parents=True)
        (imgs / "abc.png").write_bytes(PNG)
        write_json(wd / "project.json", {"source_id": "t", "doc_title": "导出装订测试",
                                         "shards": []})
        blocks = [
            Block(gid=0, shard="P1", local=0, page_idx=0, type="text",
                  text="第一章 起点", text_level=1),
            Block(gid=1, shard="P1", local=1, page_idx=0, type="text",
                  text="第一段正文。"),
            Block(gid=2, shard="P1", local=2, page_idx=1, type="image",
                  img_path="images/abc.png", caption=["图一"]),
            Block(gid=3, shard="P1", local=3, page_idx=1, type="page_footnote",
                  text="① 页下注。"),
            Block(gid=4, shard="P1", local=4, page_idx=2, type="text",
                  text="第二章 终点", text_level=1),
            Block(gid=5, shard="P1", local=5, page_idx=2, type="text",
                  text="第二段正文。"),
        ]
        calib = Calibration("t", ["P1"], {"P1": 3},
                            [Segment("P1", "body", 0, 2, 1, 3, 1, 3, 3, 1.0, "high")],
                            [], {}, [], [], "paginated", "ok", "fixture")
        outline = {"doc_title": "导出装订测试", "source_id": "t", "tree": [
            {"nid": "1", "level": 1, "title": "第一章 起点", "gid_start": 0,
             "gid_end": 3, "shard": "P1", "page_idx": 0, "marker": "heading",
             "children": []},
            {"nid": "2", "level": 1, "title": "第二章 终点", "gid_start": 4,
             "gid_end": 5, "shard": "P1", "page_idx": 2, "marker": "heading",
             "children": []},
        ]}
        meta = export("t", wd, out, blocks, calib, outline, 1,
                      footnote_mode="inline",
                      shards_meta=[{"tag": "P1", "n_pages": 3,
                                    "images_dir": str(imgs)}])
        ep = meta["epub"]
        self.assertTrue(ep["verify"]["ok"], ep["verify"]["errors"])
        self.assertEqual((ep["chapters"], ep["images"]), (2, 1))
        self.assertTrue(Path(ep["path"]).is_file())
        self.assertTrue((out / "images" / "abc.png").is_file())
        # 元数据里也带 EPUB，工作台/入库都从这里读
        saved = json.loads((out / "conversion_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["epub"]["name"], ep["name"])
        ch1 = read_zip(Path(ep["path"]))["OEBPS/text/ch001.xhtml"].decode("utf-8")
        self.assertIn("第一段正文", ch1)
        self.assertIn('src="../images/abc.png"', ch1)
        self.assertIn("① 页下注。", ch1)

        # --no-epub 重导：旧 EPUB 必须清掉，否则界面/归档会把它当成当前产物
        meta2 = export("t", wd, out, blocks, calib, outline, 1,
                       footnote_mode="inline",
                       shards_meta=[{"tag": "P1", "n_pages": 3,
                                     "images_dir": str(imgs)}],
                       epub=False)
        self.assertIsNone(meta2["epub"])
        self.assertEqual(list(out.glob("*.epub")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
