"""目录树 JSON 的导出与导回 —— AI 语义核对的唯一通道。

思路（2026-09-22 景晔拍板，取代「audit 报 findings → 人复核」的旧思路）：

  1. `toc-export` 把当前目录结构导出成一份**给人读**的 JSON：平铺、按文档顺序，
     每条带层级 / 标题 / 页码 / 字数。超过核对深度（默认 L3）的条目不导出，
     也就是「L3 之后暂时忽略」由导出直接保证。
  2. AI 读这份 JSON，从两方面做语义判断：
     - **整体层次一致性**：同级标题是否真的在同一层（第一章和第二章必须同层；
       {一(1,2,3) 二(1,2,3) 三} 的骨架不乱、不跳层）。
     - **层内连续性**：同一父级下编号有没有断档（1、2、3、5 就是缺了 4）。
  3. 疑似缺标题时，用 `grep --json --q` 去正文块里取证，拿到 gid。
  4. AI **直接改这份 JSON**（改 level / 删错条 / 加缺条），`toc-import` 导回。
     本模块把 diff 翻译成人工核定层落盘 —— AI 永远不直接写 manual_edits.json。

翻译规则（AI 只需要会改 JSON，不需要学修订单语法）：

  - 条目消失          → delete（快照由本模块从当前树补齐，AI 不用填）
  - level 变了        → level
  - 新增条目          → add（必须带 gid + title + level；gid 必须是真实正文块）
  - add: 条目改标题/级 → 更新人工新增记录（set_added 覆盖）
  - 其余键改标题      → 拒绝（真实块标题透传原文，要换标题＝删旧条＋按原文新增）

任何一条不合法，整单拒收 —— 不做「能改的先改」的半吊子执行。
"""
from __future__ import annotations

from pathlib import Path

from . import manual
from .util import now_iso, read_json, write_json

DEFAULT_DEPTH = 3
FILE = "目录树.json"
MAX_LEVEL = 9


# ---------------------------------------------------------------- 导出

def _flat_tree(tree: list | None):
    """文档顺序平铺（先序遍历）。"""
    for n in tree or []:
        yield n
        yield from _flat_tree(n.get("children") or [])


def export_toc(wd: Path, source_id: str, depth: int = DEFAULT_DEPTH) -> dict:
    """当前目录树 → 平铺 JSON（level > depth 的条目不导出＝核对时忽略）。"""
    oc = read_json(Path(wd) / "outline.json", None)
    if not oc or not oc.get("tree"):
        raise ValueError("outline.json 不存在或没有树 —— 先跑 outline")
    pj = read_json(Path(wd) / "project.json", {}) or {}
    calib = None
    if (Path(wd) / "page_calibration.json").is_file():
        try:
            from .pagecal import load_calibration
            calib = load_calibration(Path(wd))
        except Exception:                                          # noqa: BLE001
            calib = None

    nodes, all_count = [], 0
    for n in _flat_tree(oc.get("tree")):
        all_count += 1
        try:
            lvl = int(n.get("level") or 0)
        except (TypeError, ValueError):
            lvl = 0
        if lvl > depth:
            continue
        page = ""
        if calib and n.get("shard"):
            try:
                loc = calib.locator(n["shard"], int(n.get("page_idx") or 0))
                page = "" if loc is None else str(loc)
            except Exception:                                      # noqa: BLE001
                page = ""
        kids = n.get("children") or []
        n_chars = int(n.get("n_chars") or 0)
        nodes.append({
            "seq": len(nodes) + 1,
            "key": str(n.get("key") or n.get("gid_start")),
            "nid": str(n.get("nid") or ""),
            "level": lvl,
            "title": str(n.get("title") or ""),
            "gid": n.get("gid_start"),
            "page": page,
            "shard": str(n.get("shard") or ""),
            "page_idx": int(n.get("page_idx") or 0),
            "marker": str(n.get("marker") or ""),
            "offset": int(n.get("offset") or 0),
            "blocks": int(n.get("n_blocks") or 0),
            "chars": n_chars,
            "own_chars": n_chars - sum(int(c.get("n_chars") or 0) for c in kids),
            "flags": list(n.get("flags") or []),
        })
    return {
        "source_id": source_id,
        "doc_title": pj.get("doc_title", ""),
        "depth_limit": int(depth),
        "generated_at": now_iso(),
        "node_total_all": all_count,
        "node_count": len(nodes),
        "nodes": nodes,
    }


# ---------------------------------------------------------------- diff

def _real_block_gids(wd: Path) -> set[int]:
    """全部正文块号（add 的落点必须是其中之一，否则就是瞎编）。"""
    try:
        from .project import iter_blocks, load_shards
        return {b.gid for b in iter_blocks(load_shards(Path(wd)))}
    except Exception:                                              # noqa: BLE001
        return set()


def _evidence(new: dict, node: dict | None, what: str) -> str:
    """条目级 reason 优先；否则用整单 note；再不行给一句可追查的默认描述。

    fixplan 的硬闸门要求每条改动带非空 evidence —— 这里保证导回永远过闸，
    同时把「为什么改」尽量留给 AI 写清楚（reasons[key] 支持已删除的条目）。
    """
    reasons = new.get("reasons") if isinstance(new.get("reasons"), dict) else {}
    if isinstance(node, dict) and str(node.get("reason") or "").strip():
        return str(node["reason"]).strip()
    if node is not None and str(node) in reasons and str(reasons[str(node)]).strip():
        return str(reasons[str(node)]).strip()
    note = str(new.get("note") or "").strip()
    return f"toc-import：AI 语义核对，{what}" + (f"（{note}）" if note else "")


def diff_toc(wd: Path, new: dict) -> dict:
    """把 AI 改后的 JSON 与当前目录树对比，翻译成修订单条目。

    返回 {"changes": [...], "errors": [...], "warnings": [...], "items": [...],
          "old": <当前导出>}。errors 非空 → 整单拒收（调用方不落盘）。
    """
    try:
        depth = int(new.get("depth_limit") or DEFAULT_DEPTH)
    except (TypeError, ValueError):
        depth = DEFAULT_DEPTH
    old = export_toc(Path(wd), str(new.get("source_id") or ""), depth)
    old_by = {n["key"]: n for n in old["nodes"]}

    raw = new.get("nodes")
    if not isinstance(raw, list):
        raise ValueError("JSON 里没有 nodes 列表 —— 用 toc-export 导出的那份改，别换骨架")

    errors: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []
    items: list[dict] = []

    new_by: dict[str, dict] = {}
    keyless: list[dict] = []                    # 新增条目（key 留空 → 在正文块上立断点）
    for i, n in enumerate(raw):
        if not isinstance(n, dict):
            errors.append(f"nodes[{i}] 不是对象")
            continue
        key = str(n.get("key") or "").strip()
        if not key:
            keyless.append(n)
            continue
        try:
            kk = manual.norm_key(key)
        except ValueError as e:
            errors.append(f"nodes[{i}]：{e}")
            continue
        if kk in new_by:
            errors.append(f"key={kk} 出现了两次 —— 每条只留一份")
        new_by[kk] = n

    real_gids = _real_block_gids(Path(wd))

    # 1) 旧有新无 → delete（前置兜底节点除外，它承接第一个标题前的全部块）
    deleted_keys: list[str] = []
    for o in old["nodes"]:
        if o["key"] in new_by:
            continue
        if o["marker"] == "front":
            errors.append(f"「{o['title']}」是前置兜底节点，不可删 —— 把它加回去再导")
            continue
        snap = {"title": o["title"], "level": o["level"], "marker": o["marker"],
                "shard": o["shard"], "page_idx": o["page_idx"],
                "chars": o["chars"], "own_chars": o["own_chars"],
                "blocks": o["blocks"], "gid": o["gid"]}
        items.append({"action": "delete", "key": o["key"], "snapshot": snap,
                      "evidence": _evidence(new, o["key"], f"剔除「{o['title']}」")})
        changes.append(f"删　key={o['key']}　「{o['title']}」")
        deleted_keys.append(o["key"])
    if len(deleted_keys) > max(3, len(old["nodes"]) // 3):
        warnings.append(f"一次删了 {len(deleted_keys)} 条（超过三之一）—— "
                        f"确认不是把 JSON 截断了：导回前 depth_limit 要与导出时一致")

    # 2) 两边都有 → level / 标题变化
    for o in old["nodes"]:
        n = new_by.get(o["key"])
        if n is None:
            continue
        try:
            lvl = int(n.get("level")) if n.get("level") is not None else o["level"]
        except (TypeError, ValueError):
            errors.append(f"「{o['title']}」的 level={n.get('level')!r} 不是整数")
            continue
        # 越界只拦**改出去的**：前置兜底节点 level=0 原样保留不算越界
        if lvl != o["level"] and not (1 <= lvl <= MAX_LEVEL):
            errors.append(f"「{o['title']}」的 level={lvl} 越界（1–{MAX_LEVEL}）")
            continue
        title = str(n.get("title") or "").strip()
        changed_title = bool(title) and title != o["title"]
        changed_level = lvl != o["level"]
        if not (changed_title or changed_level):
            continue

        if o["key"].startswith(manual.ADD_PREFIX):
            # 人工新增过的条目：标题/层级直接覆盖 added 记录
            items.append({"action": "add", "gid": o["gid"],
                          "title": title or o["title"], "level": lvl,
                          "offset": o["offset"],
                          "evidence": _evidence(new, n,
                                                f"改人工新增「{title or o['title']}」")})
            changes.append(f"改　key={o['key']}　「{o['title']}」→「{title or o['title']}」"
                           + (f"　L{o['level']}→L{lvl}" if changed_level else ""))
        else:
            if changed_title:
                errors.append(
                    f"「{o['title']}」(key={o['key']}) 的标题不许直接改 —— 真实块标题"
                    f"要逐字透传原文。要换标题＝删掉这条，grep 拿到正文块 gid 后按原文新增")
                continue
            if changed_level:
                items.append({"action": "level", "key": o["key"], "level": lvl,
                              "evidence": _evidence(new, n,
                                                    f"「{o['title']}」定级 L{o['level']}→L{lvl}")})
                changes.append(f"级　key={o['key']}　「{o['title']}」　L{o['level']}→L{lvl}")

    # 3) 新有旧无 → add（必须在真实正文块上立断点）
    for n in keyless + [new_by[kk] for kk in new_by if kk not in old_by]:
        title = str(n.get("title") or "").strip()
        gid = n.get("gid")
        lvl = n.get("level")
        if gid is None or not title or lvl is None:
            errors.append(f"新增条目「{title or kk}」缺 gid / title / level —— "
                          f"新标题必须在正文块上立断点，先 grep --json --q 拿真实块号")
            continue
        try:
            gid, lvl = int(gid), int(lvl)
        except (TypeError, ValueError):
            errors.append(f"新增条目「{title}」的 gid/level 不是整数")
            continue
        if not (1 <= lvl <= MAX_LEVEL):
            errors.append(f"新增「{title}」的 level={lvl} 越界（1–{MAX_LEVEL}）")
            continue
        if real_gids and gid not in real_gids:
            errors.append(f"新增「{title}」的 gid={gid} 不是这本书的正文块号 —— "
                          f"用 grep --json --q 按标题原文搜，拿真实 gid")
            continue
        items.append({"action": "add", "gid": gid, "title": title, "level": lvl,
                      "offset": int(n.get("offset") or 0),
                      "evidence": _evidence(new, n, f"在正文块 gid={gid} 立标题「{title}」")})
        changes.append(f"增　gid={gid}　「{title}」　L{lvl}")

    return {"changes": changes, "errors": errors, "warnings": warnings,
            "items": items, "old": old}


# ---------------------------------------------------------------- 导回

def import_toc(wd: Path, source_id: str, new: dict, do_apply: bool = False) -> dict:
    """把 AI 改后的目录 JSON 导回。默认演练；do_apply=True 才落盘。

    落盘路径与修订单完全一致：fixplan.apply → manual_edits.json + 修订记录。
    导回不改页码锚点（那是校准层的事），所以只需重算 outline。
    """
    d = diff_toc(Path(wd), new)
    result = {"source_id": source_id,
              "ok": not d["errors"],
              "changes": d["changes"], "errors": d["errors"],
              "warnings": d["warnings"], "item_count": len(d["items"])}
    if d["errors"]:
        return result
    if not d["items"]:
        result["applied"] = 0
        return result
    plan = {
        "name": "目录 JSON 导回",
        "note": str(new.get("note") or ""),
        "based_on_audit_at": d["old"].get("generated_at", ""),
        "items": d["items"],
    }
    if not do_apply:
        return result
    from .fixplan import apply as apply_plan
    rec = apply_plan(plan, Path(wd), source_id)
    result["applied"] = rec["applied"]
    result["failed"] = rec["failed"]
    result["manual_summary"] = rec.get("manual_summary")
    return result
