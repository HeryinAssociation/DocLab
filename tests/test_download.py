"""download() 的回归测试 —— 针对不可靠网络下的静默截断与连接僵住。

背景（2026-09-20 实测）：「认识论引论」15 MB 的 PDF 切 3 片，OCR 都跑完了，
下载 P1 时只落下 911,340 字节（没有中央目录的 zip），P2 则僵在恰好 6 MiB 不动。
旧实现 `read` 到空就当成功，于是把半截文件交给 zipfile，炸出
`BadZipFile: File is not a zip file`，错因看起来像「下到了坏包」。

本测试用本地 HTTP 服务复刻这两类故障，断言 download() 能自愈、且绝不留下半截产物。
不需要网络，不需要 token；用任意一个合法 zip 当样本即可。

跑法：
    python tests/test_download.py
"""
from __future__ import annotations

import http.server
import os
import pathlib
import re
import socketserver
import sys
import threading
import time
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import urllib.request  # noqa: E402

from core.config import Config  # noqa: E402
from core.mineru_api import MineruClient, MineruError  # noqa: E402

PORT = 8931
TMP = pathlib.Path(__file__).resolve().parent / "_tmp"


def make_sample_zip(dest: pathlib.Path, entries: int = 6) -> bytes:
    """造一个够大（>4 MiB）的合法 zip 当样本，便于观察中途截断。

    用随机字节 + STORED：重复字节会被 DEFLATE 压到几 KB，就测不出中途截断了。
    """
    if dest.exists() and dest.stat().st_size > (4 << 20):
        return dest.read_bytes()       # 缓存够大才复用（小样本测不出中途截断）
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = os.urandom(1 << 20)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
        for i in range(entries):
            z.writestr(f"{i}.bin", blob)
    return dest.read_bytes()


class _Server:
    """按指定「行为」服务样本的本地 HTTP 服务，支持 Range。"""

    def __init__(self, raw: bytes, behavior: str, port: int):
        self.raw = raw
        self.total = len(raw)
        self.behavior = behavior
        self.port = port
        self.hits = 0
        self._srv = None

    def start(self):
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                start = 0
                m = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
                if m:
                    start = int(m.group(1))
                if start >= outer.total:
                    self.send_response(416)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if outer.behavior == "truncate":
                    # 提前 EOF：声明全量长度，却只发一小段就正常收尾
                    sent_end = min(start + (1 << 19) * outer.hits, outer.total)
                    body = outer.raw[start:sent_end]
                    self.send_response(206 if start else 200)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{sent_end - 1}/{outer.total}")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if outer.behavior == "stall":
                    # 僵住：声明剩余全量，发一段后既不给数据也不断开
                    cap = min(start + (1 << 19) * outer.hits, outer.total)
                    self.send_response(206 if start else 200)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{outer.total - 1}/{outer.total}")
                    self.send_header("Content-Length", str(outer.total - start))
                    self.end_headers()
                    off = start
                    while off < cap:
                        k = min(1 << 16, cap - off)
                        self.wfile.write(outer.raw[off:off + k])
                        off += k
                    self.wfile.flush()
                    time.sleep(30)
                    return

                if outer.behavior == "no-range":
                    # 服务端忽略 Range，永远整包返回；用来验证「从头重来」这条路
                    self.send_response(200)
                    self.send_header("Content-Length", str(outer.total))
                    self.end_headers()
                    self.wfile.write(outer.raw)
                    return

                raise AssertionError("未知 behavior")

            def log_message(self, *a):
                pass

        class Srv(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._srv = Srv(("127.0.0.1", self.port), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()


def _client(logs: list) -> MineruClient:
    cfg = Config(api_base="http://127.0.0.1:1", token="dummy")
    return MineruClient(cfg, log=logs.append)


def case(name: str, raw: bytes, behavior: str, *, clean_part: bool, port: int) -> bool:
    srv = _Server(raw, behavior, port)
    srv.start()
    dest = TMP / f"{behavior}.zip"
    part = dest.with_name(dest.name + ".part")
    dest.unlink(missing_ok=True)
    if clean_part:
        part.unlink(missing_ok=True)
    else:
        # 预置一个「上一轮遗留」的半截 .part，验证跨进程续传
        part.write_bytes(raw[: (1 << 19)])
    logs: list[str] = []
    try:
        _client(logs).download(f"http://127.0.0.1:{port}/x.zip", dest,
                               retries=8, stall_timeout=5)
        ok = dest.exists() and zipfile.is_zipfile(dest) and dest.stat().st_size == len(raw)
        staged = part.exists()
        print(f"{'✅' if ok and not staged else '❌'} {name}")
        print(f"     连接 {srv.hits} 次 | 落盘 {dest.stat().st_size if dest.exists() else 0}"
              f"/{len(raw)} | 合法 zip {zipfile.is_zipfile(dest) if dest.exists() else False}"
              f" | .part 残留 {staged}")
        for line in logs:
            print("       ", line)
        return ok and not staged
    except MineruError as e:
        print(f"❌ {name} — 未自愈：{str(e)[:140]}")
        for line in logs:
            print("       ", line)
        return False
    finally:
        srv.stop()
        dest.unlink(missing_ok=True)
        part.unlink(missing_ok=True)


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    raw = make_sample_zip(TMP / "sample.zip")
    print(f"样本 zip：{len(raw)} bytes，合法：{zipfile.is_zipfile(TMP / 'sample.zip')}\n")

    results = [
        case("提前 EOF（每次只发一点就收尾）→ 断点续传补齐",
             raw, "truncate", clean_part=True, port=PORT),
        case("连接僵住（声明全量却不再给数据）→ stall_timeout 后断点续传",
             raw, "stall", clean_part=True, port=PORT + 1),
        case("服务端忽略 Range（永远整包）→ 从头重来也能成",
             raw, "no-range", clean_part=True, port=PORT + 2),
        case("复用上一轮遗留的 .part → 接着下",
             raw, "truncate", clean_part=False, port=PORT + 3),
    ]
    print()
    if all(results):
        print(f"全部 {len(results)} 项通过")
        return 0
    print(f"{results.count(False)} 项失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
