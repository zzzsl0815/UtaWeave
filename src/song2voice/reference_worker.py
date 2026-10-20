"""Reference windows, exact-window SingMOS and a terminal shortlist."""
import argparse
import csv
import io
from contextlib import redirect_stdout
import json
import math
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

from .references import choose_diverse
from .score import save, sha


def intervals(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges==1), np.flatnonzero(edges==-1)))


def candidates(config):
    import librosa
    out = Path(config['out'])
    wave, sr = sf.read(out/'stems/vocal_for_reference.wav', dtype='float32')
    hop = round(.01*sr)
    rms = np.sqrt(np.mean(wave[:len(wave)//hop*hop].reshape(-1,hop)**2, axis=1))
    quiet_threshold = max(.0003, float(np.quantile(rms,.9))*.015)
    quiet = [(a*.01,b*.01) for a,b in intervals(rms<quiet_threshold) if b-a>=8]
    points = sorted(set([0., len(wave)/sr]+[(a+b)/2 for a,b in quiet]))
    policy = config['policy']
    low, high = policy['minimum_seconds'], policy['preferred_seconds']
    windows = set()
    for a in points:
        possible = [b for b in points if low<=b-a<=high+.001]
        for b in possible[-2:]:
            windows.add((round(a,3),round(b,3)))
    # Fall back to bounded windows where a long phrase offers no two quiet
    # endpoints. Such endpoints are clearly marked for human listening.
    for t in np.arange(0,max(0,len(wave)/sr-high)+.001,2.):
        starts = [p for p in points if abs(p-t)<=.6]
        a = min(starts,key=lambda p:abs(p-t)) if starts else float(t)
        ends = [p for p in points if low<=p-a<=high and abs(p-(a+high))<=.6]
        b = max(ends) if ends else a+high
        if b<=len(wave)/sr:
            windows.add((round(a,3),round(b,3)))
    viable = []
    for a,b in sorted(windows):
        x = wave[round(a*sr):round(b*sr)]
        frames = rms[round(a/.01):round(b/.01)]
        if len(x)<round(low*sr) or not len(frames):
            continue
        threshold = max(.0002,float(np.quantile(frames,.9))*.08)
        active = frames>threshold
        fraction = float(active.mean())
        if fraction < policy['minimum_active_fraction'] or float(np.sqrt(np.mean(x*x)))<.004:
            continue
        silence = max((v-u)*.01 for u,v in intervals(~active)) if (~active).any() else 0.
        if silence>1.0:
            continue
        first = float(np.sqrt(np.mean(x[:round(.02*sr)]**2)))
        last = float(np.sqrt(np.mean(x[-round(.02*sr):]**2)))
        edges_quiet = first<quiet_threshold and last<quiet_threshold
        viable.append(dict(start_seconds=a,end_seconds=b,seconds=len(x)/sr,
            active_fraction=fraction,maximum_quiet_seconds=silence,quiet_edges=edges_quiet,
            flags=[] if edges_quiet else ['one_or_both_edges_inside_activity_listen_before_selecting']))
    if not viable:
        raise ValueError('No sufficiently active 6–8 second reference windows')
    # Round-robin over time bins to score the whole song, instead of only its
    # first chorus. The eventual ranking uses model scores, not this ordering.
    buckets = {}
    for row in sorted(viable,key=lambda r:(not r['quiet_edges'],-r['seconds'],-r['active_fraction'],r['start_seconds'])):
        buckets.setdefault(int(row['start_seconds']//8),[]).append(row)
    chosen = []
    for depth in range(max(map(len,buckets.values()))):
        for key in sorted(buckets):
            if depth<len(buckets[key]):chosen.append(buckets[key][depth])
            if len(chosen)>=policy['maximum_candidates']:break
        if len(chosen)>=policy['maximum_candidates']:break
    folder = out/'candidates'
    folder.mkdir(exist_ok=True)
    native,native_sr = sf.read(out/'stems/vocal_native.wav',dtype='float32',always_2d=True)
    assert native_sr==sr and len(native)==len(wave)
    native = native.mean(axis=1)
    common_gain = min(1.,.98/max(float(np.max(np.abs(native))),float(np.max(np.abs(wave))),1e-8))
    variants = [('native_vocal',native)]
    if policy['dereverb']:
        variants.append(('dereverb',wave))
    exported = []
    for i,window in enumerate(sorted(chosen,key=lambda r:r['start_seconds'])):
        for processing,source in variants:
            row = dict(window,id=f'candidate_{i+1:03d}_{processing}',processing=processing)
            x = source[round(row['start_seconds']*sr):round(row['end_seconds']*sr)]*common_gain
            x = librosa.resample(x,orig_sr=sr,target_sr=48000)
            fade = round(.003*48000)
            x[:fade] *= np.linspace(0,1,fade)
            x[-fade:] *= np.linspace(1,0,fade)
            path = folder/(row['id']+'.wav')
            sf.write(path,x,48000,subtype='PCM_24')
            row.update(path=str(path.relative_to(out)),sha256=sha(path))
            exported.append(row)
    save(out/'candidate_windows.json',dict(status='complete',candidates=exported,candidate_common_gain=common_gain,
        source_sha256=sha(config['audio']),quiet_threshold=quiet_threshold,
        window_policy='Contiguous source intervals, 6–8s; no concatenation or time stretch'))
    print('REFERENCE_WINDOWS',len(chosen),'audio variants',len(exported),'quiet edges',sum(r['quiet_edges'] for r in chosen),flush=True)


def score(config):
    import torch
    runtime, out = config['runtime'], Path(config['out'])
    torch.set_num_threads(4)
    torch.manual_seed(20261019)
    from . import quality as official
    import s3prl.upstream.wav2vec2.hubconf as ssl
    original_resolve = ssl._urls_to_filepaths
    def resolve(urls,*args,**kwargs):
        if isinstance(urls,str) and urls.endswith('/wav2vec_vox_new.pt'):
            return runtime['singmos_ssl']
        return original_resolve(urls,*args,**kwargs)
    ssl._urls_to_filepaths = resolve
    original_load = torch.load
    def load(*args,**kwargs):
        kwargs.setdefault('weights_only',False)
        return original_load(*args,**kwargs)
    torch.load = load
    model = torch.hub.load(runtime['singmos_repo'],'singmos_pro',source='local',pretrained=False,
        model_path=runtime['singmos_weights']).to('cuda:0').eval()
    capture = official.FrameScoreCapture()
    capture.register(model.decoder)
    rows = json.loads((out/'candidate_windows.json').read_text())['candidates']
    result = dict(status='scoring',model='SingMOS Pro',range=[1,5],
        method='Exact final 48kHz PCM24 WAV; official 16kHz mono FP32 evaluator and masked frame mean',
        limitation='Predicted audio quality, not speaker identity or timbre imitation accuracy',scores=[])
    try:
        for start in range(0,len(rows),4):
            batch = rows[start:start+4]
            waves = []
            for row in batch:
                if sha(out/row['path'])!=row['sha256']:raise ValueError('Candidate file changed')
                item = official.load_audio(str(out/row['path']),target_sr=16000,max_duration=None)
                if item['error']:raise ValueError('Could not decode candidate audio')
                waves.append(item['wave'])
            padded,lengths = official.pad_sequences(waves)
            with torch.inference_mode():
                lengths = lengths.to('cuda:0')
                model(padded.to('cuda:0'),lengths)
                values = official.compute_masked_mean(capture.frame_scores,lengths).cpu().numpy()
            for row,value in zip(batch,values):
                if not math.isfinite(float(value)):raise ValueError('Invalid SingMOS score')
                result['scores'].append(dict(**row,score=round(float(value),6)))
            save(out/'quality_scores.partial.json',result)
            save(out/'progress.json',dict(status='score',completed=min(start+4,len(rows)),total=len(rows)))
            print('REFERENCE_SCORED',min(start+4,len(rows)),'/',len(rows),flush=True)
    finally:
        capture.remove()
        torch.load = original_load
        ssl._urls_to_filepaths = original_resolve
    result['status'] = 'complete'
    save(out/'quality_scores.json',result)


def report(config):
    out = Path(config['out'])
    scores = json.loads((out/'quality_scores.json').read_text())
    selected = choose_diverse(scores['scores'],config['policy']['top_count'],config['policy']['maximum_overlap_fraction'])
    if not selected:raise ValueError('No ranked references')
    for i,row in enumerate(selected):
        row['shortlist_number'] = i+1
    stem = out/'stems/vocal_for_reference.wav'
    source = dict(config['metadata'])
    source['audio_sha256'] = sha(config['audio'])
    source.setdefault('title',Path(config['audio']).stem)
    record = dict(status='complete',source=source,scored_count=len(scores['scores']),
        model=scores['model'],score_method=scores['method'],candidates=selected,
        policy=config['policy'],selection=dict(status='awaiting_reference_selection',automatic_choice=False),
        source_vocal_path=str(stem.relative_to(out)),
        note='Shortlist sorted by exact-window score, excluding highly overlapping windows. Identity and doubled vocals require listening.')
    save(out/'reference_candidates.json',record)
    table = io.StringIO()
    from .terminal_review import list_candidates
    with redirect_stdout(table):list_candidates(out)
    (out/'reference_candidates.txt').write_text(table.getvalue())
    with (out/'reference_candidates.csv').open('w',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=['number','start_seconds','end_seconds','score','processing','candidate_id','path'])
        writer.writeheader()
        for row in selected:
            writer.writerow(dict(number=row['shortlist_number'],start_seconds=row['start_seconds'],
                end_seconds=row['end_seconds'],score=row['score'],processing=row.get('processing'),candidate_id=row['id'],path=row['path']))
    print('REFERENCE_SHORTLIST',json.dumps([(r['id'],r['score'],r['start_seconds'],r['end_seconds']) for r in selected]),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--stage',choices=['candidates','score','report'],required=True)
    args = parser.parse_args()
    globals()[args.stage](json.loads(args.config.read_text()))


if __name__=='__main__':main()
