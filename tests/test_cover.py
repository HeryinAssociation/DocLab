"""封面提取：PDF 第一页 / EPUB cover-image / project.json 解析。"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.cover import epub_cover, pdf_first_page, resolve_cover  # noqa: E402
from core.util import write_json  # noqa: E402

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d5b1a0000000049454e44ae426082")


def _fitz():
    sys.path.insert(0, str(ROOT / ".runtime"))
    try:
        import fitz
        return fitz
    except Exception:                                      # noqa: BLE001
        return None


class CoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="doclab-cover-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pdf_first_page_renders_jpeg(self):
        fitz = _fitz()
        if fitz is None:
            self.skipTest("PyMuPDF 不在 .runtime")
        pdf = self.root / "book.pdf"
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 80), "cover")
        doc.save(str(pdf))
        doc.close()
        cover = pdf_first_page(pdf, max_edge=400)
        self.assertEqual(cover["kind"], "pdf-first-page")
        self.assertEqual(cover["media_type"], "image/jpeg")
        self.assertTrue(cover["data"].startswith(b"\xff\xd8"))
        self.assertLessEqual(max(cover["width"], cover["height"]), 400)
        self.assertEqual(cover["source"], str(pdf))

    def test_epub_cover_image_property(self):
        epub = self.root / "book.epub"
        opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="cover" href="images/cover.png" media-type="image/png"
          properties="cover-image"/>
    <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
</package>"""
        container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
        with zipfile.ZipFile(epub, "w") as z:
            z.writestr("META-INF/container.xml", container)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/images/cover.png", PNG)
        cover = epub_cover(epub)
        self.assertIsNotNone(cover)
        self.assertEqual(cover["media_type"], "image/png")
        self.assertEqual(cover["data"], PNG)
        self.assertIn("images/cover.png", cover["source"])

    def test_resolve_cover_from_project(self):
        fitz = _fitz()
        if fitz is None:
            self.skipTest("PyMuPDF 不在 .runtime")
        pdf = self.root / "src.pdf"
        doc = fitz.open()
        doc.new_page(width=200, height=300)
        doc.save(str(pdf))
        doc.close()
        wd = self.root / "source"
        wd.mkdir()
        write_json(wd / "project.json", {"source_file": str(pdf)})
        cover, warning = resolve_cover(wd)
        self.assertEqual(warning, "")
        self.assertEqual(cover["kind"], "pdf-first-page")

    def test_resolve_cover_missing_source_is_warning(self):
        wd = self.root / "source"
        wd.mkdir()
        write_json(wd / "project.json", {"source_file": str(self.root / "nope.pdf")})
        cover, warning = resolve_cover(wd)
        self.assertIsNone(cover)
        self.assertIn("找不到源", warning)

    def test_epub_cover_missing_returns_none(self):
        epub = self.root / "empty.epub"
        with zipfile.ZipFile(epub, "w") as z:
            z.writestr("META-INF/container.xml", "<container/>")
        self.assertIsNone(epub_cover(epub))


if __name__ == "__main__":
    unittest.main(verbosity=2)
