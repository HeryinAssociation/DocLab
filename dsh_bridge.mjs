import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const actionable=new Set(['needs_ai_review','failed','needs_review','completed']);
export async function scanJobs(ctx,jobs) {
  for(const e of await fs.readdir(jobs,{withFileTypes:true}).catch(()=>[])) {
    if(!e.isDirectory()||!/^[a-f0-9]{24}$/.test(e.name))continue;
    const dir=path.join(jobs,e.name), jobFile=path.join(dir,'job.json');
    const read=()=>fs.readFile(jobFile,'utf8').then(JSON.parse).catch(()=>null);
    const j=await read();
    if(!j?.owner_session||j.notifications_suspended)continue;
    const sent=await fs.readFile(path.join(dir,'notified.json'),'utf8').then(JSON.parse).catch(()=>({}));
    if((sent.event_seq||0)>=j.event_seq)continue;
    // Advance the cursor for silent stages too. Never replay earlier review events.
    if(!actionable.has(j.stage)) {
      await fs.writeFile(path.join(dir,'notified.json'),JSON.stringify({event_seq:j.event_seq,silent:true}));
      continue;
    }
    const latest=await read();
    if(!latest||latest.notifications_suspended||latest.event_seq!==j.event_seq||latest.stage!==j.stage)continue;
    const requestId=`doclab-${j.job_id}-${j.event_seq}`;
    await ctx.sessionController.prompt({sessionId:j.owner_session,requestId,mode:'queue',
      content:[{type:'text',text:`Doclab 任务有状态更新：job_id=${j.job_id}，event_seq=${j.event_seq}。队列通知可能延迟，请仅调用 doclab_job_status 读取当前短状态；以实时状态和用户最新暂停/取消要求为准。若该事件已过期且当前步骤已处理，静默结束，不重新核校、轮询或反复探测。确需继续时仅执行工作区已授权流程。`}],clientTimeZone:'Asia/Shanghai'},new AbortController().signal);
    await fs.writeFile(path.join(dir,'notified.json'),JSON.stringify({event_seq:j.event_seq,requestId}));
  }
}
export const name='doclab-stage-notifications';
export const inject=['sessionController','workspaceController','agentPresets'];
export function apply(ctx) {
  fs.writeFile(path.join(root,'Doclab-Inbox','config','bridge-status.local.json'),JSON.stringify({loaded:true,revision:'2026-10-17-notify-fix',at:new Date().toISOString()})).catch(()=>{});
  ctx.workspaceController.create({path:path.join(root,'Doclab-Inbox')})
    .catch(e=>ctx.logger.warn(`Doclab 工作区注册失败：${e.message}`));
  let busy=false;
  let rosterWritten=false;
  const scan=async()=>{
    if(busy)return;busy=true;
    try {
      if(!rosterWritten) {
        const roster=await ctx.agentPresets.list();
        await fs.writeFile(path.join(root,'Doclab-Inbox','config','preset-status.local.json'),JSON.stringify(roster.filter(x=>x.id==='doclab-intake')));
        rosterWritten=true;
      }
      const jobs=path.join(root,'Doclab-Inbox','jobs');
      await scanJobs(ctx,jobs);
    }catch(e){ctx.logger.warn(`Doclab 通知暂未送达：${e.message}`)}finally{busy=false}
  };
  const t=setInterval(scan,10000);t.unref();
  ctx.on('dispose',()=>clearInterval(t));
}
