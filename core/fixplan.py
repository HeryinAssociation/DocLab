"""修订单：Agent/人判定的结果**以数据的形式**落盘，再由工作台执行。

为什么不让 Agent 直接调函数改 `manual_edits.json`：
  1. **可复核**。修订单是一份 JSON，谁在什么时候因为哪条发现、依据什么证据、把哪
     个键改成了什么，全在里面。Agent 直接写文件的话，只剩一个"改完了"的结论，
     出了错无从回查 —— 而回查正是这个工作台存在的理由。
  2. **可拒绝**。凭据缺失的条目在执行层就被挡下来，不指望模型自觉。
  3. **可复现**。同一份修订单重跑一次结果相同；想撤销就把对应条目反过来写一遍。

硬闸门（执行层强制，不是提示）：
  - 每个条目**必须带非空 evidence**。没有证据的改动一律拒收 —— 「我觉得这里应该是
    L2」不构成理由，「书内目录第 3 页列出该标题、正文第 15 页出现原文」才构成。
  - `delete` 必须带 snapshot 且含 title：删掉的条目要从树里消失，界面再没处去拿它
    的标题；快照不存，「已删除」列表和「恢复」按钮就是空壳（见 core/manual）。
  - 主键一律用 `key`（＝ Node.key）：真实块是 str(gid)，合成节点是 `toc:…`，
    人工新增是 `add:…`。**不要用 nid**（重排即变），也不要用 gid 顶替合成节点的 key
    （合成节点的 gid 是借来的，会误伤同页那条真实块）。
  - 一次执行**只写 manual_edits.json**（叠加层）。绝不碰 outline.json /
    page_calibration.json —— 它们每次重跑都重推，写了也会被冲掉。

动作名与界面 `/api/edit` 完全一致，两处不要各造一套：
  level / levels / level-clear / add / add-clear / delete / delete-clear /
  anchor / anchor-clear
"""
from __future__ import annotations

from pathlib import Path

from . import manual
from .util import now_iso, read_json, write_json, write_text

FILE = "修订单.json"
LOG = "校核修订记录.md"

ACTIONS = {
    "level": ("key", "level"),
    "levels": ("pairs",),
    "level-clear": ("key",),
    "add": ("gid", "title", "level"),
    "add-clear": ("gid",),
    "delete": ("key", "snapshot"),
    "delete-clear": ("key",),
    "anchor": ("shard", "page_idx", "printed"),
    "anchor-clear": ("shard", "page_idx"),
}

# 改完后必须重算什么：锚点动过 → 页码要重推，目录也得跟着重推；
# 只动层级/条目 → 重推目录就够。
NEEDS_PAGECAL = {"anchor", "anchor-clear"}


class PlanError(ValueError):
    """修订单不合格 —— 整单拒收，不做「能改的先改」的半吊子执行。"""


def load_plan(path: Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise PlanError(f"修订单不存在：{p}")
    d = read_json(p, None)
    if not isinstance(d, dict):
        raise PlanError(f"修订单不是合法 JSON 对象：{p}")
    return d


def validate(plan: dict, wd: Path) -> tuple[list[dict], list[str]]:
    """校验修订单。返回 (可执行的条目, 警告)。

    整单拒收的原则：只要有**一条**不合格就全单不执行。理由是一份修订单通常是
    「校准偏移 + 若干定级」的成套改动，部分执行会留下一个自己都说不清的中间态 ——
    比不改更难查。
    """
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise PlanError("修订单里没有 items")
    oc = read_json(Path(wd) / "outline.json", {}) or {}
    known_keys = set()

    def walk(ns):
        for n in ns:
            known_keys.add(str(n.get("key") or n.get("gid_start")))
            walk(n.get("children") or [])

    walk(oc.get("tree") or [])
    known_gids = set()

    def walk_g(ns):
        for n in ns:
            known_gids.add(n.get("gid_start"))
            walk_g(n.get("children") or [])

    walk_g(oc.get("tree") or [])
    ed = manual.load(Path(wd))

    warns: list[str] = []
    out: list[dict] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise PlanError(f"items[{i}] 不是对象")
        act = it.get("action")
        if act not in ACTIONS:
            raise PlanError(f"items[{i}] 不认识的 action：{act!r}；"
                            f"可用：{sorted(ACTIONS)}")
        for f in ACTIONS[act]:
            if it.get(f) in (None, "", {}):
                raise PlanError(f"items[{i}]（{act}）缺少字段 {f}")
        ev = str(it.get("evidence") or "").strip()
        if not ev:
            raise PlanError(
                f"items[{i}]（{act}）没有 evidence —— 无证据的改动一律拒收。"
                f"evidence 要写清**凭什么**：引哪条校核发现、书内目录第几页、"
                f"正文块 gid 多少、原文是哪一句。")

        if act == "delete":
            snap = it.get("snapshot") or {}
            if not snap.get("title"):
                raise PlanError(f"items[{i}]（delete）的快照缺 title："
                                f"没有它，「已删除」列表与「恢复」都是空壳")
            if snap.get("gid") is None:
                warns.append(f"items[{i}]（delete）快照缺 gid，已删行可能插不回原位")
        if act in ("level", "levels", "delete", "delete-clear"):
            keys = ([it["key"]] if act != "levels" else list((it.get("pairs") or {})))
            for k in keys:
                kk = manual.norm_key(k)
                if kk not in known_keys and kk not in {str(x) for x in ed["deleted"]}:
                    warns.append(f"items[{i}] 的 key={kk} 不在当前目录树里"
                                 f"（重跑过 outline？键会随重算变化）")
        if act == "add":
            g = manual.norm_key(it["gid"])
            if not g.isdigit():
                raise PlanError(f"items[{i}]（add）的 gid 必须是真实块号，收到 {it['gid']!r}")
            if int(g) not in known_gids:
                warns.append(f"items[{i}]（add）的 gid={g} 不是任何节点的起始块 —— "
                             f"确认这是正文里的块，不是目录条目")
        out.append({**it, "_i": i})
    return out, warns


def apply(plan: dict, wd: Path, source_id: str) -> dict:
    """执行修订单。只写 manual_edits.json，然后记录一笔。"""
    wd = Path(wd)
    items, warns = validate(plan, wd)
    done, failed = [], []
    for it in items:
        act, i = it["action"], it["_i"]
        try:
            if act == "level":
                manual.set_level(wd, it["key"], it["level"])
            elif act == "levels":
                manual.set_levels(wd, it["pairs"])
            elif act == "level-clear":
                manual.clear_level(wd, it["key"])
            elif act == "add":
                manual.set_added(wd, it["gid"], it["title"], it["level"],
                                 it.get("offset", 0))
            elif act == "add-clear":
                manual.clear_added(wd, it["gid"])
            elif act == "delete":
                manual.set_deleted(wd, it["key"], it["snapshot"])
            elif act == "delete-clear":
                manual.clear_deleted(wd, it["key"])
            elif act == "anchor":
                manual.set_anchor(wd, it["shard"], it["page_idx"], it["printed"])
            elif act == "anchor-clear":
                manual.clear_anchor(wd, it["shard"], it["page_idx"])
            done.append(it)
        except Exception as e:                                    # noqa: BLE001
            failed.append({"index": i, "action": act, "error": f"{type(e).__name__}: {e}"})

    rec = {
        "source_id": source_id, "at": now_iso(),
        "plan_name": plan.get("name") or "",
        "plan_note": plan.get("note") or "",
        "based_on_audit_at": plan.get("based_on_audit_at") or "",
        "applied": len(done), "failed": failed, "warnings": warns,
        "items": [{k: v for k, v in it.items() if k != "_i"} for it in done],
        "needs_pagecal": any(it["action"] in NEEDS_PAGECAL for it in done),
        "manual_summary": manual.summary(wd),
    }
    stamp = now_iso().replace(":", "").replace("-", "")
    d = wd / "修订记录"
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / f"{stamp}.json", rec)
    # 追加式人读日志：**只追加**，不重写 —— 修订史是这条链上最有价值的东西，
    # 一次覆盖式写就把上一次的裁决理由抹了。
    log = wd / LOG
    head = "" if log.is_file() else f"# 校核修订记录：{source_id}\n"
    lines = [head, f"\n## {rec['at']}　{rec['plan_name'] or '（未命名）'}"
                   f"　应用 {rec['applied']} 条"
                   + (f"，失败 {len(failed)} 条" if failed else ""), ""]
    if rec["plan_note"]:
        lines.append(f"- 说明：{rec['plan_note']}")
    if rec["based_on_audit_at"]:
        lines.append(f"- 依据校核：{rec['based_on_audit_at']}")
    for it in rec["items"]:
        lines.append(f"- `{it['action']}` "
                     + "　".join(f"{k}={v}" for k, v in it.items()
                                 if k not in ("action", "evidence", "why", "snapshot"))
                     + f"\n    - 依据：{it.get('evidence', '')}")
    for w in warns:
        lines.append(f"- ⚠️ {w}")
    for f in failed:
        lines.append(f"- ❌ items[{f['index']}] {f['action']}：{f['error']}")
    lines.append("")
    with log.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    return rec


def template() -> dict:
    """修订单模板（Agent 照这个结构产出）。"""
    return {
        "source_id": "<source_id>",
        "name": "校准偏移 + 定级修正",
        "note": "为什么要改",
        "based_on_audit_at": "<目录校核.json 的 generated_at>",
        "items": [
            {"action": "anchor", "shard": "P1", "page_idx": 20, "printed": 8,
             "evidence": "书内目录第 3 页列出「技术与政策…」在第 3 页；"
                         "正文该标题落在 P1 物理页 15，故 offset 应为 −12；"
                         "现有锚点 page_idx=20→printed=10 与之矛盾，且使 P1/P2 接续重叠 2 页",
             "finding": "CALIB-024"},
            {"action": "level", "key": "82", "level": 2,
             "evidence": "该条与 4.1 同为章级（书内目录第 3 页平级列出）",
             "finding": "A-001"},
            {"action": "add", "gid": 301, "title": "第三章 ……", "level": 2, "offset": 0,
             "evidence": "印刷目录列出该章（第 57 页）；正文 gid=301 起首独立成段为该标题原文",
             "finding": "B-003"},
            {"action": "delete", "key": "1234",
             "snapshot": {"title": "……", "level": 3, "marker": "dun",
                          "shard": "P1", "page_idx": 44, "chars": 0,
                          "own_chars": 0, "blocks": 0, "gid": 1234},
             "evidence": "该条是页眉残片（同页已有真实标题）",
             "finding": "H-002"},
        ],
    }


def write_template(path: Path) -> Path:
    return write_text(Path(path), __import__("json").dumps(template(), ensure_ascii=False,
                                                          indent=2))
