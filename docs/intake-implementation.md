# 书籍入库接入记录

日期：2026-10-17。工作区按用户指定建在 **Doclab/Doclab-Inbox**。

## 已实施

- DSH Web 的持久配置已增加 `doclab-intake` 预设，显示名“Doclab 书籍入库”；原配置备份为 `~/.dsh/profiles/web/cordis.patch.yml.before-doclab`，其他研究角色未更改。
- 工作区已通过 DSH 自身工作区接口注册。技能显式从 Doclab-Inbox/.dsh/skills 加载，解决嵌套在 Git 仓库中时默认技能根不一致的问题。
- Doclab MCP 增加 6 个入库工具，专用预设只挂载这 6 个 Doclab 工具。原有 16 个工具保留；普通模式共 22 个。
- 后台任务落盘到 jobs；MCP 返回 job_id，不长时间占住连接。OCR 复用现有 ingest/resume，EPUB 仍走本地解析。
- 核校支持分页目录、问题清单、校准观测、完整正文块续读与原页图。修改通过原有 tocio/fixplan 落入人工层，版本不一致拒写，先演练后应用。
- 持久阶段通知插件绑定当前 DSH 会话，以稳定 requestId 交回后台事件，恢复后相同事件去重。
- 导出补齐父级导语的切分边界，并额外检查全书源块、脚注、未分类块及图片缺失。普通 G2 通过不再单独作为全书无损依据。
- 本机 Python 3.9 的 write_text 参数兼容问题已修复；PyMuPDF 已安装到项目 .runtime，原页工具按需加载。
- 安全复用本机已有 Zotero MinerU 插件中的 Token，写入已忽略提交的 .env.local；未输出 Token。真实书籍已有 OCR 缓存继续复用。
- Zotero 归档插件 0.1.2 已在本机 Zotero 10.0.6 加载，带认证的归档端点 health 通过。修复原因见 zotero-plugin/README.md。
- MinerU 的 list_items 进入正文读取，保留原 gid；深层标题可用 include_deep 分页读取并按 key 提级，完整快照比较防止误删；前置区相同印刷页码添加序列限定，保持可唯一定位。
- 通知包含事件序号，明确要求读取实时状态并忽略过期事件；无须通知的阶段也推进游标，支持维护期间挂起通知。DSH 已重载，bridge-status.local.json 的 revision=2026-10-17-notify-fix，原工作区与会话已在页面恢复显示。

## 验证

- 原有目录往返 23 项、修订单 25 项通过。
- 本地入库测试 12 项通过，新增真实 MinerU 列表载荷的导出/漏文检测、L10 标题提级、前置区同号定位符检查、完整回执恢复和错误回执拦截。
- 通知测试通过：静默阶段游标推进、维护挂起、事件去重、延迟通知措辞。
- 归档插件的隔离应用 API 夹具检查 8 项通过：鉴权、目标、路径越界、哈希变化、父条目、回读、链接和重复提交。这不能代替 Zotero 真机验收。
- 非真实书籍 EPUB 测试材料已经通过实际后台任务、目录取证、核校完成和 Markdown 导出；完整覆盖通过。section_only 对应的原有校验 warn 如实保留。
- DSH 页面已实际选择 Doclab-Inbox 与书籍入库模式，已通过一次模型自检确认 cwd、skill 与工具 schema 加载。具体 MCP 真实调用及通知验收结果以 DSH 对话和任务回执为准。

## 使用入口

本机 DSH Web 当前地址 `http://127.0.0.1:3080/`；登录使用启动器打印的本机授权入口，不把令牌写进文档。

后续可用项目根的 `启动书籍入库.bat` 启动。选择 Doclab-Inbox、Doclab 书籍入库模式，发送 PDF 或 EPUB。核校需看原页时使用可接收图像的模型；本次在会话中选择了已有的视觉模型。

默认 L3 核校、L1 分章导出归档；导出同时装订整书 EPUB，归档时保留原书、章节、整书 EPUB、元数据与图片。完整书籍压缩包、AI 核校报告不归档，AI 核校报告保留本地。归档准备不再生成完整压缩包，旧计划如含这两类文件、或缺少 EPUB，提交前要求重新 prepare。默认目标为个人库中唯一名为“书籍专著”的分类，找不到或重名会要求明确 key。新条目题录必须有来源，既有条目身份不明时暂停，不猜测。

## 尚未宣称通过的部分

- 跨设备图片同步；本机文件回读不能替代跨设备验证。
- 浏览器实际上传大 PDF 后的附件路径交接，以及进程被强制关闭时的完整恢复。

真实书籍《档案馆的现在与未来》已归入“书籍专著”（CPQ2QAY9），父条目 SHAG4LSH，任务 732e8f94323e709e74f63c89 / completed / event_seq=12，68 个附件 verified=true。新导出含 62 个 Markdown、6 张图，原始列表 246 条全部逐条进入文本层，定位符冲突为零。首次归档连接在 120 秒超时，但 Zotero 随后完成持久回执；已通过完整回执核验恢复完成状态，未重发归档请求。归档连接现允许 600 秒，MCP 工具及宿主超时相应覆盖；短状态可从完整回执恢复完成状态，拒绝缺项或错误计划回执。仍不宣称所有新书均可无人值守完成。运行材料、密钥和样书都在 .gitignore 限定目录内。

## 2026-11-17 追加：EPUB 装订进入导出与归档

- 新增 `core/epub_export.py`：读 `export` 刚写出的分节 md，装订成 EPUB3（nav + NCX、OPF 元数据、图片、脚注、引文、表格）；页锚转为不可见 `epub:type=pagebreak` 锚点，不往正文写页码。装订后立即执行 `verify_epub` 结构自检（zip/mimetype/container/OPF/spine/nav/XHTML/图片引用/章节数），失败删除临时包并抛错。MinerU 写进 `table_body` 的原始 HTML 表格（含 colspan/rowspan）与行内 `<sup>`/`<sub>`/`<br>` 会转成合法 XHTML，不再按标签文本输出。
- `core/exporter.py` 的 `export` 默认在写完 md 后装订 EPUB，`conversion_meta.json` 增加 `epub` 段（文件名、章数、图片数、目录条数、自检结果、提示、缺图），`stats` 增加 `epub_bytes` / `epub_chapters`。
- CLI：`export` 默认装订，新增 `--no-epub`；新增 `epub` 子命令用已有 md 单独重做；`run` 透传 `--no-epub`；`sources` 阶段增加 EPUB 标记，`status` 显示每个导出目录的 EPUB。
- 工作台：新增 `/api/epub`（下载与 JSON 里每个深度的 EPUB 摘要）和 `epub_export` 接口能力自检；导出面板增加“同时装订 EPUB”与“重做 EPUB”，并显示文件名、章数、大小和结构自检结果。
- 入库：`finalize` 的导出会同时生成 EPUB，并把 `epub`（name/path/sha256/chapters/images/nav_entries/verified/missing_images）写进 job 摘要；EPUB 结构自检失败则 finalize 失败。`archive prepare` 要求导出目录恰有 1 个 EPUB，校验任务记录的文件名与裸 sha256，生成 `role=epub` 的附件项；缺失、换名或内容变化都会要求重新 finalize。Zotero 插件原已把 `.epub` 映射为 `application/epub+zip`，归档回执按既有逐文件哈希回读。
- 缺图处理：md 引用而图片未搬运或格式不受 EPUB 支持（如 tif）时，EPUB 写占位并记 warning；入库侧同时把该情况并入 coverage 的 `missing_images` 并置 fail，阶段回到 `needs_review`，不会把残书归档。
- 验证：新增 `tests/test_epub_export.py` 14 项（结构/元数据、正文转换、目录顺序、坏包自检、章节数不符、缺图 strict/占位、不支持格式、引用编码、原始 HTML 表格/上标、可重复、外部输出不误删、经 `core/epub.py` 往返、exporter 串联、空目录）；`tests/test_intake.py` 扩到 17 项（finalize 产物、归档计划含 EPUB、EPUB 缺失/变化拒绝归档、旧计划缺 EPUB 拒绝提交、同名附件消歧）。真实导出目录 `_regtest/_exp_regress`（21 节、506 个页锚、无 images/）装订冒烟通过，缺图按占位处理。
- MCP：新增 `doclab_epub` 工具与 `doclab_export.epub` 开关；修复 `mcp_server.cjs` 的 JSON 提取。旧实现按 `^[[{]/m` 找第一个 JSON 起点，日志行 `[export]`/`[epub]` 会被当成数组开头，导致 `doclab_export`/`doclab_epub` 报「CLI 未返回 JSON」。提取逻辑移到 `mcp_json.cjs`，从输出末尾往前找可解析的起点；`tests/test_mcp_json.cjs` 6 组用例通过，MCP 握手后实际调用 `doclab_sources` 与 `doclab_epub` 均成功。
- 真实补挂：2026-11-17 为已完成入库的《空间叙事学》（父条目 9C58XLZE）和《被计算的历史：数智变革中的档案记忆范式转型》（父条目 H4D9E8TW）从归档导出目录装订 EPUB，并用既有 Zotero 插件以独立补挂计划写入原父条目：新增附件 WKYPLIV9 / PN2NV89R，`contentType=application/epub+zip`，回执 `verified=true`，独立读取 Zotero storage 的完整 SHA-256 与源文件一致。未重建条目、未删除或改动其他既有附件；两个父条目附件数由 25/20 变为 26/21。补挂计划与回执保留在 `jobs/5495900dffaefcaf4f34a893`、`jobs/1c54009829a89bd9a99a8563`，`resource_sync_verified=false`，跨设备同步仍未验证。
- 表格修订（《被计算的历史》）：首版 EPUB 把 MinerU 的 8 处原始 HTML `<table>` 与 3 处 `<sup>` 按文本输出；随后修 `core/epub_export.py`，增加原始 HTML 表格（colspan/rowspan）与行内 `sup`/`sub`/`br` 渲染，生成修订版并替换同一附件 PN2NV89R 的文件（附件 key、标题、父条目不变；旧文件备份在 `jobs/1c54009829a89bd9a99a8563/exports/被计算的历史：数智变革中的档案记忆范式转型.epub.pre-table-fix.epub`）。用新补挂计划 `jobs/7758bac0eae376b14dfd53c5` 让插件重新逐字节校验：`verified=true`，storage 中 `<table>` 8 个、无 `&lt;table`、`<sup>` 3 个；旧补挂记录已标注 `superseded_by`。
- 未验证：EPUB 在真实阅读器中的排版与脚注跳转；Zotero 实库对 EPUB 附件的跨设备同步（本机 storage 回读通过）；大图/多图书籍的 EPUB 体积与阅读性能。
- 内置 AI（源级）：新增 `core/ai_review.py`、`ai-review` CLI、工作台设置/一键核校/预览/应用和 `/api/ai/*`。OpenAI 兼容；token 只进 `.env.local` 的 `DOCLAB_AI_TOKEN`（界面掩码，不回传）；默认 `deepseek-chat`，输入上限 52k 字。模型输出逐条校验 key/gid/标题原文/evidence，应用前备份、应用后重算并重跑 audit；页码只做原文字符/相邻观测序列推断，不做原页视觉。外部 MCP 的 `review_read`/`review_apply` 与 DSH 入库流程未改；内置 AI 应用后 revision 变化，进行中的 job 需重新读取目录。真实实验调用 DeepSeek 兼容端点（实际返回 `deepseek-flash`）5 次（3 次内容预览 + 2 次连接自检）、约 2.4 万 tokens，只生成预览、未应用真实改动；离线测试 `tests/test_ai_review.py` 18 项。工作台冒烟新增 `/api/ai` 配置与 `ai_review` cap 检查。
- EPUB 封面：`core/cover.py` 从源 PDF 第 1 页渲染 JPEG，或从源 EPUB 的 `cover-image`/guide/封面页首图提取；`build_epub` 写 `text/cover.xhtml`、`properties="cover-image"` 和 guide cover，spine 首项为封面。找不到封面记 warning 但不阻断。PDF 渲染依赖 PyMuPDF（`.runtime` 或 pip 安装）；macOS 启动器安装 `pypdf` + `PyMuPDF`。已归档的旧 EPUB 需重做才会带封面。XHTML 转义前会清理 XML 1.0 非法控制符；自检报错时附带出错行列附近原文。
