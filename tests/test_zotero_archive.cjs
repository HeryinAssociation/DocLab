// Execute the real plugin in an isolated application-API fixture; no Zotero writes.
const fs=require('fs'),vm=require('vm'),crypto=require('crypto'),assert=require('assert/strict'),path=require('path').win32;
async function main(){
 const files=new Map(),receipts=new Map(),items=new Map();let imports=0,seq=0;
 const root='C:\\Doclab\\Doclab-Inbox',jid='a'.repeat(24),job=path.join(root,'jobs',jid);
 const file1=path.join(job,'one.md'),file2=path.join(job,'00-目录.md');
 const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
 files.set(file1,'# 第一章\n正文');files.set(file2,'[第一章](one.md)');
 const cfg={token:'fixture-only',jobs_root:path.join(root,'jobs'),allowed_roots:[path.join(root,'jobs'),path.join(root,'inbox')]};
 const col={id:10,key:'BOOKS',name:'书籍专著'};
 function Item(type){this.itemType=type;this.id=++seq;this.key='KEY'+seq;this.data={};this.collections=[];this.deleted=false;items.set(this.key,this)}
 Item.prototype={setField(k,v){this.data[k]=v},getField(k){return this.data[k]||''},setCreators(){},addTag(){},
  setCollections(c){this.collections=c},getCollections(){return this.collections},async saveTx(){},
  getAttachments(){return [...items.values()].filter(x=>x.parentID===this.id).map(x=>x.id)},
  async getFilePathAsync(){return this.file}};
 const getid=id=>[...items.values()].find(x=>x.id===id);
 const fakefile=p=>({path:path.normalize(p),normalize(){this.path=path.normalize(this.path)},exists(){return files.has(this.path)},isFile(){return true}});
 const Zotero={initializationPromise:Promise.resolve(),version:'fixture-10',File:{pathToFile:fakefile,
  async getContentsFromURLAsync(){throw new Error('jar: resource has HTTP status 0')},
  async getResourceAsync(uri){assert.ok(uri.startsWith('jar:file:'));return JSON.stringify(cfg)}},Libraries:{userLibraryID:1,get:()=>({editable:true,filesEditable:true})},
  Collections:{getByLibrary:()=>[col],getByLibraryAndKeyAsync:async(l,k)=>k===col.key?col:null},
  Items:{getByLibraryAndKeyAsync:async(l,k)=>items.get(k),getAsync:async ids=>Array.isArray(ids)?ids.map(getid):getid(ids)},
  Item,Search:function(){this.addCondition=()=>{};this.search=async()=>[]},Server:{Endpoints:{}},logError(){},
  Attachments:{async importFromFile(o){imports++;const a=new Item('attachment');a.parentID=o.parentItemID;a.libraryID=1;
   a.file=path.join('C:\\Zotero\\storage',a.key,path.basename(o.file));files.set(a.file,files.get(o.file));return a}}
 };
 const IOUtils={readJSON:async p=>{if(!receipts.has(p))throw Error('absent');return structuredClone(receipts.get(p))},
  writeJSON:async(p,v)=>receipts.set(p,structuredClone(v)),readUTF8:async p=>files.get(p),writeUTF8:async(p,v)=>files.set(p,v),
  makeDirectory:async()=>{},copy:async(a,b)=>files.set(b,files.get(a))};
 const Components={interfaces:{nsIFileInputStream:{},nsICryptoHash:{}},classes:{
  '@mozilla.org/network/file-input-stream;1':{createInstance:()=>({init(f){this.file=f},close(){}})},
  '@mozilla.org/security/hash;1':{createInstance:()=>({SHA256:1,init(){},updateFromStream(s){this.text=files.get(s.file.path)},finish(){return crypto.createHash('sha256').update(this.text).digest('latin1')}})}
 }};
 const context=vm.createContext({Zotero,IOUtils,PathUtils:{join:path.join,parent:path.dirname},Components,console});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../zotero-plugin/bootstrap.js'),'utf8'),context);
 await context.startup({rootURI:'jar:file:///fixture.xpi!/'},1);
 const endpoint=new Zotero.Server.Endpoints['/doclab/archive']();
 const call=async(data,token='fixture-only')=>{const r=await endpoint.init({headers:{Authorization:'Bearer '+token},data});return {status:r[0],body:JSON.parse(r[2])}};
 assert.equal((await call({action:'health'},'wrong')).status,403);
 const target={library_id:1,collection_key:'BOOKS',parent_key:null,metadata:{title:'明确测试材料'},metadata_evidence:'fixture'};
 assert.equal((await call({action:'prepare',...target})).body.ok,true);
 const plan={job_id:jid,plan_id:'b'.repeat(64),target,files:[
  {path:file1,relative:'one.md',role:'file',sha256:hash(files.get(file1))},
  {path:file2,relative:'00-目录.md',role:'index',sha256:hash(files.get(file2))}],assets:[]};
 const bad=structuredClone(plan);bad.files[0].path='C:\\Other\\secret.md';files.set(bad.files[0].path,'x');
 assert.equal((await call({action:'commit',plan:bad})).body.ok,false);
 assert.equal(items.size,0,'path rejection occurs before a parent is created');
 const stale=structuredClone(plan);stale.files[0].sha256='0'.repeat(64);
 assert.equal((await call({action:'commit',plan:stale})).body.ok,false);
 assert.equal(items.size,0,'hash rejection occurs before a parent is created');
 const first=(await call({action:'commit',plan})).body;
 assert.equal(first.ok,true);assert.equal(first.verified,true);assert.equal(first.files.length,2);
 const idx=items.get(first.files.find(f=>f.relative==='00-目录.md').key);
 assert.ok(files.get(idx.file).includes('zotero://select/library/items/'));
 assert.equal(imports,2);
 const second=(await call({action:'commit',plan})).body;
 assert.equal(second.verified,true);assert.equal(imports,2,'repeat commit must not import twice');
 console.log('8 archive contract checks passed: auth, target, path, hash, parent, readback, links, idempotency.');
}
main().catch(e=>{console.error(e);process.exitCode=1});
