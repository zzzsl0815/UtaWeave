'use strict';
const $ = id => document.getElementById(id);
let mode = 'song', uploads = {}, current = null, prepared = null, labels = [], loadedJob = null, busy = false;
let audioContext = null, oscillators = [], pollTimer;
const stageNames = {queued:'排队中',input:'读取输入',lyrics:'转换读音',separate:'分离人声',annotate:'自动转谱',review:'等待核对乐谱',score:'检查乐谱',reference:'准备音色参考',render:'生成演唱',complete:'生成完成',error:'任务失败',stopped:'任务已停止'};
const statusNames = {queued:'排队中',running:'进行中',awaiting_score:'待核对',complete:'已完成',failed:'失败',cancelled:'已停止'};
function notice(message) { $('notice').textContent = message || ''; $('notice').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-UtaWeave':'local'},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || `请求失败 (${response.status})`);
  return result;
}
async function action(fn, button) {
  notice(''); if (button) button.disabled = true;
  try { await fn(); } catch (error) { notice(error.message); }
  finally { if (button) button.disabled = false; }
}
function setMode(value) {
  mode = value;
  document.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.mode === mode)));
  $('song-input').hidden = mode !== 'song'; $('midi-input').hidden = mode !== 'midi';
  const same = $('reference-mode').querySelector('[value="same_song"]'); same.hidden = mode !== 'song';
  if (mode === 'midi' && $('reference-mode').value === 'same_song') $('reference-mode').value = 'netease';
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
      data.midi.tracks.forEach(track => {
        const option = document.createElement('option'); option.value = track.index;
        option.textContent = `${track.name} · ${track.count} 个音符 · ${track.seconds.toFixed(1)} 秒${track.polyphonic?' · 存在复音，不能作为旋律轨':''}`;
        option.disabled = track.polyphonic; $('midi-track').append(option);
      });
      const first = data.midi.tracks.find(t => !t.polyphonic); if (first) $('midi-track').value = first.index;
      else throw Error('没有可用的单声部旋律轨，请在 MIDI 编辑器中分离主旋律。');
    }
    if (key === 'reference') { $('reference-local-player').src = `/uploads/${data.id}`; $('reference-local-player').hidden = false; }
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
  const request = {mode,lyrics_text:$('lyrics-text').value,lyrics_url:$('lyrics-url').value.trim(),bpm:Number($('bpm').value)};
  if (mode === 'midi') {
    if (!uploads.midi) throw Error('请先上传 MIDI。');
    Object.assign(request,{midi_asset:uploads.midi,track:Number($('midi-track').value),accompaniment_asset:uploads.accompaniment || null});
  } else {
    request.source_url = $('source-url').value.trim(); request.source_asset = uploads.source;
    if (!request.source_url && !request.source_asset) throw Error('请输入原曲链接或上传音频。');
  }
  const job = await api('/api/jobs',request); current = job.id; loadedJob = null; prepared = null;
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
function mappingStatus() {
  const missing = labels.filter(v => !v.trim()).length;
  $('mapping-status').textContent = `${labels.length-missing} / ${labels.length} 已填写${missing ? `，还有 ${missing} 个空项` : ''}${prepared?.unmatched_moras ? `；原文比音符多 ${prepared.unmatched_moras} 个拍音，请合并读音或核对歌词范围，不要遗漏。` : ''}`;
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
  const input=$(`note-${best}`);input?.scrollIntoView({block:'nearest'});input?.focus();drawRoll(best);
});
window.addEventListener('resize',()=>drawRoll());
function showPrepared(data, saved) {
  prepared=data;labels=[...(saved?.labels || data.labels)];setMode(data.mode);
  $('input-panel').hidden=true;$('review-panel').hidden=false;$('voice-panel').hidden=false;
  $('score-summary').replaceChildren();
  for (const text of [`${data.notes.length} 个${data.mode==='midi'?'音符':'歌词单元'}`,`${data.bpm.toFixed(1)} BPM`,`${data.notes.at(-1).end.toFixed(1)} 秒`]) {
    const span=document.createElement('span');span.textContent=text;$('score-summary').append(span);
  }
  $('score-warning').textContent=data.warnings.join(' ');
  $('original-lyrics').textContent=data.lyrics.original || '没有独立歌词原文';$('auto-reading').textContent=data.reading?.reading || '';
  $('mapping-hint').textContent=data.mode==='midi'?'每行对应一个音符，填写日语假名。一个音符可填写多个拍音；同一字跨多个连续音符时，后续音符填 -。不演唱的音符填 SP。MIDI 中的原有休止自动保留。':'每行对应自动标注的一个歌词单元，可修改假名读音。此处保持音符及时间不变；如需修改旋律，可使用 MIDI 入口。';
  $('confirmed').checked=false;$('export-midi').hidden=data.mode!=='midi';$('note-rows').replaceChildren();
  data.notes.forEach((note,i)=>{
    const row=document.createElement('tr');if(!labels[i])row.classList.add('missing');
    for(const text of [i+1,format(note.start),pitchName(note.pitch),(note.end-note.start).toFixed(3)+' s']){
      const cell=document.createElement('td');cell.textContent=text;row.append(cell);
    }
    const cell=document.createElement('td'),input=document.createElement('input');input.id=`note-${i}`;input.value=labels[i];input.maxLength=64;input.setAttribute('aria-label',`第 ${i+1} 个音符的歌词读音`);
    input.addEventListener('input',()=>{labels[i]=input.value.trim();row.classList.toggle('missing',!labels[i]);$('confirmed').checked=false;mappingStatus();});
    input.addEventListener('focus',()=>{document.querySelectorAll('#note-rows tr').forEach(r=>r.classList.remove('selected'));row.classList.add('selected');drawRoll(i);});
    input.addEventListener('keydown',e=>{if(e.key==='Enter'){$(`note-${i+1}`)?.focus();e.preventDefault();}});
    cell.append(input);row.append(cell);$('note-rows').append(row);
  });
  if(saved){$('seed-steps').value=saved.steps;$('seed-cfg').value=saved.cfg;$('reference-mode').value=saved.reference.mode;$('reference-url').value=saved.reference.url||'';uploads.reference=saved.reference.asset;if(uploads.reference)$('reference-local-player').src=`/uploads/${uploads.reference}`;updateReference();}
  drawRoll();mappingStatus();
}
$('new-input').addEventListener('click',()=>{$('input-panel').hidden=false;$('input-panel').scrollIntoView({behavior:'smooth'});});
$('export-midi').onclick=()=>action(async()=>{const data=await api(`/api/jobs/${current}/midi`,{labels});const a=document.createElement('a');a.href=data.url;a.download='lyrics.mid';a.click();},$('export-midi'));
$('render').addEventListener('click',()=>action(async()=>{
  if(!$('confirmed').checked)throw Error('请核对歌词和音符对应，并勾选确认。');
  if(labels.some(x=>!x.trim()))throw Error('请补齐空白音符；延音填 -，不演唱的音符填 SP。');
  const reference={mode:$('reference-mode').value,asset:uploads.reference,url:$('reference-url').value.trim()};
  if(['clip','audio'].includes(reference.mode)&&!reference.asset)throw Error('请上传音色参考。');
  if(reference.mode==='netease'&&!reference.url)throw Error('请填写目标音色歌曲链接。');
  await api(`/api/jobs/${current}/render`,{labels,confirmed:true,reference,steps:Number($('seed-steps').value),cfg:Number($('seed-cfg').value)});
  $('result-panel').hidden=true;await poll();
},$('render')));
function updateTask(state) {
  busy=['queued','running'].includes(state.status);document.querySelector('aside').classList.toggle('busy',busy);
  $('task-title').textContent=stageNames[state.stage]||state.status;
  let message=state.message;
  if(state.pipeline_stages){const active=Object.entries(state.pipeline_stages).find(([,v])=>v==='running');if(active)message+=` · ${active[0]}`;}
  if(state.chunks?.total)message+=` · ${state.chunks.completed}/${state.chunks.total} 句`;
  $('task-message').textContent=message;$('task-elapsed').textContent=`任务 ${current.slice(0,8)} · 已经过 ${Math.floor(((state.completed_at||Date.now()/1000)-state.created_at)/60)} 分钟`;
  $('cancel').hidden=!busy;$('retry').hidden=!['failed','cancelled'].includes(state.status)||!!prepared;
  $('view-log').hidden=false;$('prepare').disabled=busy;$('render').disabled=busy;
  const step=state.status==='complete'?4:['reference','render'].includes(state.stage)?3:prepared?2:1;
  document.querySelectorAll('[data-step]').forEach(e=>e.classList.toggle('active',Number(e.dataset.step)===step));
  const index=state.status==='complete'?5:state.stage==='render'?3:state.stage==='reference'?2:prepared?1:0;
  [...$('task-stages').children].forEach((li,i)=>{li.className=i<index?'done':i===index?'current':'';});
  $('progress-track').firstElementChild.style.width=`${(index+.3)/5.3*100}%`;
  if(state.status==='failed')notice(state.error || state.message);
}
async function poll() {
  clearTimeout(pollTimer);
  try {
    if(current){
      const data=await api(`/api/jobs/${current}`);
      if(data.prepared&&loadedJob!==current){showPrepared(data.prepared,data.render_request);loadedJob=current;}
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
  } catch(error){$('connection').textContent='连接暂时中断，自动重试';}
  pollTimer=setTimeout(poll,2500);
}
async function history(){
  try{const jobs=await api('/api/jobs');$('history-list').replaceChildren();
    if(!jobs.length){$('history-list').textContent='还没有任务';return;}
    jobs.forEach(job=>{const button=document.createElement('button'),small=document.createElement('small');button.textContent=`${statusNames[job.status]||job.status} · ${job.id.slice(0,8)}`;small.textContent=new Date(job.created_at*1000).toLocaleString();button.append(small);button.onclick=()=>{current=job.id;loadedJob=null;prepared=null;$('result-panel').hidden=true;location.hash=`job=${current}`;poll();};$('history-list').append(button);});
  }catch(_){};
}
$('cancel').onclick=()=>action(async()=>{await api(`/api/jobs/${current}/cancel`,{});await poll();},$('cancel'));
$('retry').onclick=()=>action(async()=>{await api(`/api/jobs/${current}/retry`,{});await poll();},$('retry'));
$('view-log').onclick=()=>action(async()=>{const data=await api(`/api/jobs/${current}/log`);$('task-log').textContent=data.text;$('task-log').hidden=false;});
function stopMelody(){oscillators.forEach(o=>{try{o.stop();}catch(_){}});oscillators=[];$('melody-play').textContent='试听旋律';}
$('melody-play').onclick=()=>{
  if(oscillators.length){stopMelody();return;}if(!prepared)return;
  audioContext ||= new AudioContext();audioContext.resume();const start=audioContext.currentTime+.1,offset=prepared.notes[0].start;
  prepared.notes.filter(n=>n.pitch>0&&n.start-offset<30).forEach(n=>{const osc=audioContext.createOscillator(),gain=audioContext.createGain();osc.frequency.value=440*Math.pow(2,(n.pitch-69)/12);gain.gain.setValueAtTime(0,start+n.start-offset);gain.gain.linearRampToValueAtTime(.07,start+n.start-offset+.01);gain.gain.setValueAtTime(.07,start+Math.max(n.start+.01,n.end-.03)-offset);gain.gain.linearRampToValueAtTime(0,start+n.end-offset);osc.connect(gain).connect(audioContext.destination);osc.start(start+n.start-offset);osc.stop(start+n.end-offset);oscillators.push(osc);});
  $('melody-play').textContent='停止旋律（预览前 30 秒）';setTimeout(stopMelody,Math.min(30,prepared.notes.at(-1).end-offset)*1000+300);
};
(async()=>{try{const cfg=await api('/api/config');if(!cfg.ready.netease_api_module)notice('网易云音频接口尚未配置；可以上传本地音频，歌词链接仍可单独使用。');}catch(e){notice('暂时无法连接本地服务。');}
const match=location.hash.match(/^#job=([a-f0-9]{32})$/);if(match)current=match[1];await history();await poll();setInterval(history,10000);})();
