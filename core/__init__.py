"""doclab —— 文献处理工作台内核（MinerU 适配层）。

定位：输入 PDF/EPUB → 经 MinerU 识别 → 输出按层级切分、带纸书页码锚的 markdown，
供多 Agent workflow 作为「文献处理」环节调用。

纪律（对齐 JingyeLab APR-20260920-001）：
  - 只输出 md 与元数据，不写 Zotero、不写 evidence_table、不写 library_index；
  - 页码校准不出时整书降级 section_only，禁止猜偏移；
  - 一切结论落盘（state.json / 各产物 json），会话不是唯一真相。
"""

__version__ = "0.1.0"
