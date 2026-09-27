"""按层级切分导出：一节一个 md，段落级页锚，附转换元数据。

输出严格对齐 JingyeLab APR-20260920-001：
  - 页锚唯一样式 `<!-- p=N -->`（N = 纸书页码），前置页 `<!-- p=front-N -->`；
  - 无法映射到纸书页码的页显式写 `<!-- p=pdf-N unmapped -->`，不冒充纸书页码；
  - `conversion_meta.json` 必含 converter / converted_at / page_offset / locator_type / outputs；
  - 只输出 md 与元数据，不碰 Zotero / evidence_table / library_index。
"""
from __future__ import annotations

import csv
import re
import shutil
from dataclasses import replace
from pathlib import Path

from .pagecal import Calibration
from .project import NOISE_TYPES, Block, is_noise_text
from .util import (now_iso, write_json, read_json, safe_filename, sha256_text,
                   write_text)

DOCLAB_VERSION = "0.1.0"
FOOTNOTE_MODES = ("page-end", "inline", "drop")
# doclab 自己切出来的正文文件名（`NNN_标题.md`）。重跑导出时靠它认领「上一轮的遗留文件」，
# 见 export 里清理那一段。刻意不匹配 `00-目录.md`（分隔符是 `-`）与 `校验报告.md`。
BODY_FILE_RE = re.compile(r"^\d{3,}_.+\.md$")


# ---------------------------------------------------------------- 切分边界

def frontier(nodes: list[dict], depth: int) -> list[dict]:
    """按深度取切分边界。

    depth=0 → 整本一个文件（返回一个虚拟节点）。
    depth=k → 沿树下行到第 k 层；某支在第 k 层之前就断了，则该叶节点即为边界。
    """
    if depth <= 0:
        return []
    out: list[dict] = []
    for n in nodes:
        if n["level"] >= depth or not n.get("children"):
            out.append(n)
        else:
            out.extend(frontier(n["children"], depth))
    return out


def breadcrumb(tree: list[dict], target: dict) -> list[str]:
    """目标节点的祖先标题链（不含自身）。"""
    def dfs(nodes, path):
        for n in nodes:
            if n["nid"] == target["nid"]:
                return path
            got = dfs(n.get("children", []), path + [n["title"]])
            if got is not None:
                return got
        return None

    return dfs(tree, []) or []


# 文件名里标题链的总长上限（不含左侧序号与 `.md`）。
# 定 100 的依据是**整条全路径**要留在 Windows 的 260 字以内：本机前缀
# `…\doclab\out\<source_id>\L<depth>\` 最坏约 100 字（源 id 本身就可能是
# 一长串「书名（丛书）（作者）」），加上 100 字文件名与 `.md` 仍有富余。
# 定得比 70 宽是因为链要放下 L1_L2_L3 一整条；太窄会出现「同目录里有几条
# 保留了最外层、另几条没有」这种不一致。层更深时仍靠丢最外层祖先来收敛。
NAME_CHAIN_MAX = 100


def _title_chain(crumbs: list[str], title: str) -> str:
    """文件名主体 = 层级标题链：`L1标题_L2标题_…_自身标题`。

    为什么不是一串层级数字（`8_1`）：数字要先知道每层各有几个节点才看得懂，
    标题则一眼就知道这个文件挂在树的哪一支。序号只保留最前面那**一个**全局号，
    它的职责是让文件夹里按名排序就等于书的顺序。

    超长时从**最外层**祖先开始往外丢。safe_filename 是从右往左截的，整条链
    直接交给它会先把最该留的末层（自身标题）切掉 —— 那样文件名就认不出来了。
    """
    parts = [safe_filename(p, NAME_CHAIN_MAX) for p in (crumbs or []) + [title]]
    while len(parts) > 1 and len("_".join(parts)) > NAME_CHAIN_MAX:
        parts.pop(0)
    stem = "_".join(parts)
    if len(stem) > NAME_CHAIN_MAX:      # 光自身标题就超长 → 退回旧行为，右侧截断
        stem = stem[:NAME_CHAIN_MAX].rstrip()
    return stem or "untitled"


# ---------------------------------------------------------------- 正文渲染

ANCHOR_RE = re.compile(r"^<!--\s*(p=|doclab:).*-->\s*$")


def render_blocks(blocks: list[Block], calib: Calibration, global_page_of,
                  footnote_mode: str = "page-end") -> tuple[list[str], dict]:
    """把块序列渲染成 md 行，按页插入页锚。

    返回 (行列表, 统计)。header / page_number 是版面噪声，一律不进正文
    （页眉信息已由页码校准消化）。
    """
    lines: list[str] = []
    stats = {"blocks_rendered": 0, "anchors": 0, "unmapped": 0,
             "footnotes": 0, "images": 0, "tables": 0, "skipped_noise": 0}
    last_key = None
    pending_footnotes: dict[tuple[str, int], list[str]] = {}

    def flush_footnotes():
        for (s, p), notes in pending_footnotes.items():
            if not notes:
                continue
            lines.append("")
            lines.append(f"**脚注**（{s} 物理页 {p}）：")
            for nt in notes:
                lines.append(f"- {nt}")
            stats["footnotes"] += len(notes)
        pending_footnotes.clear()

    for b in blocks:
        if b.type in ("header", "page_number", "footer", "aside_text"):
            stats["skipped_noise"] += 1
            continue

        key = (b.shard, b.page_idx)
        if key != last_key:
            if footnote_mode == "page-end" and pending_footnotes:
                flush_footnotes()
            loc = calib.locator(b.shard, b.page_idx)
            if loc is None:
                stats["unmapped"] += 1
                lines.append(calib.anchor(b.shard, b.page_idx, global_page_of(b.shard, b.page_idx)))
            else:
                lines.append(f"<!-- p={loc} -->")
            stats["anchors"] += 1
            last_key = key

        if b.type == "page_footnote":
            if footnote_mode == "inline":
                lines.append(f"<!-- footnote --> {b.text}")
                stats["footnotes"] += 1
            elif footnote_mode == "page-end":
                pending_footnotes.setdefault(key, []).append(b.text)
            continue

        if b.type == "text":
            if b.text.strip():          # 空 text 是版面残留，不产生空行
                lines.append(b.text)
                stats["blocks_rendered"] += 1
        elif b.type in ("image", "chart"):
            cap = "；".join(b.caption) if b.caption else ""
            rel = Path(b.img_path).name if b.img_path else ""
            lines.append(f"![{cap}](images/{rel})" if rel else f"![{cap}]()")
            if cap:
                lines.append(f"*{cap}*")
            for nt in b.footnote:
                lines.append(f"> {nt}")
            stats["images"] += 1
            stats["blocks_rendered"] += 1
        elif b.type == "table":
            for cap in b.caption:
                lines.append(f"**{cap}**")
            if b.table_body:
                lines.append(b.table_body)
            for nt in b.footnote:
                lines.append(f"> {nt}")
            stats["tables"] += 1
            stats["blocks_rendered"] += 1
        elif b.type in ("equation", "interline_equation"):
            lines.append(b.text)
            stats["blocks_rendered"] += 1
        else:
            if b.text:
                lines.append(b.text)
                stats["blocks_rendered"] += 1

    if footnote_mode == "page-end" and pending_footnotes:
        flush_footnotes()
    return lines, stats


def anchor_range(calib: Calibration, blocks: list[Block]) -> tuple[str, str, int]:
    """返回 (首个可映射定位符, 末个, 未映射页数)。"""
    locs = [calib.locator(b.shard, b.page_idx) for b in blocks if b.is_content]
    locs = [x for x in locs if x]
    if not locs:
        return "", "", len([x for x in locs if x is None])
    return locs[0], locs[-1], 0


def _slice_block(b: Block, a: int, z: int | None) -> Block | None:
    """取块文本的 [a, z) 一段，造一个副本（绝不改动原始块）。

    人工新增的标题可以**切进段落内部**（offset > 0）：这时那个段落要一分为二，
    前半留在前一个文件、后半归新断点。返回 None 表示这一段被切空了。
    """
    if not a and z is None:
        return b
    s = b.text or ""
    if not s:
        return b if not a else None
    piece = s[a:z] if z is not None else s[a:]
    if not piece.strip():
        return None
    return replace(b, text=piece)


# ---------------------------------------------------------------- 主流程

def export(source_id: str, wd: Path, out_root: Path, blocks: list[Block],
           calib: Calibration, outline: dict, depth: int,
           copy_images: bool = True, footnote_mode: str = "page-end",
           shards_meta: list[dict] | None = None) -> dict:
    if footnote_mode not in FOOTNOTE_MODES:
        raise ValueError(f"footnote_mode 必须是 {FOOTNOTE_MODES}")

    wd, out_root = Path(wd), Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    images_out = out_root / "images"
    if copy_images:
        images_out.mkdir(parents=True, exist_ok=True)

    # 全局物理页序（跨片累加），用于不可映射页的 p=pdf-N 标注
    order = [s["tag"] for s in (shards_meta or [])] or calib.shards
    n_pages = {s["tag"]: int(s.get("n_pages") or 0) for s in (shards_meta or [])}
    gstart: dict[str, int] = {}
    acc = 0
    for tag in order:
        gstart[tag] = acc
        acc += n_pages.get(tag, calib.shard_pages.get(tag, 0))

    def global_page_of(shard: str, page_idx: int) -> int:
        return gstart.get(shard, 0) + page_idx + 1

    nodes = frontier(outline["tree"], depth)
    if not nodes:
        nodes = [{
            "nid": "0", "level": 1, "title": (outline.get("doc_title") or source_id),
            "gid_start": min((b.gid for b in blocks), default=0),
            "gid_end": max((b.gid for b in blocks), default=0),
            "shard": blocks[0].shard if blocks else "", "page_idx": 0,
            "marker": "whole", "children": [],
        }]

    by_gid = {b.gid: b for b in blocks}
    outputs: list[dict] = []
    index_rows: list[dict] = []
    block_index: list[dict] = []       # 供 verify 做无损回查
    copied: set[str] = set()
    total_chars = 0

    # 早先这里按「标题重名」临时给父章前缀做消歧。现在文件名自带层级标题链
    # （见 _title_chain），同名节天然就分开了，这一段撤掉；万一链 + 标题仍撞名，
    # 左侧那个全局序号也保证了唯一。

    # 断点位置表：每个导出文件覆盖到「下一个断点之前」为止。
    # 断点位置 ＝ (块的 gid, 块内字符位移)。人工新增的标题可以切进段落内部，
    # 所以后半段那个块要从这里截断，不能整块抄两遍。
    # 用 frontier 出来的 nodes（不是全部节点）算：depth 设得浅时，更深的断点
    # 本来就该合并在父文件里，不该在父文件中间划一刀。
    _marks = sorted({(int(n["gid_start"]), int(n.get("offset") or 0)) for n in nodes})
    _nxt: dict[tuple[int, int], tuple[int, int] | None] = {
        m: (_marks[i + 1] if i + 1 < len(_marks) else None)
        for i, m in enumerate(_marks)
    }

    for seq, n in enumerate(nodes, 1):
        g0, g1 = int(n["gid_start"]), int(n["gid_end"])
        lo = int(n.get("offset") or 0)
        stop = _nxt.get((g0, lo))
        seg = []
        # 本文件的内容管到「下一个断点之前」。**只有块内断点**（offset > 0）才需要
        # 把右端扩出去 —— 那时下一块的前半截仍归本文件，而节点自己的覆盖范围是按
        # 整块算的、表达不了这个。块首断点（offset == 0）时下一块整块归下一个文件，
        # 与改造前逐块一致，不扩。
        # （顺带记一笔：浅层节点的「直属正文」——第一个子节点之前那几块——在按更深
        #   一层导出时本来就没有接收者，旧算法直接丢掉、新算法也照样不补。这是既有
        #   问题，不在这里顺手改，免得动到他已经在用的产物。）
        hi = g1
        if stop is not None and stop[1] > 0 and stop[0] > g1:
            hi = stop[0]
        for g in range(g0, hi + 1):
            b = by_gid.get(g)
            if b is None:
                continue
            a = lo if (g == g0 and lo) else 0           # 本文件可能从段落中间开始
            if stop is not None and g == stop[0]:       # 也可能在段落中间结束
                if stop[1] == 0:
                    break                               # 这一块整块归下一个文件
                b = _slice_block(b, a, stop[1])
                if b is not None:
                    seg.append(b)
                break                                   # 断点之后的内容不属于本文件
            b = _slice_block(b, a, None)
            if b is not None:
                seg.append(b)
        # 版面噪声（页眉/页脚/页码）与 MinerU 流水线残留英文不入正文。
        # 用 NOISE_TYPES 而不是手写 ("header","page_number")：漏掉 footer 会让
        # 每页都带的「中国知网 https://www.cnki.net」之类进正文，统计也会偏小。
        dropped_noise = [b for b in seg
                         if b.type in NOISE_TYPES or is_noise_text(b.text)]
        seg = [b for b in seg if not (b.type in NOISE_TYPES or is_noise_text(b.text))]

        body, stats = render_blocks(seg, calib, global_page_of, footnote_mode)
        crumbs = breadcrumb(outline["tree"], n)
        title = n["title"] or f"节点{n['nid']}"
        fname = f"{seq:03d}_{_title_chain(crumbs, title)}.md"
        header = [
            f"<!-- doclab v{DOCLAB_VERSION} | source={source_id} | node={n['nid']} "
            f"| depth={depth} | locator_type={calib.locator_type} -->",
            f"<!-- source_range: gid {g0}-{g1} | shard {n['shard']} p{n['page_idx']} -->",
        ]
        parts = [f"# {title}", ""]
        if crumbs:
            parts += [f"> 归属：{' › '.join(crumbs)}", ""]
        if depth == 0:
            parts = [f"# {title}", ""]
        content = "\n".join(header + parts + body) + "\n"
        dest = out_root / fname
        write_text(dest, content)

        first_loc, last_loc, _ = anchor_range(calib, seg)
        chars = sum(len(b.text or b.table_body or "") for b in seg if b.is_content)
        total_chars += chars

        # 图片搬运（MinerU 图片名是内容哈希，跨片不会撞名）
        n_img = 0
        if copy_images:
            for b in seg:
                if b.type in ("image", "chart") and b.img_path:
                    rel = Path(b.img_path).name
                    if rel in copied:
                        n_img += 1
                        continue
                    for sm in (shards_meta or []):
                        src_dir = sm.get("images_dir")
                        if not src_dir:
                            continue
                        cand = Path(src_dir) / rel
                        if cand.exists():
                            shutil.copy2(cand, images_out / rel)
                            copied.add(rel)
                            n_img += 1
                            break

        rec = {
            "seq": seq, "nid": n["nid"], "level": n["level"], "title": title,
            "path": fname, "marker": n["marker"],
            "belongs_to": " › ".join(crumbs),
            "gid_range": [g0, g1],
            "shard": n["shard"], "page_idx": n["page_idx"],
            "printed_first": first_loc, "printed_last": last_loc,
            "chars": chars, "blocks": stats["blocks_rendered"],
            "images": n_img, "unmapped_pages": stats["unmapped"],
            "noise_dropped": len(dropped_noise),
            "flags": n.get("flags", []),
            "sha256": sha256_text(content),
        }
        outputs.append(rec)
        index_rows.append(rec)
        block_index.append({
            "path": fname,
            "blocks": [{"gid": b.gid, "type": b.type,
                        "text": (b.text or b.table_body or "")}
                       for b in seg if b.is_content],
        })

    # 重跑导出 ＝ 这份产物重来一遍：上一轮切出来的正文文件，这一轮若不再产生
    # （改了层级、撤了人工新增的标题、断点挪了位置），留在目录里就是**幽灵文件** ——
    # 目录清单里没有它、按文件名看它又像正式产物。下游按文件夹去读（这个工作台的产物
    # 正是给文献流水线用的）就会被它骗到，读到一份已经不存在于目录里的正文。
    # 只删本目录下、长得就是 doclab 切出来那种名字的 md：
    # 不碰 00-目录.md、校验报告.md、images/，更不碰这个目录之外的任何东西。
    wrote = {r["path"] for r in outputs}
    stale = sorted(p.name for p in out_root.glob("*.md")
                   if BODY_FILE_RE.match(p.name) and p.name not in wrote)
    for name in stale:
        (out_root / name).unlink()

    # 元数据：字段名对齐 APR-20260920-001 §2.3
    offsets = {}
    for s in calib.segments:
        offsets.setdefault(s.shard, []).append({"kind": s.kind, "offset": s.offset,
                                                "page_idx_range": [s.page_idx_start, s.page_idx_end],
                                                "printed_range": [s.printed_start, s.printed_end],
                                                "confidence": s.confidence})
    meta = {
        "converter": f"doclab/{DOCLAB_VERSION} (MinerU v4 API adapter)",
        "converted_at": now_iso(),
        "source_id": source_id,
        "source_file": outline.get("doc_title") or source_id,
        "decode_depth": depth,
        "page_offset": offsets or None,
        "locator_type": calib.locator_type,
        "calibration_verdict": calib.verdict,
        "calibration_note": calib.reason,
        "footnote_mode": footnote_mode,
        "outputs": outputs,
        "stats": {
            "files": len(outputs),
            "chars": total_chars,
            "images_copied": len(copied),
            "unmapped_pages": sum(o["unmapped_pages"] for o in outputs),
            "stale_removed": len(stale),
        },
        "stale_removed": stale[:40],
        "gate_note": ("书级文献入库前须满足：md 有页锚或 locator_type=section_only；"
                      "md_status 由单写者按闸门裁定，本工具只输出依据，不落 library_index。"),
    }
    write_json(out_root / "conversion_meta.json", meta)
    write_json(wd / "export_index.json", {"source_id": source_id, "depth": depth,
                                          "files": block_index})
    write_json(out_root / "manifest.json", {
        "source_id": source_id, "depth": depth, "generated_at": now_iso(),
        "files": outputs,
    })

    # 清单 CSV
    with (out_root / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "nid", "level", "title", "belongs_to", "path", "shard", "page_idx",
                    "printed_first", "printed_last", "chars", "blocks", "images", "flags"])
        for r in outputs:
            w.writerow([r["seq"], r["nid"], r["level"], r["title"], r.get("belongs_to", ""),
                        r["path"], r["shard"],
                        r["page_idx"], r["printed_first"], r["printed_last"], r["chars"],
                        r["blocks"], r["images"], ",".join(r["flags"])])

    write_text(out_root / "00-目录.md", build_index_md(outline, outputs, depth, calib))
    return meta


def build_index_md(outline: dict, outputs: list[dict], depth: int, calib: Calibration) -> str:
    L = [f"# {outline.get('doc_title') or outline.get('source_id')} —— 分节目录", "",
         f"- 切分深度：第 {depth} 层（L{depth}）",
         f"- 文件数：{len(outputs)}",
         f"- 定位符类型：`{calib.locator_type}`（{calib.verdict}）",
         f"- 页码区间：{len(calib.segments)} 段，偏移 "
         f"{'、'.join(f'{s.shard}:{s.offset:+d}' for s in calib.segments)}", "",
         "| # | 标题 | 归属 | 纸书页 | 字数 | 文件 |",
         "| --- | --- | --- | --- | --- | --- |"]
    for r in outputs:
        page = f"{r['printed_first']}–{r['printed_last']}" if r["printed_first"] else "—"
        up = (r.get("belongs_to") or "").replace("|", "\\|")
        if len(up) > 44:
            up = up[-42:]
            up = "…" + up.lstrip("… ")
        L.append(f"| {r['seq']} | {r['title'][:60]} | {up} | {page} | {r['chars']:,} "
                 f"| [{r['path']}]({r['path']}) |")
    L.append("")
    return "\n".join(L)
