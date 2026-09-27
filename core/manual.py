"""人工核定层：目录层级与页码的人工修正。

景晔的原话：「工作台的意义就在于你程序识别的目录，识别的页码有误了之后，
我们人类可以调整。」所以这一层是工作台的核心，不是附属功能。

**为什么单独存一个文件，而不是直接改 outline.json / page_calibration.json：**
  这两份产物每次重跑（pagecal → outline → export）都从 MinerU 原始块**重新推断**，
  任何直接写进去的人工修改都会被下一次重跑冲掉。所以人工结果存
  `manual_edits.json`，重跑时读进来**覆盖**自动值 —— 自动逻辑一行不用改，
  人的裁决永远赢。

**键（key）为什么不是 nid、也不只是 gid：**
  - nid（"1.2.3"）是重排的产物，改一次层级就全变，用 nid 存人工成果下次就全对不上；
  - gid 指向 MinerU 原始块，跨重跑稳定 —— 但**不是唯一的**：从印刷目录补出来的
    合成节点借用了「该页第一个真实块」的 gid，于是《认识论引论》的「第一章」(真实,
    gid=19) 与「一认识论的对象」(合成, gid=19) 共用一个数字键，人在界面上点一条、
    另一条跟着变，看着像层级算法把邻居改了，其实是键本身撞了。
  所以键改为 `Node.key`：
    真实块 = str(gid)（**与历史数据逐字一致，老 manual_edits 免迁移**）；
    合成节点 = "toc:<shard>:<gid>:<标题>"（唯一）；
    人工新增 = "add:<gid>"（把某个正文块升格成标题断点，见下）。

**人工新增标题（`added` 段）—— 「在文档里加断点」：**
  MinerU 按段落切块，标题被漏掉时它就以正文的身份待在块序列里。人在这里补一个
  断点：`added[str(gid)] = {title, level, offset}`。
    - 键用 gid（那个块），不用页码 —— 与 levels/deleted 同一把钥匙，跨重跑稳定；
    - `offset` 是断点在**块内**的字符位置（0 = 从块首断开）。有了它，断点可以落在
      文档的任意位置，而不只是段落边界；实测 MinerU 的块就是段落、标题几乎总独立
      成段，所以绝大多数情况 offset=0。
  为什么不改成「纯文档流 + 全局字符偏移」：那会丢掉跨重跑稳定性 —— OCR 重跑后
  多认一个字，全部断点集体错位，人工成果一次性报废。gid 即使正文变了也仍然有效。
  正文本身**一字不动**（原始块只读，人工层是叠加层）：新增的标题会在导出 md 里
  以 `# 标题` 出现，而它对应的那块原文照旧留在正文里。
"""
from __future__ import annotations

from pathlib import Path

from .util import now_iso, read_json, write_json

FILE = "manual_edits.json"

SYNTH_PREFIX = "toc:"
ADD_PREFIX = "add:"


def norm_key(v) -> str:
    """把主键规范成字符串。

    真实块的历史键是纯数字串；允许传 int 或 "019" 这类写法，一律归一成 "19"，
    否则同一块换个写法就查不到自己的人工成果。
    """
    if isinstance(v, bool):
        raise ValueError(f"主键不能是布尔值：{v!r}")
    if isinstance(v, int):
        return str(v)
    s = str(v or "").strip()
    if not s:
        raise ValueError("主键不能为空")
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return str(int(s))
    return s


def path_of(wd: Path) -> Path:
    return Path(wd) / FILE


def exists(wd: Path) -> bool:
    return path_of(wd).is_file()


def load(wd: Path) -> dict:
    d = read_json(path_of(wd), None) or {}
    d.setdefault("levels", {})
    d.setdefault("anchors", {})
    d.setdefault("deleted", {})
    d.setdefault("added", {})
    return d


def levels_for(wd: Path) -> dict:
    """给 build_tree 用的人工层级。"""
    return load(wd)["levels"]


def anchors_for(wd: Path) -> dict:
    """给 calibrate 用的人工锚点。"""
    return load(wd)["anchors"]


def deleted_for(wd: Path) -> dict:
    """给 build_tree 用的人工删除表（键是 Node.key 字符串）。"""
    return load(wd)["deleted"]


def added_for(wd: Path) -> dict:
    """给 build_tree 用的人工新增标题表（键是 gid 字符串）。"""
    return load(wd)["added"]


def save(wd: Path, data: dict) -> Path:
    data["updated_at"] = now_iso()
    return write_json(path_of(wd), data)


# ---------------------------------------------------------------- 目录层级

def set_level(wd: Path, key: int | str, level: int) -> dict:
    """把某个标题定为第 level 级。key 见模块开头（真实块＝数字串，合成节点＝toc:…）。"""
    lv = int(level)
    if not 1 <= lv <= 9:
        raise ValueError(f"层级只能是 1–9，收到 {level}")
    d = load(wd)
    d["levels"][norm_key(key)] = lv
    return save(wd, d)


def set_levels(wd: Path, pairs: dict) -> dict:
    """批量定级（界面一次提交多行改动时用，只写一次盘）。"""
    d = load(wd)
    for k, level in (pairs or {}).items():
        lv = int(level)
        if not 1 <= lv <= 9:
            raise ValueError(f"层级只能是 1–9，收到 {level}（key={k}）")
        d["levels"][norm_key(k)] = lv
    return save(wd, d)


def clear_level(wd: Path, key: int | str) -> dict:
    """撤销某一处人工定级，回到自动推断的层级。"""
    d = load(wd)
    d["levels"].pop(norm_key(key), None)
    return save(wd, d)


def clear_levels(wd: Path) -> dict:
    d = load(wd)
    d["levels"] = {}
    return save(wd, d)


# ---------------------------------------------------------------- 人工新增标题（断点）

def set_added(wd: Path, gid: int | str, title: str, level: int,
              offset: int = 0) -> dict:
    """在某个正文块处**新增**一个标题断点（MinerU 漏识别的那种）。

    gid    —— 断点落在哪个块上（键就用它，跨重跑稳定）
    title  —— 标题文本。默认取块首那一段，人可以在界面上改。
    level  —— 定为第几级
    offset —— 断点在**块内**的字符位置（0 = 从块首断开）。块内位移让断点能落在
              文档任意位置，而不只是段落边界；实测 MinerU 的块就是段落，
              标题几乎总独立成段，所以通常就是 0。

    **不动正文**：原始块一字不改，这块原文照旧留在正文里；新增的标题在导出时
    以 `# 标题` 出现（景晔确认的「两处并存」口径）。
    """
    g = norm_key(gid)
    if not g.isdigit():
        raise ValueError(f"新增断点必须落在真实块上，收到 gid={gid!r}")
    t = (title or "").strip()
    if not t:
        raise ValueError("新增标题必须有标题文本")
    lv = int(level)
    if not 1 <= lv <= 9:
        raise ValueError(f"层级只能是 1–9，收到 {level}")
    off = max(0, int(offset or 0))
    d = load(wd)
    d["added"][g] = {"title": t, "level": lv, "offset": off, "at": now_iso()}
    return save(wd, d)


def clear_added(wd: Path, gid: int | str) -> dict:
    """撤销一处人工新增标题。"""
    d = load(wd)
    d["added"].pop(norm_key(gid), None)
    return save(wd, d)


# ---------------------------------------------------------------- 条目删除

def set_deleted(wd: Path, key: int | str, info: dict | None = None) -> dict:
    """把某个标题条目从目录树里剔除（识别错了的：页眉、图注碎片、目录页残留…）。

    只删这一条，**不动它的下级** —— 重排时它的子条会向上提一级挂到它的父级，
    正文归属跟着走，一个字都不丢（见 outline.reflow 的过滤 + 层级栈）。

    info 里存一份被删时的快照（title/marker/shard/page_idx/chars/blocks）。
    必须存：条目一旦从 outline.json 的树里消失，界面就再也没处去拿它的标题，
    「已删除」列表和「恢复」按钮就成了空壳。
    **同时要存 gid**：已删行要插回目录列表的正确位置，而合成节点的 gid 是借的，
    插回位置得靠它自己那个数字，不能靠键去解析。
    """
    k = norm_key(key)
    d = load(wd)
    if str(k).startswith(ADD_PREFIX):
        # 删掉一条**人工新增**的标题 ＝ 撤销这次新增。它不是「MinerU 认错、被我剔掉」
        # 的条目，所以不写进 deleted —— 那是给识别错的条目留的档案，写进去会让
        # 「已删除」列表里混进一条**从来没被识别出来过**的标题。
        # 要重新加回来，回正文块视图再点一次「设为标题」。
        d["added"].pop(str(k)[len(ADD_PREFIX):], None)
        d["levels"].pop(k, None)
        return save(wd, d)
    rec = {"at": now_iso()}
    for f in ("title", "level", "marker", "shard", "page_idx", "chars",
              "own_chars", "blocks", "gid", "offset"):
        if isinstance(info, dict) and info.get(f) is not None:
            rec[f] = info[f]
    d["deleted"][k] = rec
    # 删掉的条目不该再留着人工定级 —— 留着的话恢复之后会突然跳回一个旧级数
    # （新增条目会退回到 added 里记的初始级数，那才是它该有的样子）
    d["levels"].pop(k, None)
    return save(wd, d)


def clear_deleted(wd: Path, key: int | str) -> dict:
    """恢复一个被删掉的条目。"""
    d = load(wd)
    d["deleted"].pop(norm_key(key), None)
    return save(wd, d)


def clear_deleted_all(wd: Path) -> dict:
    """恢复全部被删条目。"""
    d = load(wd)
    d["deleted"] = {}
    return save(wd, d)


# ---------------------------------------------------------------- 页码锚点

def set_anchor(wd: Path, shard: str, page_idx: int, printed: int) -> dict:
    """在某分片的某物理页立一个锚：这一页的印刷页码是 printed。

    从这里往后按同一 offset 逐页递加（见 pagecal.apply_anchors）。
    """
    d = load(wd)
    items = [a for a in d["anchors"].get(shard, [])
             if int(a.get("page_idx", -1)) != int(page_idx)]
    items.append({"page_idx": int(page_idx), "printed": int(printed)})
    items.sort(key=lambda a: a["page_idx"])
    d["anchors"][shard] = items
    return save(wd, d)


def clear_anchor(wd: Path, shard: str, page_idx: int) -> dict:
    d = load(wd)
    items = [a for a in d["anchors"].get(shard, [])
             if int(a.get("page_idx", -1)) != int(page_idx)]
    if items:
        d["anchors"][shard] = items
    else:
        d["anchors"].pop(shard, None)
    return save(wd, d)


def clear_anchors(wd: Path, shard: str | None = None) -> dict:
    """撤销某分片（或全部）的人工锚点，回到自动校准。"""
    d = load(wd)
    if shard:
        d["anchors"].pop(shard, None)
    else:
        d["anchors"] = {}
    return save(wd, d)


def clear_all(wd: Path) -> dict:
    """把人工成果整体清空（回到纯自动）。会先把当前内容备份成 .bak。"""
    p = path_of(wd)
    if p.is_file():
        p.with_suffix(".json.bak").write_text(p.read_text(encoding="utf-8"),
                                              encoding="utf-8")
    d = {"levels": {}, "anchors": {}, "deleted": {}, "added": {}}
    return save(wd, d)


def summary(wd: Path) -> dict:
    d = load(wd)
    return {
        "levels": len(d["levels"]),
        "anchors": {k: len(v) for k, v in d["anchors"].items()},
        "anchor_total": sum(len(v) for v in d["anchors"].values()),
        "deleted": len(d["deleted"]),
        "added": len(d["added"]),
        "updated_at": d.get("updated_at", ""),
    }
