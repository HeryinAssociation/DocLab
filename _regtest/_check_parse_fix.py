# -*- coding: utf-8 -*-
"""验证目录页码解析放宽（点线引导 + 折行拼接）对各书印刷目录条目的影响。

判据：**基线书（冯惠玲本书）必须逐字零变化**，其余书只应“多出条目”或“拼回折行”，
不允许出现凭空造出来的标题。
"""
import os
import sys
import re
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
sys.path.insert(0, BASE)
from core.outline import (PART_RE, clean_title, _looks_like_authors,   # noqa: E402
                          _TOC_PAGE_RE, _TOC_DOT_RE)

OLD_NUM = re.compile(r"[ \u3000](\d{1,4})\s*$")
OLD_DOT = re.compile(r"[.．·…]{2,}")
SKIP = ("目录", "目 录", "Contents", "CONTENTS")


def parse(lines, new: bool):
    """返回 [(title, printed)]。"""
    out, pending = [], None
    for raw in lines:
        line = raw.strip()
        if not line or line in SKIP:
            continue
        if PART_RE.match(line):
            out.append((line, None))
            pending = None
            continue
        m_num = (_TOC_PAGE_RE if new else OLD_NUM).search(line)
        if not m_num:
            pending = line
            continue
        printed = int(m_num.group(1))
        m_dot = (_TOC_DOT_RE if new else OLD_DOT).search(line)
        if new:
            head = line[:m_dot.start()].strip() if m_dot else line[:m_num.start()].strip()
            if _looks_like_authors(head):
                title = pending or head
            else:
                title = head if len(head) >= 3 else (pending or "")
        else:
            if m_dot:
                head = line[:m_dot.start()].strip()
                title = head if len(head) >= 3 else (pending or "")
            else:
                head = line[:m_num.start()].strip()
                title = pending if (pending and _looks_like_authors(head)) else head
        pending = None
        title = clean_title(title)
        if len(title) >= 3:
            out.append((title, printed))
    return out


def main():
    work = os.path.join(BASE, "_work")
    grand = 0
    for sid in sorted(os.listdir(work)):
        op = os.path.join(work, sid, "outline.json")
        if not os.path.isfile(op):
            continue
        o = json.load(open(op, encoding="utf-8"))
        toc = o.get("printed_toc") or {}
        lines = (toc.get("raw") or "").split("\n")
        if not lines or not any(l.strip() for l in lines):
            print(f"-- {sid[:56]}  <无目录原文>")
            continue
        old = parse(lines, new=False)
        new = parse(lines, new=True)
        ot = [t for t, _ in old]
        nt = [t for t, _ in new]
        gained = [t for t in nt if t not in ot]
        lost = [t for t in ot if t not in nt]
        # 旧条目是否逐字仍在（拼接会改写的只是新条目，旧条目若消失要看是不是被拼进去了）
        print("=" * 78)
        print(f"{sid[:70]}")
        print(f"  行数 {sum(1 for l in lines if l.strip()):3d} | 旧 {len(old):3d} 条 → 新 {len(new):3d} 条"
              f" | 旧有今无 {len(lost)}")
        if lost:
            for t in lost:
                print(f"     [消失] {t[:70]}")
        for t in gained:
            print(f"     [新增] {t[:70]}")
        grand += len(gained)
    print("=" * 78)
    print(f"合计新增条目 {grand}")


main()
