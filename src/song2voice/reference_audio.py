"""Separation backend, executed in the configured SingCrawl environment."""
import argparse
import gc
import json
from pathlib import Path
import sys

from .score import save, sha


def prepare(config_path):
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    config = json.loads(Path(config_path).read_text())
    out = Path(config['out'])
    runtime = config['runtime']
    audio_dir = out/'stems'
    audio_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    msst = Path(runtime['msst_root'])
    sys.path.insert(0, str(msst))
    from inference.msst_infer import MSSeparator
    wave, sr = librosa.load(config['audio'], sr=44100, mono=False)
    if wave.ndim == 1:
        wave = np.stack([wave, wave])
    expected = config.get('metadata', {}).get('duration_seconds')
    if expected and abs(wave.shape[1]/sr-expected) > 3:
        raise ValueError('Decoded source duration does not match full-song metadata')
    print('SEPARATING', wave.shape[1]/sr, 'seconds', flush=True)
    model = MSSeparator('mel_band_roformer', runtime['separator_config'], runtime['separator_weights'],
        device='cuda', device_ids=[0], output_format='wav')
    native = model.separate(wave)['vocals']
    if native.shape != wave.T.shape or not np.isfinite(native).all():
        raise ValueError('Invalid separated vocal')
    instrumental = wave.T-native
    sf.write(audio_dir/'vocal_native.wav', native, sr, subtype='FLOAT')
    sf.write(audio_dir/'instrumental.wav', instrumental, sr, subtype='FLOAT')
    reconstruction = float(np.max(np.abs(wave.T-(native+instrumental))))
    del model, instrumental, wave
    gc.collect()
    torch.cuda.empty_cache()
    if config['policy']['dereverb']:
        print('DEREVERB', flush=True)
        model = MSSeparator('mel_band_roformer', runtime['dereverb_config'], runtime['dereverb_weights'],
            device='cuda', device_ids=[0], output_format='wav')
        dry = model.separate(native.T)['noreverb']
        if dry.shape != native.shape or not np.isfinite(dry).all():
            raise ValueError('Invalid dereverberated vocal')
        del model
    else:
        dry = native
    dry = dry.mean(axis=1)
    gain = min(1., .98/max(1e-8,float(np.max(np.abs(dry)))))
    sf.write(audio_dir/'vocal_for_reference.wav', dry*gain, sr, subtype='FLOAT')
    record = dict(status='complete', sample_rate=sr, seconds=len(dry)/sr,
        source_sha256=sha(config['audio']), dereverb=config['policy']['dereverb'],
        one_common_vocal_gain=gain, reconstruction_max_abs_error=reconstruction, files={})
    for path in audio_dir.glob('*.wav'):
        record['files'][str(path.relative_to(out))] = dict(sha256=sha(path), bytes=path.stat().st_size)
    save(out/'separation.json', record)
    print('REFERENCE_STEMS_READY', json.dumps(record, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    prepare(parser.parse_args().config)


if __name__ == '__main__':
    main()
