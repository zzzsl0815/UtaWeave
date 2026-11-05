"""Immutable, resumable VR-guide → Seed-VC conversion with a frozen reference."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

from ..score import sha
from ..selection import atomic_json, require_reference, selection_lock


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def source_hashes(folder):
    folder = Path(folder)
    return {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob('*'))
            if p.is_file() and p.suffix in {'.py', '.json', '.yaml', '.yml'}
            and not any(x in p.parts for x in ('.git', '__pycache__', 'checkpoints', '.cache'))}


def resolve_config(filename):
    filename = Path(filename).resolve()
    cfg = read(filename)
    if set(cfg) - {'repo', 'python', 'assets', 'steps', 'cfg', 'fp16', 'seed'}:
        raise ValueError('Unknown Seed-VC configuration fields')
    for key in ('repo', 'python', 'assets'):
        # Do not resolve a venv interpreter symlink into the system interpreter.
        cfg[key] = os.path.abspath(filename.parent/Path(cfg[key]).expanduser())
        if not Path(cfg[key]).exists():
            raise ValueError('Missing Seed-VC '+key+': '+cfg[key])
    for key, default in [('steps', 30), ('cfg', .7), ('fp16', True), ('seed', 20261027)]:
        cfg.setdefault(key, default)
    if (type(cfg['steps']) is not int or cfg['steps'] < 1 or type(cfg['fp16']) is not bool
            or type(cfg['seed']) is not int or not 0 <= cfg['seed'] < 2**32
            or type(cfg['cfg']) not in (int, float) or not math.isfinite(cfg['cfg']) or cfg['cfg'] < 0):
        raise ValueError('Invalid Seed-VC inference settings')
    return cfg


def runtime_recipe(filename):
    cfg = resolve_config(filename)
    lock = read(Path(__file__).with_name('svc.lock.json'))
    assets = {}
    for row in lock['assets']:
        path = Path(cfg['assets'])/row['path']
        if not path.is_file() or sha(path) != row['sha256']:
            raise ValueError('Missing or corrupt Seed-VC asset: '+str(path))
        assets[row['path']] = row['sha256']
    script = """import json,sys,importlib.metadata as m
names=['torch','torchaudio','numpy','scipy','librosa','transformers','huggingface-hub',
       'einops','omegaconf','soundfile','pyworld','descript-audio-codec','descript-audiotools']
print(json.dumps(dict(python=sys.version,packages={n:m.version(n) for n in names})))
"""
    versions = json.loads(subprocess.check_output([cfg['python'], '-c', script], text=True))
    return dict(config=cfg, assets=assets, runtime=versions, upstream=source_hashes(cfg['repo']),
        implementation={p.name: sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))})


def result_valid(path, expected):
    try:
        result = read(path)
        return (result['status'] == 'complete' and result['input_signature'] == expected
                and sha(result['audio']) == result['audio_sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def convert(config_file, sources, reference_run, out):
    """sources: nonempty [{id, audio, optional title/lyric/...}], in stable order."""
    import numpy as np
    import soundfile as sf
    reference = require_reference(reference_run)
    ref_audio, ref_sr = sf.read(reference['audio'], dtype='float32', always_2d=True)
    if not 1 <= len(ref_audio)/ref_sr <= 25 or not np.isfinite(ref_audio).all():
        raise ValueError('Seed-VC reference must contain 1–25 seconds of finite audio')
    if np.max(abs(ref_audio.mean(axis=1))) < 1e-6:
        raise ValueError('Reference is silent after mono downmix')
    cases = []
    for source in sources:
        row = dict(source)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', row['id']):
            raise ValueError('Invalid guide ID')
        row['audio'] = str(Path(row['audio']).resolve())
        row['sha256'] = sha(row['audio'])
        wave, _ = sf.read(row['audio'], dtype='float32', always_2d=True)
        if not len(wave) or not np.isfinite(wave).all() or np.max(abs(wave.mean(axis=1))) < 1e-6:
            raise ValueError('Guide must contain finite, non-silent audio: '+row['id'])
        cases.append(row)
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Provide distinct, nonempty guide IDs')
    runtime = runtime_recipe(config_file)
    cfg = runtime['config']
    frozen = dict(reference='reference'+Path(reference['audio']).suffix.lower(),
                  guides={c['id']:c['id']+Path(c['audio']).suffix.lower() for c in cases})
    recipe = dict(schema_version=2, runtime=runtime, reference=reference, cases=cases, inputs=frozen,
                  protocol=dict(pitch_shift=0, auto_shift=False, length_adjust=1.))
    out = Path(out).resolve()
    run = out/digest(recipe)[:16]
    run.mkdir(parents=True, exist_ok=True)
    with selection_lock(run):
        if (run/'recipe.json').exists() and read(run/'recipe.json') != recipe:
            raise ValueError('Conversion recipe collision')
        atomic_json(run/'recipe.json', recipe)
        inputs, folder = run/'inputs', run/'seed_vc'
        inputs.mkdir(exist_ok=True); folder.mkdir(exist_ok=True)
        for source, name, expected in [(reference['audio'], frozen['reference'], reference['sha256']),
                                      *[(c['audio'], frozen['guides'][c['id']], c['sha256']) for c in cases]]:
            dst = inputs/name
            if not dst.exists():
                shutil.copyfile(source, dst)
            if sha(dst) != expected:
                raise ValueError('Frozen input corrupted: '+str(dst))
        jobs = []
        signatures = {}
        for i, case in enumerate(cases):
            seed = (cfg['seed']+i) % 2**32
            signature = digest(dict(recipe=run.name, case=case['id'], seed=seed))
            signatures[case['id']] = signature
            result = folder/(case['id']+'.json')
            if not result_valid(result, signature):
                jobs.append(dict(id=case['id'], source=str(inputs/frozen['guides'][case['id']]), seed=seed,
                    input_signature=signature, result=str(result), output=str(folder/(case['id']+'.wav'))))
        status = dict(status='running', run=str(run), total=len(cases), pending=len(jobs), quality_status='not_assessed')
        atomic_json(run/'status.json', status)
        try:
            if jobs:
                request = dict(options=cfg, assets=cfg['assets'], jobs=jobs,
                    reference=str(inputs/frozen['reference']), run=str(run))
                atomic_json(folder/'request.json', request)
                env = dict(os.environ, OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
                           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
                env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])+os.pathsep+env.get('PYTHONPATH', '')
                print('SEED-VC', len(jobs), 'pending;', folder/'worker.log', flush=True)
                with (folder/'worker.log').open('a') as log:
                    subprocess.run([cfg['python'], '-u', '-m', 'song2voice.voice_conversion.worker',
                        str(folder/'request.json')], cwd=cfg['repo'], env=env,
                        stdout=log, stderr=subprocess.STDOUT, check=True)
            results = []
            for case in cases:
                path = folder/(case['id']+'.json')
                if not result_valid(path, signatures[case['id']]):
                    raise ValueError('Incomplete or corrupt conversion: '+case['id'])
                results.append(read(path))
            from .report import build_report
            build_report(run, recipe)
            status.update(status='complete', completed=len(results), pending=0, results=results, report=str(run/'index.html'))
            atomic_json(run/'status.json', status)
            atomic_json(out/'latest.json', status)
        except Exception as error:
            status.update(status='failed', error=str(error))
            atomic_json(run/'status.json', status)
            raise
    return run
