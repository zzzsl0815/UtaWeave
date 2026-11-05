"""One process per upstream avoids conflicting modules and releases GPU memory."""
import json
from pathlib import Path
import random
import sys
import time
import traceback

from ..score import sha
from ..selection import atomic_json


def main(filename):
    import numpy as np
    import soundfile as sf
    import torch
    from .seed import SeedVC
    request = json.loads(Path(filename).read_text())
    torch.set_num_threads(4)
    torch.manual_seed(request['jobs'][0]['seed'])
    np.random.seed(request['jobs'][0]['seed'])
    random.seed(request['jobs'][0]['seed'])
    started = time.monotonic()
    model = SeedVC(request)
    print('MODEL READY', 'Seed-VC', time.monotonic()-started, flush=True)
    failed = False
    for job in request['jobs']:
        row = dict(id=job['id'], input_signature=job['input_signature'], seed=job['seed'],
                   source_sha256=sha(job['source']), reference_sha256=sha(request['reference']))
        try:
            torch.manual_seed(job['seed'])
            torch.cuda.manual_seed_all(job['seed'])
            random.seed(job['seed'])
            np.random.seed(job['seed'])
            torch.cuda.reset_peak_memory_stats()
            start = time.monotonic()
            with torch.inference_mode():
                audio, sr, extra = model.convert(job['source'], request['reference'])
            torch.cuda.synchronize()
            elapsed = time.monotonic()-start
            audio = np.asarray(audio, dtype=np.float32).squeeze()
            if audio.ndim != 1 or len(audio) == 0 or not np.isfinite(audio).all():
                raise ValueError('Invalid generated waveform')
            if float(np.max(np.abs(audio))) < 1e-6:
                raise ValueError('Silent generated waveform')
            sf.write(job['output'], audio, sr, subtype='FLOAT')
            source_seconds = sf.info(job['source']).duration
            seconds = len(audio)/sr
            row.update(status='complete', audio=job['output'], audio_sha256=sha(job['output']),
                sample_rate=sr, seconds=seconds, source_seconds=source_seconds,
                duration_delta_ms=1000*(seconds-source_seconds), render_seconds=elapsed,
                peak=float(np.max(np.abs(audio))), rms=float(np.sqrt(np.mean(audio.astype(float)**2))),
                peak_memory_gib=torch.cuda.max_memory_allocated()/2**30,
                warnings=['duration_changed_over_50ms'] if abs(seconds-source_seconds) > .05 else [],
                settings=extra)
            print('COMPLETE', job['id'], round(elapsed, 2), 'seconds', flush=True)
        except Exception as error:
            failed = True
            row.update(status='failed', error=str(error))
            traceback.print_exc()
        atomic_json(Path(job['result']), row)
    if failed:
        raise RuntimeError('One or more conversion jobs failed')


if __name__ == '__main__':
    main(sys.argv[1])
