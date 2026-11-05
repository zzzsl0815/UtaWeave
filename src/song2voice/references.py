"""Two reference inputs: a provided clip, or automatic selection from a song."""
import json
import os
from pathlib import Path
import subprocess

from .score import save, sha
from .selection import atomic_json, select_reference, require_reference, selection_lock


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


def prepare_clip(clip, out):
    """Freeze a supplied clip byte-for-byte; never split or silently truncate it."""
    import hashlib
    import shutil
    import numpy as np
    import soundfile as sf
    clip, out = Path(clip).expanduser().resolve(), Path(out).resolve()
    wave, sr = sf.read(clip, dtype='float32', always_2d=True)
    duration = len(wave)/sr
    if not 1 <= duration <= 25:
        raise ValueError('Provide a 1–25 second vocal clip; use --audio for automatic splitting')
    if not np.isfinite(wave).all() or np.max(np.abs(wave.mean(axis=1))) < 1e-6:
        raise ValueError('Provided reference is non-finite or silent after mono downmix')
    recipe = dict(schema_version=1, mode='clip', source_sha256=sha(clip),
                  suffix=clip.suffix.lower(), seconds=duration, sample_rate=sr,
                  channels=wave.shape[1], processing='provided_unchanged')
    signature = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    run = out/signature
    run.mkdir(parents=True, exist_ok=True)
    with selection_lock(run):
        target = run/('provided'+clip.suffix.lower())
        if not target.exists():
            shutil.copyfile(clip, target)
        if sha(target) != recipe['source_sha256']:
            raise ValueError('Provided reference snapshot changed')
        atomic_json(run/'reference_recipe.json', recipe)
        row = dict(id='provided', path=target.name, sha256=sha(target), score=None,
                   start_seconds=0., end_seconds=duration, seconds=duration,
                   processing='provided_unchanged')
        atomic_json(run/'reference_candidates.json', dict(status='complete',
            source=dict(audio_sha256=recipe['source_sha256'], title=clip.stem), candidates=[row]))
    selection = select_reference(run, 'provided', method='provided_clip')
    atomic_json(out/'latest.json', dict(run=str(run), selection=selection))
    return run


def prepare_reference(out, *, clip=None, audio=None, from_run=None, runtime_path=None, policy=None):
    """Exactly two modes; from_run reuses separation for the automatic mode."""
    if sum(value is not None for value in (clip, audio, from_run)) != 1:
        raise ValueError('Supply exactly one of --clip, --audio or --from-run')
    if clip is not None:
        if runtime_path is not None or policy is not None:
            raise ValueError('A provided clip does not use a runtime or an automatic policy')
        return prepare_clip(clip, out)
    from .reference_auto import prepare, policy_with, digest
    selected_policy = policy_with(policy)
    if audio is not None:
        if runtime_path is None:
            raise ValueError('--audio requires --runtime-config for separation and scoring')
        runtime = json.loads(Path(runtime_path).read_text())
        # A source/policy/runtime-specific folder prevents mixing cached stems.
        recipe = dict(audio_sha256=sha(audio), runtime=runtime,
                      dereverb=selected_policy['processing']=='dereverb',
                      code_sha256=sha(Path(__file__).with_name('reference_audio.py')))
        asset_keys = ['separator_config', 'separator_weights']
        if recipe['dereverb']:
            asset_keys += ['dereverb_config', 'dereverb_weights']
        recipe['assets'] = {key: sha(runtime[key]) for key in asset_keys}
        from_run = Path(out).resolve()/'separation'/digest(recipe)[:16]
        prepare_stems(audio, from_run, runtime_path, recipe['dereverb'])
    elif runtime_path is not None:
        raise ValueError('--from-run uses the runtime recorded in the separation task')
    return prepare(from_run, out, selected_policy)
