"""导出命名回归：真跑一次导出，核对新文件名（`{全局序号}_{层级标题链}.md`）。

**完全不碰他的目录**：导出写到系统临时目录，wd 也用一个空临时目录。
基线（2026-09-21）：L2 = 21 个 md / 400,233 字 / 43 张图，locator_type=paginated。
本轮预期变化**只有文件名**，所以：
  · 文件数、字数、图片、页锚、脚注 —— 必须与基线一致（改名不该动内容）；
  · 文件名 —— 必须等于「序号 + 祖先标题链 + 自身标题」；
  · 00-目录.md / manifest.csv 里的路径 —— 必须逐条指向真实存在的文件。
"""
from __future__ import annotations

import csv
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                                        # noqa: E402
from core.exporter import _title_chain, breadcrumb, export, frontier     # noqa: E402
from core.outline import load_outline                                    # noqa: E402
from core.pagecal import load_calibration                                # noqa: E402
from core.project import iter_blocks, load_shards                        # noqa: E402
from core.verify import run_all                                          # noqa: E402

SID = "中国数字人文发展报告"
OUT = ROOT / "_regtest" / "_diag_name_export.txt"
BASE = {"files": 21, "chars": 400233, "images": 43}

L: list[str] = []
ok = True


def chk(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    L.append(f"  {'PASS' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")


real_wd = work_dir(SID)
shards_obj = load_shards(real_wd)
blocks = iter_blocks(shards_obj)
outline = load_outline(real_wd)
calib = load_calibration(real_wd)
shards = [s.to_dict() for s in shards_obj]

tmp = Path(tempfile.gettempdir()) / "_doclab_namecheck"
L.append(f"临时输出目录（工作区之外）：{tmp}")

for depth in (2, 3):
    od = tmp / f"L{depth}"
    L.append(f"\n================= L{depth} 导出 =================")
    meta = export(SID, tmp, od, blocks, calib, outline, depth,
                  copy_images=True, footnote_mode="page-end", shards_meta=shards)
    st = meta["stats"]
    L.append(f"  files={st['files']}  chars={st['chars']:,}  images={st['images_copied']}")

    # ---- 1. 文件名 = 序号 + 层级标题链，且与 breadcrumb 逐字对得上
    nodes = frontier(outline["tree"], depth)
    bad_name, bad_chain = [], []
    for i, n in enumerate(nodes, 1):
        crumbs = breadcrumb(outline["tree"], n)
        title = n["title"] or f"节点{n['nid']}"
        want = f"{i:03d}_{_title_chain(crumbs, title)}.md"
        if not (od / want).exists():
            bad_name.append(want)
        # 链上每一截都该能在文件名里找到（被丢的祖先除外）
        for c in crumbs[1:]:
            head = c[:10]
            if head and head not in want:
                bad_chain.append((want, c))
    chk("每个节点都有对应文件，且名字 = 序号_标题链", not bad_name,
        f"缺 {len(bad_name)} 个：{bad_name[:2]}")
    chk("层级链完整（近层祖先一截不缺）", not bad_chain,
        f"{len(bad_chain)} 处：{bad_chain[:2]}")

    # ---- 2. 撞名
    names = [p.name for p in od.glob("*.md") if re.match(r"^\d{3,}_", p.name)]
    chk("无撞名", len(names) == len(set(names)), f"{len(names)} - {len(set(names))}")

    # ---- 3. 目录 / 清单里的路径逐条落地
    idx = (od / "00-目录.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)]+\.md)\)", idx)
    miss = [x for x in links if not (od / x).exists()]
    chk("00-目录.md 的链接全部指向真实文件", not miss or links == [], f"断链 {len(miss)}")

    with (od / "manifest.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    miss2 = [r["path"] for r in rows if not (od / r["path"]).exists()]
    chk("manifest.csv 的 path 列全部落地", not miss2, f"断链 {len(miss2)}")
    chk("manifest.csv 行数 = 文件数", len(rows) == st["files"], f"{len(rows)} vs {st['files']}")

    # ---- 4. 内容基线：改名不该动字数
    if depth == 2:
        chk(f"files 与基线一致（{BASE['files']}）", st["files"] == BASE["files"], str(st["files"]))
        chk(f"chars 与基线一致（{BASE['chars']:,}）", st["chars"] == BASE["chars"], f"{st['chars']:,}")
        chk(f"images 与基线一致（{BASE['images']}）", st["images_copied"] == BASE["images"],
            str(st["images_copied"]))

        md = sorted(p for p in od.glob("*.md") if p.name != "00-目录.md")

        def count_unmapped(files) -> int:
            return sum(
                1 for p in files
                for m in re.finditer(r"^<!--\s*p=([^>]*?)\s*-->\s*$", p.read_text(encoding="utf-8"), re.M)
                if "unmapped" in m.group(1)
            )

        n_unmapped = count_unmapped(md)
        anch = [m.group(1) for p in md
                for m in re.finditer(r"^<!--\s*p=([^>]*?)\s*-->\s*$", p.read_text(encoding="utf-8"), re.M)]
        # 页锚与本次改动无关（只改了文件名），所以拿他目录里**上一轮**的导出当场对比。
        # 早先这里硬写 unmapped==0 是错的：这本书本来就有降级页（覆盖率 0.964），
        # 那种断言是在测一个从来没成立过的性质。
        old_dir = ROOT / "out" / SID / "L2"
        if old_dir.is_dir():
            old_unmapped = count_unmapped(sorted(p for p in old_dir.glob("*.md")
                                                 if p.name != "00-目录.md"))
            chk("unmapped 页锚数与本轮改动前一致", n_unmapped == old_unmapped,
                f"新 {n_unmapped} / 旧 {old_unmapped}（共 {len(anch)} 条锚）")
        else:
            L.append(f"  -- 旧导出目录不在，unmapped 未对比（{n_unmapped}/{len(anch)}）")
        chk("页锚仍是 `p=N` / `p=front-N` 样式，无冒牌纸书页码",
            all(re.fullmatch(r"(front-)?\d+|pdf-\d+ unmapped|unmapped", a) for a in anch),
            f"{len(anch)} 条锚")
        foot = sum(len(re.findall(r"^\*\*脚注\*\*", p.read_text(encoding="utf-8"), re.M)) for p in md)
        chk("脚注仍在（本轮不改这块）", foot > 0, f"{foot} 处")
        rep = run_all(tmp, od, blocks, calib)
        L.append(f"  -- 闸门总判定：{rep['verdict']}")
        for g in rep["gates"]:
            L.append(f"     {g['id']} {g['name']}: {g['status']}")

    # ---- 5. 抽样给人看
    L.append("  -- 前 3 个文件名：")
    for i, n in enumerate(nodes[:3], 1):
        crumbs = breadcrumb(outline["tree"], n)
        L.append(f"     {i:03d}_{_title_chain(crumbs, n['title'])}.md")

L.append(f"\n{'=' * 60}\n总判定：{'全部通过' if ok else '有失败项'}")
L.append(f"（临时目录 {tmp}，可随时删；他的 out/ 与 _work/ 一个字节没动）")

OUT.write_text("\n".join(L), encoding="utf-8")
print("\n".join(L))
sys.exit(0 if ok else 1)
