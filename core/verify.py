"""校验闸门：导出产物能不能用，由这里裁定。

三道闸门（缺一不可）：
  G1 页锚覆盖率   —— 能不能按刊期/页码引用（对齐 §2.3 入库硬条件）
  G2 内容无损     —— 导出文件能否逐块回查到源块，不增不减不改字
  G3 页码一致性   —— 区间内步长恒为 1、断点已显式声明、跨片接续通过

任何一道不通过都会写进 report，且整体 verdict 置 fail/warn —— 不静默放行。
"""
from __future__ import annotations

import re
from pathlib import Path

from .pagecal import Calibration
from .project import Block
from .util import now_iso, read_json, write_json, write_text

ANCHOR_LINE_RE = re.compile(r"^<!--\s*p=.*-->\s*$", re.M)
META_LINE_RE = re.compile(r"^<!--.*-->\s*$", re.M)
WS_RE = re.compile(r"\s+")


def norm(s: str) -> str:
    """比对用归一：去掉所有空白与常见全半角差异。"""
    return WS_RE.sub("", (s or "").replace("\u3000", ""))


def gate_anchor_coverage(blocks: list[Block], calib: Calibration) -> dict:
    content = [b for b in blocks if b.is_content]
    if not content:
        return {"id": "G1", "name": "页锚覆盖率", "status": "skip", "note": "无内容块"}
    resolvable = sum(1 for b in content if calib.locator(b.shard, b.page_idx))
    rate = resolvable / len(content)
    if calib.locator_type == "section_only":
        status, note = "warn", "整书已按规范降级 section_only，页锚不作入库硬条件"
    elif rate >= 0.95:
        status, note = "pass", "页锚覆盖良好"
    elif rate >= 0.80:
        status, note = "warn", "存在未映射页，已用 p=pdf-N unmapped 显式标注"
    else:
        status, note = "fail", "页锚覆盖不足，不满足书级文献入库硬条件（§2.3）"
    return {"id": "G1", "name": "页锚覆盖率", "status": status, "note": note,
            "content_blocks": len(content), "resolvable": resolvable,
            "rate": round(rate, 4)}


def gate_lossless(wd: Path, out_root: Path) -> dict:
    idx = read_json(Path(wd) / "export_index.json", None)
    if not idx:
        return {"id": "G2", "name": "内容无损", "status": "fail",
                "note": "找不到 export_index.json，无法回查"}
    total = missing = 0
    bad: list[dict] = []
    for f in idx.get("files", []):
        p = Path(out_root) / f["path"]
        if not p.exists():
            bad.append({"path": f["path"], "reason": "文件不存在"})
            continue
        hay = norm(META_LINE_RE.sub("", p.read_text(encoding="utf-8")))
        for b in f.get("blocks", []):
            t = norm(b.get("text", ""))
            if not t:
                continue
            total += 1
            if t not in hay:
                missing += 1
                if len(bad) < 30:
                    bad.append({"path": f["path"], "gid": b["gid"], "type": b["type"],
                                "excerpt": b.get("text", "")[:60]})
    rate = (total - missing) / total if total else 1.0
    status = "pass" if missing == 0 else ("warn" if rate >= 0.99 else "fail")
    return {"id": "G2", "name": "内容无损", "status": status,
            "note": "全部源块可在导出文件中逐字回查" if missing == 0
                    else f"{missing}/{total} 个源块未能回查",
            "checked": total, "missing": missing, "examples": bad}


def gate_page_consistency(blocks: list[Block], calib: Calibration) -> dict:
    issues: list[dict] = []
    for s in calib.segments:
        span = s.page_idx_end - s.page_idx_start + 1
        if s.printed_end - s.printed_start != span - 1:
            issues.append({"shard": s.shard, "type": "步长异常",
                           "page_idx_range": [s.page_idx_start, s.page_idx_end],
                           "printed_range": [s.printed_start, s.printed_end]})
        if s.confidence == "low":
            issues.append({"shard": s.shard, "type": "低置信区间",
                           "page_idx_range": [s.page_idx_start, s.page_idx_end],
                           "note": f"仅 {s.n_obs} 页观测、覆盖 {s.coverage:.0%}"})
    if not calib.continuity.get("ok", True):
        for c in calib.continuity.get("checks", []):
            if not c["ok"]:
                issues.append({"shard": c["to"], "type": "跨片接续异常", "detail": c})

    # 已声明的断点不算问题，但要报出来
    declared = calib.gaps
    hard = [i for i in issues if i["type"] == "步长异常"]
    status = "fail" if hard else ("warn" if issues else "pass")
    return {"id": "G3", "name": "页码一致性", "status": status,
            "note": ("区间步长一致、跨片接续通过"
                     if not issues else f"{len(issues)} 处需注意（含 {len(declared)} 处已声明断点）"),
            "issues": issues, "declared_gaps": declared}


def gate_anchor_presence(out_root: Path, calib: Calibration) -> dict:
    """§2.3 硬条件：有页锚 或 locator_type=section_only，二者必居其一。"""
    md_files = sorted(p for p in Path(out_root).glob("*.md") if p.name != "00-目录.md")
    has_anchor = 0
    for p in md_files:
        if ANCHOR_LINE_RE.search(p.read_text(encoding="utf-8")):
            has_anchor += 1
    ok = has_anchor > 0 or calib.locator_type == "section_only"
    return {"id": "G4", "name": "入库硬条件（§2.3）", "status": "pass" if ok else "fail",
            "note": (f"{has_anchor}/{len(md_files)} 个文件含页锚"
                     + ("；locator_type=section_only 满足替代条件"
                        if calib.locator_type == "section_only" else "")),
            "files_with_anchor": has_anchor, "files": len(md_files)}


def run_all(wd: Path, out_root: Path, blocks: list[Block], calib: Calibration) -> dict:
    gates = [
        gate_anchor_coverage(blocks, calib),
        gate_lossless(wd, out_root),
        gate_page_consistency(blocks, calib),
        gate_anchor_presence(out_root, calib),
    ]
    verdict = "pass"
    if any(g["status"] == "fail" for g in gates):
        verdict = "fail"
    elif any(g["status"] == "warn" for g in gates):
        verdict = "warn"
    report = {"generated_at": now_iso(), "verdict": verdict, "gates": gates}
    write_json(Path(wd) / "verify.json", report)
    return report


def verify_report_md(rep: dict, source_id: str) -> str:
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "➖"}
    L = [f"# 校验报告：{source_id}", "",
         f"- 生成：{rep['generated_at']}",
         f"- 总判定：**{icon.get(rep['verdict'],'')} {rep['verdict']}**", "",
         "| 闸门 | 名称 | 结果 | 说明 |", "| --- | --- | --- | --- |"]
    for g in rep["gates"]:
        L.append(f"| {g['id']} | {g['name']} | {icon.get(g['status'],'')} {g['status']} "
                 f"| {g.get('note','')} |")
    L.append("")
    for g in rep["gates"]:
        if g["id"] == "G2" and g.get("examples"):
            L += ["## G2 未回查到的块", "",
                  "| 文件 | gid | 类型 | 摘录 |", "| --- | --- | --- | --- |"]
            for e in g["examples"]:
                L.append(f"| {e.get('path','')} | {e.get('gid','')} | {e.get('type','')} "
                         f"| {e.get('excerpt','')} |")
            L.append("")
        if g["id"] == "G3":
            if g.get("declared_gaps"):
                L += ["## G3 已声明断点（不填、不外推）", ""]
                for d in g["declared_gaps"]:
                    L.append(f"- {d['shard']} 物理页 {d['after_page_idx']}→{d['before_page_idx']}："
                             f"{d['n_unmapped_pages']} 页无页码观测")
                L.append("")
            if g.get("issues"):
                L += ["## G3 需注意", ""]
                for i in g["issues"]:
                    L.append(f"- {i}")
                L.append("")
    return "\n".join(L)
