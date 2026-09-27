# -*- coding: utf-8 -*-
"""诊断：印刷目录页的**页码解析覆盖率**（只读，不写任何产物）。

背景：core/outline.py 的 extract_printed_toc 里，
    m_num = re.search(r"[ 　](\d{1,4})\s*$", line)
要求页号前必须有一个**空格**。中文目录的常态是「……57」（点线紧贴页号），
于是这类整行掉进 pending 分支，被拿去跟**下一行**的页码配对 —— 条目数与页码都错。

同一文件里的 TOC_LINE_RE 用的是 `[.．·…]{3,}\s*\d{1,4}\s*$`（点线 + 可选空白 + 数字），
也就是说「目录页识别」认得这种行，「条目解析」不认得。两个正则口径不一致。

本脚本比对两套口径，列出：
  A 会被新口径多解析出来的行（并按是否以标题标记开头分类）
  B 现有条目里页码来自「错配」的（新口径下页码与旧口径不同的）
"""
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")

from core import outline as OL  # noqa: E402

WORK = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work"

OLD_NUM = re.compile(r"[ 　](\d{1,4})\s*$")
NEW_NUM = re.compile(r"[.．·…\u3000 ]*(\d{1,4})\s*$")
DOT = re.compile(r"[.．·…]{2,}")

MARKERS = [
    ("part", OL.PART_RE), ("chapter", OL.CHAPTER_RE), ("sec", OL.SEC_RE),
    ("zh_dun", OL.ZH_DUN_RE), ("paren_zh", OL.PAREN_ZH_RE),
    ("arabic", OL.ARABIC_RE), ("paren_arabic", OL.PAREN_ARABIC_RE),
    ("opening", OL.OPENING_RE), ("closing", OL.CLOSING_RE),
    ("front", OL.FRONT_RE), ("back", OL.BACK_RE),
]


def starts_entry(head: str) -> str | None:
    for name, rx in MARKERS:
        if rx.match(head):
            return name
    return None


def tail_page(line: str):
    m = NEW_NUM.search(line)
    if not m:
        return None
    if not line[:m.start()].strip():
        return None
    return m


def main() -> int:
    rows = []
    with io.open(os.path.join(os.path.dirname(__file__), "_out", "_tocparse.txt"),
                 "w", encoding="utf-8") as fh:
        def out(s=""):
            fh.write(s + "\n")
            print(s)

        for sid in sorted(os.listdir(WORK)):
            op = os.path.join(WORK, sid, "outline.json")
            if not os.path.isfile(op):
                continue
            oc = json.load(io.open(op, encoding="utf-8"))
            pt = oc.get("printed_toc") or {}
            if not pt.get("found"):
                continue
            lines = [l.strip() for l in (pt.get("raw") or "").split("\n") if l.strip()]
            lines = [l for l in lines if l not in ("目录", "目 录", "Contents", "CONTENTS")]

            new_ok, old_ok, gained, shifted = [], [], [], []
            for l in lines:
                mo = OLD_NUM.search(l)
                mn = tail_page(l)
                if mn:
                    new_ok.append(l)
                if mo:
                    old_ok.append(l)
                if mn and not mo:
                    d = DOT.search(l)
                    head = (l[:d.start()] if d else l[:mn.start()]).strip()
                    gained.append((l, head, starts_entry(head)))
                elif mn and mo and int(mn.group(1)) != int(mo.group(1)):
                    shifted.append((l, int(mo.group(1)), int(mn.group(1))))

            ents = pt.get("entries") or []
            out("=" * 78)
            out(f"{sid}")
            out(f"  目录页 {pt.get('shard')} {pt.get('pages')}｜原文行 {len(lines)}｜"
                f"旧口径可解析 {len(old_ok)}｜新口径可解析 {len(new_ok)}｜"
                f"现存条目 {len(ents)}（printed 为空 {sum(1 for e in ents if not e.get('printed'))}）")
            clean = [g for g in gained if g[2]]
            wrap = [g for g in gained if not g[2]]
            out(f"  ▸ 新增解析 {len(gained)} 行 = 标题标记开头 {len(clean)} + 非标记开头 {len(wrap)}")
            if clean:
                out("    [以标题标记开头 · 几乎确定是真条目]")
                for l, head, mk in clean:
                    out(f"      ({mk}) {l}")
            if wrap:
                out("    [非标记开头 · 需要「折行续接」规则，否则会被当成新条目]")
                for l, head, mk in wrap:
                    out(f"       ??? {l}")
            if shifted:
                out("    [新旧页码不一致]")
                for l, a, b in shifted:
                    out(f"      old={a} new={b}  {l}")
            rows.append((sid, len(lines), len(old_ok), len(new_ok), len(ents)))

        out("=" * 78)
        out("汇总：")
        out(f"  {'source':<58s} {'原行':>4s} {'旧解':>4s} {'新解':>4s} {'条目':>4s}")
        for sid, a, b, c, d in rows:
            out(f"  {sid[:58]:<58s} {a:>4d} {b:>4d} {c:>4d} {d:>4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
