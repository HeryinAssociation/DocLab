"""诊断两本校核里🔴的根因。只读，不改任何产物。

两问：
  A 魂系：人工锚点 P1 16→1 生成的区间为什么 kind=front？(_kind_at 在观测缺口处
    继承邻居) —— 重算干净自动分段，把「16 落在哪/前一后一段是谁」摆出来。
  B 现代档案：P2/P3 一串单页区间的印刷值是不是 OCR 误读？直接读那些页上的
    page_number 块原文，跟前后页比。
"""
from __future__ import annotations

import sys
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DOC))

from core.config import work_dir                                   # noqa: E402
from core.project import load_shards, iter_blocks                   # noqa: E402
from core.pagecal import calibrate, _kind_at, load_calibration      # noqa: E402

L: list[str] = []


def w(s: str = "") -> None:
    L.append(s)


def seg_table(tag: str, segs: list) -> None:
    w(f"| {tag} | shard | 物理 | 印刷 | off | obs | 覆盖 | 置信 | kind |")
    w("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in sorted(segs, key=lambda x: (x.shard, x.page_idx_start)):
        w(f"| {tag} | {s.shard} | {s.page_idx_start}–{s.page_idx_end} "
          f"| {s.printed_start}–{s.printed_end} | {s.offset:+d} | {s.n_obs} "
          f"| {s.coverage:.0%} | {s.confidence} | {s.kind} |")


def load(sid_sub: str):
    from core.config import resolve_source
    sid = resolve_source(sid_sub)
    wd = work_dir(sid)
    shards = load_shards(wd)
    blocks = iter_blocks(shards)
    return sid, wd, shards, blocks


# ---------------------------------------------------------------- A 魂系
w("## A 魂系历史主义：人工锚点区间的 kind 是怎么来的")
sid, wd, shards, blocks = load("魂系")
smeta = [s.to_dict() for s in shards]
auto = calibrate(sid, blocks, smeta, anchors={})          # 干净自动分段
w(f"- source_id `{sid}`｜片 {auto.shard_pages}")
w(f"- 自动判定 `{auto.verdict}`／`{auto.locator_type}`：{auto.reason}")
w("")
w("### 自动分段（未套人工锚点）")
seg_table("auto", auto.segments)
w("")
w("### 人工锚点 P1 16→1 / 199→184 套上之后")
man = load_calibration(wd)
seg_table("manual", man.segments)
w("")
w("### 关键：apply_anchors 给 16 号物理页挑 kind 时会看到什么")
prev = [s for s in auto.segments if s.shard == "P1" and s.page_idx_end < 16]
cont = [s for s in auto.segments if s.shard == "P1" and s.contains(16)]
w(f"- 自动段中**包含**物理页 16 的：{[(s.page_idx_start, s.page_idx_end, s.kind) for s in cont] or '（无 —— 16 落在观测缺口里）'}")
w(f"- 落在 16 之前、最近的一段：{[(s.page_idx_start, s.page_idx_end, s.kind, f'{s.offset:+d}') for s in prev][-1:]}")
w(f"- `_kind_at(auto, 'P1', 16)` → **{_kind_at(auto.segments, 'P1', 16)}**")
w("")
w("### 同一 offset 的自动段是谁")
w(f"- offset −15 的自动段：{[(s.page_idx_start, s.page_idx_end, s.kind) for s in auto.segments if s.shard == 'P1' and s.offset == -15]}")
w("")
w("### 落盘后若干物理页的定位符（manual）")
for p in (24, 31, 70, 95, 140, 198, 199):
    w(f"- P1 物理 {p} → `{man.locator('P1', p)}`")
w("")
w("### 标签冲突检查：有没有两个物理页拿到同一个定位符")
seen: dict[str, list] = {}
for p in range(0, 200):
    loc = man.locator("P1", p)
    if loc:
        seen.setdefault(loc, []).append(p)
dup = {k: v for k, v in seen.items() if len(v) > 1}
w(f"- P1 内重复的定位符（前 10 条）：{dict(list(dup.items())[:10])}")
w(f"- 重复定位符共 {len(dup)} 个，涉及 {sum(len(v) for v in dup.values())} 个物理页")

# ---------------------------------------------------------------- B 现代档案
w("")
w("")
w("## B 现代档案与文件管理必读：单页离群区间是不是 OCR 误读")
sid2, wd2, shards2, blocks2 = load("现代档案")
smeta2 = [s.to_dict() for s in shards2]
auto2 = calibrate(sid2, blocks2, smeta2, anchors={})
w(f"- source_id `{sid2}`｜片 {auto2.shard_pages}")
w(f"- 自动判定 `{auto2.verdict}`／`{auto2.locator_type}`：{auto2.reason}")
w("")
w("### 自动分段")
seg_table("auto", auto2.segments)
w("")


def pnums(shard: str, pages: list[int]) -> None:
    """把这些物理页上的 page_number 块原文摊开。"""
    rows = []
    for b in blocks2:
        if b.shard == shard and b.page_idx in pages and b.type == "page_number":
            rows.append((b.page_idx, repr((b.text or "").strip())))
    seen_p = set()
    for p in sorted(pages):
        got = [t for (pp, t) in rows if pp == p]
        seen_p.add(p)
        w(f"  - {shard} 物理 {p}: {'／'.join(got) if got else '（无 page_number 块）'}")


w("### P2 各离群页 vs 前后页的页码块原文（看印刷值是否只是末位/首位被认错）")
for lo, hi in ((123, 129), (138, 144), (157, 162), (169, 174), (181, 186),
               (22, 27), (47, 52)):
    w(f"**P2 物理 {lo}–{hi}**")
    pnums("P2", list(range(lo, hi + 1)))
    w("")
w("### P3 各离群页")
for lo, hi in ((11, 19), (145, 153)):
    w(f"**P3 物理 {lo}–{hi}**")
    pnums("P3", list(range(lo, hi + 1)))
    w("")
w("### P1 各离群页")
for lo, hi in ((2, 12), (41, 45), (58, 62)):
    w(f"**P1 物理 {lo}–{hi}**")
    pnums("P1", list(range(lo, hi + 1)))
    w("")

# ---------------------------------------------------------------- C 入库进度
w("")
w("## C 后台入库进度")
logd = DOC / "_regtest" / "_ingest_logs"
for tag in ("dinghuadong", "huhongjie"):
    p = logd / f"{tag}.log"
    w(f"### {tag}")
    if not p.exists():
        w("- （还没有日志）")
        continue
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    w(f"- 共 {len(lines)} 行，最后 12 行：")
    for x in lines[-12:]:
        w(f"  - {x}")

out = DOC / "_regtest" / "_diag_calib.md"
out.write_text("\n".join(L), encoding="utf-8")
print(f"written {out}")
