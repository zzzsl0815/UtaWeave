'use strict';
const $ = id => document.getElementById(id);
let mode = 'song', uploads = {}, current = null, prepared = null, labels = [], loadedJob = null, busy = false;
let audioContext = null, oscillators = [], pollTimer, melodyTimer;
let phraseDrafts = new Map(), phraseJob = null, pendingAlignment = null, alignmentSubmitting=false;
let readingInputMode='romaji', readingEditors=[];
let restoringDraft=false, draftChoices=[], pollSerial=0, draftLoadSerial=0, deferredRevision=null;
const draftStore=new EditorDraftStore({capture:captureDraft,report:(job,text,unsafe)=>{
  if(job===current){$('draft-status').textContent=text;$('draft-status').classList.toggle('reading-error',unsafe);}
}});
const stageNames = {queued:'排队中',input:'读取输入',lyrics:'转换读音',separate:'分离人声',annotate:'自动转谱',review:'等待核对乐谱',voice:'选择音色参考',score:'检查乐谱',reference:'准备音色参考',render:'生成演唱',complete:'生成完成',error:'任务失败',stopped:'任务已停止'};
const statusNames = {queued:'排队中',running:'进行中',awaiting_score:'待核对',awaiting_voice:'待选音色',complete:'已完成',failed:'失败',cancelled:'已停止'};
function notice(message) {
  $('notice').textContent=message||'';$('notice').hidden=!message;
  if(message){const close=document.createElement('button');close.textContent='×';close.className='notice-close';close.setAttribute('aria-label','关闭提示');close.onclick=()=>notice('');$('notice').append(close);}
}
async function api(path, data) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(path,{signal:controller.signal,cache:'no-store',...(data===undefined?{}:
      {method:'POST',headers:{'Content-Type':'application/json','X-UtaWeave':'local'},body:JSON.stringify(data)})});
    const result=await response.json();if(!response.ok)throw Error(result.error||`请求失败 (${response.status})`);return result;
  }catch(error){if(error.name==='AbortError')throw Error('请求超时。请检查连接；操作可能已被后台接收，请先查看任务状态。');throw error;}
  finally{clearTimeout(timer);}
}
async function action(fn, button) {
  notice(''); if (button) button.disabled = true;
  try { await fn(); } catch (error) { notice(error.message); }
  finally { if (button) button.disabled = false; }
}
function setMode(value) {
  mode = value;
  document.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.mode === mode)));
  $('song-input').hidden = mode !== 'song'; $('midi-input').hidden = mode === 'song';
  $('embedded-input').hidden=mode!=='midi_lyrics';$('lyrics-section').hidden=mode==='midi_lyrics';
  $('prepare').textContent=mode==='midi_lyrics'?'读取 MIDI，选择音色 →':'准备乐谱 →';
  document.querySelector('[data-step="2"]').innerHTML=mode==='midi_lyrics'?'<b>02</b> 读取歌词':'<b>02</b> 核对乐谱';
  $('task-stages').children[1].textContent=mode==='midi_lyrics'?'读取内嵌歌词':'核对乐谱与歌词';
  const same = $('reference-mode').querySelector('[value="same_song"]'); same.hidden = mode !== 'song';
  if (mode !== 'song' && $('reference-mode').value === 'same_song') $('reference-mode').value = 'netease';
  updateReference();
}
document.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click',() => setMode(b.dataset.mode)));
for (const key of ['source','midi','accompaniment','reference']) {
  $(`${key}-file`).addEventListener('change',() => action(async () => {
    const file = $(`${key}-file`).files[0]; delete uploads[key]; if (!file) return;
    $(`${key}-file-info`).textContent = '正在上传…';
    const response = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'X-UtaWeave':'local'},body:file});
    const data = await response.json(); if (!response.ok) throw Error(data.error);
    uploads[key] = data.id; $(`${key}-file-info`).textContent = `${file.name} · ${(file.size/1024/1024).toFixed(1)} MiB · 已上传`;
    if (key === 'source') $('source-url').value = '';
    if (key === 'midi') {
      $('midi-track').replaceChildren();
      $('midi-lyric-track').replaceChildren(new Option('自动选择（旋律轨优先）',''));
      (data.midi.lyric_tracks||[]).forEach(track=>$('midi-lyric-track').append(new Option(`${track.name} · ${track.events.filter(e=>e.text.trim()).length} 条歌词`,track.index)));
      data.midi.tracks.forEach(track => {
        const option = document.createElement('option'); option.value = track.index;
        option.textContent = `${track.name} · ${track.count} 个音符 · ${track.notes.filter(n=>n.lyric).length} 条同轨歌词 · ${track.seconds.toFixed(1)} 秒${track.polyphonic?' · 存在复音，不能作为旋律轨':''}`;
        option.disabled = track.polyphonic; $('midi-track').append(option);
      });
      const first = data.midi.tracks.find(t => !t.polyphonic); if (first) $('midi-track').value = first.index;
      else throw Error('没有可用的单声部旋律轨，请在 MIDI 编辑器中分离主旋律。');
    }
    if (key === 'reference') { $('reference-local-player').src = `/uploads/${data.id}`; $('reference-local-player').hidden = false;saveEditorDraft(); }
  }));
}
$('fetch-lyrics').addEventListener('click',() => action(async () => {
  const url = $('lyrics-url').value.trim() || $('source-url').value.trim();
  if (!url) throw Error('先填写歌词来源的网易云歌曲链接。');
  const data = await api('/api/lyrics',{url});
  if (!data.original) throw Error('没有获取到歌词，请自行粘贴。');
  $('lyrics-text').value = data.original;
},$('fetch-lyrics')));
$('prepare').addEventListener('click',() => action(async () => {
  saveEditorDraft();
  const request = {mode:mode==='midi_lyrics'?'midi':mode,language:$('chinese-mode').checked?'zh':'ja',lyrics_text:$('lyrics-text').value,lyrics_url:$('lyrics-url').value.trim(),bpm:Number($('bpm').value)};
  if (mode !== 'song') {
    if (!uploads.midi) throw Error('请先上传 MIDI。');
    if(mode==='midi_lyrics')Object.assign(request,{lyric_source:'embedded',lyric_track:$('midi-lyric-track').value===''?null:Number($('midi-lyric-track').value),lyrics_text:'',lyrics_url:''});
    Object.assign(request,{midi_asset:uploads.midi,track:Number($('midi-track').value),accompaniment_asset:uploads.accompaniment || null});
  } else {
    request.source_url = $('source-url').value.trim(); request.source_asset = uploads.source;
    if (!request.source_url && !request.source_asset) throw Error('请输入原曲链接或上传音频。');
  }
  const job = await api('/api/jobs',request); current = job.id; loadedJob = null; prepared = null;
  for(const id of ['review-panel','embedded-panel','voice-panel','result-panel'])$(id).hidden=true;
  location.hash = `job=${current}`; await poll();
},$('prepare')));
function updateReference() {
  const value = $('reference-mode').value;
  $('reference-url-field').hidden = value !== 'netease';
  $('reference-upload').hidden = !['audio','clip'].includes(value);
  $('reference-local-player').hidden = !['audio','clip'].includes(value) || !uploads.reference;
}
$('reference-mode').addEventListener('change',updateReference);
function format(seconds) { return `${Math.floor(seconds/60)}:${(seconds%60).toFixed(2).padStart(5,'0')}`; }
function pitchName(p) { return p ? ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][p%12] + (Math.floor(p/12)-1) : '未知'; }
const isChinese=()=>prepared?.language==='zh';
const isEmbedded=()=>prepared?.mode==='midi'&&prepared?.lyric_source==='embedded'&&prepared?.review_required===false;
function showRomaji(node,reading,needsReadingHint='含汉字时，请应用本句以更新读音') {
  node.hidden=isChinese();if(node.hidden){node.textContent='';return;}
  const result=romanizeReading(reading);
  node.textContent=`罗马音：${result.text || '—'}${result.needsReading?' · '+needsReadingHint:''}`;
}
function readingInputHelp(){
  $('reading-input-help').textContent=readingInputMode==='romaji'?"直接输入 kimi、gakkou 等罗马音，可按拍音加空格。英文用 {tick tack} 包住。n' 明确表示 ん，xtsu 表示小 っ；单个音符仍可填 SP 或 -。下方预览是实际使用的读音。":"按原方式输入日文或英文；字母按英文处理。需要用罗马音输入日语时，请切换上方模式。";
}
function createReadingEditor(input,preview,value,{kind,onEdit,onPreview,state}={}){
  const editor={kind,id:input.id,canonical:value,error:'',mode:readingInputMode};
  const render=()=>{
    input.setCustomValidity(editor.error);input.setAttribute('aria-invalid',String(!!editor.error));
    preview.textContent=editor.error || `将使用：${editor.canonical || '—'}`;preview.classList.toggle('reading-error',!!editor.error);
    onPreview?.(editor.canonical);
  };
  editor.setMode=mode=>{
    editor.mode=isChinese()?'native':mode;input.value=editor.mode==='romaji'?readingToRomajiInput(editor.canonical):editor.canonical;render();
  };
  editor.setCanonical=value=>{editor.canonical=value;editor.error='';editor.setMode(readingInputMode);};
  editor.snapshot=()=>({raw:input.value,mode:editor.mode,error:editor.error});
  editor.value=()=>{if(editor.error)throw Error(editor.error);return editor.canonical;};
  input.maxLength=kind==='note'?256:6000;input.spellcheck=false;input.autocapitalize='off';
  const convert=()=>{
    try{
      const value=editor.mode==='romaji'?romajiToReading(input.value):input.value.trim();
      if(value.length>(kind==='note'?64:2000))throw Error('转换后的读音太长，请缩短输入。');
      editor.canonical=value;editor.error='';
    }catch(error){editor.error=error.message;}
    render();onEdit?.(editor);
  };
  input.addEventListener('input',convert);editor.setMode(readingInputMode);
  if(state&&(!isChinese()||state.mode==='native')){editor.mode=state.mode;input.value=state.raw;editor.error=state.error || '';render();}
  readingEditors.push(editor);return editor;
}
function requireNoteReadings(){
  const invalid=readingEditors.find(e=>e.kind==='note'&&e.error);if(invalid){focusNote(Number(invalid.id.slice(5)));throw Error('有音符的罗马音拼写尚未完成：'+invalid.error);}
}
$('reading-input-mode').onchange=()=>{
  const value=$('reading-input-mode').value;
  if(readingEditors.some(e=>e.error)){notice('请先修正标红的拼写，再切换输入方式。');$('reading-input-mode').value=readingInputMode;return;}
  readingInputMode=value;readingEditors.forEach(e=>e.setMode(value));
  for(const draft of phraseDrafts.values())delete draft.inputState;
  readingInputHelp();
  saveEditorDraft();
};
readingInputHelp();
function mappingStatus() {
  const missing = labels.filter(v => !v.trim()).length;
  const aligned=prepared?.alignment;
  $('mapping-status').textContent = `${labels.length-missing} / ${labels.length} 音符已填写${missing ? `，还有 ${missing} 个空项` : ''}${prepared?.unmatched_moras ? `；还有 ${prepared.unmatched_moras} 个读音单元未分配，请修正对应，不要遗漏。` : ''}`;
  const annotated=value=>isChinese()?value:`${value} (${romanizeReading(value).text})`;
  const extra=aligned ? aligned.phrases.flatMap(p=>p.unassigned.map(i=>`第 ${p.id+1} 句：${annotated(p.units[i].text)}`)) : (prepared?.reading?.moras?.slice(prepared.notes.length) || []).map(annotated);
  $('unassigned-lyrics').hidden=!extra.length;
  $('unassigned-lyrics').textContent=extra.length ? `未分配的读音（${extra.length} 个单元）：\n${extra.join(' ')}\n\n请修正分句范围或读音并重新分配。` : '';

}
function drawRoll(selected = -1) {
  if (!prepared) return;
  const canvas = $('piano-roll'), width = canvas.clientWidth || 600, height = 180, ratio = window.devicePixelRatio || 1;
  canvas.width = width*ratio; canvas.height = height*ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio,ratio); ctx.clearRect(0,0,width,height);
  const notes = prepared.notes, pitches = notes.map(n=>n.pitch).filter(p=>p>0), min = Math.min(...pitches)-2, max = Math.max(...pitches)+2;
  const duration = notes[notes.length-1].end || 1;
  for (let p = min; p <= max; p++) {
    const y = 12+(max-p)/(max-min)*140; ctx.strokeStyle = '#e5e9df'; ctx.beginPath(); ctx.moveTo(30,y); ctx.lineTo(width,y); ctx.stroke();
    if (p%12===0) {ctx.fillStyle='#7c887b';ctx.font='10px system-ui';ctx.fillText(pitchName(p),2,y+3);}
  }
  notes.forEach((n,i) => {
    const x = 32+n.start/duration*(width-42), y = 12+(max-n.pitch)/(max-min)*140;
    ctx.fillStyle = i===selected?'#b27a46':'#527762';ctx.fillRect(x,y-4,Math.max(2,(n.end-n.start)/duration*(width-42)-1),7);
  });
  ctx.fillStyle = '#7c887b';ctx.font='10px system-ui';ctx.fillText('0:00',32,173);ctx.fillText(format(duration),width-50,173);
}
$('piano-roll').addEventListener('click',e => {
  if (!prepared) return;
  const rect = e.currentTarget.getBoundingClientRect(), total = prepared.notes.at(-1).end, time = (e.clientX-rect.left-32)/(rect.width-42)*total;
  let best=0;prepared.notes.forEach((n,i)=>{if(Math.abs(n.start-time)<Math.abs(prepared.notes[best].start-time))best=i;});
  focusNote(best);
});
window.addEventListener('resize',()=>drawRoll());
function showPrepared(data, saved, draft=null) {
  readingEditors=[];
  prepared=data;labels=[...(draft?.labels||(saved&&(!data.revision||saved.prepared_revision===data.revision)?saved.labels:data.labels))];setMode(isEmbedded()?'midi_lyrics':data.mode);
  if(isEmbedded()){labels=[...data.labels];phraseDrafts.clear();}
  $('embedded-panel').hidden=!isEmbedded();
  $('segment-panel').hidden=data.mode!=='midi';$('segment-result').hidden=true;
  $('connect-short-rests').checked=draft?(draft.fields?.['connect-short-rests']??$('connect-short-rests').checked):Boolean(saved?.connect_short_rests);
  if(draft){readingInputMode=draft.reading_mode;phraseJob=current;phraseDrafts=new Map(draft.phrase_drafts);}
  if(isChinese()||isEmbedded())readingInputMode='native';
  $('chinese-mode').checked=isChinese();$('reading-mode-panel').hidden=isChinese();$('romaji-help').hidden=isChinese();
  $('reading-input-mode').value=readingInputMode;readingInputHelp();
  $('input-panel').hidden=true;$('review-panel').hidden=isEmbedded();$('voice-panel').hidden=false;
  $('score-summary').replaceChildren();
  for (const text of [isChinese()?'中文 · 原版 VR（无 LoRA）':'日语 · VR + 日语 LoRA',`${data.notes.length} 个${data.mode==='midi'?'音符':'歌词单元'}`,`${data.bpm.toFixed(1)} BPM`,`${data.notes.at(-1).end.toFixed(1)} 秒`]) {
    const span=document.createElement('span');span.textContent=text;$('score-summary').append(span);
  }
  $('score-warning').textContent=data.warnings.join(' ');
  if(isEmbedded()){
    $('embedded-summary').replaceChildren(...[...$('score-summary').children].map(n=>n.cloneNode(true)));
    $('embedded-summary').append(element('span',`${data.segment_preview?.chunk_count||0} 段 · 每段不超过 8 秒`));
    $('embedded-warning').textContent=data.warnings.join(' ');
    $('embedded-lyrics').textContent=data.lyrics.original;
  }
  $('original-lyrics').textContent=data.lyrics.original || '没有独立歌词原文';$('auto-reading').textContent=data.reading?.reading || '';
  showRomaji($('auto-romaji'),data.reading?.reading || '');$('auto-romaji').hidden=isChinese()||!data.reading?.reading;
  $('mapping-hint').textContent=isChinese()?(data.mode==='midi'?'直接填写汉字或英文单词；一个音符可填多个字。同一字跨连续音符时，后续填 -；不演唱填 SP。多音字仍需试听核对。':'直接修正汉字或英文歌词；保留原有音符及时间。'):data.mode==='midi'?'每行对应一个音符，填写日语假名或英文单词。一个音符可填写多个拍音；同一字跨多个连续音符时，后续音符填 -。不演唱的音符填 SP。MIDI 中的原有休止自动保留。':'每行对应自动标注的一个歌词单元，可修改假名读音。此处保持音符及时间不变；如需修改旋律，可使用 MIDI 入口。';
  $('confirmed').checked=false;$('export-midi').hidden=data.mode!=='midi';$('note-rows').replaceChildren();
  data.notes.forEach((note,i)=>{
    const row=document.createElement('tr');if(!labels[i])row.classList.add('missing');
    for(const text of [i+1,format(note.start),pitchName(note.pitch),(note.end-note.start).toFixed(3)+' s']){
      const cell=document.createElement('td');cell.textContent=text;row.append(cell);
    }
    const cell=document.createElement('td'),input=document.createElement('input');input.id=`note-${i}`;input.value=labels[i];input.maxLength=64;input.setAttribute('aria-label',`第 ${i+1} 个音符的歌词读音`);
    const romaji=element('div',undefined,'romaji-text note-romaji');romaji.id=`note-romaji-${i}`;
    const preview=element('div',undefined,'reading-preview note-preview');preview.id=`note-preview-${i}`;input.setAttribute('aria-describedby',`${preview.id} ${romaji.id}`);
    createReadingEditor(input,preview,labels[i],{kind:'note',state:draft?.note_states?.[input.id],onPreview:value=>showRomaji(romaji,value,'请填写假名读音'),onEdit:editor=>{labels[i]=editor.canonical;row.classList.toggle('missing',!labels[i]||!!editor.error);$('confirmed').checked=false;mappingStatus();}});
    input.addEventListener('focus',()=>{document.querySelectorAll('#note-rows tr').forEach(r=>r.classList.remove('selected'));row.classList.add('selected');drawRoll(i);});
    input.addEventListener('keydown',e=>{if(e.key==='Enter'){$(`note-${i+1}`)?.focus();e.preventDefault();}});
    cell.append(input,preview,romaji);row.append(cell);$('note-rows').append(row);
  });
  if(saved){$('seed-steps').value=saved.steps;$('seed-cfg').value=saved.cfg;$('reference-mode').value=saved.reference.mode;$('reference-url').value=saved.reference.url||'';uploads.reference=saved.reference.asset;if(uploads.reference)$('reference-local-player').src=`/uploads/${uploads.reference}`;updateReference();}
  showPhrases();drawRoll();mappingStatus();
  if(draft){
    for(const [id,value] of Object.entries(draft.fields||{})){
      const node=$(id);if(!node||node.type==='file'||!node.closest('#review-panel,#voice-panel'))continue;
      if(node.type==='checkbox')node.checked=Boolean(value);else node.value=String(value);
    }
    uploads.reference=draft.reference_asset||undefined;
    if(uploads.reference)$('reference-local-player').src=`/uploads/${uploads.reference}`;
    updateReference();
    filterPhrases();
    for(const id of draft.open_phrases||[]){const row=$(`phrase-${id}`);if(row){row.open=true;row.hidden=false;}}
    $('confirmed').checked=false;
  }
  if(isEmbedded()){phraseDrafts.clear();labels=[...data.labels];}
  updateDraftMarkers();
}
$('embedded-new-input').onclick=()=>$('new-input').click();
$('new-input').addEventListener('click',()=>{$('input-panel').hidden=false;$('input-panel').scrollIntoView({behavior:'smooth'});});
$('export-midi').onclick=()=>action(async()=>{requireNoteReadings();saveEditorDraft();const data=await api(`/api/jobs/${current}/midi`,{labels,prepared_revision:prepared.revision});const a=document.createElement('a');a.href=data.url;a.download='lyrics.mid';a.click();if(phraseDrafts.size)notice(`已按当前逐音符歌词导出。${phraseDrafts.size} 句整句读音草稿尚未应用，不影响已手动调整的音符。`);},$('export-midi'));
$('preview-segments').onclick=()=>action(async()=>{
  requireNoteReadings();saveEditorDraft();
  const job=current,revision=prepared.revision,input=JSON.stringify(labels),connect=$('connect-short-rests').checked;
  const data=await api(`/api/jobs/${job}/segments`,{labels,prepared_revision:revision,connect_short_rests:connect});
  if(current!==job||prepared.revision!==revision||JSON.stringify(labels)!==input||$('connect-short-rests').checked!==connect)throw Error('读音、乐谱或休止设置已变化，请重新预览切分。');
  $('segment-result').hidden=false;$('segment-rows').replaceChildren();
  $('segment-summary').textContent=`${data.chunk_count} 段 · 最长演唱跨度 ${data.longest_content_seconds.toFixed(2)} 秒 · 含首尾留白的模型条件最长 ${data.longest_condition_seconds.toFixed(2)} 秒 · ${data.short_chunk_ids.length} 段不足 2 秒 · ${data.connected_short_rests||0} 处句内短休止转为前音延续`;
  for(const c of data.chunks){
    const row=element('tr');for(const text of [Number(c.id.slice(6))+1,`${format(c.content_start)}–${format(c.content_end)}`,c.content_seconds.toFixed(2)+' s',c.estimated_duration.toFixed(2)+' s',c.lyric_phrase_ids.map(i=>i+1).join('、')||'按音符停顿'])row.append(element('td',text));
    const reading=element('td',c.kana),romaji=element('span',undefined,'romaji-text');showRomaji(romaji,c.kana);reading.append(romaji);row.append(reading);$('segment-rows').append(row);
  }
  if(phraseDrafts.size)notice('预览使用已应用的分句范围和当前逐音符歌词；尚未应用的整句草稿未参与本次切分。');
},$('preview-segments'));
$('render').addEventListener('click',()=>action(async()=>{
  if(!isEmbedded())requireNoteReadings();
  if(phraseDrafts.size){focusPendingPhrase();throw Error('有整句读音草稿尚未应用，请检查标记的句子。备份状态见“保存草稿”区域；应用本句会保留手动修改的音符。');}
  if(!isEmbedded()&&!$('confirmed').checked)throw Error('请核对歌词和音符对应，并勾选确认。');
  if(labels.some(x=>!x.trim()))throw Error('请补齐空白音符；延音填 -，不演唱的音符填 SP。');
  if(prepared.alignment?.unassigned_units>0)throw Error('仍有整句读音未分配，请先按句修正并重新分配。');
  if(prepared.unmatched_moras>0&&labels.every((x,i)=>x===prepared.labels[i]))throw Error(`还有 ${prepared.unmatched_moras} 个拍音未分配，请先修改歌词与音符对应，再生成。`);
  const reference={mode:$('reference-mode').value,asset:uploads.reference,url:$('reference-url').value.trim()};
  if(['clip','audio'].includes(reference.mode)&&!reference.asset)throw Error('请上传音色参考。');
  if(reference.mode==='netease'&&!reference.url)throw Error('请填写目标音色歌曲链接。');
  await api(`/api/jobs/${current}/render`,{labels,prepared_revision:prepared.revision,confirmed:isEmbedded()?false:true,reference,steps:Number($('seed-steps').value),cfg:Number($('seed-cfg').value),connect_short_rests:prepared.mode==='midi'&&$('connect-short-rests').checked});
  $('result-panel').hidden=true;await poll();
},$('render')));
function updateTask(state) {
  busy=alignmentSubmitting||['queued','running'].includes(state.status);document.querySelector('aside').classList.toggle('busy',busy);
  $('task-title').textContent=stageNames[state.stage]||state.status;
  let message=state.message;
  if(state.pipeline_stages){const active=Object.entries(state.pipeline_stages).find(([,v])=>v==='running');if(active)message+=` · ${active[0]}`;}
  if(state.chunks?.total)message+=` · ${state.chunks.completed}/${state.chunks.total} 句`;
  $('task-message').textContent=message;$('task-elapsed').textContent=`任务 ${current.slice(0,8)} · 已经过 ${Math.floor(((state.completed_at||Date.now()/1000)-state.created_at)/60)} 分钟`;
  $('cancel').hidden=!busy;$('retry').hidden=!['failed','cancelled'].includes(state.status)||!!prepared;
  $('view-log').hidden=false;$('prepare').disabled=busy;$('render').disabled=busy;
  document.querySelectorAll('[data-align],#phrase-list input,#phrase-list textarea,#note-rows input,#reading-input-mode').forEach(e=>e.disabled=busy);
  if(state.status!=='complete')$('result-panel').hidden=true;
  const step=state.status==='complete'?4:['voice','reference','render'].includes(state.stage)?3:prepared?2:1;
  document.querySelectorAll('[data-step]').forEach(e=>e.classList.toggle('active',Number(e.dataset.step)===step));
  const index=state.status==='complete'?5:state.stage==='render'?3:['voice','reference'].includes(state.stage)?2:prepared?1:0;
  [...$('task-stages').children].forEach((li,i)=>{li.className=i<index?'done':i===index?'current':'';});
  $('progress-track').firstElementChild.style.width=`${(index+.3)/5.3*100}%`;
  if(state.status==='failed')notice(state.error || state.message);
}
async function poll() {
  clearTimeout(pollTimer);
  const serial=++pollSerial,job=current;
  try {
    if(current){
      const data=await api(`/api/jobs/${current}`);
      if(serial!==pollSerial||job!==current)return;
      const revision=current+':'+(data.prepared?.revision||'');
      if(data.prepared&&loadedJob!==revision){
        const old=prepared&&loadedJob?.startsWith(current+':')?captureDraft():null;
        const edited=pendingAlignment?.job===current&&pendingAlignment.revision!==data.prepared.revision?pendingAlignment.phrase_id:null;
        if(old&&old.prepared_revision!==data.prepared.revision&&!pendingAlignment&&hasEditorChanges()){
          if(deferredRevision!==revision){deferredRevision=revision;saveEditorDraft();notice('服务器上的乐谱已更新。当前编辑已保留并备份，请下载备份后重新打开任务，使用“恢复其他草稿”合并修改。');}
          updateTask(data.status);return;
        }
        if(old)saveEditorDraft();
        if(edited!=null)phraseDrafts.delete(edited);
        showPrepared(data.prepared,data.render_request);loadedJob=revision;deferredRevision=null;
        if(edited!=null&&$(`phrase-${edited}`)){$(`phrase-${edited}`).hidden=false;$(`phrase-${edited}`).open=true;}
        pendingAlignment=null;
        if(old){saveEditorDraft();}else{loadEditorDrafts().catch(error=>notice('草稿恢复失败：'+error.message));}
      }
      if(['failed','cancelled'].includes(data.status.status))pendingAlignment=null;
      updateTask(data.status);
      if(data.reference && $('selected-reference').dataset.key!==current+'/'+data.reference.file){
        const holder=$('selected-reference');holder.replaceChildren();holder.dataset.key=current+'/'+data.reference.file;
        const label=document.createElement('p');label.className='hint';label.textContent=`本次目标参考 · ${data.reference.seconds.toFixed(1)} 秒${data.reference.score!=null?' · SingMOS '+Number(data.reference.score).toFixed(2):' · 手动片段'}`;
        const audio=document.createElement('audio');audio.controls=true;audio.preload='metadata';audio.src=`/files/${current}/${data.reference.file}`;holder.append(label,audio);
      }
      if(data.status.status==='complete'){
        const url=`/files/${current}/${data.status.report}`;$('result-panel').hidden=false;$('full-report').href=url;
        if($('result-frame').getAttribute('src')!==url)$('result-frame').src=url;
        $('score-download').href=`/files/${current}/score.vr.json`;$('midi-download').hidden=prepared.mode!=='midi';$('midi-download').href=`/files/${current}/lyrics.mid`;
      }
    }
    $('connection').textContent='本地服务已连接';
  } catch(error){$('connection').textContent='连接或页面更新失败：'+error.message;}
  finally{if(serial===pollSerial)pollTimer=setTimeout(poll,2500);}
}
async function history(){
  try{const jobs=await api('/api/jobs');$('history-list').replaceChildren();
    if(!jobs.length){$('history-list').textContent='还没有任务';return;}
    jobs.forEach(job=>{const button=document.createElement('button'),small=document.createElement('small');button.textContent=`${statusNames[job.status]||job.status} · ${job.id.slice(0,8)}`;small.textContent=new Date(job.created_at*1000).toLocaleString();button.append(small);button.onclick=()=>{if(job.id===current)return;saveEditorDraft();current=job.id;loadedJob=null;prepared=null;phraseDrafts.clear();$('review-panel').hidden=true;$('embedded-panel').hidden=true;$('voice-panel').hidden=true;$('result-panel').hidden=true;location.hash=`job=${current}`;poll();};$('history-list').append(button);});
  }catch(_){};
}
$('cancel').onclick=()=>action(async()=>{await api(`/api/jobs/${current}/cancel`,{});await poll();},$('cancel'));
$('retry').onclick=()=>action(async()=>{await api(`/api/jobs/${current}/retry`,{});await poll();},$('retry'));
$('view-log').onclick=()=>action(async()=>{const data=await api(`/api/jobs/${current}/log`);$('task-log').textContent=data.text;$('task-log').hidden=false;});
function focusNote(index){$('note-editor').open=true;const input=$(`note-${index}`);input?.scrollIntoView({block:'nearest'});input?.focus();drawRoll(index);}
function stopMelody(){clearTimeout(melodyTimer);oscillators.forEach(o=>{try{o.stop();}catch(_){}});oscillators=[];$('melody-play').textContent='试听旋律';}
function playNotes(notes){
  stopMelody();if(!notes.length)return;
  audioContext ||= new AudioContext();audioContext.resume();const start=audioContext.currentTime+.1,offset=notes[0].start;
  notes.filter(n=>n.pitch>0&&n.start-offset<30).forEach(n=>{const osc=audioContext.createOscillator(),gain=audioContext.createGain();osc.frequency.value=440*Math.pow(2,(n.pitch-69)/12);gain.gain.setValueAtTime(0,start+n.start-offset);gain.gain.linearRampToValueAtTime(.07,start+n.start-offset+.01);gain.gain.setValueAtTime(.07,start+Math.max(n.start+.01,n.end-.03)-offset);gain.gain.linearRampToValueAtTime(0,start+n.end-offset);osc.connect(gain).connect(audioContext.destination);osc.start(start+n.start-offset);osc.stop(start+n.end-offset);oscillators.push(osc);});
  $('melody-play').textContent='停止旋律';melodyTimer=setTimeout(stopMelody,Math.min(30,notes.at(-1).end-offset)*1000+300);
}
$('melody-play').onclick=()=>{if(oscillators.length)stopMelody();else if(prepared)playNotes(prepared.notes);};
function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function filterPhrases(){document.querySelectorAll('#phrase-list details').forEach(row=>row.hidden=$('phrase-review-only').checked&&row.dataset.review!=='true');}
$('phrase-review-only').onchange=filterPhrases;
async function alignRequest(options){
  if(busy)throw Error('任务正在执行，请等待完成。');
  requireNoteReadings();
  saveEditorDraft();
  const job=current,request={...options,labels:[...labels],prepared_revision:prepared.revision};
  alignmentSubmitting=true;busy=true;
  document.querySelectorAll('[data-align],#phrase-list input,#phrase-list textarea,#note-rows input,#reading-input-mode').forEach(e=>e.disabled=true);
  pendingAlignment={job,revision:prepared.revision,phrase_id:options.phrase_id};
  try{await draftStore.flush();if(!draftStore.durable(job))throw Error('草稿备份失败，已停止重新分配。请先下载备份。');await api(`/api/jobs/${job}/align`,request);$('confirmed').checked=false;}
  catch(error){pendingAlignment=null;throw error;}
  finally{alignmentSubmitting=false;await poll();}
}
function showPhrases(){
  if(phraseJob!==current){phraseDrafts.clear();phraseJob=current;}
  const a=prepared.alignment;$('phrase-panel').hidden=!a;$('note-editor').open=!a;
  if(!a)return;
  $('phrase-summary').textContent=`${a.phrases.length} 句 · ${a.total_units-a.unassigned_units} / ${a.total_units} 个读音单元已分配（${isChinese()?'汉字 / 英文词':'日语拍音 / 英文词'}） · ${a.review_phrases} 句需优先核对`;
  $('lrc-offset').value=a.clock.offset_seconds;$('lrc-scale').value=a.clock.scale;
  $('phrase-list').replaceChildren();
  a.phrases.forEach(p=>{
    const row=element('details',undefined,'phrase');row.id=`phrase-${p.id}`;row.dataset.review=String(p.needs_review);
    const romanSummary=element('span',undefined,'romaji-text');romanSummary.id=`phrase-romaji-${p.id}`;
    const summary=element('summary');summary.append(element('span',`${p.id+1}. ${p.text}`),romanSummary,element('small',`${p.note_indices.length} 音符 / ${p.units.length} 读音单元${p.needs_review?' · 需核对':' · 自动草稿'}`));row.append(summary);
    const range=p.midi_start==null?'尚未匹配':`${format(p.midi_start)}–${format(p.midi_end)}`;
    row.append(element('p',`LRC ${format(p.lrc_start)} → MIDI ${range}`,'hint'));
    if(p.warnings.length)row.append(element('p',p.warnings.join('；'),'phrase-warning'));
    const draft=phraseDrafts.get(p.id)||{reading:p.reading,note_start:p.note_start,note_end:p.note_end};
    const field=element('label','本句读音'),input=element('textarea');input.rows=3;input.id=`phrase-reading-${p.id}`;field.append(input);row.append(field);
    const preview=element('p',undefined,'reading-preview');preview.id=`phrase-preview-${p.id}`;
    const romanDraft=element('p',undefined,'romaji-text');romanDraft.id=`phrase-reading-romaji-${p.id}`;input.setAttribute('aria-describedby',`${preview.id} ${romanDraft.id}`);row.append(preview,romanDraft);
    const controls=element('div',undefined,'settings');
    const first=element('input'),last=element('input');first.type=last.type='number';first.min=last.min=1;first.max=last.max=prepared.notes.length;first.value=draft.note_start+1;last.value=draft.note_end;
    first.id=`phrase-first-${p.id}`;last.id=`phrase-last-${p.id}`;
    for(const [name,node] of [['首音符编号',first],['尾音符编号',last]]){const label=element('label',name);label.append(node);controls.append(label);}row.append(controls);
    const remember=()=>{phraseDrafts.set(p.id,{reading:editor.canonical,inputState:editor.snapshot(),note_start:Number(first.value)-1,note_end:Number(last.value)});$('confirmed').checked=false;updateDraftMarkers();saveEditorDraft();};
    const editor=createReadingEditor(input,preview,draft.reading,{kind:'phrase',state:draft.inputState,onEdit:remember,
      onPreview:value=>{showRomaji(romanSummary,value);showRomaji(romanDraft,value);}});
    [first,last].forEach(node=>node.addEventListener('input',remember));
    const buttons=element('div',undefined,'inline');const apply=element('button','应用本句读音 · 保留手动音符','secondary');apply.type='button';apply.dataset.align='';
    row.append(element('p','整句修改自动保存为草稿。此按钮更新句内自动分配，已手动修改的音符会保留；无需点击它来保存逐音符修改。','hint'));
    apply.onclick=()=>action(async()=>{await alignRequest({mode:'phrase',phrase_id:p.id,reading:editor.value(),note_start:Number(first.value)-1,note_end:Number(last.value)});},apply);
    const listen=element('button','试听本句旋律','text-button');listen.type='button';listen.onclick=()=>playNotes(prepared.notes.slice(Number(first.value)-1,Number(last.value)));
    const jump=element('button','查看对应音符','text-button');jump.type='button';jump.onclick=()=>focusNote(Math.max(0,Number(first.value)-1));buttons.append(apply,listen,jump);
    if(p.units.length&&p.note_indices.length>3*p.units.length&&p.units.every(u=>u.lang==='ja')){
      const repeat=element('button','按原读音重复填满','text-button');repeat.type='button';repeat.dataset.align='';
      repeat.onclick=()=>{const count=Number(last.value)-Number(first.value)+1;if(!Number.isInteger(count)||count<1||count>500) {notice('先填写有效的首尾音符编号（最多 500 个）。');return;}editor.setCanonical(Array.from({length:count},(_,i)=>p.units[i%p.units.length].text).join(' '));remember();notice('已生成重复读音草稿并尝试自动备份，尚未应用。仅在本句确实循环演唱这些读音时使用，请核对后应用。');};
      buttons.append(repeat);
    }
    row.append(buttons);$('phrase-list').append(row);
  });
  const ids=a.unowned_notes,groups=[];ids.forEach(i=>{if(groups.length&&groups.at(-1).at(-1)===i-1)groups.at(-1).push(i);else groups.push([i]);});
  $('unowned-panel').hidden=!ids.length;$('unowned-summary').textContent=`${ids.length} 个音符暂设 SP（无歌词时间窗），查看范围`;$('unowned-list').replaceChildren();
  groups.forEach(g=>{const button=element('button',`#${g[0]+1}–${g.at(-1)+1} · ${format(prepared.notes[g[0]].start)}`,'text-button');button.type='button';button.onclick=()=>focusNote(g[0]);$('unowned-list').append(button);});
  filterPhrases();
}
for(const [id,auto] of [['lrc-apply',false],['lrc-auto',true]])$(id).onclick=()=>action(async()=>{
  if(phraseDrafts.size)throw Error('请先应用修改过的整句读音，再重排全部音符。保存草稿不会执行重排。');
  if(!auto&&(!$('lrc-offset').value||!$('lrc-scale').value))throw Error('请填写偏移和比例。');
  await alignRequest({mode:'global',...(auto?{}:{offset:Number($('lrc-offset').value),scale:Number($('lrc-scale').value)})});
},$(id));
function captureDraft(){
  if(!current||!prepared||!loadedJob?.startsWith(current+':'))return null;
  const fields={};document.querySelectorAll('#review-panel input,#review-panel select,#review-panel textarea,#voice-panel input,#voice-panel select').forEach(node=>{
    if(node.id&&node.type!=='file')fields[node.id]=node.type==='checkbox'?node.checked:node.value;
  });
  return {job_id:current,prepared_revision:prepared.revision,reading_mode:readingInputMode,labels:[...labels],
    base_labels:[...prepared.labels],notes:prepared.notes.map(n=>[n.start,n.end,n.pitch]),fields,
    note_states:Object.fromEntries(readingEditors.filter(e=>e.kind==='note').map(e=>[e.id,e.snapshot()])),
    phrase_drafts:[...phraseDrafts],reference_asset:uploads.reference||null,
    open_phrases:[...document.querySelectorAll('#phrase-list details[open]')].map(n=>Number(n.id.slice(7)))};
}
function hasEditorChanges(){return phraseDrafts.size||labels.some((x,i)=>x!==prepared.labels[i])||readingEditors.some(e=>e.error);}
function saveEditorDraft(){if(restoringDraft)return;$('segment-result').hidden=true;return draftStore.save();}
function updateDraftMarkers(){
  $('draft-pending').textContent=phraseDrafts.size?`${phraseDrafts.size} 句整句读音待应用；逐音符修改直接保存，无需重新分配。`:'逐音符修改自动保存，无需重新分配。';
  $('draft-jump').hidden=!phraseDrafts.size;
  document.querySelectorAll('#phrase-list details').forEach(row=>{
    const changed=phraseDrafts.has(Number(row.id.slice(7)));row.classList.toggle('draft-pending',changed);
    let marker=row.querySelector('.draft-marker');if(!marker){marker=element('span',undefined,'draft-marker');row.querySelector('summary').append(marker);}
    marker.textContent=changed?'读音草稿待应用':'';
  });
}
function focusPendingPhrase(){const id=phraseDrafts.keys().next().value,row=$(`phrase-${id}`);if(row){row.hidden=false;row.open=true;row.scrollIntoView({block:'center'});}}
function restoreEditorDraft(source){
  const draft=structuredClone(source);
  if(draft.schema!==1||draft.job_id!==current||!['romaji','native'].includes(draft.reading_mode)||
    !Array.isArray(draft.labels)||draft.labels.length!==prepared.notes.length||draft.labels.some(x=>typeof x!=='string'||x.length>64)||
    !Array.isArray(draft.phrase_drafts)||typeof draft.fields!=='object'||!draft.fields||
    JSON.stringify(draft.notes)!==JSON.stringify(prepared.notes.map(n=>[n.start,n.end,n.pitch])))throw Error('草稿格式无效或属于另一份乐谱，未覆盖当前输入。');
  if(draft.prepared_revision!==prepared.revision){
    if(!Array.isArray(draft.base_labels)||draft.base_labels.length!==labels.length)throw Error('旧版草稿缺少比较依据，无法自动恢复；可下载备份核对。');
    const conflicts=draft.labels.flatMap((x,i)=>x!==draft.base_labels[i]&&prepared.labels[i]!==draft.base_labels[i]&&x!==prepared.labels[i]?[i+1]:[]);
    if(conflicts.length)throw Error(`草稿与服务器在音符 ${conflicts.join('、')} 上有冲突，未覆盖当前输入。请下载所选草稿备份核对。`);
    draft.labels=draft.labels.map((x,i)=>{
      if(x!==draft.base_labels[i])return x;
      if(prepared.labels[i]!==x){delete draft.note_states?.[`note-${i}`];delete draft.fields[`note-${i}`];}
      return prepared.labels[i];
    });
  }
  for(const item of draft.phrase_drafts){
    if(!Array.isArray(item)||item.length!==2||!Number.isInteger(item[0])||!prepared.alignment?.phrases[item[0]]||typeof item[1]?.reading!=='string')throw Error('草稿的分句格式不正确，未覆盖当前输入。');
  }
  saveEditorDraft();restoringDraft=true;
  try{showPrepared(prepared,null,draft);}finally{restoringDraft=false;}
  saveEditorDraft();if(!isEmbedded())notice('草稿已恢复，包括未完成的拼写和逐音符修改；没有执行重新分配。');
}
async function loadEditorDrafts(automatic=true){
  const job=current,serial=++draftLoadSerial,sequence=draftStore.sequence;
  const {items,online}=await draftStore.candidates(job);
  if(job!==current||serial!==draftLoadSerial)return;
  draftChoices=items;$('draft-choices').replaceChildren();
  for(const [i,item] of items.entries()){
    const option=element('option',`${new Date(item.updated_at).toLocaleString()} · ${item.title||item.client_id.slice(0,6)}${item.prepared_revision!==prepared.revision?' · 旧版乐谱':''}`);option.value=i;$('draft-choices').append(option);
  }
  $('draft-recover').hidden=!items.length;
  // Never replace input typed while the server request was in flight.
  if(automatic&&items.length&&sequence===draftStore.sequence&&items[0].prepared_revision===prepared.revision){
    const draft=await draftStore.read(job,items[0]);
    if(job===current&&serial===draftLoadSerial&&sequence===draftStore.sequence)restoreEditorDraft(draft);
  }else if(automatic&&!hasEditorChanges())$('draft-status').textContent=items.length?'发现旧版草稿，可在下方选择恢复':online?'开始编辑后自动保存':'暂未连接草稿服务；编辑会先保存在本机';
}
document.addEventListener('input',event=>{if(event.target.closest('#review-panel,#voice-panel'))saveEditorDraft();});
document.addEventListener('change',event=>{if(event.target.closest('#review-panel,#voice-panel'))saveEditorDraft();});
$('draft-save').onclick=()=>action(async()=>{saveEditorDraft();await draftStore.flush();},$('draft-save'));
$('draft-download').onclick=()=>action(()=>draftStore.download(),$('draft-download'));
$('draft-jump').onclick=focusPendingPhrase;
$('draft-refresh').onclick=()=>action(()=>loadEditorDrafts(false),$('draft-refresh'));
$('draft-restore').onclick=()=>action(async()=>{const job=current,sequence=draftStore.sequence;const draft=await draftStore.read(job,draftChoices[Number($('draft-choices').value)]);if(job!==current||sequence!==draftStore.sequence)throw Error('读取草稿期间有新编辑，已取消恢复；请停止输入后重试。');restoreEditorDraft(draft);},$('draft-restore'));
$('draft-download-selected').onclick=()=>action(async()=>draftStore.download(await draftStore.read(current,draftChoices[Number($('draft-choices').value)])),$('draft-download-selected'));
$('draft-import').onchange=()=>action(async()=>{const file=$('draft-import').files[0];if(!file)return;if(file.size>1024*1024)throw Error('草稿文件过大（上限 1 MiB）');restoreEditorDraft(JSON.parse(await file.text()));});
(async()=>{try{const cfg=await api('/api/config');if(!cfg.ready.netease_api_module)notice('网易云音频接口尚未配置；可以上传本地音频，歌词链接仍可单独使用。');}catch(e){notice('暂时无法连接本地服务。');}
const match=location.hash.match(/^#job=([a-f0-9]{32})$/);if(match)current=match[1];await history();await poll();setInterval(history,10000);})();
