# 本仓库接口约定

适用于 Doclab 0.3.0；运行前核实工具实际参数。CLI 在 Doclab 根目录执行，MCP 通过 node mcp_server.cjs 调用同一 CLI，DOCLAB_PYTHON 必须指向真实解释器。

## 常用映射

| 任务 | MCP | CLI |
| --- | --- | --- |
| 源清单 | doclab_sources({}) | python doclab.py sources --json |
| 全状态 | doclab_status({source}) | python doclab.py status SID --json |
| 校准 | doclab_pagecal({source}) | python doclab.py pagecal SID --json |
| 建索引 | doclab_outline({source}) | python doclab.py outline SID --json |
| 核对输入 | doclab_toc_export({source, depth:3}) | python doclab.py toc-export SID -d 3 --json |
| AI 核校（源级预览） | 当前无（内置功能，非入库 MCP） | python doclab.py ai-review SID -d 3 --json；应用追加 --apply |
| AI 接口自检 | 当前无 | python doclab.py ai-review --test --json |
| 搜正文 | doclab_grep({source,q,limit:40}) | python doclab.py grep SID --q "标题片段" --json |
| 展开块范围 | MCP grep 的 q 必填，不能无词展开 | python doclab.py grep SID --gid 100-140 --json |
| 导回演练 | doclab_toc_import({source,file,apply:false}) | python doclab.py toc-import SID --file FILE --json |
| 导回应用 | doclab_toc_import({source,file,apply:true}) | 上条追加 --apply |
| 机械核查 | 当前无 | python doclab.py audit SID -d 3 --json |
| 页码修订 | 当前无 | python doclab.py fix SID --plan FILE --json；应用追加 --apply |
| 导出 | doclab_export({source,depth:2,footnotes:"inline"}) | python doclab.py export SID -d 2 --footnotes inline --json |
| 只导 md | doclab_export({source,depth:2,epub:false}) | python doclab.py export SID -d 2 --no-epub --json |
| 重做 EPUB | doclab_epub({source,depth:1}) | python doclab.py epub SID -d 1 --json |
| 校验 | doclab_verify({source,depth:2}) | python doclab.py verify SID -d 2 --json |
| 分节清单 | doclab_sections({source,depth:2}) | python doclab.py sections SID -d 2 --json |
| 读章节 | doclab_read_section({source,section:"001",depth:2}) | python doclab.py read 001 SID -d 2 --json |

CLI 日志与 JSON 都写 stdout，不保证整个 stdout 是单一 JSON；MCP 内部有提取逻辑。本地需处理日志与 JSON 的分界，不能把业务 errors 忽略。file 是 MCP 服务所在机器的路径，不是客户端另一台机器的同名路径。

status 返回 project、outline、page_calibration、verify 和 outputs，含可读的工作目录／工程登记信息。可据此读取本地原始块；没有远程全文／原页读取能力时不要假设客户端能访问服务端路径。

## 目录 JSON

必须从真实 toc-export 结果复制，不从零手造整书列表。现有节点字段有 seq/key/nid/level/title/gid/page/shard/page_idx/marker/offset/blocks/chars/own_chars/flags。保留 depth_limit 和未修改的条目。

新增节点示意（数字及标题必须替换为已核实真实值，不可直接执行）：

```json
{"key":"","gid":301,"title":"第三章 原文标题","level":1,"offset":0,"reason":"正文 gid=301 独立标题，与原书目录及前后章一致"}
```

层级修改写节点 reason；删除理由写顶层 `reasons`，以原 key 为键；顶层 note 是整单说明。toc-import 不处理页码锚点、不支持用 JSON 顺序移动节点。普通节点改标题会拒收；add: 节点可覆盖其人工新增记录。

## 页码修订单

独立 JSON，人工层和生成文件都不直接编辑。下例仅展示真实字段，不是可应用的书籍事实：

```json
{
  "source_id":"实际源ID",
  "name":"页码核校",
  "note":"说明已核实的区间、边界及接续",
  "items":[
    {
      "action":"anchor",
      "shard":"P1",
      "page_idx":20,
      "printed":8,
      "evidence":"替换为实际原页位置、可见页码和整个受影响区间的核对依据"
    }
  ]
}
```

撤销某锚点用 anchor-clear（shard/page_idx/evidence），不是写一个猜测的新 printed。printed 为整数，接口没有表达任意页码标签或显式空白 gap 的动作。

fix 按 CLI 的 SID 选择实际源，不能仅信 JSON source_id；执行前自行核对两者。fix 校验每项 evidence 非空，但不验证语义；对未知 key、部分 gid 条件可能只警告，且运行时逐项 apply 可能部分成功。目录导回也依赖此层，不能将“全部参数验证后执行”称为原子事务。

fix 应用后若锚点改变，自动重算 pagecal，再重算 outline；toc-import 应用后只重算 outline。两者都需随后重新 export 与 verify。修订历史保存在工作目录的修订记录/ 与校核修订记录.md。

## 需谨慎的已有行为

- doclab_adopt 自动附加 --force；日常核校不要调用。
- doclab_run 不检查每一步的业务结论便继续调用，不能替代逐步核校。
- export 默认同时装订 EPUB；EPUB 是 md 的派生物，页锚不显示、缺图会置占位。EPUB 结构自检（epub.verify.ok）通过不代表排版或语义已验收；正文修改仍以 md/源块为准。
- grep 的页码过滤使用当前推断映射，不能作为该映射正确的独立证明。
- 人工锚点将覆盖到后续锚点／分片尾，且可把 locator_type 改为 paginated；该标记不是视觉验证结果。
- G2 只在已输出块中做包含检索；G4 只检查存在页锚或 section_only 条件，不能证明所有文件页码正确。
- 同深度重新导出会更新并清理旧章节文件与旧 EPUB，先保存需要保留的旧版本。
- 内置 `ai-review` 只是源级预览：token 在 `.env.local` 的 `DOCLAB_AI_TOKEN`，模型输出要经 key/gid/标题/evidence/页码依据校验；不做原页视觉，页码无字符或相邻序列依据时只进 unresolved。它不取代本 skill 的 MCP 流程；应用后 revision 变化，需重新 review_read。
