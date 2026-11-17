/* Zotero-owned storage APIs only. No external SQLite access or arbitrary eval. */
var DoclabArchive;
async function startup(data, reason) {
  try {
    await startArchive(data, reason);
  } catch (error) {
    // Keep startup failures visible in Zotero's error console. Never log the config/token.
    Zotero.logError(new Error('Doclab Archive startup failed: ' + String(error.message || error)));
    throw error;
  }
}
async function startArchive(data, reason) {
  await Zotero.initializationPromise;
  const uri = data.rootURI || data.resourceURI.spec;
  // Installed XPIs have a jar: URI, which is not an HTTP channel (XHR status is 0).
  const cfg = JSON.parse(await Zotero.File.getResourceAsync(uri + 'bridge-config.json'));
  if (!cfg.token || !cfg.jobs_root || !Array.isArray(cfg.allowed_roots))
    throw new Error('Invalid bridge configuration');
  const Cc = Components.classes, Ci = Components.interfaces;
  const json = obj => [200, 'application/json', JSON.stringify(obj)];
  function checked(p) {
    const f = Zotero.File.pathToFile(p); f.normalize();
    const roots = cfg.allowed_roots.map(r => {const x=Zotero.File.pathToFile(r);x.normalize();return x.path.toLowerCase()});
    const path = f.path.toLowerCase();
    if(!roots.some(r => path.startsWith(r + '\\') || path.startsWith(r + '/'))) throw new Error('路径超出归档范围');
    if(!f.exists() || !f.isFile()) throw new Error('归档文件不存在');
    return f;
  }
  function sha(p) {
    const file=Zotero.File.pathToFile(p);
    const stream=Cc['@mozilla.org/network/file-input-stream;1'].createInstance(Ci.nsIFileInputStream);
    stream.init(file,1,0,0);
    try {
      const h=Cc['@mozilla.org/security/hash;1'].createInstance(Ci.nsICryptoHash);
      h.init(h.SHA256);h.updateFromStream(stream,0xffffffff);
      return Array.from(h.finish(false),c=>c.charCodeAt(0).toString(16).padStart(2,'0')).join('');
    } finally {stream.close()}
  }
  async function target(t) {
    const lib=t.library_id || Zotero.Libraries.userLibraryID;
    const library=Zotero.Libraries.get(lib);
    if(!library?.editable || !library.filesEditable)throw new Error('目标库不可写或不能存储附件');
    let col;
    if(t.collection_key) {
      col=await Zotero.Collections.getByLibraryAndKeyAsync(lib,t.collection_key);
      if(!col)throw new Error('目标分类不存在');
    } else {
      const cs=Zotero.Collections.getByLibrary(lib).filter(c=>c.name==='书籍专著');
      if(cs.length!==1)throw new Error('请明确 collection_key；“书籍专著”不是唯一分类');
      col=cs[0];
    }
    let parent;
    if(t.parent_key){
      parent=await Zotero.Items.getByLibraryAndKeyAsync(lib,t.parent_key);
      if(!parent || parent.deleted || parent.itemType!=='book')throw new Error('指定父条目不是有效专著');
      if(!parent.getCollections().includes(col.id))throw new Error('既有条目不在指定分类；不会擅自移动');
    }else{
      if(!t.metadata?.title || !t.metadata_evidence)throw new Error('缺少有依据的题名');
      const search=new Zotero.Search();search.libraryID=lib;
      search.addCondition('itemType','is','book');search.addCondition('title','contains',t.metadata.title);
      const ids=await search.search();
      const matches=(await Zotero.Items.getAsync(ids)).filter(x=>!x.deleted);
      if(matches.length)throw new Error('存在相似专著，请核实并指定 parent_key：'+matches.map(x=>x.key+' '+x.getField('title')).slice(0,8).join('；'));
    }
    return {library_id:lib,collection_key:col.key,parent_key:parent?.key || null,
      metadata:t.metadata||{},metadata_evidence:t.metadata_evidence||''};
  }
  function itemURI(lib,key){
    if(lib===Zotero.Libraries.userLibraryID)return 'zotero://select/library/items/'+key;
    const g=Zotero.Groups.getByLibraryID(lib);return 'zotero://select/groups/'+g.id+'/items/'+key;
  }
  async function commit(p) {
    if(!/^[a-f0-9]{24}$/.test(p.job_id)||!/^[a-f0-9]{64}$/.test(p.plan_id))throw new Error('非法计划身份');
    const receiptPath=PathUtils.join(cfg.jobs_root,p.job_id,'zotero-receipt.json');
    let rec=await IOUtils.readJSON(receiptPath).catch(()=>null);
    if(rec && rec.plan_id!==p.plan_id)throw new Error('已有不同归档计划，请核实已有附件');
    for(const f of [...p.files,...p.assets]){
      const ff=checked(f.path);if(sha(ff.path)!==f.sha256)throw new Error('文件哈希已变：'+f.relative);
    }
    let parent;
    if(rec?.parent_key) parent=await Zotero.Items.getByLibraryAndKeyAsync(rec.library_id,rec.parent_key);
    else if(p.target.parent_key) {
      await target(p.target);
      parent=await Zotero.Items.getByLibraryAndKeyAsync(p.target.library_id,p.target.parent_key);
    } else {
      // A marker on a new parent allows recovery if a crash precedes receipt writing.
      const marker='doclab-job:'+p.job_id;
      const s=new Zotero.Search();s.libraryID=p.target.library_id;s.addCondition('tag','is',marker);
      const found=await s.search();
      if(found.length===1)parent=await Zotero.Items.getAsync(found[0]);
      else if(found.length>1)throw new Error('任务父条目不唯一');
      else {
        await target(p.target);
        parent=new Zotero.Item('book');parent.libraryID=p.target.library_id;
        for(const k of ['title','publisher','date','ISBN','edition','language','place','series']){
          if(p.target.metadata[k])parent.setField(k,String(p.target.metadata[k]));
        }
        if(p.target.metadata.creators)parent.setCreators(p.target.metadata.creators);
        parent.addTag(marker);
        const col=await Zotero.Collections.getByLibraryAndKeyAsync(parent.libraryID,p.target.collection_key);
        parent.setCollections([col.id]);await parent.saveTx();
      }
    }
    if(!parent || parent.deleted || parent.itemType!=='book')throw new Error('恢复的父条目无效');
    const persist=async()=>IOUtils.writeJSON(receiptPath,rec,{tmpPath:receiptPath+'.tmp'});
    rec=rec||{plan_id:p.plan_id,job_id:p.job_id,library_id:parent.libraryID,parent_key:parent.key,files:[]};
    await persist();
    const all=await Zotero.Items.getAsync(parent.getAttachments());
    const byRel={};
    const ordered=[...p.files.filter(f=>f.role!=='index'),...p.files.filter(f=>f.role==='index')];
    for(const f of ordered){
      let source=f.path, expected=f.sha256;
      if(f.role==='index'){
        let text=await IOUtils.readUTF8(f.path);
        text=text.replace(/\]\(([^)]+)\)/g,(whole,link)=>{
          let rel;try{rel=decodeURIComponent(link)}catch{rel=link}
          return byRel[rel] ? ']('+itemURI(parent.libraryID,byRel[rel])+')' : whole;
        });
        source=PathUtils.join(cfg.jobs_root,p.job_id,'00-Zotero目录.md');
        await IOUtils.writeUTF8(source,text);expected=sha(source);
      }
      let entry=rec.files.find(x=>x.relative===f.relative);
      let att=entry?await Zotero.Items.getByLibraryAndKeyAsync(parent.libraryID,entry.key):null;
      if(!att){
        // Recover a file import that completed just before a process failure.
        for(const x of all){
          if(x.deleted)continue;
          const fp=await x.getFilePathAsync();
          if(fp && sha(fp)===expected){att=x;break}
        }
      }
      if(!att){
        const mime=f.relative.endsWith('.md')?'text/markdown':f.relative.endsWith('.json')?'application/json':
          f.relative.endsWith('.pdf')?'application/pdf':f.relative.endsWith('.epub')?'application/epub+zip':
          f.relative.endsWith('.zip')?'application/zip':'text/csv';
        att=await Zotero.Attachments.importFromFile({file:source,parentItemID:parent.id,title:f.relative,contentType:mime});
        all.push(att);
      }
      if(att.parentID!==parent.id)throw new Error('附件挂载父条目错误');
      const stored=await att.getFilePathAsync();
      if(!stored || sha(stored)!==expected)throw new Error('附件回读不一致：'+f.relative);
      if(f.relative.endsWith('.md')){
        for(const a of p.assets){
          if(!/^images\/[A-Za-z0-9._-]+$/.test(a.relative))throw new Error('资源相对路径不合法');
          const dst=PathUtils.join(PathUtils.parent(stored),...a.relative.split('/'));
          await IOUtils.makeDirectory(PathUtils.parent(dst),{ignoreExisting:true});
          await IOUtils.copy(a.path,dst,{noOverwrite:false});
          if(sha(dst)!==a.sha256)throw new Error('图片资源回读不一致');
        }
      }
      byRel[f.relative]=att.key;
      const value={relative:f.relative,key:att.key,source_sha256:f.sha256,stored_sha256:expected};
      if(entry)Object.assign(entry,value);else rec.files.push(value);
      await persist();
    }
    rec.ok=true;rec.verified=true;rec.item_uri=itemURI(parent.libraryID,parent.key);
    rec.resource_sync_verified=false;rec.note='本机逐附件和图片哈希已回读；跨设备同步尚未验证';
    await persist();return rec;
  }
  let serial=Promise.resolve();
  const handler=function(){};
  handler.prototype={supportedMethods:['POST'],supportedDataTypes:['application/json'],permitBookmarklet:false,
    init:async function(req){
      if((req.headers.Authorization||req.headers.authorization)!=='Bearer '+cfg.token)
        return [403,'application/json',JSON.stringify({ok:false,message:'Unauthorized'})];
      const run=async()=>{
        try {
          const body=typeof req.data==='string'?JSON.parse(req.data):req.data;
          if(body.action==='health')return json({ok:true,version:'0.1.2',zotero_version:Zotero.version});
          if(body.action==='prepare')return json({ok:true,target:await target(body)});
          if(body.action==='commit')return json(await commit(body.plan));
          return json({ok:false,message:'未知归档动作'});
        }catch(e){Zotero.logError(e);return json({ok:false,message:String(e.message||e)})}
      };
      const result=serial.then(run,run);serial=result.then(()=>{},()=>{});return result;
    }};
  Zotero.Server.Endpoints['/doclab/archive']=handler;
  DoclabArchive={handler};
}
function shutdown(data,reason){if(DoclabArchive && Zotero.Server.Endpoints['/doclab/archive']===DoclabArchive.handler)delete Zotero.Server.Endpoints['/doclab/archive'];DoclabArchive=null;}
function install(){}
function uninstall(){}
