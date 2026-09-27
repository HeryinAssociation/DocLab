"""界面的页码递加逻辑必须和后端 pagecal.apply_anchors 逐页一致。

为什么值得单独测：
  「填一个锚点、后面逐页递加」在界面上是**前端算**的（不然每敲一格都要等一次
  往返，没法用），而后端 apply_anchors 才是真正写进 md 页锚的那份。
  两份实现一旦漂移，就会出现「界面显示 288、导出的 md 里写着 290」这种
  最难查的错误 —— 而且它不报错，只是悄悄错。
  所以这里把 index.html 里的 pageMap() 抽出来，和 Python 版对同一组锚点跑，
  要求**逐页完全相同**。
"""
import json
import pathlib
import re
import copy
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import work_dir                       # noqa: E402
from core.pagecal import apply_anchors, calibrate       # noqa: E402
from core.project import iter_blocks, load_shards      # noqa: E402

SID = "中国数字人文发展报告"
NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"

EXTRACT_JS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const m = src.match(/function pageMap\(\)\{[\s\S]*?\n\}/);
if (!m) { console.error('没找到 pageMap —— 界面重构了？同步改这个测试'); process.exit(2); }
global.EDIT = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const pageMap = eval('(' + m[0] + ')');
const pm = pageMap();
const out = {};
for (const k of Object.keys(pm)) {
  out[k] = {};
  // 比 printed 之外还要比 kind：前置页(front)的编号不是正文页码，
  // 界面若把它同化成正文数字，人校对时对的就是错的对象。
  for (const [i, v] of Object.entries(pm[k])) out[k][i] = [v.printed, v.kind];
}
console.log(JSON.stringify(out));
"""


def frontend_map(edit: dict, tmp: pathlib.Path) -> dict:
    """调 node 跑界面里的 pageMap()。"""
    js = tmp / "_pagemap_probe.js"
    payload = tmp / "_pagemap_edit.json"
    js.write_text(EXTRACT_JS, encoding="utf-8")
    payload.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(ROOT / "ui" / "index.html"), str(payload)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"node 跑失败：{r.stderr}")
    return json.loads(r.stdout)


def backend_map(calib, anchors: dict) -> dict:
    """后端真值：直接按 apply_anchors 之后的分段逐页展开（与前端同一口径）。

    基准必须和前端**是同一份**。前端拿的是 `calibrate(..., anchors={})` 的干净结果，
    而后端这里一度读的是 `load_calibration()` —— 落盘那一份，里面早就烘进了操作者
    在界面上存过的锚点。于是人一旦点过「应用并重算」，两边起点就差出几个页的偏移，
    整张表 188 处不一致、红给你看：**那是测试自己搭错了台，不是界面在骗人。**
    apply_anchors 是就地改，所以每个场景都要拿一份干净拷贝，否则场景之间互相污染。
    """
    c = copy.deepcopy(calib)
    apply_anchors(c, anchors)
    out = {}
    for shard in c.shards:
        m = {}
        for s in c.segments:
            if s.shard != shard:
                continue
            for i in range(s.page_idx_start, s.page_idx_end + 1):
                m[str(i)] = [i + s.offset, s.kind]
        out[shard] = m
    return out


def main() -> int:
    wd = work_dir(SID)
    tmp = ROOT / "_regtest"
    tmp.mkdir(exist_ok=True)

    shards = load_shards(wd)
    blocks = iter_blocks(shards)
    calib = calibrate(SID, blocks, [s.to_dict() for s in shards], anchors={})

    base = {
        "shard_order": calib.shards,
        "shard_pages": calib.shard_pages,
        "auto_segments": {},
        "anchors": {},
        "observations": {},
    }
    for s in calib.segments:
        base["auto_segments"].setdefault(s.shard, []).append(
            {"start": s.page_idx_start, "end": s.page_idx_end,
             "printed_start": s.printed_start, "printed_end": s.printed_end,
             "offset": s.offset, "kind": s.kind})

    cases = {
        "无锚点（纯自动）": {},
        "单锚点 P1:100→90": {"P1": [{"page_idx": 100, "printed": 90}]},
        "单锚点 P1:15→3（落在正文段起点）": {"P1": [{"page_idx": 15, "printed": 3}]},
        "多锚点 P2 分段": {"P2": [{"page_idx": 0, "printed": 200},
                                  {"page_idx": 50, "printed": 255}]},
        "三片各有锚点": {"P1": [{"page_idx": 100, "printed": 90}],
                         "P2": [{"page_idx": 10, "printed": 210}],
                         "P3": [{"page_idx": 5, "printed": 400}]},
    }

    fails = 0
    for name, anchors in cases.items():
        edit = dict(base)
        edit["anchors"] = {k: v for k, v in anchors.items()}
        fe = frontend_map(edit, tmp)
        be = backend_map(calib, anchors)
        bad = []
        for shard in be:
            f, b = fe.get(shard, {}), be[shard]
            for i in set(f) | set(b):
                if f.get(i) != b.get(i):
                    bad.append((shard, i, f.get(i), b.get(i)))
        status = "PASS" if not bad else "FAIL"
        print(f"  {status}  {name}"
              + (f"  —— {len(bad)} 处不一致，前 3: {bad[:3]}" if bad else ""))
        fails += bool(bad)

    print("\n" + ("全通过：界面上看到的页码，就是会写进 md 的页码"
                  if not fails else f"{fails} 个场景不一致 —— 界面在骗人，必须修"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
