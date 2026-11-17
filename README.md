# Doclab

PDF / EPUB 文献处理工作台：把电子书转换为正文块，建立章节索引，核校目录层级和页码，再按深度输出 Markdown、整书目录与转换元数据，并把同一份分节 Markdown 装订成带封面的 EPUB，供 AI 按章节读取，也便于作为附件归入 Zotero 专著条目。

本文依据本仓库实际代码编写。Doclab 核心版本为 `0.3.0`；图形工作台、命令行与 stdio MCP 共用同一套处理逻辑。旧 CLI 导出写入本地文件；入库工作流可通过本机 Zotero 插件归档。

2026-10-17 已接入 DSH 书籍工作区 `Doclab-Inbox`、6 个入库 MCP 工具和 Zotero 归档插件 0.1.2。《档案馆的现在与未来》已归入“书籍专著”，68 个附件本机回读通过；跨设备同步尚未验证。当前接入状态、测试范围和剩余工作见 [接入记录](docs/intake-implementation.md)，原打通方案见 [实施方案](docs/dsh-doclab-zotero-integration-plan.md)。

入库默认按 L3 核校、按 L1 分章导出归档；导出时同时装订整书 EPUB。完整书籍压缩包和 AI 核校报告不归档，AI 核校报告保留在本地。

## 实际处理流程

| 输入 | 处理方式 | 定位方式 |
| --- | --- | --- |
| PDF | 调用 MinerU v4 API；默认每片 200 页；也可登记已有 MinerU 工程，免于重跑 OCR | 从页码块和部分带编号页眉建立分段映射；支持人工锚点覆盖 |
| EPUB | 本地解析，不调用 MinerU；输出兼容内部块模型的工程 | `section_only`；`page_idx` 是 spine 阅读顺序单元，不是印刷页码 |

流程是：登记／处理源 → `pagecal` 页码校准 → `outline` 建立索引 → 目录与页码核校 → `export` 导出 Markdown 并装订 EPUB → `verify` 检查。

`run` 的实际执行顺序也是 `pagecal → outline → export → verify`。页码要先于索引，因为索引会使用校准结果，尝试根据书内印刷目录补齐漏标章节。部分命令帮助中的旧顺序描述尚未同步，以执行逻辑为准。EPUB 由 `export` 在写完全部分节 md 之后生成，读的就是这一份 md，不重跑 OCR 或切分算法。

## 启动工作台

在项目目录执行：

```powershell
python -m pip install -r requirements.txt
python doclab.py ui
```

也可双击 `启动界面.bat`。它优先寻找当前用户目录下的 WorkBuddy Python，不存在则尝试 PATH 中的 `python`；缺少 `pypdf` 时会尝试安装。

macOS 双击 `启动.command`。首次运行会创建 `.venv`、安装 `pypdf` 与 `PyMuPDF`，然后启动同一条 `doclab.py ui`。若系统拦截，右键选“打开”，或在终端执行 `chmod +x 启动.command`。

默认地址是 `http://127.0.0.1:8765/`；端口不可用会继续尝试后续端口，以启动日志地址为准。也可指定 `python doclab.py ui --port 9000`，或用 `--no-browser` 关闭自动打开浏览器。

核心实现以 Python 标准库为主，依赖清单包含 `pypdf>=5.0` 与 `PyMuPDF>=1.24`（后者只用于把 PDF 第一页渲染成封面）；MCP 另外需要 Node.js，其服务端无需安装 npm 包。本次已在 Python 3.9.13、Node.js 24.19.0 上验证命令行入口与 MCP 握手，未做跨版本兼容性承诺。

工作台提供“目录层级”“页码对照”、正文块检索补标题，以及导出与校验。导出面板同时装订 EPUB，可单独重做或下载；导出产物与校验结果以实际目录为准。人工编辑先记录在叠加层，需“应用并重算”后反映到生成结果；导出文件还需重新导出。目录的显示深度／隐藏开关只影响显示，不改变真实结构或导出范围。

## MinerU 配置

Token 读取顺序是：

1. 环境变量 `MINERU_TOKEN`。
2. 项目根目录的 `.env.local` 中的 `MINERU_TOKEN`。
3. `config.json` 的 `mineru.token`。

工作台填写的 Key 写入 `.env.local`；如果环境变量已设置，它仍然优先。不要把 Token 写进 README、skill 或提交到仓库。

`config.json` 的 `api` 支持 `base_url`、`language`、公式／表格开关、`is_ocr`、`model_version`、`shard_pages`、`shard_mode`、轮询间隔与超时；已有工程发现路径由顶层 `mineru_output_root` 指定，默认是用户目录下的 `MinerU`。

`shard_mode=mineru` 使用 API 的 `page_ranges`，`local` 使用本机 pypdf 先切分。这里的默认 200 页是本项目配置，不代表已重新核实外部服务当前限制。

```powershell
python doclab.py probe --json
```

`probe` 会检查 API 连通与鉴权；它不是纯离线检查。EPUB 本地处理不需要 MinerU Token。

## 常用命令

以下命令在项目根目录执行。将示例路径与 `SOURCE_ID` 替换为实际值；有多个源时应明确指定源。

```powershell
# PDF 调用 MinerU；EPUB 使用相同入口、本地解析
python doclab.py ingest "D:\Books\book.pdf"
python doclab.py ingest "D:\Books\book.epub"

# 或复用已有 MinerU 成果；多个分片目录按原书顺序给出
python doclab.py adopt --dirs "D:\MinerU\book-part1" "D:\MinerU\book-part2"

python doclab.py sources --json
python doclab.py status SOURCE_ID --json
python doclab.py pagecal SOURCE_ID --json
python doclab.py outline SOURCE_ID --json

# 一次执行处理与导出（md + EPUB）；这不等于已完成人工／AI 语义核校
python doclab.py run SOURCE_ID -d 2

# 单独导出（默认同时装订 EPUB）、只导 md、单独重做 EPUB、验证、读取
python doclab.py export SOURCE_ID -d 2
python doclab.py export SOURCE_ID -d 2 --no-epub
python doclab.py epub SOURCE_ID -d 2
python doclab.py verify SOURCE_ID -d 2 --json
python doclab.py sections SOURCE_ID -d 2 --json
python doclab.py read 001 SOURCE_ID -d 2 --json
```

`depth=0` 为整本一个文件；其余深度按当前索引树切分，含较浅叶节点的处理。PDF 命令行默认页末脚注，EPUB 命令行默认随文脚注；可用 `--footnotes page-end|inline|drop` 明确指定。需要保留注释时不要选择 `drop`。工作台导出和 MCP `doclab_run` 的默认脚注行为与命令行未必相同，EPUB 建议明确传 `inline`。

OCR 未完成可用 `python doclab.py resume --source SOURCE_ID` 补未就绪分片。移出源的 `remove` 将工作区和导出目录移入 `_trash`，不删除原 PDF 或原 MinerU 工程；这不是核校的日常步骤。

## EPUB 导出

`export` 在写完全部分节 Markdown 后，用同一份 `out/SOURCE_ID/L<N>/` 装订出 `<书名>.epub`。实现见 `core/epub_export.py`，装订本身只依赖标准库；封面提取见 `core/cover.py`。它不是第二条转换链：EPUB 读的就是刚导出的 md，所以 md 里是什么，EPUB 里就是什么。

装订时保留：

- 章节顺序与 `manifest.json` 的 `seq` 一致；EPUB3 nav 与 NCX 双目录，层级来自导出节点的 `level`。
- 页锚 `<!-- p=N -->` 变成不可见的 `epub:type="pagebreak"` 锚点（`id=p57` / `pfront-9` / `ppdf-12`）；**不把页码印成正文数字**。这些锚可跳转、可被引用，但阅读器不显示页号。
- 随文脚注（`<!-- footnote -->`）、页末脚注列表（`**脚注**…` → `ul.fn-list`）、引文块、图片与图注、Markdown 表格、`> 归属：…` 标题链。
- 封面：PDF 源把第 1 页渲染成 JPEG；EPUB 源从原书 `cover-image`、guide cover 或封面页首图提取。写入 `text/cover.xhtml` 与 `images/cover.*`，OPF 用 `properties="cover-image"`，spine 首项是封面，guide 加 `type="cover"`。找不到封面只记 warning，不阻断导出。
- OPF 元数据：书名、作者（EPUB 源取原书 `dc:creator`，PDF 源为空）、语言（EPUB 源取 `dc:language`，否则 `zh`）、由 source_id 派生的稳定 `urn:uuid`、`dcterms:modified`。
- 图片按 md 引用复制到 `OEBPS/images/`。导出时用了 `--no-images` 的目录，装订会把缺图行换成“图片缺文件”占位，并在结果里记 warning，不写坏 `<img>` 引用。

`verify_epub` 随装订自动执行：检查 `mimetype` 是否第一个且不压缩、`container.xml` → OPF、manifest/spine/nav 引用是否都能落地（含封面图）、每个 XHTML 是否合法、图片引用是否存在、章节数是否与导出记录一致。结构不合格时 `build_epub` 删除临时包并抛错，不会留下坏 EPUB。

需要明确的限制：

- 这是**结构核验**，不是阅读器视觉验收；排版效果仍需人打开看。
- 支持 MinerU 直接写进 `table_body` 的原始 HTML `<table>`（含 colspan/rowspan）与行内 `<sup>`、`<sub>`、`<br>`；其他未列出的原始 HTML 标签仍按文本保留，不执行脚本、不加载外部资源。
- EPUB 是 md 的派生产物。正文要改，应改源工程或人工核定层后重新 `export`，不要直接编辑 EPUB，也不要通过改 EPUB 去“修复”目录。
- 页锚是定位信息，不是原书排版；EPUB 阅读器自己的页码/进度与纸书页码无关。
- `doclab_run`、工作台和入库流程都会同时生成 EPUB；`doclab_export` 可传 `epub=false`（CLI `--no-epub`）只导 md，此时会清掉该导出目录上一轮的 EPUB，避免界面或归档把旧 EPUB 当成当前产物。`epub` 子命令用于 md 已存在时单独重做，不重跑导出。

## 内置 AI 核校（可选）

外部 AI 通过 MCP 的 `review_read` / `review_apply` 仍是正式通道；工作台另提供内置 AI。两者共用同一套 `manual` / `tocio` / `fixplan` / `pagecal` 人工核定层，互不替代。

- 设置：源详情页“内置 AI 核校”填 `base_url`、`model`、`API Token`。默认 OpenAI 兼容（`https://api.deepseek.com/v1`，`deepseek-chat`；实际返回模型可能由服务商映射，本次实验为 `deepseek-flash`）；DeepSeek、OpenAI、通义、Kimi、本地 vLLM/Ollama 等兼容端点可用。
- Token 只写 `.env.local` 的 `DOCLAB_AI_TOKEN`（`.gitignore` 已排除），界面只显示掩码；不写日志、不进报告。base/model 写 `config.json` 的 `ai` 段。
- 点击“AI 一键核校”：Doclab 收集目录、audit findings、正文取证窗口、页码观测和原文字符（**不读原页图**），调用模型；输出只作为建议，逐条校验 key / gid / 标题原文 / evidence / 页码依据后生成预览。
- 预览不落盘；点“应用这些修改”才写人工层。应用前自动备份 `manual_edits.json`、`outline.json`、`page_calibration.json`；应用后重算并重跑 audit，报告在 `_work/<sid>/AI核校报告.md`，备份在 `_work/<sid>/AI核校备份/`。
- 页码只做字符/序列推断：同页 `page_number`/`header` 原文（含 O/0、l/1 等 OCR 混淆）或同分片前后相邻观测形成连续序列；没有依据的页码建议进 `unresolved`，不新造锚点。**不做原页视觉核验。**
- 目录改动必须通过 `tocio` 校验；新增标题必须逐字来自真实块；不能删前置兜底节点、不能只删有子节点的父级、不能无 evidence。
- CLI：

```powershell
python doclab.py ai-review --test                    # 只测接口
python doclab.py ai-review SOURCE_ID -d 3 --json     # 只生成预览
python doclab.py ai-review SOURCE_ID --apply         # 应用最近一次预览
```

- 内置 AI 不自动改入库 job 的 revision；如果同一本书正在入库任务中，应用后外部流程需重新读取目录。外部 MCP 工具、参数和行为未改。
- 限制：文本模型不能替代原书语义核校；模型判断可能误报/漏报，预览里的 `unresolved` 要人工处理。书籍文字片段会发送到所选 API，先确认服务商、隐私与费用。默认输入上限 52,000 字；大书可降到 L2 或继续用外部 AI 分批核校。

## AI 目录核校：已有 JSON 往返通道

```powershell
python doclab.py toc-export SOURCE_ID -d 3 --json
python doclab.py audit SOURCE_ID -d 3 --json
python doclab.py grep SOURCE_ID --q "第三章" --json
python doclab.py grep SOURCE_ID --gid 300-340 --json

# 编辑导出 JSON 的副本后，先演练，再应用
python doclab.py toc-import SOURCE_ID --file "D:\Review\toc-reviewed.json" --json
python doclab.py toc-import SOURCE_ID --file "D:\Review\toc-reviewed.json" --apply --json
```

`toc-export` 写入 `_work/SOURCE_ID/目录树.json`。AI 应保留整份 JSON 骨架和核对深度：改变已有节点的 `level`，移除误识别条目，或用 `grep` 找到真实正文块后新增条目。深于 `depth_limit` 的节点不在这次核校范围内。

关键约束：

- 使用稳定 `key`，不用重排后会变化的 `nid`。真实节点、目录补全节点与人工新增节点的键分别形如 `"82"`、`"toc:…"`、`"add:301"`；合成节点的 `gid` 可能借自其他节点，不能拿它替代 `key`。
- 普通节点的标题不允许直接改写。新条目应使用正文原文并带真实 `gid`、`title`、`level`，`key` 留空；必要时提供块内字符位置 `offset`。
- 保留前置兜底节点；不要把被截断的节点列表导回，否则缺失条目会被解释为删除。
- 导回先检查合法性，应用后写入人工核定层并重建索引。它不修改页码锚点，也不会自动重新导出 Markdown。

`audit` 提供机械检查和正文取证窗口，不能单凭编号断档就认定原书漏识别。正文搜索每条最多返回 400 字；命中数量大于返回数量或文本截断时，应继续取证，不能将截断内容当作全文。

## 页码核校：通过修订单持久保存

`pagecal` 从 `page_number` 与指定模式的 `header` 收集观测，按分片推断区间、断点与接续；不足以定位时降级为 `section_only`。PDF `page_idx` 是**分片内从 0 开始**的页面索引；展示给人的第几页需另行转换。

人工锚点通过命令行修订单写入，先演练再应用：

```powershell
python doclab.py fix SOURCE_ID --template "D:\Review\page-plan.json"
# 根据原书证据编辑模板；不能直接应用模板中的示例数据
python doclab.py fix SOURCE_ID --plan "D:\Review\page-plan.json" --json
python doclab.py fix SOURCE_ID --plan "D:\Review\page-plan.json" --apply --json
```

页码锚点的动作是 `anchor`，字段为 `shard`、`page_idx`、整数 `printed` 和 `evidence`。锚点从所在页向后覆盖至下一锚点前，末锚点一直覆盖到分片末尾；它**不是只修正一页**。必须核对受影响范围、后续编号变化和跨片接续，不能用一个孤立页码猜测全片。

`fix` 保存修订记录，页码变动时先重算页码，再重算索引。不要直接修改 `manual_edits.json`、`outline.json` 或 `page_calibration.json` 绕过这些入口。格式校验通过不等于执行具有事务性：应用过程中可能逐条失败，必须检查返回的 `failed` 并重读结果。

## 数据与导出文件

```text
DocLab/
├─ doclab.py                 命令行入口
├─ mcp_server.cjs            stdio MCP，调用命令行，不另写算法
├─ core/                     OCR、EPUB 解析／装订、索引、校准、人工层、导出、核验
├─ ui/index.html             本地工作台
├─ _work/SOURCE_ID/
│  ├─ project.json           源及分片登记
│  ├─ manual_edits.json      持久修正：levels / anchors / deleted / added
│  ├─ outline.json           可重算的索引树
│  ├─ page_calibration.json  可重算的页码映射
│  ├─ 目录树.json            AI 往返核校输入
│  ├─ 目录校核.json / .md    audit 结果
│  ├─ 修订记录/              修订单应用历史
│  ├─ 校核修订记录.md        追加式修订日志
│  ├─ export_index.json      最近导出的块回查记录
│  ├─ AI核校预览.json        内置 AI 的未应用建议、依据与 revision
│  ├─ AI核校报告.md/.json    应用后的报告（本地保留，不归档）
│  ├─ AI核校备份/            每次应用前的人工层/目录/校准备份
│  └─ verify.json            校验结果
└─ out/SOURCE_ID/L2/
   ├─ 00-目录.md              整书结构及章节链接
   ├─ 001_…md                带层级标题链的章节文件
   ├─ 书名.epub              整书 EPUB（由同一批 md 装订）
   ├─ images/                搬运的图片（启用时）
   ├─ conversion_meta.json   转换、定位、输出与 EPUB 元数据
   ├─ manifest.json / .csv   章节清单
   └─ 校验报告.md             执行 verify 后生成
```

目录在执行相应阶段后生成。已映射页锚形如 `<!-- p=57 -->`，前置页用 `front-N`；未映射位置显式写为 `<!-- p=pdf-N unmapped -->`。EPUB 中的 `pdf-N` 是当前内部格式的占位序号，不代表 EPUB 有 PDF 物理页。EPUB 里页锚转为不可见定位点，正文不出现这些标记。正文中的 `F7`、`E423` 等外文版边码保留原文，不用来建立本书页码锚。

旧 CLI 重新导出同一深度会更新已有产物，并清理不再使用的章节文件；不要把导出 Markdown 当作永久编辑入口。入库工作流按任务版本保存导出，归档时保留章节、元数据、图片和整书 EPUB，并将目录链接转换为 Zotero 条目链接。

## 校验含义与现有限制

实际执行的是四道检查：G1 页锚覆盖率、G2 内容块回查、G3 页码区间与跨片接续、G4 页锚或 `section_only` 替代条件。`section_only` 可能正常产生 `warn`，不能改成假页码来消除警告。EPUB 另有一层独立的 `verify_epub` 结构自检（zip/OPF/spine/nav/XHTML/图片引用），不并入这四道闸门的 verdict；装订失败会让导出直接失败，EPUB 结构不合格不会进入入库归档。

这些检查不等同于原书逐页视觉核实，也不保证目录语义正确。尤其 G2 只检查 `export_index.json` 中登记的块是否能在文件中找到，并非将全书所有原始块与全部导出做完整相等性比较。当前导出代码还注明，较深切分可能遗漏父节点在第一个子节点之前的直属正文，因此核校需额外比较全书块覆盖。

`verify -d N` 在目标深度不存在时会回退到其他导出目录；务必核对实际目录。`export_index.json` 是源级最近导出记录，核验不同深度时应按“导出该深度 → 立即验证该深度”的顺序执行。

## MCP

启动入口是 `node mcp_server.cjs`，使用 stdio、逐行 JSON-RPC。可以在支持 stdio MCP 的客户端填写以下服务器配置；外层配置键是否为 `mcpServers` 取决于客户端。

```json
{
  "mcpServers": {
    "doclab": {
      "command": "node",
      "args": ["C:\\Users\\jingye\\Desktop\\CODE\\DocLab\\mcp_server.cjs"],
      "env": {
        "DOCLAB_PYTHON": "C:\\Users\\jingye\\anaconda3\\python.exe",
        "DOCLAB_ROOT": "C:\\Users\\jingye\\Desktop\\CODE\\DocLab"
      }
    }
  }
}
```

以上解释器路径是本次本机实际检测值，换机器需修改。务必明确设置 `DOCLAB_PYTHON`：源码默认值硬编码为另一个用户的 Python 路径，不能假定开箱即用。`DOCLAB_ROOT` 默认是服务脚本所在目录。

已通过实际 `tools/list` 确认 16 个工具：

| 用途 | 工具 |
| --- | --- |
| 环境与源 | `doclab_probe`、`doclab_sources`、`doclab_status` |
| 登记与 OCR | `doclab_adopt`、`doclab_ingest` |
| 自动构建 | `doclab_pagecal`、`doclab_outline`、`doclab_run` |
| 目录核校 | `doclab_grep`、`doclab_toc_export`、`doclab_toc_import` |
| 输出与读取 | `doclab_export`、`doclab_epub`、`doclab_verify`、`doclab_sections`、`doclab_read_section` |

`doclab_export` 默认同时装订 EPUB，可传 `epub=false` 只导 md；`doclab_epub` 用于 md 已存在时单独重做。

上述旧 MCP 工具未单独暴露 `audit`、`fix` 和原页图像读取；新增入库工具通过 `review_read` 提供问题、校准与原页图，通过 `review_apply` 应用有证据的页码及目录修改。`doclab_adopt` 包装器会自动附加 `--force`；不要将它作为普通核校起手动作。MCP 包装器也不能只凭 `isError=false` 判断业务成功，须检查返回中的 `ok`、`errors`、`failed`、`verdict` 和 `epub.verify.ok`。

配套 [AI 核校实施方案](docs/ai-proofreading-plan.md) 和 [doclab-proofread skill](skills/doclab-proofread/SKILL.md) 已按这些实际接口编写；skill 尚未安装到用户全局目录。

## 本次核实范围

2026-10-15：已读取 CLI、MCP、工作台服务、人工修订层、目录往返、校准、导出、验证和 EPUB 入口，并运行命令帮助、源清单、MCP 握手／工具枚举／源清单调用。`test_tocio.py` 23 项及 `test_fixplan.py` 25 项通过。

上述为 2026-10-15 的初始核实记录。2026-10-17 已处理真实 PDF 并完成 Zotero 归档；恢复 246 条列表参考文献、修正 7 个类目层级、消除前置页定位符冲突。另有 12 项入库测试、8 项插件契约检查和通知测试通过。`test_pagemap.py` 仍依赖另一份旧本机样书，未纳入本轮通过项。

2026-11-17：新增 Markdown → EPUB 装订与结构自检（`core/epub_export.py`），接入 CLI、工作台、MCP 和入库 finalize/归档。新测试 `tests/test_epub_export.py` 16 项、`tests/test_intake.py` 17 项、`tests/test_mcp_json.cjs` 6 组通过；对真实导出目录 `_regtest/_exp_regress`（21 节、506 个页锚）完成装订冒烟，结构自检通过。同日新增封面：PDF 第一页渲染或原 EPUB 封面提取，写入 `text/cover.xhtml` 与 `properties="cover-image"`；`tests/test_cover.py` 5 项、带封面的 `_regtest` 冒烟通过（1021×1433 JPEG，整包 522 KB，结构自检通过）。顺带修复 MCP 包装器的 JSON 提取：CLI 日志行以 `[export]`、`[epub]` 开头时，旧实现会误把日志当 JSON 起点而报「未返回 JSON」。同日为已完成入库的《空间叙事学》《被计算的历史》补挂整书 EPUB；后者 8 处原始 HTML 表格与 3 处 `<sup>` 已由修订后的转换器渲染，替换同一附件 PN2NV89R 的文件并重新校验（附件 key 不变），结构自检通过，独立读取 Zotero storage 的哈希与源文件一致。未重建条目、未删除或改动其他既有附件。未做阅读器视觉验收，跨设备同步仍未验证。

2026-11-17 内置 AI 目录核校：新增 OpenAI 兼容客户端、证据包、严格校验、预览/应用与备份；工作台可填 token 后点“AI 一键核校”，外部 MCP 通道保持原样。离线测试 `tests/test_ai_review.py` 18 项通过。真实实验使用 DeepSeek 兼容端点（实际返回模型 `deepseek-flash`），对 2 本书做了 3 次内容预览、2 次连接自检，共 5 次请求、约 2.4 万 tokens；只生成预览，未应用任何真实改动。模型对一本 76 节点书正确判断出证据包内 39 条编号“重复”为误报，未强行改目录；页码仅在字符/序列有据时提出，否则列入未决。
