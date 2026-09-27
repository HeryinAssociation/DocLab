---
name: epub-to-markdown
description: 把 EPUB（尤其是中文纸书 OCR 件）按书籍逻辑章节导出为 Markdown 分章文件：自动归并跨页续片、识别页下注与原版边码，并做文本保真度自检
---

# epub-to-markdown

把一个 EPUB 转成 `md/NN-章名.md` 的分章 Markdown。核心脚本 `epub2md.py`（与本 SKILL.md 同目录，纯 Python 标准库，无需 pip）。

## 何时用

- 用户给了 EPUB，想要按章的 Markdown（喂 LLM、做笔记、二次排版）。
- 源是纸书扫描 OCR 生成的 EPUB：正文里混着原版边码、页下注、被分页切断的句子、误判成 `<sub>/<sup>` 的杂字符。

## 步骤

1. **先看结构，不要直接转换。**
   ```bash
   python -m zipfile -l BOOK.epub
   python SCRIPT BOOK.epub --manifest map.json
   ```
   读 `map.json` 核对三件事：章数是否符合书的目录；有没有把“节”误认成“章”；章标题里有没有混进注码/换行噪声。

2. **确认归组无误后再落地：**
   ```bash
   python SCRIPT BOOK.epub -o OUT_MD
   ```
   末行打印 `源正文净字符 / MD净字符 / 比例`。**逐文件 ratio 应在 0.97–1.01**，合计 ≥0.985。低了就是丢内容或并错章，回去调旋钮，不要交付。

3. **抽查三处**（导论开头、注释最密的章、文末附录/索引）：标题层级、`> ① …` 注块、`` `F86` `` 边码是否在原位；有无跨页断成两段的句子。

4. **补 `00-目录.md`**：分组链向各章文件、标注各章节数，并写“转换说明”表（归并规则、边码/页下注约定、图片是否导出、已知损失）。源文件本身缺的（如某章在源里就没有某个节号）如实记录，不要杜撰补齐。

## 关键判定（可调旋钮）

| 机制 | 依据 | 覆盖方式 |
| --- | --- | --- |
| 章起始 | TOC 标签/首个标题命中 `--chapter-re`，**或** 该文件首个标题层级 == 全书最浅标题层级的众数（默认 h3=章、h4=节） | `--chapter-re` |
| 文件内切刀 | 命中 `--split-re` 的非首个标题，把混在正文末尾的索引切出来，同类自动并章 | `--split-re`、`MERGE_CLASSES` |
| 页下注 | 段首为带圈数字 ①②… 或 `list-style:none` 的 ul>li | `CIRCLED`、`is_note()` |
| 原版边码 | `<div><p>F7</p></div>`、正文粘着的 `F7`/`E423`/`FXVII` | `EDGE_*` |
| OCR 噪声 | `<sub>` 全丢；`<sup>` 仅保留含数字/`[` 者 | `inline()` |
| 跨页断句 | 段间夹有注/边码，或前段以顿逗冒号、未闭合括号收尾 → 并段 | `reflow()`、`looks_truncated()` |

结构迥异的书（EPUB3 nav、真脚注 `epub:type="footnote"`、目录无标题等）先 `--manifest` 看归组，再考虑改脚本或用 `--map map.json` 人工指定章与源文件的对应关系。

## 注意

- 脚本与中间产物放临时目录，只把 `md/` 留在用户工作区。
- Windows 控制台是 GBK：打印中文/特殊字符前先 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`，或重定向到文件再读。
- 别把 `inspect.py`/`json.py` 之类名字的脚本放进运行目录，会遮蔽标准库。
- **不校订 OCR 错字**（`Ren6 Descartes`、`6dn`、`哲学冢` 等），保持与原文一致，并在转换说明里写明。
