import {scanJobs} from '../dsh_bridge.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=await fs.mkdtemp(path.join(os.tmpdir(),'doclab-notify-'));
try {
  const id='a'.repeat(24),dir=path.join(root,id);await fs.mkdir(dir);
  const j={job_id:id,owner_session:'fixture',stage:'validated',event_seq:6};
  const write=()=>fs.writeFile(path.join(dir,'job.json'),JSON.stringify(j));
  await write();await fs.writeFile(path.join(dir,'notified.json'),JSON.stringify({event_seq:4}));
  const calls=[],ctx={sessionController:{prompt:async p=>calls.push(p)}};
  await scanJobs(ctx,root);
  assert.equal(calls.length,0);
  assert.equal(JSON.parse(await fs.readFile(path.join(dir,'notified.json'),'utf8')).event_seq,6);
  j.stage='needs_review';j.event_seq=7;j.notifications_suspended=true;await write();
  await scanJobs(ctx,root);assert.equal(calls.length,0);
  j.notifications_suspended=false;await write();await scanJobs(ctx,root);await scanJobs(ctx,root);
  assert.equal(calls.length,1);assert.ok(calls[0].content[0].text.includes('event_seq=7'));
  assert.ok(!calls[0].content[0].text.includes('stage=needs_review'));
  console.log('Notification checks passed: silent stage cursor, suspension, deduplication, stale-event wording.');
} finally { await fs.rm(root,{recursive:true,force:true}); }
