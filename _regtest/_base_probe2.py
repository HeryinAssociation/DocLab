# -*- coding: utf-8 -*-
"""基线书 red#2 取证：locator_type / 校准段 / 4.2 标题页附近的观测页码。"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
SID = "中国数字人文发展报告"
WD = os.path.join(BASE, "_work", SID)

rep = json.load(open(os.path.join(WD, "目录校核.json"), encoding="utf-8"))
cal = json.load(open(os.path.join(WD, "page_calibration.json"), encoding="utf-8"))

print("=== audit rep 概要 ===")
for k in ("locator_type", "focus_depth", "outline_node_total", "verdict"):
    print(k, "=", rep.get(k))
print("calib 段：")
print(json.dumps(rep.get("calibration") or {}, ensure_ascii=False, indent=2)[:1500])

print("\n=== page_calibration ===")
print(json.dumps(cal, ensure_ascii=False, indent=2)[:3000])
