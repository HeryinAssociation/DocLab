# DocLab（砚）· 文献处理工作台

文献处理工作台：将 PDF / EPUB 转化为**按章节层级切分、带纸书页码锚**的 Markdown，方便 AI（多 Agent workflow / RAG / 笔记）进行知识抽取与精确引用。

- **PDF** → 调 [MinerU](https://mineru.net/) API 做 OCR，超页数上限自动分片，支持断点续跑；
- **EPUB** → 本机纯标准库解析，**不调 MinerU、不烧配额**；
- 目录层级由中文编号规则推断（不盲信 OCR 的标题标签），并与书内印刷目录交叉校验、自动补漏标章节；
- 页码校准把「PDF 物理页」映射到「纸书印刷页码」，导出时每个段落带 `<!-- p=N -->` 页锚——AI 引用页码直接可用；
- 全流程可校验、可回查、可人工修订，修改默认**演练模式**（`--apply` 才落盘）。

> 设计原则：逻辑只在内核实现一次；CLI / Web UI / MCP 都是它的薄壳。

---

## 它解决什么问题

直接把一本几百页的书丢给 LLM，页码、章节结构、脚注都会糊成一团。DocLab 把「书」变成机器友好的形态：

| 痛点 | DocLab 的做法 |
| --- | --- |
| PDF 版面噪声（页眉/页脚/水印/扫描残留） | 块级分类过滤，噪声类型显式清单 + 可疑文本剔除 |
| 标题层级识别不可靠（不同书标签体系差异极大） | 优先按中文编号推断（第X编 > 第X章 > 第X节 > 一、 > （一） > 1.），标签只作兜底 |
| MinerU 漏标章节 | 用书内印刷目录交叉校验，自动补章；逐条留痕 |
| 纸书页码 ≠ PDF 页码 | 页码校准：只在「差值恒定」的连续区间内建立映射，**禁止猜偏移**，建立不出就显式降级 `section_only` |
| OCR 漏检难以人工复核 | `audit` 把「通读几百条目录」压缩成「复核 N 条待定项」，`grep` 无头取证 |
| 内容丢失静默发生 | 四道校验闸门（G1 页锚覆盖 / G2 内容无损 / G3 页码一致 / G4 入库硬条件），不静默放行 |
| 误删/误改不可恢复 | 删源进 `_trash` 可捞回；一切修订走「人工核定层」，只叠加不覆盖 |

---

## 快速开始

### 环境要求

- Python 3.10+（开发环境 3.13）
- 依赖只有 `pypdf`（PDF 分片用）；EPUB 链路与 Web UI 均为纯标准库
- 想处理 PDF 需要一个 MinerU API Token（[mineru.net](https://mineru.net/) 免费申请）；只处理 EPUB 则不需要

### 安装与启动

```bash
pip install pypdf
```

**Windows 一键启动**（自动找 Python、缺依赖自动装）：双击 `启动界面.bat`

**命令行启动界面：**

```bash
python doclab.py ui --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

界面里可以：导入 PDF/EPUB、看分片进度、配 MinerU Key、浏览目录树选切分深度、触发导出与校验、逐块修订目录。

### 配置 Token

优先级：环境变量 `MINERU_TOKEN` > `.env.local` > `config.json`。推荐在界面「MinerU Key 设置」里填，或手写 `.env.local`（已被 .gitignore 排除，永不入库）：

```
MINERU_TOKEN=eyJhbGciOi...
```

`config.json` 控制 MinerU API 参数（识别语言、公式/表格开关、分片页数上限等），改完重启生效。

---

## 典型工作流

```
ingest ──► pagecal ──► outline ──► export ──► verify
 (入库)     (页码校准)   (目录索引)    (切分导出)   (闸门校验)
                          │
                          ├─ audit（只读校核，产出待定项清单）
                          ├─ grep（正文检索取证，拿块 gid）
                          └─ fix / toc-export + toc-import（人工核定层修订）
```

一条龙（推荐先跑这个看全貌）：

```bash
python doclab.py run <source_id> -d 2
```

分步：

```bash
# 1. 入库（PDF 走 MinerU OCR；EPUB 本机解析）
python doclab.py ingest 某本书.pdf
python doclab.py ingest 某本书.epub

# 2. 页码校准：物理页 → 纸书页码
python doclab.py pagecal <source_id>

# 3. 目录索引：先看各层节点数，再选深度
python doclab.py outline <source_id>

# 4. 按深度切分导出（一节一个 md）
python doclab.py export <source_id> -d 2

# 5. 校验
python doclab.py verify <source_id>

# 不确定 source_id？列出所有源（支持前缀匹配）
python doclab.py sources
```

### 目录精修（可选，质量要求高时）

OCR 的标题不会全对，DocLab 把修订做成**证据驱动、可回滚**的闭环：

```bash
# 只读校核：结构/编号/页码/印刷目录对照，产出待复核清单
python doclab.py audit <source_id>

# 无头取证：在正文块里搜关键词，拿到漏掉标题的块 gid
python doclab.py grep <source_id> --q 第三章 --json

# AI 核对闭环：目录导出 JSON → AI 改 → 导回（diff 自动翻译成修订）
python doclab.py toc-export <source_id> -d 2
#   ……（AI 修改 toc.json）……
python doclab.py toc-import <source_id> --file toc.json --apply

# 或按修订单（每条必须带 evidence，无证据拒收）
python doclab.py fix <source_id> --plan 修订单.json --apply
```

所有修订默认**演练**（只打印将要发生什么），加 `--apply` 才落盘；修订只写入人工核定层 `manual_edits.json`，绝不直接改 outline / pagecal 产物。

### AI / Agent 消费导出结果

```bash
python doclab.py sections <source_id> -d 2   # 列出分节清单（标题/页码范围/字数）
python doclab.py read <source_id> 014 -d 2   # 读某一节正文（含页锚）
```

---

## 已有 MinerU 产物？不重跑 OCR

桌面版 MinerU 已经跑过的书，直接登记进工作台：

```bash
python doclab.py adopt --keyword 认识论          # 在 MinerU 输出根下按关键词找
python doclab.py adopt --dirs C:/path/to/工程目录  # 或显式给目录
```

中断的 OCR 批次可续跑（只补未就绪的分片，不重复烧配额）：

```bash
python doclab.py resume --source <source_id>
```

---

## MCP 接入（让 AI Agent 直接用）

`mcp_server.cjs` 是 doclab CLI 的 MCP（Model Context Protocol）薄封装，把 `probe / sources / ingest / pagecal / outline / export / verify / grep / sections / read` 等暴露成 MCP 工具。逻辑仍只在 Python 内核里，Node 侧只做参数转换。

```json
{
  "mcpServers": {
    "doclab": {
      "command": "node",
      "args": ["D:/code/DocLab/mcp_server.cjs"]
    }
  }
}
```

可用环境变量：`DOCLAB_PYTHON`（覆盖 Python 解释器路径）、`DOCLAB_ROOT`（覆盖项目根目录）。

---

## 目录结构

```
DocLab/
├── doclab.py            # CLI 入口（唯一的功能总纲）
├── core/                # 内核：逻辑只在这里实现一次
│   ├── ingest.py        #   入库：PDF→MinerU API / EPUB 本机解析 / adopt / resume / remove
│   ├── mineru_api.py    #   MinerU v4 API 客户端（上传/轮询/下载/解压）
│   ├── project.py       #   块模型与分片管理（Block / shard / 工程读取）
│   ├── pagecal.py       #   页码校准（物理页→纸书页码，禁止猜偏移）
│   ├── outline.py       #   目录树构建（中文编号推断 + 印刷目录交叉校验 + 补章）
│   ├── exporter.py      #   按深度切分导出（页锚 / 脚注三档 / conversion_meta）
│   ├── verify.py        #   四道校验闸门
│   ├── audit.py         #   目录校核（只读，产出待复核清单）
│   ├── manual.py        #   人工核定层（定级/删除/新增/页码锚点）
│   ├── fixplan.py       #   修订单（证据驱动，默认演练）
│   ├── tocio.py         #   目录 JSON 往返（AI 语义核对入口）
│   ├── epub.py          #   EPUB → 块序列（移植自 epub-to-markdown 技能）
│   ├── server.py        #   本地界面后端（纯标准库 http.server，零依赖）
│   └── config.py        #   配置 / Token 分级读取 / source_id 解析
├── ui/index.html        # 单文件 Web 界面
├── mcp_server.cjs       # MCP 服务器（薄壳）
├── epub-to-markdown/    # 独立技能目录（epub2md.py + SKILL.md，逻辑已内联进 core）
├── tests/               # 自测脚本（直接 python 运行，断言规则而非某本书）
├── 启动界面.bat          # Windows 一键启动
├── config.json          # MinerU API 参数（不含 token）
└── _work/  out/  _trash/  # 运行时生成：工程数据 / 导出产物 / 回收站（均不入库）
```

### 产物布局

每本书是一个「源」（`source_id` = 文件名 + 内容哈希，天然幂等）：

```
_work/<source_id>/          # 工程索引与中间产物
    project.json            #   分片清单与元信息
    page_calibration.json   #   页码校准（区间表 + 断点）
    outline.json            #   目录树（含层级统计、目录补章记录）
    manual_edits.json       #   人工核定层（修订只叠加在这里）
    校验报告.md / 目录校核.md
out/<source_id>/L<N>/       # 按第 N 层切出的分节 Markdown
    00-目录.md              #   全书目录索引（链到各节）
    NNN_节标题.md           #   正文 + <!-- p=纸书页码 --> 页锚
    conversion_meta.json / manifest.json / manifest.csv
```

---

## 设计约定（改代码前先读）

1. **禁止伪造页码。** 页码校准只在「印刷页码与物理页号差值恒定」的连续区间内建立映射；断裂处记 gap 不填；整书建立不出就降级 `section_only`。EPUB 无物理页码，导出页锚显式写 `p=pdf-N unmapped`。
2. **逻辑只实现一次。** 新功能写进 `core/`，CLI / UI / MCP 复用，不许出现平行实现。
3. **修改默认演练。** 一切落盘动作（fix / toc-import）必须显式 `--apply`；落盘只写人工核定层，不直接改算出来的产物。
4. **结论落盘，会话不是唯一真相。** 状态全部在 `_work/*/` 的 JSON 里，进程随时可死、随时可续。
5. **静默丢内容是最坏的错。** 未知块类型顶到台面报警；校验闸门 fail/warn 必须显式报告，不静默放行。
6. **删源不真删。** 产物整体移入 `_trash/` 可人工捞回；原 PDF / MinerU 工程目录从不移动。

---

## 测试

每个测试是独立脚本，直接运行：

```bash
python tests/test_epub.py      # EPUB 全链路：合成一本书走 ingest→…→verify
python tests/test_tocio.py     # 目录 JSON 往返
python tests/test_audit.py     # 目录校核
python tests/smoke_ui.py       # 界面后端冒烟（假源，不碰真书）
# …其余 test_*.py 同理，逐个 python 运行即可
```

测试不依赖真书：用合成 EPUB / 假工程目录钉住每条判定规则，回归有稳定基准。

## License

社团内部项目，暂未设置开源协议。
