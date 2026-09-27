"""人工新增标题断点 —— 导出端到端验证（只写 _regtest 临时目录）。

用法：python _regtest/_diag_export_added.py [source_id]
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                                  # noqa: E402
from core.exporter import export                                  # noqa: E402
from core.outline import build_tree, node_key, walk_nodes          # noqa: E402
from core.pagecal import calibrate                                # noqa: E402
from core.project import iter_blocks, load_shards                  # noqa: E402

SID = sys.argv[1] if len(sys.argv) > 1 else "认识论引论-夏甄陶.2b04297f3c"
wd = work_dir(SID)
pj = json.loads((wd / "project.json").read_text(encoding="utf-8"))
shards = load_shards(wd)
blocks = iter_blocks(shards)
calib = calibrate(SID, blocks, [s.to_dict() for s in shards], anchors={})
bmap = {b.gid: b for b in blocks}
OUT = ROOT / "_regtest" / "_exp_added"


def build(added, levels=None):
    roots, oc = build_tree(SID, blocks, doc_title=pj.get("doc_title", ""), calib=calib,
                           manual_levels=levels or {}, manual_deleted={},
                           manual_added=added)
    ns = [n for r in roots for n in walk_nodes(r)]
    return ns, oc, [n.to_dict() for n in roots]


def do_export(tree, depth, tag):
    d = OUT / tag
    if d.exists():
        shutil.rmtree(d)
    export(SID, wd, d, blocks, calib,
           {"tree": tree, "doc_title": pj.get("doc_title", ""), "source_id": SID},
           depth, copy_images=False, shards_meta=[s.to_dict() for s in shards])
    return d


base_ns, _, base_tree = build({})
in_tree = {n.gid_start for n in base_ns}
body = next(b for b in blocks
            if b.type == "text" and b.gid not in in_tree and b.gid > 30
            and len(b.text) > 120)
print(f"源 {SID}")
print(f"目标块 gid={body.gid} {body.shard}p{body.page_idx} len={len(body.text)}")
print(f"   原文：{body.text[:70]}")

for tag, off in (("off0", 0), ("off30", 30)):
    ns, oc, tree = build({str(body.gid): {"title": "测试断点", "level": 2,
                                          "offset": off}})
    d = do_export(tree, 2, tag)
    files = sorted(p for p in d.glob("*.md") if p.name != "00-目录.md")
    hit = [p for p in files if "测试断点" in p.read_text(encoding="utf-8")[:400]]
    print(f"\n=== offset={off} → {len(files)} 个文件 | 断点文件 {len(hit)} 个")
    if not hit:
        print("   !! 没有找到新增标题的文件")
        continue
    f = hit[0]
    txt = f.read_text(encoding="utf-8")
    lines = [x for x in txt.split("\n")]
    print(f"   {f.name}")
    print(f"   正文（跳过头部注释与面包屑）：")
    body_start = next(i for i, x in enumerate(lines) if x.startswith("<!-- p="))
    for x in lines[body_start:body_start + 4]:
        print(f"     | {x[:86]}")
    # 断点后第一段正文的首句应该＝原文（offset=0）或原文第 30 字起（offset=30）
    segs = [x for x in lines[body_start:] if x.strip() and not x.startswith("<!--")]
    first = segs[0] if segs else ""
    want = body.text[off:off + 30]
    print(f"   断点后首段以「{first[:26]}」开头；期望以「{want[:26]}」开头 → "
          f"{'一致' if first.startswith(want[:20]) else '不一致'}")
    # 前一个文件：断点块的原文不该在这里完整出现
    prev = files[files.index(f) - 1]
    ptxt = prev.read_text(encoding="utf-8")
    if off == 0:
        has = body.text[:30] in ptxt
        print(f"   前一个文件 {prev.name}：还含整段原文？{'是（错）' if has else '否（对）'}")
    else:
        head = body.text[:off]
        tail = body.text[off:off + 30]
        print(f"   前一个文件 {prev.name}：含前 {off} 字？"
              f"{'是（对）' if head in ptxt else '否（错）'}；"
              f"含断点后的字？{'是（错）' if tail in ptxt else '否（对）'}")

# 无新增时导出结果应与改造前一致（回归）
d0 = do_export(base_tree, 2, "base")
import hashlib                                                    # noqa: E402
h = hashlib.sha256()
for p in sorted(d0.glob("*.md")):
    h.update(p.name.encode("utf-8"))
    h.update(p.read_bytes())
print(f"\n无人工新增时导出：{len(list(d0.glob('*.md')))} 个 md，"
      f"合计 sha256={h.hexdigest()[:16]}…")
