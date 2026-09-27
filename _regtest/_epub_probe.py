# -*- coding: utf-8 -*-
"""EPUB 结构探针：看 spine / TOC / 标题层级 / 页码候选 / 边码。输出 UTF-8 文件。"""
import io, os, re, sys, zipfile
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\epub-to-markdown")
import epub2md as E

OUT = io.open(r"C:\Users\Zhaoshuochen\Desktop\_tmp_epubprobe.txt", "w", encoding="utf-8", newline="\n")


def p(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")


for ep in sys.argv[1:]:
    z = zipfile.ZipFile(ep)
    names = z.namelist()
    p("=" * 100)
    p("EPUB:", os.path.basename(ep), "| 条目:", len(names))
    p("--- 非图片/样式条目 ---")
    for n in names:
        if not n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".css")):
            p("   ", n)

    toc = E.read_toc(z)
    p("--- TOC %d 条 ---" % len(toc))
    for t, f in toc[:60]:
        p("    %-50s %s" % (t[:48], f))

    files = E.spine_files(z)
    base_of = {}
    for n in names:
        base_of.setdefault(os.path.basename(n), n)
    p("--- spine %d ---" % len(files))
    nchars_all = 0
    for fn in files:
        full = base_of.get(fn)
        if not full or not full.lower().endswith((".xhtml", ".html", ".htm")):
            continue
        raw = z.read(full).decode("utf-8", "replace")
        items = []
        E.walk(E.parse(raw), items)
        nchars = sum(len(re.sub(r"\s", "", t)) for _, t, _ in items)
        nchars_all += nchars
        heads = [(k, t) for k, t, _ in items if re.match(r"^h[1-6]$", k)]
        cands = [(k, t) for k, t, _ in items
                 if re.fullmatch(r"[0-9]{1,4}|[ivxlcdmIVXLCDM]{1,7}", t.strip())]
        edges = [t for k, t, _ in items if k == "edge"]
        notes = sum(1 for k, _, _ in items if k == "note")
        p("  %-34s items=%5d chars=%7d notes=%4d heads=%-24s first=%s" % (
            fn, len(items), nchars, notes, ",".join(h[0] for h in heads[:5]),
            (heads[0][1][:30] if heads else "-")))
        if cands or edges:
            p("        数字候选(%d)=%s  边码(%d)=%s" % (
                len(cands), [c[1] for c in cands[:10]], len(edges), edges[:10]))
        if heads:
            p("        heads: %s" % [(h[0], h[1][:26]) for h in heads[:8]])
    p("--- 全书清洗后字符数 %d ---" % nchars_all)
OUT.close()
print("ok")
