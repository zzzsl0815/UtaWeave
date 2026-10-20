"""Run the archived short-phrase render, alignment, retry and export baseline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .generate import read, write, sha
from ..selection import require_reference

PATH_KEYS = ('score', 'reference_run', 'checkpoint', 'stems', 'base_model',
             'vocalrender_repo', 'tifa_repo', 'tifa_weights')


def resolve_config(config_file):
    config_file = Path(config_file).resolve()
    config = read(config_file)
    for key in PATH_KEYS:
        if not config.get(key):
            raise ValueError('Missing render config field: '+key)
        value = Path(config[key]).expanduser()
        value = value if value.is_absolute() else config_file.parent/value
        config[key] = str(value.resolve())
        if not value.exists():
            raise ValueError(f'Missing {key}: {value}')
    for key in ('render_python', 'align_python', 'ffmpeg'):
        value = config.get(key, 'ffmpeg' if key == 'ffmpeg' else sys.executable)
        if '/' in value and not Path(value).is_absolute():
            value = str((config_file.parent/value).resolve())
        executable = shutil.which(value)
        if not executable:
            raise ValueError('Executable not found: '+key)
        config[key] = executable
    require_reference(config['reference_run'])
    if config.get('batch_size', 4) < 1:
        raise ValueError('batch_size must be positive')
    return config


def main(config_file, out):
    config = resolve_config(config_file)
    package = Path(__file__).resolve().parent
    sources = [*package.glob('*.py'), *package.parent.glob('*.py')]
    reference = require_reference(config['reference_run'])
    recipe = dict(config=config,
        code_sha256={str(p.relative_to(package.parent)): sha(p) for p in sorted(sources)},
        score_sha256=sha(config['score']), reference_sha256=sha(reference['audio']),
        checkpoint_sha256=sha(Path(config['checkpoint'])/'lora_weights.safetensors'),
        checkpoint_config_sha256=sha(Path(config['checkpoint'])/'lora_config.json'),
        base_sha256=sha(Path(config['base_model'])/'model.safetensors'),
        audiovae_sha256=sha(Path(config['base_model'])/'audiovae.pth'),
        aligner_sha256=sha(Path(config['tifa_weights'])/'model.pt'),
        stems_sha256={name: sha(Path(config['stems'])/name)
                      for name in ['vocal_native.wav','instrumental.wav']})
    signature = hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()
    out = Path(out).resolve()
    run = out/signature[:12]
    run.mkdir(parents=True,exist_ok=True)
    if (run/'pipeline_recipe.json').exists() and read(run/'pipeline_recipe.json') != recipe:
        raise ValueError('Pipeline recipe collision')
    write(run/'pipeline_recipe.json',recipe)
    snapshot = run/'recipe_source'
    snapshot.mkdir(exist_ok=True)
    for source in sources:
        relative = source.relative_to(package.parent)
        (snapshot/relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source,snapshot/relative)
    runtime_file = run/'runtime.local.json'
    write(runtime_file,config)
    target_file, reference_file = run/'target.local.json', run/'reference.local.json'
    write(target_file,dict(score=config['score'],score_sha256=recipe['score_sha256']))
    write(reference_file,dict(run=config['reference_run']))
    env = dict(os.environ)
    env['UTAWEAVE_RENDER_CONFIG'] = str(runtime_file)
    source_root = str(package.parents[1])
    env['PYTHONPATH'] = source_root+os.pathsep+env.get('PYTHONPATH','')
    env.update(OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
    state = dict(status='running',run=str(run),stages={},quality_status='not_assessed')
    vr, aligner = config['render_python'],config['align_python']

    def stage(name,python,module,arguments):
        command = [python,'-u','-m','song2voice.rendering.'+module,*map(str,arguments)]
        state['stages'][name] = dict(status='running',command=command)
        write(run/'pipeline_status.json',state)
        print('STAGE',name,flush=True)
        log = run/(name+'.log')
        with log.open('a') as stream:
            subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True,env=env)
        state['stages'][name].update(status='complete',log=str(log))
        write(run/'pipeline_status.json',state)

    try:
        base = run/'generation'
        arguments = ['--out',base,'--target',target_file,'--reference',reference_file,
                     '--checkpoint',config['checkpoint'],'--stems',config['stems'],
                     '--batch-size',config.get('batch_size',4)]
        if config.get('whole_lines',True):
            arguments.append('--whole-lines')
        stage('generate',vr,'generate',arguments)
        if read(base/'manifest.json')['status'] != 'complete':
            stage('align',aligner,'align',['--out',base])
        alignment = read(base/'generated_alignment.json')
        needs_retry = any(a['compressed_units'] or a['warning_length_cap'] or
                          a['warning_tempo_outside_preferred'] for a in alignment)
        selected = base
        if needs_retry:
            retries,selected = run/'retries',run/'selected'
            stage('retry',vr,'retry',['--parent',base,'--out',retries])
            stage('align_retries',aligner,'align',['--out',retries])
            stage('select',vr,'select',['--parent',base,'--retries',retries,'--out',selected])
        if read(selected/'manifest.json')['status'] != 'complete':
            stage('assemble',vr,'assemble',['--out',selected])
        manifest = read(selected/'manifest.json')
        import numpy as np
        import soundfile as sf
        checks = []
        for key,output in manifest['outputs'].items():
            wav,sr = sf.read(output['wav'],dtype='float32',always_2d=True)
            if sr != 48000 or not np.isfinite(wav).all() or np.max(abs(wav)) >= 1.:
                raise ValueError('Invalid WAV export: '+key)
            if sha(output['wav']) != output['wav_sha256'] or sha(output['mp3']) != output['mp3_sha256']:
                raise ValueError('Export hash mismatch: '+key)
            if key != 'natural' and abs(len(wav)/sr-manifest['plan']['source_seconds']) >= 1/sr:
                raise ValueError('Export source clock mismatch: '+key)
            decoded,mp3_sr = sf.read(output['mp3'],dtype='float32',always_2d=True)
            if mp3_sr != sr or not np.isfinite(decoded).all() or np.max(abs(decoded)) >= 1.:
                raise ValueError('Invalid MP3 export: '+key)
            checks.append(dict(kind=key,seconds=len(wav)/sr,decodable=True))
        state.update(status='rendered',completed_at=datetime.now(timezone.utc).isoformat(),
            final_manifest=str(selected/'manifest.json'),
            remaining_flags=read(selected/'alignment_verification.json')['warnings'],exports=checks,
            conclusion='Numerical and export checks passed; listening quality has not been certified.')
        write(run/'pipeline_status.json',state)
        write(out/'latest.json',state)
        print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
    except Exception as error:
        state.update(status='failed',error=str(error))
        write(run/'pipeline_status.json',state)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    arguments = parser.parse_args()
    main(arguments.config,arguments.out)
