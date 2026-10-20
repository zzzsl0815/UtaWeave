"""Bounded automatic retries for generated alignment warnings; frozen labels."""
import argparse
import copy
import math
from pathlib import Path
import random
import sys

import numpy as np
import soundfile as sf
import torch

from .generate import read, write, sha, progress
from .runtime import runtime, load_model
from .audio import activity


def main(parent,out):
    base=read(parent/'manifest.json');alignments=read(parent/'generated_alignment.json')
    assert base['status'] in ['complete','generated']
    flagged=[a['id'] for a in alignments if a['compressed_units'] or a['warning_length_cap'] or a['warning_tempo_outside_preferred']]
    assert flagged
    manifest=copy.deepcopy(base);manifest.update(status='loading',results=[],retry_parent=str(parent))
    manifest.pop('outputs',None);manifest.pop('completed_at',None)
    chunks=[]
    for c in base['plan']['chunks']:
        if c['id'] not in flagged:continue
        for attempt in range(2):
            candidate=copy.deepcopy(c);candidate['original_chunk_id']=c['id']
            candidate['id']=c['id']+f'_retry{attempt+1}';chunks.append(candidate)
    manifest['plan']['chunks']=chunks;manifest['plan']['chunk_count']=len(chunks)
    manifest['protocol']['retry_rule']='Two samples for compressed units or tempo outside preferred range; preserve all attempts.'
    out.mkdir(parents=True,exist_ok=True);write(out/'manifest.json',manifest)
    write(out/'chunk_plan.json',manifest['plan'])
    progress(out,'loading_retry_model',source_chunks=flagged,candidates=len(chunks))
    torch.set_num_threads(4);torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    torch.set_float32_matmul_precision('highest')
    model, _ = load_model(Path(base['checkpoint']))
    prompt=torch.load(parent/'inputs.pt',weights_only=False)['prompt'].float()
    folder=out/'chunks';folder.mkdir(exist_ok=True)
    for start in range(0,len(chunks),4):
        batch=chunks[start:start+4];seed=runtime().get('seed',20261020)+500+start//4
        max_len=min(125,math.ceil((max(c['estimated_duration'] for c in batch)+3)/.16))
        random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            audios=model.generate_batch(target_texts=[c['svs_prompt'] for c in batch],prompt_audio_feats=[prompt]*len(batch),
                min_len=2,max_len=max_len,cfg_value=2.,inference_timesteps=10,verbose=False)
        for c,tensor in zip(batch,audios):
            raw=tensor.detach().float().cpu().numpy().flatten();assert np.isfinite(raw).all()
            path=folder/(c['id']+'.wav');sf.write(path,raw,48000,subtype='FLOAT')
            result=dict(id=c['id'],original_chunk_id=c['original_chunk_id'],path=str(path.relative_to(out)),
                sha256=sha(path),seed=seed,activity=activity(raw),max_len_patches=max_len,
                hit_length_cap=len(raw)/48000>=(max_len-1)*.16)
            write(path.with_suffix('.json'),result);manifest['results'].append(result)
        write(out/'manifest.json',manifest);progress(out,'retry_rendering',completed=len(manifest['results']),total=len(chunks))
    manifest['status']='generated';write(out/'manifest.json',manifest);progress(out,'generated',candidates=len(chunks))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();main(a.parent,a.out)
