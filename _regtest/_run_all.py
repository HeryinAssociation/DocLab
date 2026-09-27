"""把 tests/ 下所有用例跑一遍，汇总通过情况（回归用）。

为什么要有这个：改一处内核要跑七八个用例，一条条手敲既慢又容易漏。
本脚本只做「依次子进程跑 + 汇总 + 保留失败输出」，不替任何用例做判断。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = Path(r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe")

TESTS = ["test_key", "test_multisel", "test_chars", "test_manual", "test_pagemap",
         "test_add", "test_add_e2e", "test_epub", "test_shards", "test_download"]


def main(argv: list[str]) -> int:
    only = [a for a in argv if not a.startswith("-")]
    names = only or TESTS
    out = ROOT / "_regtest" / "_run_all.log"
    lines: list[str] = []
    bad: list[str] = []
    for n in names:
        f = ROOT / "tests" / f"{n}.py"
        if not f.is_file():
            lines.append(f"!! {n}: 没有这个文件")
            bad.append(n)
            continue
        r = subprocess.run([str(PY), str(f)], cwd=str(ROOT),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tag = "PASS" if r.returncode == 0 else f"FAIL({r.returncode})"
        lines.append(f"\n{'=' * 70}\n{tag}  {n}\n{'=' * 70}")
        lines.append((r.stdout or "").rstrip())
        if r.returncode != 0:
            lines.append("---- stderr ----")
            lines.append((r.stderr or "").rstrip()[-4000:])
            bad.append(n)
        print(f"{tag}  {n}", flush=True)
    lines.append(f"\n\n汇总：{len(names) - len(bad)}/{len(names)} 通过"
                 + (f"；失败 {bad}" if bad else ""))
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"汇总：{len(names) - len(bad)}/{len(names)} 通过" + (f"；失败 {bad}" if bad else ""))
    print(f"日志：{out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
