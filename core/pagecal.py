"""页码校准：把 MinerU 的物理页序（page_idx）映射到纸书页码。

依据两类版面块（MinerU 已单独分类）：
  - type=page_number   页脚/页边的印刷页码 —— 主证据
  - type=header        带编号的页眉（如「序言 1」）—— 佐证

核心原则（对齐 APR-20260920-001 §2.1）：
  **禁止猜偏移。** 只在「印刷页码与物理页号差值恒定」的连续区间内建立映射；
  区间断裂处记为 gap，不填、不外推；整书无法建立可信映射时降级 section_only。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

from .project import Block, parse_page_number
from .util import now_iso, write_json, roman
import copy
import re

# 判定阈值（改这里就是改口径，全部写进产物的 thresholds 字段）
THRESHOLDS = {
    "seg_high_obs": 3,      # 区间内观测页数 ≥3 → high
    "seg_medium_obs": 2,    # ==2 → medium（1 → low）
    "seg_high_coverage": 0.75,   # 区间内观测覆盖率 ≥0.75 → high
    "seg_min_coverage": 0.50,    # <0.50 → low
    "body_min_obs": 5,      # 正文区间观测 <5 页 → 整书降级
    "body_min_coverage": 0.60,
}

# 带编号的页眉：序言 1 / 附录 3 / 前言 2 …
NUMBERED_HEADER_RE = re.compile(
    r"^(序言|前言|序|目录|附录|参考文献|索引|后记|结语|绪论)\s*[·:：]?\s*(\d{1,4})$"
)


@dataclass
class Obs:
    shard: str
    page_idx: int
    printed: int
    source: str            # page_number | header


@dataclass
class Segment:
    shard: str
    kind: str                                # body | front
    page_idx_start: int
    page_idx_end: int
    printed_start: int
    printed_end: int
    offset: int                              # printed - page_idx，区间内恒定
    n_obs: int
    span: int
    coverage: float
    confidence: str                          # high | medium | low
    basis_pages: list[int] = field(default_factory=list)
    derived: bool = False                    # True 表示区间含按同一线性映射补出的页

    def to_dict(self) -> dict:
        return asdict(self)

    def contains(self, page_idx: int) -> bool:
        return self.page_idx_start <= page_idx <= self.page_idx_end

    def contains_printed(self, printed: int) -> bool:
        return self.printed_start <= printed <= self.printed_end


@dataclass
class Calibration:
    source_id: str
    shards: list[str]
    shard_pages: dict
    segments: list[Segment]
    gaps: list[dict]
    continuity: dict
    obs: list[Obs]
    header_conflicts: list[dict]
    locator_type: str
    verdict: str
    reason: str
    thresholds: dict = field(default_factory=lambda: dict(THRESHOLDS))
    generated_at: str = field(default_factory=now_iso)

    # ---------------------------------------------------------- 映射

    def locator(self, shard: str, page_idx: int) -> str | None:
        """物理页 → 定位符。

        返回 '3'（纸书页码） / 'front-3'（前置页自有编号） / None（区间外，不可定）。
        """
        for seg in self.segments:
            if seg.shard != shard or not seg.contains(page_idx):
                continue
            printed = page_idx + seg.offset
            return f"front-{printed}" if seg.kind == "front" else str(printed)
        return None

    def physical_of(self, printed: int, prefer_body: bool = True) -> tuple[str, int] | None:
        """纸书页码 → (分片, 片内物理页)。

        同一印刷页号可能同时落在前置区间与正文区间（本书序言用 1–7、正文也从 3 起），
        默认优先取正文区间。
        """
        cands = [s for s in self.segments if s.contains_printed(printed)]
        if not cands:
            return None
        if prefer_body:
            cands.sort(key=lambda s: (s.kind != "body", -s.n_obs))
        else:
            cands.sort(key=lambda s: -s.n_obs)
        s = cands[0]
        return s.shard, printed - s.offset

    def anchor(self, shard: str, page_idx: int, global_page: int | None = None) -> str:
        """页锚行（HTML 注释，与 APR-20260920-001 §2.1 唯一样式一致）。"""
        loc = self.locator(shard, page_idx)
        if loc:
            return f"<!-- p={loc} -->"
        if global_page is not None:
            # 无法映射到纸书页码时显式声明是物理页序，绝不冒充纸书页码
            return f"<!-- p=pdf-{global_page} unmapped -->"
        return "<!-- p=unmapped -->"

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "source_id": self.source_id,
            "locator_type": self.locator_type,
            "verdict": self.verdict,
            "reason": self.reason,
            "shards": self.shards,
            "shard_pages": self.shard_pages,
            "thresholds": self.thresholds,
            "segments": [s.to_dict() for s in self.segments],
            "gaps": self.gaps,
            "continuity": self.continuity,
            "header_conflicts": self.header_conflicts,
            "observation_count": len(self.obs),
            "observations": [asdict(o) for o in self.obs],
        }

    @staticmethod
    def from_dict(d: dict) -> "Calibration":
        segs = [Segment(**{k: v for k, v in s.items() if k in Segment.__dataclass_fields__})
                for s in d.get("segments", [])]
        return Calibration(
            source_id=d.get("source_id", ""),
            shards=d.get("shards", []),
            shard_pages=d.get("shard_pages", {}),
            segments=segs,
            gaps=d.get("gaps", []),
            continuity=d.get("continuity", {}),
            obs=[],
            header_conflicts=d.get("header_conflicts", []),
            locator_type=d.get("locator_type", "unknown"),
            verdict=d.get("verdict", "unknown"),
            reason=d.get("reason", ""),
            thresholds=d.get("thresholds", dict(THRESHOLDS)),
            generated_at=d.get("generated_at", ""),
        )


# ---------------------------------------------------------------- 观测收集

def collect_observations(blocks: list[Block]) -> tuple[list[Obs], list[dict]]:
    """从块序列里取页码观测与页眉冲突。"""
    primary: dict[tuple[str, int], int] = {}
    secondary: dict[tuple[str, int], int] = {}
    for b in blocks:
        if b.type == "page_number":
            n = parse_page_number(b.text)
            if n is not None:
                primary.setdefault((b.shard, b.page_idx), n)
        elif b.type == "header":
            m = NUMBERED_HEADER_RE.match(b.text.strip())
            if m:
                secondary.setdefault((b.shard, b.page_idx), int(m.group(2)))

    obs = [Obs(s, p, n, "page_number") for (s, p), n in primary.items()]
    obs += [Obs(s, p, n, "header") for (s, p), n in secondary.items()]
    obs.sort(key=lambda o: (o.shard, o.page_idx))

    conflicts = []
    for (s, p), n in secondary.items():
        pn = primary.get((s, p))
        if pn is not None and pn != n:
            conflicts.append({"shard": s, "page_idx": p, "page_number": pn, "header": n})
    return obs, conflicts


# ---------------------------------------------------------------- 分段

def _segments_for_shard(obs: list[Obs], shard: str) -> list[Segment]:
    """把同一分片的观测切成「offset 恒定」的连续区间。"""
    items = sorted([o for o in obs if o.shard == shard], key=lambda o: o.page_idx)
    if not items:
        return []

    segs: list[Segment] = []
    cur = [items[0]]
    for o in items[1:]:
        prev = cur[-1]
        same_offset = (o.printed - o.page_idx) == (prev.printed - prev.page_idx)
        forward = o.printed > prev.printed and o.page_idx > prev.page_idx
        if same_offset and forward:
            cur.append(o)
        else:
            segs.append(_finalize(cur, shard))
            cur = [o]
    segs.append(_finalize(cur, shard))
    return segs


def _finalize(g: list[Obs], shard: str) -> Segment:
    first, last = g[0], g[-1]
    span = last.page_idx - first.page_idx + 1
    cov = len(g) / span if span else 0.0
    if len(g) >= THRESHOLDS["seg_high_obs"] and cov >= THRESHOLDS["seg_high_coverage"]:
        conf = "high"
    elif len(g) >= THRESHOLDS["seg_medium_obs"] and cov >= THRESHOLDS["seg_min_coverage"]:
        conf = "medium"
    else:
        conf = "low"
    return Segment(
        shard=shard, kind="body",
        page_idx_start=first.page_idx, page_idx_end=last.page_idx,
        printed_start=first.printed, printed_end=last.printed,
        offset=first.printed - first.page_idx,
        n_obs=len(g), span=span, coverage=round(cov, 3), confidence=conf,
        basis_pages=[o.page_idx for o in g if o.source == "page_number"],
        derived=span > len(g),
    )


def _extend_with_headers(segs: list[Segment], obs: list[Obs], shard: str) -> list[Segment]:
    """用带编号页眉把区间向外延伸（只延不缩，且必须与既有 offset 一致）。"""
    hdr = sorted([o for o in obs if o.shard == shard and o.source == "header"],
                 key=lambda o: o.page_idx)
    for o in hdr:
        for seg in segs:
            if (o.printed - o.page_idx) != seg.offset:
                continue
            if seg.page_idx_start - 12 <= o.page_idx < seg.page_idx_start:
                seg.page_idx_start, seg.printed_start = o.page_idx, o.printed
                seg.span = seg.page_idx_end - seg.page_idx_start + 1
                seg.coverage = round(seg.n_obs / seg.span, 3)
            elif seg.page_idx_end < o.page_idx <= seg.page_idx_end + 12:
                seg.page_idx_end, seg.printed_end = o.page_idx, o.printed
                seg.span = seg.page_idx_end - seg.page_idx_start + 1
                seg.coverage = round(seg.n_obs / seg.span, 3)
    return segs


# ---------------------------------------------------------------- 主流程

def _absorb_outlier_pages(all_segs: list["Segment"], shard_order: list[str]
                          ) -> tuple[list["Segment"], list[dict]]:
    """把「单页、offset 与两侧主导序列都不一致」的观测段并回主导序列。

    那一页上的页码块只读到一个数字，
    且与前后整段序列只差一两位（314→**815**→316、238→**288**）。原样留着，它就
    独占一个 1 页的区间，`locator()` 会把该页读成 815 —— 导出的 `<!-- p=815 -->`
    是个真书里根本不存在的页码，比「少一个锚」更坏。

    判定是**确定性的、不外推**：必须是单页段（span ≤ 2）、非人工锚点、offset 与
    本片主导序列不同，**且前后各有至少一段实测的主导 offset 段**（两侧都有据可依）。
    满足就把它并进前一段，并留一条记录。audit 的 `page_obs_outlier` 用的是同一判据，
    两边口径一致：校核说它是误读，校准就把它按误读处理。
    """
    drop: set[int] = set()
    recs: list[dict] = []
    for shard in shard_order:
        ss = sorted([s for s in all_segs if s.shard == shard and id(s) not in drop],
                    key=lambda s: s.page_idx_start)
        if len(ss) < 3:
            continue
        dom = max(ss, key=lambda s: (s.n_obs, -s.span)).offset
        for i, s in enumerate(ss):
            if s.span > 2 or s.confidence == "manual" or s.offset == dom:
                continue
            prev = next((x for x in reversed(ss[:i]) if x.offset == dom), None)
            nxt = next((x for x in ss[i + 1:] if x.offset == dom), None)
            if prev is None or nxt is None:
                continue
            drop.add(id(s))
            prev.page_idx_end = max(prev.page_idx_end, s.page_idx_end)
            prev.printed_end = prev.page_idx_end + prev.offset
            prev.span = prev.page_idx_end - prev.page_idx_start + 1
            prev.coverage = round(prev.n_obs / prev.span, 3) if prev.span else 0.0
            recs.append({"shard": shard, "page_idx": s.page_idx_start,
                         "observed": s.printed_start, "expected": s.page_idx_start + dom})
    kept = [s for s in all_segs if id(s) not in drop]
    return kept, recs


# ---------------------------------------------------------------- 人工锚点

def _kind_at(orig: list["Segment"], shard: str, page_idx: int,
             offset: int | None = None) -> str:
    """人工段继承原自动段的前置/正文属性（前置页的 label 是 front-N，语义不同）。

    锚点常常落在**观测缺口**里（那一页前后都没有页码观测，自动段只好断开），这时
    「看左边那一段」会误判：魂系的锚点 P1 16→1 左邻是 10–12 的前置页，于是它后头
    183 页正文全被标成 front-1…front-183（12 条 top_node_in_front_zone 由此而来）。
    判据改成**谁的 offset 与锚点一致就随谁** —— 同一套编号体系才是同类，这比左右
    方位硬；没有 offset 可依时退回原行为（跟左边）。
    """
    near = [s for s in orig if s.shard == shard]
    for s in near:
        if s.contains(page_idx):
            return s.kind
    if offset is not None:
        tie = [s for s in near if s.offset == offset]
        if tie:
            pick = min(tie, key=lambda s: min(abs(s.page_idx_start - page_idx),
                                              abs(s.page_idx_end - page_idx)))
            return pick.kind
    before = [s for s in near if s.page_idx_end < page_idx]
    if before:
        return max(before, key=lambda s: s.page_idx_end).kind
    return "body"


def apply_anchors(calib: "Calibration", anchors: dict) -> "Calibration":
    """用人工锚点覆盖自动分段。

    对应界面上那张「PDF 第几页 ↔ 印刷页码」的对照表：人在某一页写下一个对应关系，
    就等于在该页立了一个锚 —— **从它起按恒定 offset 逐页递加**，加到下一个锚点之前；
    没有下一个锚点，就一路加到分片末尾。

    关键：锚点只影响**它自己及其之后**。第一个锚点之前的自动分段原样保留
    （跨在锚点上的那一段被截断），否则人改一页就把前面上百页的页码全废了
    —— 那不是「校正」，那是「重来」。

    这也没有破坏「禁止猜偏移」：锚点是人明确指定的，不是程序外推的。
    两个锚点各自算出的 offset 不同，中间自然断成两段，不替人做插值。
    """
    if not anchors:
        return calib

    orig = list(calib.segments)
    segs = list(orig)
    used = 0

    for shard, items in anchors.items():
        items = [a for a in (items or []) if a.get("page_idx") is not None
                 and a.get("printed") is not None]
        if not items:
            continue
        items.sort(key=lambda a: int(a["page_idx"]))
        total = int(calib.shard_pages.get(shard) or 0)
        first = max(0, int(items[0]["page_idx"]))

        segs = [s for s in segs if s.shard != shard]
        # 锚点之前的自动分段保留下来（跨越锚点的那段截到锚点前一页）
        for s in orig:
            if s.shard != shard or s.page_idx_start >= first:
                continue
            if s.page_idx_end < first:
                segs.append(s)
            else:
                cut = copy.copy(s)
                cut.page_idx_end = first - 1
                cut.printed_end = cut.page_idx_end + s.offset
                cut.span = cut.page_idx_end - cut.page_idx_start + 1
                cut.coverage = round(cut.n_obs / cut.span, 3) if cut.span else 0.0
                segs.append(cut)

        for i, a in enumerate(items):
            start = max(0, int(a["page_idx"]))
            nxt = items[i + 1] if i + 1 < len(items) else None
            end = (max(start, int(nxt["page_idx"]) - 1) if nxt
                   else max(start, total - 1))
            printed = int(a["printed"])
            offset = printed - start
            segs.append(Segment(
                shard=shard, kind=_kind_at(orig, shard, start, offset),
                page_idx_start=start, page_idx_end=end,
                printed_start=start + offset, printed_end=end + offset,
                offset=offset, n_obs=0, span=end - start + 1, coverage=0.0,
                confidence="manual", derived=True,
            ))
            used += 1

    if not used:
        return calib

    calib.segments = sorted(segs, key=lambda s: (s.shard, s.page_idx_start))
    # 人工锚点建立了映射，定位能力就不该再是 section_only ——
    # 否则 verify 的 §2.3 会因为 locator_type 把成果判掉。
    calib.locator_type = "paginated"
    calib.verdict = "manual"
    calib.reason = (f"含 {used} 处人工锚点，按人给定的对应关系逐页递加；"
                    f"锚点之间 offset 变化处自然断开，不插值。原自动判定：{calib.reason}")
    return calib


def calibrate(source_id: str, blocks: list[Block], shards_meta: list[dict],
              anchors: dict | None = None) -> Calibration:
    shard_order = [s["tag"] for s in shards_meta]
    shard_pages = {s["tag"]: int(s.get("n_pages") or 0) for s in shards_meta}

    obs, conflicts = collect_observations(blocks)

    all_segs: list[Segment] = []
    for tag in shard_order:
        segs = _segments_for_shard(obs, tag)
        segs = _extend_with_headers(segs, obs, tag)
        all_segs.extend(segs)

    # 单页 OCR 误读（314→815→316）会独占一个 1 页区间，把页锚写错。先并回主导序列。
    all_segs, absorbed = _absorb_outlier_pages(all_segs, shard_order)

    # 分片内：跨度最大的区间定为正文区，其前的标 front
    for tag in shard_order:
        mine = [s for s in all_segs if s.shard == tag]
        if not mine:
            continue
        body = max(mine, key=lambda s: (s.span, s.n_obs))
        for s in mine:
            s.kind = "body" if s is body else ("front" if s.page_idx_end < body.page_idx_start
                                               else "body")

    # 跨片连续性：前一正文段末 + 1 == 后一正文段首
    body_segs = [s for tag in shard_order for s in all_segs if s.shard == tag and s.kind == "body"]
    checks = []
    for a, b in zip(body_segs, body_segs[1:]):
        expect = a.printed_end + 1
        checks.append({
            "from": f"{a.shard}:{a.page_idx_end}",
            "to": f"{b.shard}:{b.page_idx_start}",
            "prev_printed": a.printed_end,
            "next_printed": b.printed_start,
            "expected": expect,
            "ok": b.printed_start >= expect,
            "note": "接续" if b.printed_start == expect else (
                "断开（中间可能有插图页/空白页未编号）" if b.printed_start > expect else "倒退"),
        })
    continuity = {
        "ok": all(c["ok"] for c in checks) if checks else True,
        "checks": checks,
    }

    # 断点：同一分片内相邻区间之间未覆盖的物理页
    gaps = []
    for tag in shard_order:
        mine = sorted([s for s in all_segs if s.shard == tag], key=lambda s: s.page_idx_start)
        for a, b in zip(mine, mine[1:]):
            if b.page_idx_start - a.page_idx_end > 1:
                gaps.append({
                    "shard": tag,
                    "after_page_idx": a.page_idx_end,
                    "before_page_idx": b.page_idx_start,
                    "n_unmapped_pages": b.page_idx_start - a.page_idx_end - 1,
                    "missing_printed": [a.printed_end + i + 1
                                        for i in range(b.page_idx_start - a.page_idx_end - 1)],
                    "note": "该区间未观测到页码，页锚按物理页序标注，不外推",
                })

    # 判定
    tot_pages = sum(shard_pages.values())
    tot_obs = len([o for o in obs if o.source == "page_number"])
    coverage = round(tot_obs / tot_pages, 3) if tot_pages else 0.0

    body_obs = sum(s.n_obs for s in body_segs)
    body_span = sum(s.span for s in body_segs)
    body_cov = round(body_obs / body_span, 3) if body_span else 0.0

    if not body_segs:
        verdict, locator, reason = "degraded", "section_only", "未观测到任何印刷页码，无法建立映射"
    elif body_obs < THRESHOLDS["body_min_obs"] or body_cov < THRESHOLDS["body_min_coverage"]:
        verdict, locator = "degraded", "section_only"
        reason = (f"正文区观测不足（观测 {body_obs} 页 / 覆盖 {body_cov}），"
                  f"低于阈值 {THRESHOLDS['body_min_obs']} 页 / {THRESHOLDS['body_min_coverage']}，"
                  f"按规范降级且不猜偏移")
    elif not continuity["ok"]:
        verdict, locator = "needs_review", "paginated"
        reason = "跨分片页码接续存在断点，映射可用但需人工确认分片顺序与封面插页"
    else:
        verdict, locator = "calibrated", "paginated"
        reason = (f"正文区 {len(body_segs)} 个区间、共 {body_obs} 页观测、覆盖 {body_cov}，"
                  f"分片接续通过")

    if absorbed:
        reason += (f"；另有 {len(absorbed)} 页单页页码与整段序列冲突"
                   f"（如物理 {absorbed[0]['page_idx']} 观测 {absorbed[0]['observed']}、"
                   f"按两侧序列应为 {absorbed[0]['expected']}），判为 OCR 误读，已并回主导序列")

    calib = Calibration(
        source_id=source_id, shards=shard_order, shard_pages=shard_pages,
        segments=all_segs, gaps=gaps, continuity=continuity, obs=obs,
        header_conflicts=conflicts, locator_type=locator, verdict=verdict, reason=reason,
    )
    # 收尾处套人工锚点：CLI 与界面都走这一个入口，不会有一边忘了应用。
    return apply_anchors(calib, anchors or {})


def front_label(seg: Segment) -> str:
    """前置页区间的人类可读标签。"""
    return f"front-{roman(seg.printed_start)}–{roman(seg.printed_end)}"


def write_calibration(wd: Path, calib: Calibration) -> Path:
    return write_json(Path(wd) / "page_calibration.json", calib.to_dict())


def load_calibration(wd: Path) -> Calibration:
    from .util import read_json
    d = read_json(Path(wd) / "page_calibration.json", None)
    if not d:
        raise FileNotFoundError(f"{wd} 下没有 page_calibration.json —— 先跑 pagecal")
    return Calibration.from_dict(d)


def calibration_report(calib: Calibration) -> str:
    """人读报告：区间表 + 断点 + 接续。"""
    L = ["# 页码校准报告", "",
         f"- 生成：{calib.generated_at}",
         f"- 判定：**{calib.verdict}** ／ locator_type = `{calib.locator_type}`",
         f"- 依据：{calib.reason}", "",
         "## 区间表", "",
         "| 分片 | 类型 | 物理页 range | 纸书页 range | offset | 观测 | 覆盖 | 置信 |",
         "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in calib.segments:
        L.append(f"| {s.shard} | {s.kind} | {s.page_idx_start}–{s.page_idx_end} "
                 f"| {s.printed_start}–{s.printed_end} | {s.offset:+d} | {s.n_obs} "
                 f"| {s.coverage:.0%} | {s.confidence} |")
    L += ["", "> offset = 纸书页码 − 物理页序，区间内恒定。", ""]
    if calib.gaps:
        L += ["## 断点（不填、不外推）", ""]
        for g in calib.gaps:
            L.append(f"- {g['shard']} 物理页 {g['after_page_idx']}→{g['before_page_idx']} 之间 "
                     f"漏 {g['n_unmapped_pages']} 页（推算纸书页 {g['missing_printed']}）")
        L.append("")
    if calib.continuity.get("checks"):
        L += ["## 跨分片接续", "", "| 从 | 到 | 期望 | 实际 | 结论 |", "| --- | --- | --- | --- | --- |"]
        for c in calib.continuity["checks"]:
            L.append(f"| {c['from']} | {c['to']} | {c['expected']} | {c['next_printed']} "
                     f"| {'✅' if c['ok'] else '⚠️'} {c['note']} |")
        L.append("")
    if calib.header_conflicts:
        L += ["## 页眉与页码冲突（已以 page_number 为准）", ""]
        for c in calib.header_conflicts:
            L.append(f"- {c['shard']} 物理页 {c['page_idx']}：page_number={c['page_number']}，"
                     f"header={c['header']}")
        L.append("")
    return "\n".join(L)
