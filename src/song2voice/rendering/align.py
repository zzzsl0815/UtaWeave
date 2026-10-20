"""Align generated audio against the frozen Japanese/English phone sequence."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf
import torch
import torchaudio
import yaml

from .generate import read, write, progress
from .runtime import runtime


def main(out):
    torch.set_num_threads(4)
    manifest = read(out/'manifest.json')
    assert manifest['status']=='generated'
    config=runtime()
    sys.path.insert(0,str(Path(config['tifa_repo']).resolve()))
    from inference.backend import ForcedAlignmentInferenceModel
    from lib.config.schema import ConfigurationScope, ModelConfig, InferenceConfig
    from lib.vocabulary import Vocabulary
    model_path=Path(config['tifa_weights'])
    cfg=yaml.safe_load((model_path/'config.yaml').read_text())
    model=ForcedAlignmentInferenceModel(
        ModelConfig.model_validate(cfg['model'],scope=ConfigurationScope.FA),
        InferenceConfig.model_validate({'features':cfg['inference']['features']}))
    model.load_state_dict(torch.load(model_path/'model.pt',map_location='cpu',weights_only=True)['state_dict'],strict=True)
    model=model.to('cuda').eval()
    vocab=Vocabulary.from_file(model_path/'vocabulary.json')
    audits=[]
    for chunk, result in zip(manifest['plan']['chunks'],manifest['results']):
        assert chunk['id']==result['id']
        audio,sr=sf.read(out/result['path'],dtype='float32')
        wave=torch.from_numpy(audio)
        if sr!=model.sample_rate:
            wave=torchaudio.functional.resample(wave,sr,model.sample_rate)
        duration=len(wave)/model.sample_rate
        wave=torch.nn.functional.pad(wave,(0,(-len(wave))%model.spec_fn.hop_length)).to('cuda')
        phones=[]; mapping=[]
        for i,w in enumerate(chunk['words']):
            phones.extend(w['phones']); mapping.extend([i]*len(w['phones']))
        with torch.inference_mode():
            spec=model.spectrogram(wave[None],torch.tensor([duration],device='cuda'))
            aligned=model.align(spec,tokens=torch.tensor([[vocab.encode(p,'ja') for p in phones]],device='cuda'),
                groups=torch.tensor([[i+1 for i in mapping]],device='cuda'),unit='frame',skip_penalty=.5)
        spans=np.clip(aligned.spans[0].cpu().numpy()*model.timestep,0,len(audio)/sr)
        units=[]
        for i,w in enumerate(chunk['words']):
            indices=[j for j,owner in enumerate(mapping) if owner==i]
            units.append(dict(line_index=w['line_index'],word_index=w['word_index'],word=w['word'],
                source_start=w['start'],source_end=w['end'],
                generated_start=float(spans[indices[0],0]),generated_end=float(spans[indices[-1],1]),
                has_measured_or_inferred_pitch=any(p>0 for p in w['pitch'])))
        # Zero-pitch 10 ms alignment artifacts remain in the audio and score,
        # but do not serve as clock anchors for time stretching.
        anchors=[u for u in units if u['has_measured_or_inferred_pitch'] and
            u['source_end']-u['source_start']>=.03 and u['generated_end']-u['generated_start']>=.03]
        assert len(anchors)>=2
        first,last=anchors[0],anchors[-1]
        generated_span=last['generated_end']-first['generated_start']
        source_span=last['source_end']-first['source_start']
        ratio=generated_span/source_span
        placement=first['source_start']-first['generated_start']/ratio
        tiny=[i for i,u in enumerate(units) if u['generated_end']-u['generated_start']<.015]
        compressed=[i for i,u in enumerate(units) if u['has_measured_or_inferred_pitch'] and
            u['source_end']-u['source_start']>.08 and u['generated_end']-u['generated_start']<.03]
        record=dict(id=chunk['id'],agreement=float(aligned.agreement[0]),units=units,
            first_lyric_start=units[0]['generated_start'],last_lyric_end=units[-1]['generated_end'],
            clock_anchor_first=dict(line_index=first['line_index'],word_index=first['word_index']),
            clock_anchor_last=dict(line_index=last['line_index'],word_index=last['word_index']),
            tempo_ratio=ratio,placement_seconds=placement,tiny_units=tiny,compressed_units=compressed,
            english_phones=sum(p.startswith('en/') for p in phones),
            warning_tempo_outside_preferred=not .85<ratio<1.18,
            warning_tempo_outside_limit=not .8<ratio<1.25,
            warning_length_cap=result['hit_length_cap'])
        audits.append(record)
        write(out/'generated_alignment.json',audits)
        progress(out,'aligning_generated',completed=len(audits),total=len(manifest['results']),
            chunk=chunk['id'],tempo_ratio=round(ratio,4),tiny_units=len(tiny))
    assert len(audits)==manifest['plan']['chunk_count']
    write(out/'alignment_verification.json',dict(status='complete',all_phone_sequences_from_frozen_score=True,
        lyric_units=sum(len(r['units']) for r in audits),chunks=len(audits),
        english_phone_count=sum(r['english_phones'] for r in audits),
        warnings=[dict(chunk=r['id'],tempo_ratio=r['tempo_ratio'],tiny_units=r['tiny_units'],
            compressed_units=r['compressed_units'],length_cap=r['warning_length_cap'])
            for r in audits if r['warning_tempo_outside_preferred'] or r['tiny_units'] or
                r['compressed_units'] or r['warning_length_cap']]))
    progress(out,'aligned',chunks=len(audits))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    main(p.parse_args().out)
