"""Select a continuous reference by exact-window quality and effective duration."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from .score import sha
from .selection import atomic_json, inside, select_reference, selection_lock

DEFAULT_POLICY = dict(
    durations=[3., 4., 5., 6., 7., 8., 9., 10., 12., 15., 18., 21., 24.], maximum_seconds=24., stride_seconds=2.,
    candidates_per_duration=24, minimum_active_fraction=.75, maximum_quiet_seconds=1.,
    minimum_rms=.004, minimum_quality=3.5, maximum_quality_drop=.35,
    duration_bonus_per_doubling=.30, edge_penalty=.03, processing='native_vocal',
    probe_seconds=4., probe_stride_seconds=1., refinement_regions=6)


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def policy_with(overrides=None):
    policy = dict(DEFAULT_POLICY)
    if overrides:
        if set(overrides)-set(policy):
            raise ValueError('Unknown reference policy options: '+str(set(overrides)-set(policy)))
        policy.update(overrides)
    numeric = [v for k,v in policy.items() if k not in ('processing', 'durations')] + list(policy['durations'])
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numeric):
        raise ValueError('Reference policy requires finite numeric values')
    if any(type(policy[k]) is not int or policy[k] < 1 for k in ('candidates_per_duration','refinement_regions')):
        raise ValueError('candidates_per_duration must be an integer')
    if (not policy['durations'] or not 3 <= policy['maximum_seconds'] <= 25
            or any(not 3 <= d <= policy['maximum_seconds'] for d in policy['durations'])
            or not .5 <= policy['minimum_active_fraction'] <= 1
            or not 1 <= policy['minimum_quality'] <= 5
            or not 0 <= policy['maximum_quality_drop'] <= 1
            or not 0 <= policy['duration_bonus_per_doubling'] <= 1
            or policy['stride_seconds'] <= 0 or policy['candidates_per_duration'] < 1
            or policy['processing'] not in ('native_vocal', 'dereverb')
            or policy['maximum_quiet_seconds'] < 0 or policy['minimum_rms'] <= 0
            or policy['edge_penalty'] < 0 or policy['probe_stride_seconds'] <= 0
            or not 3 <= policy['probe_seconds'] <= 25):
        raise ValueError('Invalid automatic reference policy')
    return policy


def intervals(mask):
    import numpy as np
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))


def window_stats(wave, sr, start, end, quiet_threshold):
    import numpy as np
    x = wave[round(start*sr):round(end*sr)]
    hop = round(sr*.01)
    if len(x) < hop:
        raise ValueError('Empty reference window')
    frames = np.sqrt(np.mean(x[:len(x)//hop*hop].reshape(-1, hop)**2, axis=1))
    active = frames > max(.0002, float(np.quantile(frames, .9))*.08)
    quiet = max(((b-a)*.01 for a,b in intervals(~active)), default=0.)
    edge = round(.02*sr)
    return dict(start_seconds=round(start, 6), end_seconds=round(end, 6), seconds=len(x)/sr,
        active_fraction=float(active.mean()), effective_seconds=float(active.sum())*.01,
        maximum_quiet_seconds=quiet, rms=float(np.sqrt(np.mean(x.astype(float)**2))),
        quiet_edges=bool(max(np.sqrt(np.mean(x[:edge]**2)),np.sqrt(np.mean(x[-edge:]**2)))<quiet_threshold))


def rejection(row, policy):
    reasons = []
    if row['seconds'] > policy['maximum_seconds']+.001: reasons.append('too_long')
    if row['active_fraction'] < policy['minimum_active_fraction']: reasons.append('insufficient_activity')
    if row['maximum_quiet_seconds'] > policy['maximum_quiet_seconds']: reasons.append('long_silence')
    if row['rms'] < policy['minimum_rms']: reasons.append('too_quiet')
    return reasons


def rank_candidates(rows, policy):
    qualified = [r for r in rows if not rejection(r, policy) and math.isfinite(r['score'])]
    if not qualified:
        raise ValueError('No active scored references; no automatic selection made')
    best = max(r['score'] for r in qualified)
    floor = max(policy['minimum_quality'], best-policy['maximum_quality_drop'])
    ranked = []
    for source in rows:
        row = dict(source)
        reasons = rejection(row, policy)
        if not math.isfinite(row['score']) or row['score'] < floor:
            reasons.append('quality_below_threshold')
        bonus = policy['duration_bonus_per_doubling']*math.log2(max(row['effective_seconds'], .01)/7.)
        penalty = 0. if row['quiet_edges'] else policy['edge_penalty']
        row.update(eligible=not reasons, rejection_reasons=reasons, quality_threshold=floor,
                   duration_bonus=bonus, boundary_penalty=penalty,
                   utility=row['score']+bonus-penalty)
        ranked.append(row)
    ranked.sort(key=lambda r:(not r['eligible'], -r['utility'], -r['effective_seconds'], r['id']))
    if not ranked[0]['eligible']:
        raise ValueError('No reference meets the quality floor; no low-quality fallback selected')
    return ranked


def export_windows(config):
    import librosa
    import numpy as np
    import soundfile as sf
    run, policy = Path(config['out']), config['policy']
    wave, sr = sf.read(config['stem'], dtype='float32', always_2d=True)
    if not len(wave) or not np.isfinite(wave).all():
        raise ValueError('Invalid vocal stem')
    wave = wave.mean(axis=1)
    seconds = len(wave)/sr
    if seconds < min(policy['durations']):
        raise ValueError('Vocal stem is shorter than the minimum reference duration')
    hop = round(.01*sr)
    rms = np.sqrt(np.mean(wave[:len(wave)//hop*hop].reshape(-1,hop)**2, axis=1))
    quiet_threshold = max(.0003, float(np.quantile(rms,.9))*.015)
    quiet_points = [0.,seconds]+[(a+b)*.005 for a,b in intervals(rms<quiet_threshold) if b-a>=8]
    chosen = {}
    for duration in (() if config.get('anchors') else policy['durations']):
        possible = {}
        starts = list(np.arange(0,max(0., seconds-duration)+.001,policy['stride_seconds']))
        starts += [p for p in quiet_points if p+duration<=seconds]
        for proposed in starts:
            # Prefer nearby phrase boundaries without exceeding the duration limit.
            a = min(quiet_points,key=lambda p:abs(p-proposed))
            if abs(a-proposed)>.35: a = float(proposed)
            if a+duration>seconds: continue
            endpoints = [p for p in quiet_points if abs(p-(a+duration))<=.35
                         and p-a<=policy['maximum_seconds'] and p-a>=3.]
            b = min(endpoints,key=lambda p:abs(p-a-duration)) if endpoints else a+duration
            a,b = round(a,6),round(b,6)
            row = window_stats(wave,sr,a,b,quiet_threshold)
            if not rejection(row,policy): possible[(a,b)] = row
        # Equal search budget per duration, spread across the whole song.
        buckets = {}
        for key,row in sorted(possible.items(),key=lambda x:(not x[1]['quiet_edges'],-x[1]['active_fraction'],x[0])):
            buckets.setdefault(int(row['start_seconds']//20),[]).append((key,row))
        pool = []
        for depth in range(max(map(len,buckets.values()),default=0)):
            for bucket in sorted(buckets):
                if depth<len(buckets[bucket]): pool.append(buckets[bucket][depth])
        for key,row in pool[:policy['candidates_per_duration']]: chosen[key] = row
    if config.get('anchors'):
        for anchor in config['anchors']:
            left, right = anchor['start_seconds'], anchor['end_seconds']
            for duration in policy['durations']:
                for start in (left, right-duration, (left+right-duration)/2):
                    end = start+duration
                    if start < 0 or end > seconds: continue
                    row = window_stats(wave,sr,start,end,quiet_threshold)
                    if not rejection(row,policy): chosen[(round(start,6),round(end,6))] = row
    else:
        # Exhaustive short probes prevent a capped activity-based shortlist from
        # excluding an acoustically excellent region before SingMOS can hear it.
        duration = min(policy['probe_seconds'], policy['maximum_seconds'], seconds)
        for start in np.arange(0,seconds-duration+.001,policy['probe_stride_seconds']):
            row = window_stats(wave,sr,float(start),float(start+duration),quiet_threshold)
            if not rejection(row,policy): chosen[(round(float(start),6),round(float(start+duration),6))] = row
    folder = run/'candidates'
    folder.mkdir(exist_ok=True)
    common_gain = min(1.,.98/max(float(np.abs(wave).max()),1e-8))
    exported = []
    def export(row, name, role='search'):
        x = wave[round(row['start_seconds']*sr):round(row['end_seconds']*sr)]*common_gain
        x = librosa.resample(x,orig_sr=sr,target_sr=48000)
        fade = round(.003*48000)
        x[:fade] *= np.linspace(0,1,fade)
        x[-fade:] *= np.linspace(1,0,fade)
        path = folder/(name+'.wav')
        sf.write(path,x,48000,subtype='PCM_24')
        exported.append(dict(row,id=name,path=str(path.relative_to(run)),sha256=sha(path),
                             processing=policy['processing'],role=role))
    for i,row in enumerate(sorted(chosen.values(),key=lambda r:(r['start_seconds'],r['seconds']))):
        export(row,f'window_{i:03d}')
    if not exported: raise ValueError('No viable reference windows')
    atomic_json(run/'candidate_windows.json',dict(status='complete',candidates=exported,
        source_sha256=config['source']['audio_sha256'],candidate_common_gain=common_gain,
        activity_method='10ms RMS heuristic; not speaker diarization or a vocal detector',
        window_policy='Continuous source crops, no concatenation; 3ms boundary fades'))


def prepare(from_run, out, overrides=None):
    origin, out = Path(from_run).resolve(), Path(out).resolve()
    policy = policy_with(overrides)
    separation = read(origin/'separation.json')
    job = read(origin/('job.json' if (origin/'job.json').exists() else 'separation_job.local.json'))
    stem_relative = 'stems/vocal_native.wav' if policy['processing']=='native_vocal' else 'stems/vocal_for_reference.wav'
    stem = inside(origin,stem_relative)
    if separation['status']!='complete' or sha(stem)!=separation['files'][stem_relative]['sha256']:
        raise ValueError('Cached separated vocal is missing or changed')
    if sha(job['audio']) != separation['source_sha256']:
        raise ValueError('Original song changed')
    runtime = job['runtime']
    source = dict(job.get('metadata',dict(title=Path(job['audio']).stem)))
    source['audio_sha256'] = separation['source_sha256']
    package = Path(__file__).resolve().parent
    recipe = dict(schema_version=1,source=source,from_run=str(origin),separation_sha256=sha(origin/'separation.json'),
        stem_sha256=sha(stem),policy=policy,runtime=runtime,
        assets={k:sha(runtime[k]) for k in ('singmos_weights','singmos_ssl')},
        code={n:sha(package/n) for n in ('reference_auto.py','reference_worker.py','quality.py','selection.py')})
    run = out/digest(recipe)[:16]
    run.mkdir(parents=True,exist_ok=True)
    with selection_lock(run):
        atomic_json(run/'reference_recipe.json',recipe)
        def score_pass(name, anchors=None):
            stage = run/name
            stage.mkdir(exist_ok=True)
            config = dict(out=str(stage),audio=job['audio'],stem=str(stem),source=source,
                          runtime=runtime,policy=policy,anchors=anchors)
            atomic_json(stage/'job.json',config)
            if not (stage/'candidate_windows.json').exists(): export_windows(config)
            candidates = read(stage/'candidate_windows.json')['candidates']
            for row in candidates:
                if sha(inside(stage,row['path'])) != row['sha256']:
                    raise ValueError('Cached reference crop changed: '+row['id'])
            if not (stage/'quality_scores.json').exists():
                env = dict(os.environ,OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
                env['PYTHONPATH'] = str(package.parent)+os.pathsep+env.get('PYTHONPATH','')
                print('SCORING REFERENCES',stage/'score.log',flush=True)
                with (stage/'score.log').open('a') as log:
                    subprocess.run([runtime['python'],'-u','-m','song2voice.reference_worker',
                        '--config',str(stage/'job.json')],env=env,stdout=log,
                        stderr=subprocess.STDOUT,check=True)
            scored = read(stage/'quality_scores.json')
            if (scored['status']!='complete' or len(scored['scores']) != len(candidates) or
                {(r['id'],r['sha256']) for r in scored['scores']} != {(r['id'],r['sha256']) for r in candidates}):
                raise ValueError('Scored reference set does not match candidates')
            return [dict(r,id=name+'_'+r['id'],path=name+'/'+r['path']) for r in scored['scores']], scored['method']
        coarse, method = score_pass('search')
        anchors = []
        for row in sorted(coarse, key=lambda r:(-r['score'],r['id'])):
            center = (row['start_seconds']+row['end_seconds'])/2
            if any(abs(center-(r['start_seconds']+r['end_seconds'])/2)<5 for r in anchors): continue
            anchors.append(row)
            if len(anchors) == policy['refinement_regions']: break
        refined, _ = score_pass('refinement', anchors)
        # Keep one record for each exact crop, evaluated with identical processing.
        unique = {}
        for row in coarse+refined:
            unique.setdefault((row['start_seconds'],row['end_seconds']),row)
        ranked = rank_candidates(list(unique.values()),policy)
        for i,row in enumerate(ranked,1): row['shortlist_number'] = i
        atomic_json(run/'reference_candidates.json',dict(status='complete',source=source,
            candidates=ranked,policy=policy,model='SingMOS Pro',score_method=method,
            note='Predicted quality plus bounded effective-duration utility; does not measure target identity'))
        winner = ranked[0]
        decision = dict(strategy='quality_gated_effective_duration_v1',policy=policy,
            search='full-song short probes plus quality-guided duration refinement',
            formula='SingMOS + bonus_per_doubling * log2(effective_seconds / 7) - boundary_penalty',
            quality_threshold=winner['quality_threshold'],utility=winner['utility'],
            effective_seconds=winner['effective_seconds'],score=winner['score'],
            eligible_count=sum(r['eligible'] for r in ranked),candidate_count=len(ranked))
    # select_reference owns the same lock; call after releasing it.
    selection = select_reference(run,winner['id'],method='automatic',decision=decision,
                                 expected_manifest_sha256=sha(run/'reference_candidates.json'))
    atomic_json(out/'latest.json',dict(run=str(run),selection=selection))
    print(json.dumps(dict(run=str(run),selected=winner),ensure_ascii=False),flush=True)
    return run
