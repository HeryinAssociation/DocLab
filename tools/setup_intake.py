"""Generate local DSH patch and Zotero XPI. Does not start apps or alter profiles."""
from __future__ import annotations
import json
import os
import secrets
import shutil
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from core.util import write_json, write_text

def main():
    inbox=ROOT/'Doclab-Inbox'
    for d in ['config','inbox','jobs','receipts','.dsh/skills']:
        (inbox/d).mkdir(parents=True,exist_ok=True)
    cfgfile=inbox/'config/intake.local.json'
    cfg=json.loads(cfgfile.read_text(encoding='utf-8')) if cfgfile.exists() else {}
    cfg.setdefault('bridge_token',secrets.token_urlsafe(32))
    cfg.setdefault('library_id',None);cfg.setdefault('collection_key',None)
    write_json(cfgfile,cfg)
    shutil.copytree(ROOT/'skills/doclab-proofread',inbox/'.dsh/skills/doclab-proofread',dirs_exist_ok=True)
    y=lambda s:json.dumps(str(s),ensure_ascii=False)
    patch=f'''- insert:
    - id: doclab-notifications
      name: {y((ROOT/'dsh_bridge.mjs').as_uri())}
    - id: preset-doclab-intake
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: doclab-intake
        name: 'Doclab 书籍入库'
        description: '发 PDF，自动核校、导出 Markdown 与 EPUB 并归档 Zotero'
        order: 0
        plugins:
          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config:
              prefix: '你是 Doclab 书籍入库助手。先读取工作区 AGENTS.md，使用 doclab-proofread skill。通过专用 Doclab MCP 操作，不重建转换逻辑。接收文件后用 pwsh 获取 $env:DSH_SESSION_ID，并将其传入 book_start 的 session_id 以接收后台任务事件。后台事件完成后接着核校和归档，不反复轮询。只报告已验证结果。'
          - id: agent-instructions
            name: '@deepseek-ai/dsh-agent-instructions'
            config:
              maxBytes: 10000
          - id: tool-pwsh
            name: '@deepseek-ai/dsh-tool-pwsh'
          - id: tool-fs
            name: '@deepseek-ai/dsh-tool-fs'
          - id: skill-filesystem
            name: '@deepseek-ai/dsh-skill-filesystem'
            config:
              includeDefaultRoots: false
              customSkillDirs: [{y(inbox/'.dsh/skills')}]
          - id: tool-skill
            name: '@deepseek-ai/dsh-tool-skill'
          - id: tool-ask-user
            name: '@deepseek-ai/dsh-tool-ask-user'
          - id: mcp-doclab-intake
            name: '@deepseek-ai/dsh-mcp-client'
            config:
              transport: stdio
              serverName: doclab
              command: 'node'
              args: [{y(ROOT/'mcp_server.cjs')}]
              cwd: {y(ROOT)}
              env:
                DOCLAB_ROOT: {y(ROOT)}
                DOCLAB_PYTHON: {y(sys.executable)}
                DOCLAB_WORKFLOW_ONLY: '1'
              toolCallTimeoutMs: 660000
              failOnStartupError: true
'''
    write_text(inbox/'config/dsh-runtime.patch.yml',patch)
    from build_zotero_plugin import build
    built=build()
    xpi=built['zotero_xpi']
    print(json.dumps({'ok':True,'workspace':str(inbox),'patch':str(inbox/'config/dsh-runtime.patch.yml'),
                      'zotero_xpi':str(xpi)},ensure_ascii=False))

if __name__=='__main__':main()
