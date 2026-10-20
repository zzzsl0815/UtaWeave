"""Reusable reference preparation and explicit human selection."""
import json
import os
from pathlib import Path
import subprocess

from .score import save, sha
from .selection import select_reference, reference_status


def prepare_stems(audio, out, runtime_path, dereverb=True):
    """Reuse the same separator for a target song without scoring references."""
    audio, out = Path(audio).resolve(), Path(out).resolve()
    runtime = json.loads(Path(runtime_path).read_text())
    config = dict(audio=str(audio),out=str(out),runtime=runtime,policy=dict(dereverb=dereverb))
    out.mkdir(parents=True,exist_ok=True)
    job = out/'separation_job.local.json'
    marker = out/'separation.json'
    if job.exists() and json.loads(job.read_text()) != config:
        raise ValueError('Separation configuration changed; use a new output directory')
    save(job,config)
    if marker.exists():
        record = json.loads(marker.read_text())
        if record['source_sha256'] != sha(audio):
            raise ValueError('Source audio changed')
        for name, asset in record['files'].items():
            if sha(out/name) != asset['sha256']:
                raise ValueError('Cached stem changed: '+name)
        return record
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])+os.pathsep+env.get('PYTHONPATH','')
    with (out/'separate.log').open('a') as stream:
        subprocess.run([runtime['python'],'-u','-m','song2voice.reference_audio','--config',str(job)],
            env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
    return json.loads(marker.read_text())


def reference_recipe(audio, runtime, policy):
    """A cached reference run is bound to inputs, weights and backend code."""
    package = Path(__file__).resolve().parent
    assets = {key:dict(path=runtime[key],sha256=sha(runtime[key])) for key in [
        'separator_config','separator_weights','singmos_weights','singmos_ssl']}
    if policy['dereverb']:
        assets.update({key:dict(path=runtime[key],sha256=sha(runtime[key])) for key in ['dereverb_config','dereverb_weights']})
    return dict(source_sha256=sha(audio),policy=policy,runtime=runtime,assets=assets,
        code={name:sha(package/name) for name in ['reference_audio.py','reference_worker.py','references.py']})


def choose_diverse(candidates, count=8, maximum_overlap_fraction=.1):
    if count < 1 or not 0 <= maximum_overlap_fraction <= 1:
        raise ValueError('Invalid candidate selection parameters')
    selected = []
    for row in sorted(candidates, key=lambda r:(-r['score'], r['id'])):
        if any(max(0., min(row['end_seconds'], old['end_seconds'])-
                   max(row['start_seconds'], old['start_seconds'])) /
               min(row['end_seconds']-row['start_seconds'], old['end_seconds']-old['start_seconds'])
               > maximum_overlap_fraction for old in selected):
            continue
        selected.append(row)
        if len(selected) == count:
            break
    return selected


def prepare_reference(audio, out, runtime_path, metadata=None, top_count=8, dereverb=True):
    audio, out = Path(audio).resolve(), Path(out).resolve()
    runtime = json.loads(Path(runtime_path).read_text())
    meta = json.loads(Path(metadata).read_text()) if metadata else dict(title=audio.stem, artists=[])
    config = dict(audio=str(audio), out=str(out), metadata=meta, runtime=runtime,
        policy=dict(dereverb=dereverb, compare_native_and_dereverb=True, preferred_seconds=8., minimum_seconds=6.,
            minimum_active_fraction=.75, maximum_candidates=96, top_count=top_count,
            maximum_overlap_fraction=.1))
    out.mkdir(parents=True, exist_ok=True)
    job = out/'job.json'
    if job.exists() and json.loads(job.read_text()) != config:
        raise ValueError('Reference job configuration changed; choose a new output directory')
    save(job, config)
    completed = out/'reference_candidates.json'
    if completed.exists():
        result = json.loads(completed.read_text())
        if result.get('status')!='complete' or result['source']['audio_sha256']!=sha(audio):
            raise ValueError('Frozen reference task does not match the input')
        state = reference_status(out)
        if state['state']!='ready':
            for candidate in result['candidates']:
                if sha(out/candidate['path'])!=candidate['sha256']:
                    raise ValueError('Frozen reference candidate changed')
        # Fully prepared outputs retain their original recipe. Updating the
        # selector or moving to a machine without models never resynthesizes
        # them. An explicit new task is required to change preparation.
        result.update(reference_state=state['state'],selection=state['selection'] or dict(status=state['state']))
        save(out/'progress.json',dict(status=state['state'],selection=result['selection']['status']))
        return result
    recipe = reference_recipe(audio,runtime,config['policy'])
    marker = out/'reference_recipe.json'
    if marker.exists() and json.loads(marker.read_text()) != recipe:
        raise ValueError('Reference inputs, model assets or implementation changed; choose a new output directory')
    save(marker,recipe)
    env = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH'] = source_root+os.pathsep+env.get('PYTHONPATH','')
    env.update(OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', PYTHONUNBUFFERED='1')
    for stage, marker, module in [
        ('separate','separation.json','song2voice.reference_audio'),
        ('candidates','candidate_windows.json','song2voice.reference_worker'),
        ('score','quality_scores.json','song2voice.reference_worker'),
        ('report','reference_candidates.json','song2voice.reference_worker')]:
        path = out/marker
        if path.exists():
            previous = json.loads(path.read_text())
            if previous.get('status') != 'complete':
                raise ValueError('Incomplete cached stage: '+stage)
            if stage == 'separate':
                if previous['source_sha256'] != sha(audio):
                    raise ValueError('Source audio changed')
                for relative, record in previous['files'].items():
                    if sha(out/relative) != record['sha256']:
                        raise ValueError('Cached stem changed: '+relative)
            if stage in ['candidates','score','report']:
                for record in previous.get('candidates', previous.get('scores', [])):
                    if sha(out/record['path']) != record['sha256']:
                        raise ValueError('Cached candidate changed: '+record['id'])
            print('REFERENCE_CACHE', stage, flush=True)
            continue
        save(out/'progress.json', dict(status=stage))
        command = [runtime['python'], '-u', '-m', module, '--config', str(job)]
        if stage != 'separate':
            command += ['--stage', stage]
        print('REFERENCE_STAGE', stage, flush=True)
        try:
            with (out/(stage+'.log')).open('a') as log:
                subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        except subprocess.CalledProcessError:
            save(out/'progress.json', dict(status='failed', stage=stage, log=stage+'.log'))
            raise ValueError('Reference preparation failed in '+stage+'; see local stage log') from None
    state = reference_status(out)
    save(out/'progress.json',dict(status=state['state']))
    result = json.loads((out/'reference_candidates.json').read_text())
    result.update(reference_state=state['state'],selection=state['selection'] or dict(status=state['state']))
    return result
