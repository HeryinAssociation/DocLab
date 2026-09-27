"""PDF 路径回归：把《中国数字人文发展报告》重导一遍，与基线逐项比对。

**完全不碰他的目录**：导出写到系统临时目录，wd 也用一个空临时目录
（export() 只会往 wd 里写 export_index.json，指向本次的临时产物）。
基线（2026-09-21）：L2 = 21 个 md / 400,233 字 / 43 张图，locator_type=paginated。

本轮唯一预期的变化：页下注以前被当噪声整类丢掉了（NOISE_TYPES 含 page_footnote，
而导出在渲染前按 NOISE_TYPES 过滤），现在会随文/页末出现。所以：
  · 文件数、字数、页锚、图片 —— 必须与基线一致；
  · 脚注条数 —— 必须从 0 变成上千。

顺带核一个疑点：level_stats 的 files_if_split（标签写「若切到本层=文件数」）
到底等不等于真实的 frontier 数。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                                        # noqa: E402
from core.exporter import export, frontier                              # noqa: E402
from core.outline import load_outline                                   # noqa: E402
from core.pagecal import load_calibration                               # noqa: E402
from core.project import iter_blocks, load_shards                       # noqa: E402
from core.verify import run_all                                         # noqa: E402

SID = "中国数字人文发展报告"
OUT = ROOT / "_regtest" / "_diag_pdf_export.txt"
BASE = {"files": 21, "chars": 400233, "images": 43}
L: list[str] = []

real_wd = work_dir(SID)
blocks = iter_blocks(load_shards(real_wd))
outline = load_outline(real_wd)
calib = load_calibration(real_wd)
shards = [s.to_dict() for s in load_shards(real_wd)]

tmp = Path(tempfile.gettempdir()) / "_doclab_pdfcheck"
od = tmp / "L2"
L.append(f"临时输出目录（工作区之外）：{od}")

# ---- files_if_split 疑点：标签说「若切到本层=文件数」，实际是「该层节点数」？
L.append("\n--- frontier 数 vs level_stats.files_if_split ---")
for d in (1, 2, 3):
    fr = frontier(outline["tree"], d)
    st = outline["level_stats"].get(str(d), {})
    L.append(f"  depth={d}: frontier={len(fr)}  files_if_split={st.get('files_if_split')}"
             f"  nodes={st.get('nodes')}  {'✅一致' if len(fr) == st.get('files_if_split') else '❌不一致'}")

meta = export(SID, tmp, od, blocks, calib, outline, 2,
              copy_images=True, footnote_mode="page-end", shards_meta=shards)
st = meta["stats"]
L.append(f"\n--- 导出统计 ---")
L.append(f"  files={st['files']}（基线 {BASE['files']}）"
         f"  {'✅' if st['files'] == BASE['files'] else '❌'}")
L.append(f"  chars={st['chars']:,}（基线 {BASE['chars']:,}）"
         f"  {'✅' if st['chars'] == BASE['chars'] else '❌'}")
L.append(f"  images_copied={st['images_copied']}（基线 {BASE['images']}）"
         f"  {'✅' if st['images_copied'] == BASE['images'] else '❌'}")
L.append(f"  unmapped_pages={st['unmapped_pages']}  stale_removed={st['stale_removed']}")
L.append(f"  locator_type={meta['locator_type']}  verdict={meta['calibration_verdict']}")

md = sorted(p for p in od.glob("*.md") if p.name != "00-目录.md")
foot = sum(len(re.findall(r"<!-- footnote -->", p.read_text(encoding="utf-8"))) for p in md)
foot_end = sum(len(re.findall(r"^\*\*脚注\*\*", p.read_text(encoding="utf-8"), re.M)) for p in md)
anch = [m.group(1) for p in md for m in re.finditer(r"^<!--\s*p=([^>]*?)\s*-->\s*$",
                                                    p.read_text(encoding="utf-8"), re.M)]
unmapped = [a for a in anch if "unmapped" in a]
L.append(f"\n--- 脚注（本轮唯一预期变化）---")
L.append(f"  随文稿记 <!-- footnote -->：{foot} 条")
L.append(f"  页末脚注块：{foot_end} 处")
L.append(f"  → {'✅ 从 0 变成有内容' if (foot or foot_end) else '❌ 仍然一条都没有'}")
L.append(f"\n--- 页锚 ---")
L.append(f"  锚总数 {len(anch)}，其中 unmapped {len(unmapped)}"
         f"（{'沿用原口径' if len(unmapped) == 0 else '有降级页，属既有行为'}）")
allmd = {p.name: p.read_text(encoding="utf-8") for p in md}
n_ren = sum(1 for t in allmd.values() if "任剑涛" in t)
# 与上一轮（改动前，2026-09-21）那次导出对读 —— 不写「改动前为 0」这种没当场验过的话
old_dir = ROOT.parent / "doclab" / "out" / SID / "L2"
old_note = old_ren = None
if old_dir.is_dir():
    old_files = sorted(p for p in old_dir.glob("*.md") if p.name != "00-目录.md")
    old_notes = sum(len(re.findall(r"^\*\*脚注\*\*（", p.read_text(encoding="utf-8"), re.M))
                    for p in old_files)
    old_ren = sum(1 for p in old_files if "任剑涛" in p.read_text(encoding="utf-8"))
    old_note = old_notes
L.append(f"  含「任剑涛」的书目引注：新版 {n_ren} 个文件"
         + (f"／旧版 {old_ren} 个文件" if old_ren is not None else "（旧目录不在，没比）"))
L.append(f"  页末脚注块：新版 {foot_end}／旧版 {old_note if old_note is not None else '—'}")

rep = run_all(tmp, od, blocks, calib)
L.append("\n--- 闸门 ---")
for g in rep["gates"]:
    L.append(f"  {g['id']} {g['name']}: {g['status']} —— {g['note']}")
L.append(f"  总判定：{rep['verdict']}")
L.append(f"\n（临时目录 {tmp}，可随时删；他的 out/ 与 _work/ 一个字节没动）")

OUT.write_text("\n".join(L), encoding="utf-8")
print("\n".join(L))
