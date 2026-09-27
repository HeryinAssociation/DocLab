"""界面后端冒烟测试：pending 检测 + 删源（移入 _trash）+ 路由存在性。

用**假源**跑，不碰任何真书：
  _work/_zz_smoke.0000000000/{project.json, ingest_state.json}
  out/_zz_smoke.0000000000/L2/02-x.md
跑完清掉假源与 _trash 里的测试箱。

    python tests/smoke_ui.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import OUT_DIR, ROOT, TRASH_DIR, WORK_DIR, load_config, out_dir, work_dir  # noqa: E402

PORT = 8799
SID = "_zz_smoke.0000000000"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机必须绕开代理


def get(path: str) -> dict:
    with opener.open(f"http://127.0.0.1:{PORT}{path}", timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_job(jid: str, timeout: float = 30) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = get(f"/api/job?id={jid}")
        if j.get("status") in ("done", "error"):
            return j
        time.sleep(0.3)
    raise TimeoutError(jid)


def make_fake_source() -> None:
    wd = work_dir(SID)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "project.json").write_text(json.dumps(
        {"source_id": SID, "doc_title": "冒烟测试书", "mode": "mineru-api",
         "shards": [{"tag": "P1", "n_pages": 200, "n_items": 10}]},
        ensure_ascii=False), encoding="utf-8")
    # 一个「跑到一半」的 ingest：P1 就绪、P2 失败 —— 正是用户说的 P1/P2/P3 场景
    (wd / "ingest_state.json").write_text(json.dumps({
        "source_id": SID, "source_file": r"C:\nope\冒烟.pdf", "doc_title": "冒烟测试书",
        "shard_mode": "mineru", "shard_pages": 200, "submitted_at": "2026-09-20T00:00:00",
        "shards": [
            {"tag": "P1", "state": "unzipped", "page_ranges": "1-200", "pages": 200, "blocks": 900},
            {"tag": "P2", "state": "failed", "page_ranges": "201-400",
             "err": "BadZipFile: File is not a zip file"},
        ]}, ensure_ascii=False), encoding="utf-8")
    od = out_dir(SID) / "L2"
    od.mkdir(parents=True, exist_ok=True)
    (od / "02-x.md").write_text("# 测试\n", encoding="utf-8")


def cleanup() -> None:
    shutil.rmtree(work_dir(SID), ignore_errors=True)
    shutil.rmtree(out_dir(SID), ignore_errors=True)
    if TRASH_DIR.is_dir():
        for box in TRASH_DIR.glob(f"*{SID}*"):
            shutil.rmtree(box, ignore_errors=True)


def port_busy(port: int) -> bool:
    """端口上已经有人（多半是上一次测试残留的服务进程）。"""
    import socket
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    # 端口被占时老的报错是「✗ 界面起不来」，看不出是谁占了 —— 白查半天。
    # 实测残留进程真的会活下来（整条命令被外部回收时，子进程未必跟着走）。
    if port_busy(PORT):
        print(f"✗ 端口 {PORT} 已被占用 —— 多半是上一次测试残留的服务。\n"
              f"  查：netstat -ano | findstr {PORT}\n"
              f"  杀：taskkill /PID <那个 PID> /F")
        return 1
    cleanup()
    make_fake_source()
    fails: list[str] = []

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "doclab.py"), "ui", "--port", str(PORT), "--no-browser"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 等端口
        for _ in range(40):
            try:
                get("/api/bootstrap")
                break
            except Exception:                                   # noqa: BLE001
                time.sleep(0.25)
        else:
            print("✗ 界面起不来")
            return 1

        # 0) 页面本身要能出来，而且带上目录视图开关（藏层级 / 藏已删）与点行选择。
        #    这一条防的是「脚本里少了个容器 id」这类只在浏览器里才炸的错。
        with opener.open(f"http://127.0.0.1:{PORT}/", timeout=15) as r:
            page = r.read().decode("utf-8")
        miss = [n for n in ('id="vdKb"', 'id="showDelCk"', 'id="viewBar"',
                            "function rowClick", "let VDEPTH", "function setVDepth")
                if n not in page]
        if miss:
            fails.append("首页缺少：" + "、".join(miss))
        else:
            print("  首页 ✓ —— 目录视图开关（藏层级 / 藏已删）与点行选择都在")

        # 1) pending 检测：半成品必须出现在 bootstrap.pending 里
        b = get("/api/bootstrap")
        pend = {p["source_id"]: p for p in b.get("pending", [])}
        if SID not in pend:
            fails.append("bootstrap.pending 没认出半成品 ingest")
        else:
            p = pend[SID]
            if (p["done"], p["total"]) != (1, 2):
                fails.append(f"pending 就绪计数错：{p['done']}/{p['total']}")
            if [s["state"] for s in p["shards"]] != ["unzipped", "failed"]:
                fails.append("pending 分片状态不对")
            if p["shards"][1]["err"][:11] != "BadZipFile:":
                fails.append("pending 没带上失败原因")
            print(f"  待续跑：{SID}  {p['done']}/{p['total']} 片就绪，"
                  f"P2 错误已带出 → {p['shards'][1]['err'][:40]}")

        # 2) /api/source 里也带上 pending_ingest
        d = get(f"/api/source?id={SID}")
        if not d.get("pending_ingest"):
            fails.append("source_detail 缺 pending_ingest")
        else:
            print("  source_detail.pending_ingest ✓")

        # 3) 删源：产物移入 _trash，且不真删
        j = wait_job(post("/api/delete", {"source": SID})["job"])
        if j["status"] != "done":
            fails.append(f"delete job 失败：{j.get('error')}")
        else:
            box = Path(j["result"]["trash"])
            moved_work = box / "work" / "project.json"
            moved_out = box / "out" / "L2" / "02-x.md"
            if not moved_work.exists() or not moved_out.exists():
                fails.append(f"产物没完整移进回收站：{box}")
            if work_dir(SID).exists() or out_dir(SID).exists():
                fails.append("工作目录没被移走")
            if SID in {s["source_id"] for s in get("/api/bootstrap")["sources"]}:
                fails.append("删源后仍在源列表里")
            print(f"  删源 ✓  →  {box}")

        # 4) 路由存在性（不应 404）
        for path in ("/api/resume", "/api/delete", "/api/ingest"):
            try:
                post(path, {})
            except urllib.error.HTTPError as e:                 # noqa: F821
                if e.code == 404:
                    fails.append(f"{path} 仍是 404")
        print("  路由 /api/resume /api/delete /api/ingest ✓")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        cleanup()

    if fails:
        print("\n✗ 冒烟未通过：")
        for f in fails:
            print("  - " + f)
        return 1
    print("\n✓ 界面后端冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
