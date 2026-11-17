'use strict';
const {spawn} = require('child_process');
const fs = require('fs');
const path = require('path');
module.exports = function(root, python) {
  const str = {type:'string'}, int = {type:'integer'}, bool = {type:'boolean'};
  const props = {
    doclab_book_start: [{file:str,job_id:str,session_id:str,review_depth:{type:'integer',default:3},
      export_depth:{type:'integer',default:1,description:'默认按 L1 分章导出归档'}}, []],
    doclab_job_status: [{job_id:str}, ['job_id']],
    doclab_review_read: [{job_id:str,kind:{type:'string',enum:['outline','issues','blocks','page','calibration']},
      offset:int,limit:int,q:str,gid_start:int,gid_end:int,text_offset:int,shard:str,page_idx:int,part:str,include_deep:bool}, ['job_id']],
    doclab_review_apply: [{job_id:str,base_revision:str,operations:{type:'array',items:{type:'object'}},
      apply:bool,complete:bool,review_note:str,unresolved:{type:'array',items:str}}, ['job_id','base_revision']],
    doclab_book_finalize: [{job_id:str,base_revision:str}, ['job_id','base_revision']],
    doclab_archive_book: [{job_id:str,action:{type:'string',enum:['prepare','commit']},plan_id:str,
      library_id:int,collection_key:str,parent_key:str,metadata:{type:'object'},metadata_evidence:str}, ['job_id']],
  };
  const desc = {
    doclab_book_start:'接收本机 PDF/EPUB 并启动可续跑后台任务。默认 L3 核校、L1 分章导出归档。传当前 DSH_SESSION_ID 为 session_id 以接收阶段通知。不重跑已完成 OCR。返回 job_id。',
    doclab_job_status:'读取短任务状态；后台阶段无需频繁轮询。',
    doclab_review_read:'分批读取核校证据：outline 分批读，include_deep=true 可查看误降级的深层标题并按 key 提级；issues；blocks 含页码噪声可检索并续读长块；page 原页图；calibration 分段/观测。page_idx 为片内0基。',
    doclab_review_apply:'按 base_revision 演练或应用有 evidence 的 level/delete/add/anchor/anchor-clear。页码操作另需 scope_evidence。先 apply=false 检查，再 apply=true。改后重读；最后无 operations、complete=true、review_note、unresolved=[] 确认全部节点已检查。最多两轮修改。',
    doclab_book_finalize:'仅对 reviewed 版本导出，执行四闸门和全书源块覆盖检查；导出同时装订 EPUB（页锚、脚注、图片、表格保留）并做结构自检。失败禁止自动归档。',
    doclab_archive_book:'prepare 核实指定 Zotero 条目/分类并生成固定 plan_id；commit 写入后逐文件回读。归档包含 PDF/EPUB 原件、分节 Markdown、整书 EPUB、图片与元数据。完整书籍压缩包和 AI 核校报告不归档。新书须 metadata.title 与 metadata_evidence。未指定分类时只自动选择唯一名为书籍专著的分类。',
  };
  return Object.entries(props).map(([name,[properties,required]]) => ({
    name,description:desc[name],inputSchema:{type:'object',properties,required},
    run: a => new Promise((resolve,reject)=>{
      const c=spawn(python,[path.join(root,'workflow.py')],{cwd:root,windowsHide:true,
        env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
      let out='',err=''; const timer=setTimeout(()=>c.kill(),name==='doclab_archive_book'?650000:180000);
      c.stdout.on('data',d=>out+=d.toString('utf8')); c.stderr.on('data',d=>err+=d.toString('utf8'));
      c.on('error',e=>{clearTimeout(timer);reject(e)});
      c.on('close',()=>{clearTimeout(timer);try{resolve(JSON.parse(out))}catch(e){reject(new Error((err||out).slice(-1200)))}});
      c.stdin.end(JSON.stringify({tool:name,arguments:a}));
    })
  }));
};
