"""导出回归：用真实数据重导一次，与 out/ 里改造前的产物逐文件比对。

目的：证明「块内断点」这次改造对**没有人工新增**的书是零影响。
只写 _regtest 临时目录，绝不碰 out/。
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import out_dir, work_dir                     # noqa: E402
from core.exporter import export                              # noqa: E402
from core.outline import load_outline                         # noqa: E402
from core.pagecal import load_calibration                     # noqa: E402
from core.project import iter_blocks, load_shards             # noqa: E402

SID = "中国数字人文发展报告"
DEPTH = 2

wd = work_dir(SID)
old = out_dir(SID) / f"L{DEPTH}"
new = ROOT / "_regtest" / "_exp_regress"
if new.exists():
    shutil.rmtree(new)

shards = load_shards(wd)
blocks = iter_blocks(shards)
calib = load_calibration(wd)
outline = load_outline(wd)
print(f"源 {SID}  depth=L{DEPTH}")
print(f"  旧产物 {old}")
print(f"  outline: 节点 {outline.get('node_total')}  "
      f"manual_added={outline.get('manual_added_count', '(旧产物无此字段)')}")

meta = export(SID, wd, new, blocks, calib, outline, DEPTH,
              copy_images=False, shards_meta=[s.to_dict() for s in shards])

old_md = sorted(p.name for p in old.glob("*.md"))
new_md = sorted(p.name for p in new.glob("*.md"))
print(f"\n文件清单：旧 {len(old_md)} / 新 {len(new_md)}  "
      f"{'完全一致' if old_md == new_md else '**不一致**'}")
if old_md != new_md:
    print("  只在旧:", set(old_md) - set(new_md))
    print("  只在新:", set(new_md) - set(old_md))

diff, same = [], 0
for name in old_md:
    a, b = old / name, new / name
    if not b.exists():
        continue
    ta, tb = a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")
    if ta == tb:
        same += 1
    else:
        diff.append((name, ta, tb))

print(f"逐字节相同：{same} / {len(old_md)}")
for name, ta, tb in diff[:3]:
    print(f"\n  === {name} 内容不同 ===")
    la, lb = ta.split("\n"), tb.split("\n")
    n = 0
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            print(f"   L{i+1} 旧| {x[:96]}")
            print(f"   L{i+1} 新| {y[:96]}")
            n += 1
            if n >= 3:
                break
    if len(la) != len(lb):
        print(f"   行数 旧 {len(la)} / 新 {len(lb)}")

h = hashlib.sha256()
for p in sorted(new.glob("*.md")):
    h.update(p.name.encode("utf-8"))
    h.update(p.read_bytes())
print(f"\n新导出全量 sha256 = {h.hexdigest()}")
print(f"导出统计 = {json.dumps(meta['stats'], ensure_ascii=False)}")
