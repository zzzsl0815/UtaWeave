"""Local-only workflow server. Workers persist across browser/SSH disconnects."""
from functools import partial
from http.server import ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs, unquote
import uuid

from ..report_server import ReportHandler
from ..selection import atomic_json
from ..midi_score import inspect_midi
from ..netease import fetch_lyrics, song_id_from_url
from .workflow import read, load_config, asset, export_lyric_midi

LOCK=threading.Lock()
STATIC=Path(__file__).with_name('static')


def identifier(value):
    if not re.fullmatch('[a-f0-9]{32}',str(value)):raise ValueError('Invalid task ID')
    return value


def alive(job):
    try:
        proc=read(job/'process.json')
        env=Path(f'/proc/{proc["pid"]}/environ').read_bytes().split(b'\0')
        return ('UTAWEAVE_JOB_ID='+job.name).encode() in env
    except (OSError,ValueError,KeyError):return False


def launch(cfg,job,action):
    if alive(job):raise ValueError('此任务正在执行，请等待或先停止')
    (job/'CANCEL').unlink(missing_ok=True)
    old=read(job/'status.json') if (job/'status.json').exists() else dict(id=job.name,created_at=time.time())
    old.update(status='queued',stage='queued',action=action,message='等待推理资源',updated_at=time.time(),error=None)
    atomic_json(job/'status.json',old)
    env=dict(os.environ,UTAWEAVE_JOB_ID=job.name)
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[2])+os.pathsep+env.get('PYTHONPATH','')
    with (job/'worker.log').open('a') as log:
        proc=subprocess.Popen([sys.executable,'-u','-m','song2voice.web.workflow','--config',cfg['config_file'],
            '--job',job.name,'--action',action],stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
    atomic_json(job/'process.json',dict(pid=proc.pid,launched_at=time.time()))
    # Reap completed child processes without tying worker lifetime to the server.
    threading.Thread(target=proc.wait,daemon=True).start()


def status(job):
    value=read(job/'status.json')
    if value['status'] in ('running','queued') and not alive(job) and time.time()-value['updated_at']>5:
        value.update(status='failed',stage='error',message='后台进程已退出，可检查日志后重试',error='Worker no longer running')
    if value.get('stage')=='render':
        matches=list((job/'render').glob('*/pipeline_status.json'))
        if matches:
            current=max(matches,key=lambda p:p.stat().st_mtime)
            pipeline=read(current);value['pipeline_stages']=pipeline.get('stages',{})
            # Commands contain local paths; the browser only needs status names.
            value['pipeline_stages']={k:v['status'] for k,v in value['pipeline_stages'].items()}
            progress=list((job/'render/guides').glob('*/generation/progress.json'))
            if progress:
                p=read(max(progress,key=lambda p:p.stat().st_mtime))
                value['chunks']={k:p[k] for k in ('completed','total') if k in p}
    return value


class Handler(ReportHandler):
    def __init__(self,*args,config,**kwargs):
        self.config=config;self.workspace=Path(config['workspace'])
        super().__init__(*args,directory=str(STATIC),**kwargs)

    def log_message(self,*args):pass

    def send_json(self,value,code=200):
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    def translate_path(self,path):
        path=unquote(urlsplit(path).path)
        if path.startswith('/files/'):
            parts=path.split('/')
            if len(parts)<4 or not re.fullmatch('[a-f0-9]{32}',parts[2]):return str(STATIC/'not-found')
            job=parts[2];root=self.workspace/'jobs'/job
            relative='/'.join(parts[3:]);target=(root/relative).resolve()
            allowed=target.suffix.lower() in ('.wav','.mp3','.flac','.ogg','.m4a','.mid','.html','.js') or target.name=='score.vr.json'
            if not target.is_relative_to(root.resolve()) or not allowed:return str(STATIC/'not-found')
            return str(target)
        if path.startswith('/uploads/'):
            try:return str(asset(self.config,identifier(path.rsplit('/',1)[-1])))
            except (OSError,ValueError):return str(STATIC/'not-found')
        target=(STATIC/(path.lstrip('/') or 'index.html')).resolve()
        return str(target if target.is_relative_to(STATIC.resolve()) else STATIC/'not-found')

    def do_GET(self):
        try:
            path=urlsplit(self.path).path
            if path=='/api/config':
                ready={k:bool(self.config.get(k) and Path(self.config[k]).exists()) for k in
                       ('render_config','reference_runtime','annotation_config','labeling_python','netease_api_module')}
                self.send_json(dict(ready=ready,version='0.2.0',defaults=dict(steps=30,cfg=.7)));return
            if path=='/api/jobs':
                jobs=sorted((self.workspace/'jobs').glob('*/status.json'),key=lambda p:p.stat().st_mtime,reverse=True)
                self.send_json([status(p.parent) for p in jobs[:30]]);return
            match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})(/log)?',path)
            if match:
                job=self.workspace/'jobs'/identifier(match[1])
                if match[2]:
                    logs=[job/'worker.log',*job.glob('render/*/*.log')]
                    chunks=[]
                    for p in sorted(logs,key=lambda p:p.stat().st_mtime if p.exists() else 0)[-4:]:
                        if not p.exists():continue
                        with p.open('rb') as f:f.seek(max(0,p.stat().st_size-4000));tail=f.read().decode('utf-8','replace')
                        chunks.append(p.name+'\n'+tail)
                    self.send_json(dict(text='\n'.join(chunks)));return
                self.send_json(dict(status=status(job),prepared=read(job/'prepared.json') if (job/'prepared.json').exists() else None,
                    render_request=read(job/'render-request.json') if (job/'render-request.json').exists() else None,
                    reference=read(job/'reference-info.json') if (job/'reference-info.json').exists() else None));return
            if path.startswith('/api/'):self.send_json(dict(error='Not found'),404);return
            super().do_GET()
        except (ValueError,OSError,KeyError) as e:self.send_json(dict(error=str(e)),404)

    def body(self):
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=1024*1024:raise ValueError('JSON 请求大小必须在 1 MiB 内')
        return json.loads(self.rfile.read(size))

    def do_POST(self):
        try:
            # Custom header forces cross-origin callers through a denied preflight.
            if self.headers.get('X-UtaWeave')!='local':raise ValueError('Missing local request header')
            origin=self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'):raise ValueError('Cross-origin requests are not accepted')
            path=urlsplit(self.path).path
            with LOCK:
                if path=='/api/upload':
                    name=Path(parse_qs(urlsplit(self.path).query).get('name',[''])[0]).name
                    ext=Path(name).suffix.lower()
                    if ext not in ('.mid','.midi','.wav','.mp3','.flac','.m4a','.ogg','.aac'):raise ValueError('请上传 MIDI 或音频文件')
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=150*1024**2:raise ValueError('文件必须在 150 MiB 内')
                    if ext in ('.mid','.midi') and size>5*1024**2:raise ValueError('MIDI 文件不能超过 5 MiB')
                    token=uuid.uuid4().hex;root=self.workspace/'uploads';dest=root/(token+ext)
                    try:
                        with dest.open('wb') as f:
                            remaining=size
                            while remaining:
                                block=self.rfile.read(min(1024*1024,remaining))
                                if not block:raise ValueError('上传中断，请重试')
                                f.write(block);remaining-=len(block)
                        midi=inspect_midi(dest) if ext in ('.mid','.midi') else None
                    except BaseException:dest.unlink(missing_ok=True);raise
                    atomic_json(root/(token+'.json'),dict(file=dest.name,name=name,bytes=size))
                    self.send_json(dict(id=token,name=name,midi=midi));return
                request=self.body()
                if path=='/api/lyrics':self.send_json(fetch_lyrics(request['url']));return
                if path=='/api/jobs':
                    mode=request.get('mode')
                    if mode not in ('midi','song'):raise ValueError('请选择 MIDI 或原曲入口')
                    if mode=='midi':
                        info=inspect_midi(asset(self.config,request['midi_asset']))
                        if type(request.get('track')) is not int:raise ValueError('请选择旋律音轨')
                        if request.get('accompaniment_asset'):asset(self.config,request['accompaniment_asset'])
                    elif request.get('source_url'):song_id_from_url(request['source_url'])
                    else:asset(self.config,request['source_asset'])
                    if request.get('lyrics_url'):song_id_from_url(request['lyrics_url'])
                    bpm=request.get('bpm',120)
                    if type(bpm) not in (int,float) or not math.isfinite(bpm) or not 20<=bpm<=400:raise ValueError('BPM 必须在 20–400 之间')
                    if len(request.get('lyrics_text',''))>20000:raise ValueError('歌词过长，请分段处理')
                    job=self.workspace/'jobs'/uuid.uuid4().hex;job.mkdir()
                    atomic_json(job/'request.json',request);launch(self.config,job,'prepare')
                    self.send_json(dict(id=job.name));return
                match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})/(render|cancel|retry|midi)',path)
                if not match:raise ValueError('Unknown action')
                job=self.workspace/'jobs'/identifier(match[1]);action=match[2]
                if action=='cancel':
                    (job/'CANCEL').touch()
                    if alive(job):
                        try:os.killpg(read(job/'process.json')['pid'],signal.SIGTERM)
                        except ProcessLookupError:pass
                    self.send_json(dict(ok=True));return
                if action=='retry':launch(self.config,job,'prepare');self.send_json(dict(ok=True));return
                if not (job/'prepared.json').exists():raise ValueError('请先完成乐谱准备')
                labels=request.get('labels',[]);expected=read(job/'prepared.json')['labels']
                if not isinstance(labels,list) or len(labels)!=len(expected) or any(not isinstance(x,str) or not x.strip() or len(x)>64 for x in labels):
                    raise ValueError('请补齐所有音符的歌词读音')
                if action=='midi':
                    if alive(job):raise ValueError('任务正在执行，请稍后导出')
                    prepared=read(job/'prepared.json');original=read(job/'request.json')
                    if prepared['mode']!='midi':raise ValueError('只有 MIDI 输入可导出内嵌歌词的 MIDI')
                    export_lyric_midi(asset(self.config,original['midi_asset']),prepared['track'],prepared['notes'],labels,job/'lyrics.mid')
                    self.send_json(dict(url=f'/files/{job.name}/lyrics.mid'));return
                if request.get('confirmed') is not True:raise ValueError('请先核对歌词对应')
                if type(request.get('steps')) is not int or not 10<=request['steps']<=100:raise ValueError('采样步数范围为 10–100')
                if type(request.get('cfg')) not in (int,float) or not math.isfinite(request['cfg']) or not 0<=request['cfg']<=3:raise ValueError('CFG 范围为 0–3')
                ref=request['reference']
                if ref.get('mode') not in ('same_song','clip','audio','netease'):raise ValueError('请选择目标音色来源')
                if ref['mode'] in ('clip','audio'):asset(self.config,ref['asset'])
                elif ref['mode']=='netease':song_id_from_url(ref['url'])
                if alive(job):raise ValueError('任务正在执行')
                atomic_json(job/'render-request.json',request);launch(self.config,job,'render');self.send_json(dict(ok=True))
        except (ValueError,OSError,KeyError,TypeError) as error:
            self.send_json(dict(error=str(error)),400)


def serve(config,bind='127.0.0.1',port=8770):
    cfg=load_config(config)
    for name in ('uploads','jobs'):(Path(cfg['workspace'])/name).mkdir(parents=True,exist_ok=True)
    with ThreadingHTTPServer((bind,port),partial(Handler,config=cfg)) as server:
        print(f'UtaWeave: http://{bind}:{server.server_port}/',flush=True)
        server.serve_forever()
