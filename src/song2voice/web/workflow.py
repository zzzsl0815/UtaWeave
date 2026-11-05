"""Background inference jobs shared by the local web interface."""
import argparse
from contextlib import nullcontext
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from ..selection import atomic_json, require_reference
from ..midi_score import inspect_midi, split_lyrics_text, notes_to_score, edit_score, require_edited_mapping


def read(path):
    return json.loads(Path(path).read_text())


def load_config(path):
    path=Path(path).absolute();cfg=read(path)
    for key in ('workspace','render_config','reference_runtime','annotation_config','labeling_python',
                'netease_bridge','netease_api_module','credential_file'):
        if cfg.get(key):cfg[key]=os.path.abspath(path.parent/Path(cfg[key]).expanduser())
    for key in ('workspace','render_config','reference_runtime','annotation_config','labeling_python'):
        if not cfg.get(key):raise ValueError('Missing web configuration: '+key)
    cfg['config_file']=str(path)
    return cfg


def asset(cfg, token):
    if not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid upload ID')
    root=Path(cfg['workspace'])/'uploads'
    meta=read(root/(token+'.json'));path=root/meta['file']
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('Uploaded file is missing')
    return path


def template(cfg):
    from ..rendering.pipeline import PATH_KEYS
    path=Path(cfg['render_config']);value=read(path)
    for key in (*PATH_KEYS,'render_python','align_python'):
        if value.get(key):value[key]=os.path.abspath(path.parent/Path(value[key]).expanduser())
    if '/' in value.get('ffmpeg',''):value['ffmpeg']=os.path.abspath(path.parent/value['ffmpeg'])
    return value


def command(arguments):
    env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2])+os.pathsep+os.environ.get('PYTHONPATH',''))
    subprocess.run([str(x) for x in arguments],check=True,env=env)


def stage(job, name, message, **extra):
    state=read(job/'status.json')
    state.update(status='running',stage=name,message=message,updated_at=time.time(),**extra)
    atomic_json(job/'status.json',state)


def g2p(cfg,job,**request):
    annotation=read(cfg['annotation_config']);base=Path(cfg['annotation_config']).parent
    request['english_runtime']={key:os.path.abspath(base/Path(annotation[key]).expanduser()) for key in ('upstream_repo','tifa_model')}
    atomic_json(job/'g2p-request.json',request)
    command([cfg['labeling_python'],'-m','song2voice.midi_score',job/'g2p-request.json',job/'g2p-result.json'])
    return read(job/'g2p-result.json')


def lyrics(cfg, request, source_url=None):
    from ..netease import fetch_lyrics
    text=request.get('lyrics_text','').strip()
    url=request.get('lyrics_url','').strip() or source_url
    result=dict(original=text,origin='输入文本',warning=None)
    if not text and url:
        try:
            result.update(fetch_lyrics(url),origin='网易云歌词')
        except Exception:
            result['warning']='歌词暂时获取失败，可返回输入页粘贴歌词；原曲转谱仍可使用音频识别。'
    result.update(split_lyrics_text(result['original']))
    return result


def download(cfg, url, out):
    from ..netease import fetch_reference_song
    if not cfg.get('netease_api_module') or not Path(cfg['netease_api_module']).is_file():
        raise ValueError('网易云音频接口尚未配置；可上传本地完整音频')
    os.environ['NETEASE_API_MODULE']=cfg['netease_api_module']
    record=fetch_reference_song(url,cfg['netease_bridge'],cfg.get('credential_file'),out)
    return out/record['audio']


def prepare(cfg, job, request):
    from ..references import prepare_stems
    stage(job,'input','读取输入与歌词')
    if request['mode']=='midi':
        source=asset(cfg,request['midi_asset']);info=inspect_midi(source)
        track=next((t for t in info['tracks'] if t['index']==request['track']),None)
        if track is None or track['polyphonic']:raise ValueError('请选择没有重叠音符的主旋律轨')
        lyric=lyrics(cfg,request)
        labels=[n.get('lyric','') for n in track['notes']]
        draft=dict(reading='',moras=[],warning='请逐音符核对歌词与读音。')
        alignment=None
        if lyric['text']:
            from ..lyric_alignment import parse_lrc,align_lrc
            parsed=parse_lrc(lyric['original'])
            stage(job,'lyrics','按歌词分句转换读音，匹配 MIDI 时间轴')
            if len(parsed['lines'])>=2:
                if parsed['untimed_lines']:raise ValueError('LRC 中有未带时间戳的正文，请补齐时间戳或改用纯文本')
                readings=g2p(cfg,job,texts=[line['text'] for line in parsed['lines']])['lines']
                alignment=align_lrc(track['notes'],parsed,readings)
                labels=alignment.pop('labels')
                draft=dict(reading='\n'.join(r['reading'] for r in readings),moras=[m for r in readings for m in r['moras']],
                    warning='已按 LRC 分句匹配并分配读音；请优先核对标记的乐句，时间和分配分数不是准确率。')
            else:
                draft=g2p(cfg,job,text=lyric['text'])
                labels=[draft['moras'][i] if i<len(draft['moras']) else '' for i in range(len(track['notes']))]
        if not any(labels):raise ValueError('MIDI 没有内嵌歌词，请输入歌词文本或歌词链接')
        warnings=[x for x in [info['warning'],draft['warning']] if x]
        if abs(info['bpm']-info['render_bpm'])>.01:
            warnings.append(f"MIDI 为 {info['bpm']:.2f} BPM；VR 使用 {info['render_bpm']} BPM 并重新换算音符拍值，原始起止时间保持不变。")
        if lyric['excluded_credit_lines']:
            warnings.append(f"已从读音草稿中排除 {len(lyric['excluded_credit_lines'])} 行作词、作曲等署名，原文仍保留。")
        prepared=dict(mode='midi',notes=track['notes'],labels=labels,bpm=info['bpm'],render_bpm=info['render_bpm'],lyrics=lyric,
                      reading=draft,track=track['index'],warnings=warnings,
                      alignment=alignment,unmatched_moras=alignment['unassigned_units'] if alignment else max(0,len(draft['moras'])-len(labels)))
    else:
        source=download(cfg,request['source_url'],job/'source') if request.get('source_url') else asset(cfg,request['source_asset'])
        lyric=lyrics(cfg,request,request.get('source_url'))
        stage(job,'separate','分离原曲人声与伴奏')
        prepare_stems(source,job/'source-stems',Path(cfg['reference_runtime']),True)
        stage(job,'annotate','识别歌词、对齐音素与提取音符')
        annotation=read(cfg['annotation_config'])
        for key in ('upstream_repo','romaji_model','qwen_model','kashi_model','tifa_model','game_model'):
            annotation[key]=os.path.abspath(Path(cfg['annotation_config']).parent/Path(annotation[key]).expanduser())
        annotation.update(audio=str(job/'source-stems/stems/vocal_for_reference.wav'),out=str(job/'annotation'),
                          song_id=job.name,bpm=request['bpm'])
        atomic_json(job/'annotation.local.json',annotation)
        command([cfg['labeling_python'],'-m','song2voice.vocal2midi_native_worker','--config',job/'annotation.local.json'])
        score=read(job/'annotation/score.vr.json')
        units=[]
        for row in score:
            cursor=row['source_window']['start_seconds']
            for i,(word,duration) in enumerate(zip(row['word'],row['word_dur'])):
                if word not in ('SP','AP'):
                    pitches=[p for p,o in zip(row['pitch'],row['pitch2word']) if o==i]
                    units.append(dict(start=cursor,end=cursor+duration,pitch=next((p for p in pitches if p>0),0),lyric=word))
                cursor+=duration
        prepared=dict(mode='song',notes=units,labels=[n['lyric'] for n in units],bpm=request['bpm'],lyrics=lyric,
                      warnings=['显示的是音频自动标注；网易云歌词仅供核对，没有直接替换自动标注。'],unmatched_moras=0)
    prepared['revision']=str(time.time_ns())
    atomic_json(job/'prepared.json',prepared)
    state=read(job/'status.json');state.update(status='awaiting_score',stage='review',message='请核对音符、歌词读音与延音，再生成演唱',updated_at=time.time())
    atomic_json(job/'status.json',state)


def export_lyric_midi(source, track_index, notes, labels, destination):
    import mido
    try:midi=mido.MidiFile(source,charset='utf-8')
    except UnicodeDecodeError:midi=mido.MidiFile(source,charset='shift_jis')
    events=[];tick=0
    for order,event in enumerate(midi.tracks[track_index]):
        tick+=event.time
        if event.type!='lyrics':events.append((tick,1,order,event.copy(time=0)))
    for i,(note,label) in enumerate(zip(notes,labels)):
        events.append((note['tick'],0,i,mido.MetaMessage('lyrics',text=label,time=0)))
    track=mido.MidiTrack();last=0
    for tick,priority,order,event in sorted(events,key=lambda r:r[:3]):
        track.append(event.copy(time=tick-last));last=tick
    midi.tracks[track_index]=track;midi.charset='utf-8';midi.save(destination)


def midi_stems(cfg,job,notes,offset,request):
    import numpy as np
    import soundfile as sf
    seconds=notes[-1]['end']+offset+1.5
    root=job/'midi-timeline';root.mkdir(exist_ok=True)
    silence=np.zeros(math_ceil(seconds*48000),dtype=np.float32)
    sf.write(root/'vocal_native.wav',silence,48000,subtype='PCM_16')
    if request.get('accompaniment_asset'):
        command([template(cfg).get('ffmpeg','ffmpeg'),'-y','-hide_banner','-loglevel','error',
                 '-i',asset(cfg,request['accompaniment_asset']),'-af',
                 f'aresample=48000,adelay={round(offset*48000)}S:all=1,apad,atrim=end_sample={len(silence)}',
                 '-ar','48000','-ac','2','-c:a','pcm_s16le',root/'instrumental.wav'])
    else:sf.write(root/'instrumental.wav',silence,48000,subtype='PCM_16')
    atomic_json(root/'timeline.json',dict(source='midi',lead_in_seconds=offset,seconds=seconds,
        silent_vocal_buffer='Timing/planning only; not an original recording, acoustic target or loudness reference'))
    return root


def math_ceil(value):
    import math
    return math.ceil(value)


def require_score_revision(prepared,request):
    if prepared.get('revision') is not None and request.get('prepared_revision')!=prepared['revision']:
        raise ValueError('乐谱已重新准备，当前页面的草稿版本过期。请刷新页面并核对新草稿后再提交。')


def realign(cfg,job,request):
    from ..lyric_alignment import align_lrc,build_alignment
    prepared=read(job/'prepared.json');require_score_revision(prepared,request)
    alignment=prepared.get('alignment')
    if prepared['mode']!='midi' or not alignment:raise ValueError('当前任务没有带时间戳的分句草稿，请用 LRC 重新准备乐谱')
    submitted=request.get('labels')
    if not isinstance(submitted,list) or len(submitted)!=len(prepared['notes']) or any(not isinstance(x,str) or len(x)>64 for x in submitted):
        raise ValueError('音符读音数量或格式不正确')
    stage(job,'lyrics','按句重新分配歌词；保留 MIDI 音高和时间')
    phrases=alignment['phrases']
    parsed=dict(lines=[dict(id=p['id'],text=p['text'],time=p['lrc_start'],end=p['lrc_end']) for p in phrases],
                offset_tag_ms=alignment['clock'].get('lrc_offset_tag_ms',0))
    readings=[dict(reading=p['reading'],units=p['units'],moras=[u['text'] for u in p['units']],warnings=p.get('reading_warnings',[])) for p in phrases]
    if request['mode']=='phrase':
        index=request.get('phrase_id')
        if type(index) is not int or not 0<=index<len(phrases):raise ValueError('无效的乐句编号')
        content=request.get('reading')
        if not isinstance(content,str) or not content.strip() or len(content)>2000:raise ValueError('本句读音应为 1–2000 字符')
        left,right=request.get('note_start'),request.get('note_end')
        if type(left) is not int or type(right) is not int or not 0<=left<right<=len(submitted):raise ValueError('本句首尾音符编号无效')
        ranges=[(p['note_start'],p['note_end']) for p in phrases]
        if (index and left<ranges[index-1][1]) or (index+1<len(ranges) and right>ranges[index+1][0]):
            raise ValueError('音符范围与相邻句重叠，请先缩短相邻句范围，再调整本句')
        old_left,old_right=ranges[index]
        # A phrase operation must not erase hand-edited note assignments. Compare
        # with the old automatic mapping too, since previous submissions may have
        # already persisted those edits in prepared.json.
        old_auto=build_alignment(prepared['notes'],parsed,readings,alignment['clock'],ranges)['labels']
        affected=set(range(old_left,old_right))|set(range(left,right))
        protected={i for i in affected if submitted[i]!=old_auto[i]}
        ranges[index]=(left,right)
        readings[index]=g2p(cfg,job,texts=[content])['lines'][0]
        if not readings[index]['units']:raise ValueError('本句没有可识别的读音')
        result=build_alignment(prepared['notes'],parsed,readings,alignment['clock'],ranges)
        labels=list(submitted)
        labels[old_left:old_right]=['SP']*(old_right-old_left)
        labels[left:right]=result['labels'][left:right]
        for i in protected:labels[i]=submitted[i]
        result['phrases'][index]['manually_adjusted']=True
        result['phrases'][index]['preserved_note_indices']=sorted(protected)
    elif request['mode']=='global':
        options={}
        for key in ('offset','scale'):
            if request.get(key) is not None:
                if type(request[key]) not in (int,float):raise ValueError('时间偏移和比例必须为数值')
                options[key]=float(request[key])
        result=align_lrc(prepared['notes'],parsed,readings,**options);labels=result['labels']
    else:raise ValueError('未知的分句操作')
    result.pop('labels')
    for i,phrase in enumerate(result['phrases']):
        if request['mode']=='phrase' and i!=request['phrase_id'] and phrases[i].get('manually_adjusted'):phrase['manually_adjusted']=True
    # One atomic prepared file is the source of truth. Keep submitted note edits
    # in the snapshot too, even if they existed only in the browser until now.
    history=job/'alignment-history';history.mkdir(exist_ok=True)
    snapshot=deepcopy(prepared);snapshot['labels']=submitted
    atomic_json(history/(str(time.time_ns())+'.json'),snapshot)
    prepared.update(labels=labels,alignment=result,unmatched_moras=result['unassigned_units'],revision=str(time.time_ns()),
        reading=dict(reading='\n'.join(r['reading'] for r in readings),moras=[m for r in readings for m in r['moras']]))
    atomic_json(job/'prepared.json',prepared)
    message='分句草稿已更新，请核对标记的乐句'
    if request['mode']=='phrase' and protected:message+=f'；保留了 {len(protected)} 个手动修改的音符'
    state=read(job/'status.json');state.update(status='awaiting_score',stage='review',message=message,updated_at=time.time(),completed_at=None,error=None)
    atomic_json(job/'status.json',state)


def render(cfg,job,request):
    from ..references import prepare_reference
    from ..rendering.generate import plan
    from ..conditioning import require_conditioning, compile_chunk_annotation, compiled_duration
    prepared=read(job/'prepared.json');labels=request['labels'];original=read(job/'request.json')
    require_score_revision(prepared,request)
    require_edited_mapping(prepared,labels)
    stage(job,'score','检查歌词与音符对应')
    wanted=labels if prepared['mode']=='midi' else [x for x,y in zip(labels,prepared['labels']) if x!=y]
    phones=g2p(cfg,job,readings=wanted) if wanted else {}
    runtime=template(cfg)
    if prepared['mode']=='midi':
        score,offset=notes_to_score(prepared['notes'],labels,phones,prepared['bpm'])
        stems=midi_stems(cfg,job,prepared['notes'],offset,original)
        runtime['source_mode']='midi'
        export_lyric_midi(asset(cfg,original['midi_asset']),prepared['track'],prepared['notes'],labels,job/'lyrics.mid')
    else:
        score=edit_score(read(job/'annotation/score.vr.json'),labels,phones)
        stems=job/'source-stems/stems';runtime['source_mode']='song'
    require_conditioning(score)
    split=plan(score,whole_lines=True,stems=stems)
    for chunk in split['chunks']:compiled_duration(compile_chunk_annotation(chunk,score[0]['bpm']))
    atomic_json(job/'score.vr.json',score)
    atomic_json(job/'score-check.json',dict(chunks=split['chunk_count'],lyric_units=split['lyric_unit_count']))
    stage(job,'reference','准备目标音色参考')
    ref=request['reference'];mode=ref['mode']
    if mode=='same_song':
        if prepared['mode']!='song':raise ValueError('仅原曲模式可使用原曲音色')
        reference=prepare_reference(job/'reference',from_run=job/'source-stems')
    elif mode=='clip':reference=prepare_reference(job/'reference',clip=asset(cfg,ref['asset']))
    else:
        audio=download(cfg,ref['url'],job/'reference-source') if mode=='netease' else asset(cfg,ref['asset'])
        reference=prepare_reference(job/'reference',audio=audio,runtime_path=Path(cfg['reference_runtime']))
    selected=require_reference(reference)
    import shutil
    preview=job/('reference-preview'+Path(selected['audio']).suffix.lower());shutil.copyfile(selected['audio'],preview)
    atomic_json(job/'reference-info.json',dict(file=preview.name,score=selected.get('score'),
        seconds=selected.get('source_window',{}).get('end_seconds',0)-selected.get('source_window',{}).get('start_seconds',0),
        method=selected.get('status')))
    seed_path=Path(runtime['seed_vc_config']);seed=read(seed_path)
    for key in ('repo','assets','python'):seed[key]=os.path.abspath(seed_path.parent/Path(seed[key]).expanduser())
    seed.update(steps=request['steps'],cfg=request['cfg']);atomic_json(job/'seed.local.json',seed)
    runtime.update(score=str(job/'score.vr.json'),stems=str(stems),reference_run=str(reference),seed_vc_config=str(job/'seed.local.json'))
    atomic_json(job/'render.local.json',runtime)
    stage(job,'render','VR 导唱 → Seed-VC 换声 → 对齐与导出')
    command([sys.executable,'-m','song2voice.rendering.pipeline','--config',job/'render.local.json','--out',job/'render'])
    result=read(job/'render/latest.json');report=Path(result['report'])
    state=read(job/'status.json');state.update(status='complete',stage='complete',message='生成完成，可以试听',
        report=str(report.relative_to(job)),updated_at=time.time(),completed_at=time.time())
    atomic_json(job/'status.json',state)


def failure_message(job,error):
    state=read(job/'status.json')
    if isinstance(error,subprocess.CalledProcessError) and state.get('stage')=='render':
        files=[p for p in job.glob('render/*/pipeline_status.json') if p.stat().st_mtime>=state['updated_at']]
        if files:
            pipeline=read(max(files,key=lambda p:p.stat().st_mtime))
            if pipeline.get('error'):return str(pipeline['error'])[:1600]
    return str(error)[:1600]


def run(config,job_id,action):
    cfg=load_config(config);job=Path(cfg['workspace'])/'jobs'/job_id
    def interrupted(*_):raise InterruptedError('任务已停止')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        cpu_only=action=='align' or action=='prepare' and read(job/'request.json')['mode']=='midi'
        context=nullcontext(None) if cpu_only else (Path(cfg['workspace'])/'inference.lock').open('a')
        with context as lock:
            if lock is not None:fcntl.flock(lock,fcntl.LOCK_EX)
            if action=='prepare':prepare(cfg,job,read(job/'request.json'))
            elif action=='align':realign(cfg,job,read(job/'alignment-request.json'))
            else:render(cfg,job,read(job/'render-request.json'))
    except BaseException as error:
        state=read(job/'status.json');cancelled=(job/'CANCEL').exists()
        state.update(status='cancelled' if cancelled else 'failed',stage='stopped' if cancelled else 'error',
                     message='任务已停止' if cancelled else '任务失败，请检查错误信息后重试',
                     error=failure_message(job,error),updated_at=time.time())
        atomic_json(job/'status.json',state)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--job',required=True)
    p.add_argument('--action',choices=['prepare','render','align'],required=True);a=p.parse_args();run(a.config,a.job,a.action)
