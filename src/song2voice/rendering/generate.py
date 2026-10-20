"""Frozen-score, short-pause inference experiment; no relabeling or filtering."""
import argparse
from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time

import numpy as np
import soundfile as sf

from ..selection import require_reference
from ..conditioning import check_conditioning, compile_chunk_annotation, require_conditioning
from .audio import intervals, activity
from .runtime import runtime, load_model, activate_vocalrender


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8*1024**2), b''):
            h.update(block)
    return h.hexdigest()


def progress(out, status, **kw):
    data = dict(status=status, updated_at=datetime.now(timezone.utc).isoformat(), **kw)
    write(out/'progress.json', data)
    print(json.dumps(data, ensure_ascii=False), flush=True)


def plan(score, maximum=14.5, whole_lines=False, stems=None):
    words = []
    for row in score:
        cursor = row['source_window']['start_seconds']
        for i, (word, duration) in enumerate(zip(row['word'], row['word_dur'])):
            start, end = cursor, cursor+duration
            cursor = end
            if word in ('SP', 'AP'):
                continue
            notes = [j for j, owner in enumerate(row['pitch2word']) if owner == i]
            phones = [p for p, owner in zip(row['ph'], row['ph2word']) if owner == i]
            assert notes and phones
            words.append(dict(word=word, start=start, end=end, line_index=row['line_index'],
                word_index=i, pitch=[row['pitch'][j] for j in notes],
                note=[row['note'][j] for j in notes], pitch_dur=[row['pitch_dur'][j] for j in notes],
                phones=phones))
    assert all(a['end'] <= b['start']+.001 for a, b in zip(words, words[1:]))
    audio, sr = sf.read(stems/'vocal_native.wav', dtype='float32', always_2d=True)
    mono = audio.mean(1); hop = round(.01*sr); count = len(mono)//hop
    rms = np.sqrt(np.mean(mono[:count*hop].reshape(count, hop)**2, axis=1))
    protected = np.zeros(count, dtype=bool)
    for w in words:
        protected[max(0, int((w['start']-.035)/.01)):min(count, math.ceil((w['end']+.035)/.01))] = True
    quiet = intervals((rms < .005) & ~protected, minimum=.25)
    ends = [w['end'] for w in words]
    nodes = {0: dict(time=0., quiet=[0., words[0]['start']], width=words[0]['start'])}
    for a, b in quiet:
        t = (a+b)/2; index = bisect_right(ends, t)
        if not 0 < index < len(words) or words[index]['start'] < t:
            continue
        node = dict(time=t, quiet=[a, b], width=b-a)
        if index not in nodes or b-a > nodes[index]['width']:
            nodes[index] = node
    seconds = len(mono)/sr
    nodes[len(words)] = dict(time=seconds, quiet=[words[-1]['end'], seconds], width=seconds-words[-1]['end'])
    best = {0: (0., [])}
    for right in sorted(nodes)[1:]:
        options = []
        for left in sorted(nodes):
            if left >= right: break
            if left not in best: continue
            duration = words[right-1]['end']-words[left]['start']
            if duration > maximum: continue
            cost = .3/max(.1, nodes[right]['width']) if right < len(words) else 0
            if right < len(words) and words[right-1]['line_index'] == words[right]['line_index']:
                cost += 1000 if whole_lines else 10
            if whole_lines:
                # Prefer a complete original line. Merge only when a measured
                # pause cannot separate the lines; never break a lyric unit.
                line_count=len({w['line_index'] for w in words[left:right]})
                cost += 25 + (line_count-1)*35
            else:
                cost += 100 + max(0, 10-duration)**2*.3 + .015*(duration-17)**2
            options.append((best[left][0]+cost, best[left][1]+[(left, right)]))
        if options: best[right] = min(options, key=lambda v: v[0])
    if len(words) not in best:
        raise ValueError(f'No full-coverage pause plan within {maximum} seconds')
    chunks = []
    for i, (left, right) in enumerate(best[len(words)][1]):
        selected = words[left:right]
        chunks.append(dict(id=f'chunk_{i:02d}', words=selected, content_start=selected[0]['start'],
            content_end=selected[-1]['end'], content_seconds=selected[-1]['end']-selected[0]['start'],
            kana=''.join(w['word'] for w in selected), lyric_lines=sorted({w['line_index'] for w in selected}),
            left_boundary=nodes[left], right_boundary=nodes[right]))
    assert [(w['line_index'], w['word_index']) for c in chunks for w in c['words']] == [(w['line_index'], w['word_index']) for w in words]
    return dict(chunks=chunks, chunk_count=len(chunks), original_phrase_count=len(score),
        lyric_unit_count=len(words), all_lyrics_covered_once=True, max_content_seconds=maximum,
        source_seconds=seconds, quiet_rms_threshold=.005, minimum_quiet_seconds=.25)


def generate(out, batch_size, whole_lines, target_file, reference_file, checkpoint, stems):
    import torch
    activate_vocalrender()
    from vocalrender.preprocessing import rebuild_svs_prompt, create_lightweight_preprocessor
    from vocalrender.model.svs_utils import estimate_svs_duration
    from infer_vocalrender_svs_single import encode_prompt_audio
    target = read(target_file); reference = read(reference_file)
    selected = require_reference(reference['run'])
    if target.get('score_sha256') and sha(target['score'])!=target['score_sha256']:
        raise ValueError('Target score changed since its validation record')
    if batch_size<1:raise ValueError('Batch size must be positive')
    score = read(target['score'])
    out.mkdir(parents=True, exist_ok=True)
    preflight=[dict(item_name=e.get('item_name'),**check_conditioning(e)) for e in score]
    write(out/'score_preflight.json',preflight)
    require_conditioning(score)
    split = plan(score, whole_lines=whole_lines,stems=stems)
    split['whole_lyric_lines']=whole_lines
    for chunk in split['chunks']:
        chunk['annotation']=compile_chunk_annotation(chunk,score[0]['bpm'])
    write(out/'compiled_condition_preflight.json',require_conditioning([c['annotation'] for c in split['chunks']]))
    recipe=dict(score_sha256=sha(target['score']),reference_sha256=sha(selected['audio']),
        checkpoint_sha256=sha(checkpoint/'lora_weights.safetensors'),checkpoint_config_sha256=sha(checkpoint/'lora_config.json'),
        stems={name:sha(stems/name) for name in ['vocal_native.wav','instrumental.wav']},
        batch_size=batch_size,whole_lines=whole_lines,seed_base=runtime().get('seed',20261020),
        runtime=runtime(),base_model_sha256=sha(Path(runtime()['base_model'])/'model.safetensors'),
        renderer_sha256=sha(Path(__file__)),conditioning_code_sha256=sha(Path(__file__).resolve().parents[1]/'conditioning.py'))
    if (out/'recipe.json').exists() and read(out/'recipe.json')!=recipe:
        raise ValueError('Input, settings or code changed; use a new render directory')
    write(out/'recipe.json',recipe);recipe_sha=sha(out/'recipe.json')
    if (out/'manifest.json').exists() and read(out/'manifest.json')['status']=='complete':
        prior=read(out/'manifest.json')
        if prior.get('recipe_sha256')!=recipe_sha:raise ValueError('Completed render has a different recipe')
        for result in prior['results']:
            if sha(out/result['path'])!=result['sha256']:raise ValueError('Completed chunk changed')
        progress(out,'complete_reused',chunks=split['chunk_count']);return
    write(out/'chunk_plan.json', split)
    manifest = dict(status='loading', target=target, reference=selected, plan=split,
        checkpoint=str(checkpoint), checkpoint_sha256=sha(checkpoint/'lora_weights.safetensors'),
        score_sha256=sha(target['score']), stems=str(stems), results=[],recipe_sha256=recipe_sha,
        protocol=dict(native_eos=True, min_len=2, cfg_value=2., inference_timesteps=10,
            batch_size=batch_size, seed_base=runtime().get('seed',20261020), lora_scales=runtime().get('lora_scales',{}),
            quality_action='flag_only', label_changes=False,
            known_unresolved_pitch_units=sum(len(w['word_indices']) for r in preflight for w in r['warnings'] if w['code']=='unknown_lyric_pitch')))
    write(out/'manifest.json', manifest)
    progress(out, 'loading_model', chunks=split['chunk_count'], lyric_units=split['lyric_unit_count'])
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32=False; torch.backends.cudnn.allow_tf32=False
    torch.set_float32_matmul_precision('highest'); torch.manual_seed(20261020)
    model, manifest['lora_scales'] = load_model(checkpoint)
    prompt = encode_prompt_audio(selected['audio'], model, max_frames=50).float()
    manifest['reference_feature_shape'] = list(prompt.shape)
    preprocessor=create_lightweight_preprocessor(model.text_tokenizer.tokenizer)
    for chunk in split['chunks']:
        entry=chunk['annotation']
        text=rebuild_svs_prompt(entry,model.text_tokenizer.tokenizer,preprocessor=preprocessor)
        chunk.update(annotation=entry,svs_prompt=text,estimated_duration=estimate_svs_duration(text))
        assert chunk['estimated_duration']<23.5
    write(out/'chunk_plan.json', split)
    torch.save(dict(prompt=prompt.cpu(),chunks=split['chunks']),out/'inputs.pt')
    folder=out/'chunks';folder.mkdir(exist_ok=True)
    started=time.monotonic()
    for begin in range(0,len(split['chunks']),batch_size):
        batch=split['chunks'][begin:begin+batch_size];seed=runtime().get('seed',20261020)+begin//batch_size
        max_len=min(125,math.ceil((max(c['estimated_duration'] for c in batch)+3)/.16))
        cached=[folder/(c['id']+'.json') for c in batch]
        if all(p.exists() for p in cached):
            records=[read(p) for p in cached]
            assert all(sha(out/r['path'])==r['sha256'] for r in records)
            if any(r.get('recipe_sha256')!=recipe_sha for r in records):raise ValueError('Cached audio was generated with a different recipe')
            manifest['results'].extend(records)
            continue
        random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            audios=model.generate_batch(target_texts=[c['svs_prompt'] for c in batch],
                prompt_audio_feats=[prompt]*len(batch),min_len=2,max_len=max_len,
                cfg_value=2.,inference_timesteps=10,verbose=False)
        assert len(audios)==len(batch)
        for chunk,tensor in zip(batch,audios):
            raw=tensor.detach().float().cpu().numpy().flatten()
            assert np.isfinite(raw).all()
            path=folder/(chunk['id']+'.wav');sf.write(path,raw,48000,subtype='FLOAT')
            info=activity(raw)
            record=dict(id=chunk['id'],path=str(path.relative_to(out)),sha256=sha(path),seed=seed,
                activity=info,max_len_patches=max_len,hit_length_cap=len(raw)/48000>=(max_len-1)*.16,
                recipe_sha256=recipe_sha)
            write(path.with_suffix('.json'),record);manifest['results'].append(record)
        manifest['status']='rendering';write(out/'manifest.json',manifest)
        progress(out,'rendering',completed=len(manifest['results']),total=split['chunk_count'],
            elapsed_seconds=round(time.monotonic()-started,1))
    manifest['status']='generated';write(out/'manifest.json',manifest)
    progress(out,'generated',completed=len(manifest['results']),total=split['chunk_count'])


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--batch-size',type=int,default=4)
    ap.add_argument('--whole-lines',action='store_true')
    for name in ['target','reference','checkpoint','stems']:
        ap.add_argument('--'+name,type=Path,required=True)
    args=ap.parse_args()
    generate(args.out,args.batch_size,args.whole_lines,args.target,args.reference,args.checkpoint,args.stems)
