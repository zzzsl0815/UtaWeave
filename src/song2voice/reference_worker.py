"""Score exact reference windows with SingMOS Pro in its isolated runtime."""
import argparse
import json
import math
from pathlib import Path
from .score import save, sha


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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    score(json.loads(args.config.read_text()))
