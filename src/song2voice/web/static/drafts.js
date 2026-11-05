'use strict';
// Each page owns a separate branch: a stale tab cannot overwrite another tab.
class EditorDraftStore {
  constructor({capture, report}) {
    this.capture=capture;this.report=report;this.client=crypto.randomUUID().replaceAll('-','');
    this.sequence=0;this.pending=new Map();this.localOK=new Map();this.ack=new Map();
    this.prefix='utaweave:editor:v1:';
    window.addEventListener('online',()=>this.flush());
    window.addEventListener('pagehide',()=>this.flush());
    document.addEventListener('visibilitychange',()=>{if(document.hidden)this.flush();});
    window.addEventListener('beforeunload',event=>{
      if([...this.pending.values()].some(d=>!this.localOK.get(d.job_id)&&this.ack.get(d.job_id)!==d.sequence)){
        event.preventDefault();event.returnValue='';
      }
    });
  }
  key(d){return this.prefix+d.job_id+':'+d.client_id;}
  durable(job){const draft=this.pending.get(job);return !!draft&&(this.localOK.get(job)||this.ack.get(job)===draft.sequence);}
  save() {
    const state=this.capture();if(!state)return null;
    const draft={...state,schema:1,client_id:this.client,sequence:++this.sequence,updated_at:Date.now()};
    this.pending.set(draft.job_id,draft);
    try{localStorage.setItem(this.key(draft),JSON.stringify(draft));this.localOK.set(draft.job_id,true);}
    catch(error){this.localOK.set(draft.job_id,false);}
    this.status(draft);clearTimeout(this.timer);this.timer=setTimeout(()=>this.flush(),800);return draft;
  }
  status(draft,error=''){
    const server=this.ack.get(draft.job_id)===draft.sequence, local=this.localOK.get(draft.job_id);
    this.report(draft.job_id,server?'已保存到服务器'+(local?'及本机':'（本机存储不可用）'):
      local?'本机已保存 · '+(error?'服务器暂未同步，自动重试':'等待同步到服务器'):
      '尚未安全保存 · '+(error||'本机存储不可用，正在尝试服务器')+'，请下载备份',!server&&!local);
  }
  async flush(){
    if(this.sending)return this.sending;
    clearTimeout(this.timer);
    this.sending=(async()=>{
      for(const [job,draft] of this.pending){
        if(this.ack.get(job)===draft.sequence)continue;
        try{
          const result=await api(`/api/jobs/${job}/draft`,draft);
          if(result.sequence<draft.sequence)throw Error('草稿未确认');
          this.ack.set(job,draft.sequence);
          this.status(this.pending.get(job));
        }catch(error){this.status(this.pending.get(job),error.message);}
      }
    })();
    try{await this.sending;}finally{
      this.sending=null;
      if([...this.pending].some(([job,d])=>this.ack.get(job)!==d.sequence))this.timer=setTimeout(()=>this.flush(),3000);
    }
  }
  async candidates(job){
    const found=new Map();
    try{
      for(let i=0;i<localStorage.length;i++){
        const key=localStorage.key(i);if(!key?.startsWith(this.prefix+job+':'))continue;
        try{const d=JSON.parse(localStorage.getItem(key));found.set(d.client_id,{...d,local:d});}catch(_){}
      }
    }catch(_){}
    let online=true;
    try{
      const items=await api(`/api/jobs/${job}/drafts`);
      for(const d of items){const old=found.get(d.client_id);if(!old||d.sequence>old.sequence)found.set(d.client_id,d);}
    }catch(_){online=false;}
    return {online,items:[...found.values()].sort((a,b)=>b.updated_at-a.updated_at)};
  }
  async read(job,item){return item.local||await api(`/api/jobs/${job}/drafts/${item.client_id}`);}
  download(draft=this.save()){
    if(!draft)throw Error('先打开需要备份的乐谱');
    const url=URL.createObjectURL(new Blob([JSON.stringify(draft,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=`utaweave-draft-${draft.job_id}-${Date.now()}.json`;a.click();
    setTimeout(()=>URL.revokeObjectURL(url),10000);
  }
}
