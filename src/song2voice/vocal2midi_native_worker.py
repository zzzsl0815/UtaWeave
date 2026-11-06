"""Audio-only Vocal2Midi labeling; no LRC, downloaded lyrics or romaji input.

Calls upstream slicing, ASR text preparation, TiFA and GAME. A small language
router selects the upstream Japanese/Mandarin/English paths for mixed singing.
VR serialization is a separate output adapter, not a pronunciation correction.
"""
import argparse
from collections import Counter
import gc
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess

from .score import save, sha
from .vocal2midi import UPSTREAM_COMMIT, use_upstream, aligned_words_to_annotation
from contextlib import contextmanager

NON_LYRICS = {'SP', 'AP', 'EP', 'sil', 'pau', 'br'}
FORBIDDEN_INPUTS = {'reference_moras', 'reference_audit', 'source_intervals',
                    'original_lyrics', 'lyrics_json', 'lyrics_file', 'romalrc', 'lrc'}


@contextmanager
def bounded_session_threads(count):
    """Configure upstream-created CPU sessions in this isolated worker only.

    Several graph bundles otherwise each create a full machine-sized pool.
    Restore the factory immediately after synchronous model construction;
    audio processing and all neural graph operations remain upstream.
    """
    import onnxruntime as ort
    original = ort.InferenceSession
    def create(path, sess_options=None, *args, **kwargs):
        options = sess_options if sess_options is not None else ort.SessionOptions()
        options.intra_op_num_threads = count
        options.inter_op_num_threads = 1
        return original(path, options, *args, **kwargs)
    ort.InferenceSession = create
    try: yield
    finally: ort.InferenceSession = original


def validate_audio_only_config(config):
    if config.get('language','ja') not in ('ja','zh','en'):
        raise ValueError('Unsupported song language')
    supplied = FORBIDDEN_INPUTS.intersection(config)
    if supplied:
        raise ValueError('Audio-only labeling does not accept reference inputs: '+', '.join(sorted(supplied)))
    if not config.get('audio'):
        raise ValueError('Audio-only labeling requires an audio file')


def text_languages(text, primary='ja'):
    langs = []
    if re.search(r'[\u3040-\u30ff]',text):
        if primary=='zh':raise ValueError('中文识别结果含日语假名，请核对输入语言')
        langs.append('ja')
    if re.search(r'[\u3400-\u9fff]', text):
        lang='zh' if primary=='zh' else 'ja'
        if lang not in langs:langs.append(lang)
    if re.search(r'[A-Za-z]', text): langs.append('en')
    return langs


def language_spans(words, duration):
    """Split a mixed ASR chunk only at boundaries found by upstream TiFA."""
    groups = []
    for word in words:
        if word.text in NON_LYRICS: continue
        tags = {p.text.split('/')[0] for p in word.phonemes if '/' in p.text}
        if len(tags) != 1 or not tags <= {'ja', 'zh', 'en'}:
            raise ValueError('Mixed-language routing needs explicit phone language tags')
        language = tags.pop()
        if groups and groups[-1]['language'] == language:
            groups[-1]['end'] = word.end
        else:
            groups.append(dict(language=language, start=word.start, end=word.end))
    if not groups: raise ValueError('No lyric words in mixed-language alignment')
    boundaries = [0.] + [(a['end']+b['start'])/2 for a,b in zip(groups, groups[1:])] + [duration]
    return [dict(language=g['language'], start=a, end=b)
            for g,a,b in zip(groups,boundaries,boundaries[1:])]


def bound_audio_chunks(chunks, sr, minimum=5., maximum=10., splitter=None):
    """Bound upstream default segments without losing or duplicating samples."""
    if not 0 < minimum <= maximum <= 10:
        raise ValueError('ASR slice bounds must satisfy 0 < min <= max <= 10 seconds')
    if splitter is None:
        from inference.API.slicer_api import _sliding_window_split
        splitter = _sliding_window_split
    result = []
    for chunk in chunks:
        wave = chunk['waveform']
        if len(wave)/sr <= maximum:
            result.append(chunk)
            continue
        pieces = splitter(wave, sr, minimum, maximum, -30., 2048, 512)
        cursor = 0
        for piece in pieces:
            start = int(piece['offset']*sr)
            count = len(piece['waveform'])
            if start != cursor or not 0 < count <= round(maximum*sr):
                raise ValueError('Slicer changed coverage or exceeded ASR input capacity')
            # Slice by the original sample clock instead of relying on copied data.
            result.append(dict(offset=chunk['offset']+cursor/sr, waveform=wave[cursor:cursor+count]))
            cursor += count
        if cursor != len(wave):
            raise ValueError('Slicer lost samples')
    return result


def check_asr_capacity(samples, sample_rate, fixed_samples, asr_sample_rate):
    if isinstance(fixed_samples, int) and math.ceil(samples*asr_sample_rate/sample_rate) > fixed_samples:
        raise ValueError('ASR input exceeds model capacity; split before recognition, never truncate')


def record_empty_asr(row, rms, typical_rms):
    """Retain a review record for weak unrecognized residue; never discard strong singing."""
    if rms > .2*typical_rms:
        raise ValueError(f'Upstream ASR produced no lyrics for active chunk {row["line_index"]}')
    return dict(row, status='unrecognized_low_energy', rms=rms, typical_rms=typical_rms,
                action='audio retained for review; no invented lyric or pitch',
                limitation='Low energy and empty ASR do not prove absence of singing')


class MixedG2P:
    def __init__(self, pipeline, primary='ja'): self.pipeline, self.primary = pipeline, primary
    def convert(self, text, **kwargs):
        return self.pipeline.convert(text, languages=list(dict.fromkeys([self.primary,'en'])))


def main(config):
    validate_audio_only_config(config)
    primary_language=config.get('language','ja')
    use_upstream(config['upstream_repo'])
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_asr import Qwen3ASRModel
    from inference.io.audio_io import load_audio
    from inference.API.slicer_api import slice_audio_with_custom_bounds
    from inference.API.asr_api import _sanitize_qwen_asr_results, DEFAULT_QWEN_ASR_PROMPT_EN
    from inference.API.lfa_api import process_asr_to_phonemes, _direct_moras_to_display_tokens
    from inference.romaji_asr.runtime import RomajiASROnnxModel
    from inference.TiFA.runtime import load_tifa_model
    from inference.TiFA.g2p_pipeline import build_g2p_pipeline
    from inference.TiFA.aligner import run_tifa_fa
    from inference.API.game_api import load_game_model, extract_pitches_and_align
    from inference.io.note_io import _save_text, _save_midi
    from .conditioning import quantize_annotation, require_conditioning

    out = Path(config['out']); out.mkdir(parents=True, exist_ok=True)
    if config.get('ffmpeg'): os.environ['V2M_FFMPEG'] = config['ffmpeg']
    commit = subprocess.check_output(['git','-C',config['upstream_repo'],'rev-parse','HEAD'], text=True).strip()
    if commit != UPSTREAM_COMMIT: raise ValueError('Unexpected Vocal2Midi revision')
    recipe = dict(config=config, audio_sha256=sha(config['audio']), upstream_commit=commit,
                  worker_sha256=sha(Path(__file__)), adapter_sha256=sha(Path(__file__).with_name('vocal2midi.py')),
                  reference_inputs_used=False)
    recipe['model_releases'] = {}
    for key in ['romaji_model','qwen_model','tifa_model','game_model','kashi_model']:
        if not config.get(key): continue
        directory = Path(config[key])
        release = directory/'release.json'
        if not release.exists(): release = directory.parent/'release.json'
        recipe['model_releases'][key] = sha(release) if release.exists() else {
            p.name:sha(p) for p in directory.glob('*.json')}
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    if (out/'recipe.json').exists() and json.loads((out/'recipe.json').read_text()) != dict(sha256=digest,**recipe):
        raise ValueError('Output belongs to another input/code revision')
    save(out/'recipe.json', dict(sha256=digest,**recipe))
    if (out/'manifest.json').exists():
        previous = json.loads((out/'manifest.json').read_text())
        if previous.get('status') == 'exported' and sha(out/'score.vr.json') == previous['score_sha256']:
            print('Already complete:',out,flush=True); return
    torch.set_num_threads(config.get('cpu_threads',4))
    wave, sr = load_audio(config['audio'], 44100)
    native_chunks = slice_audio_with_custom_bounds(wave, sr, 'default', min_len_sec=5., max_len_sec=10.)
    chunks = bound_audio_chunks(native_chunks, sr)
    native_dir = out/'native_chunks'; native_dir.mkdir(exist_ok=True)
    rows = []
    for i, chunk in enumerate(chunks):
        path = native_dir/f'chunk_{i}.wav'; sf.write(path,chunk['waveform'],sr,subtype='PCM_24')
        rows.append(dict(index=i,audio=str(path),start_seconds=float(chunk['offset']),
                         end_seconds=float(chunk['offset'])+len(chunk['waveform'])/sr))
    save(out/'native_chunks.json',rows)
    save(out/'slicing_audit.json',dict(original_chunks=len(native_chunks),bounded_chunks=len(chunks),
        maximum_seconds=max(len(c['waveform'])/sr for c in chunks),
        original_retained_samples=sum(len(c['waveform']) for c in native_chunks),
        bounded_retained_samples=sum(len(c['waveform']) for c in chunks),
        policy='Retain upstream default voiced intervals; split long intervals at low-energy boundaries'))
    typical_rms=float(np.quantile([np.sqrt(np.mean(c['waveform'].astype(float)**2)) for c in chunks], .75))
    print('UPSTREAM_SLICES',len(rows),flush=True)

    # This is the same Qwen3-ASR model family as upstream; Transformers/CUDA
    # replaces the Windows ONNX/llama.cpp runtime, not the recognition task.
    asr = Qwen3ASRModel.from_pretrained(config['qwen_model'], dtype=torch.bfloat16,
            device_map='cuda:0', max_inference_batch_size=4,max_new_tokens=256,
            attn_implementation='sdpa')
    detection_file = out/'language_detection.json'
    detection = json.loads(detection_file.read_text()) if detection_file.exists() else []
    for begin in range(len(detection),len(rows),4):
        # Match upstream's explicit song-language setting. Unconstrained
        # detection can translate short sung fragments into another language.
        # Read raw text before the upstream language filter for code switching.
        primary = {'ja':'Japanese','zh':'Chinese','en':'English'}[primary_language]
        found = asr.transcribe(audio=[r['audio'] for r in rows[begin:begin+4]],language=primary,context='')
        detection.extend(dict(row,detected_language=p.language,raw_text=p.text)
                         for row,p in zip(rows[begin:begin+4],found))
        save(detection_file,detection)
        print('LANGUAGE_ASR',len(detection),'/',len(rows),flush=True)
    if len(detection) != len(rows): raise ValueError('Incomplete language-recognition output')
    with bounded_session_threads(config.get('cpu_threads',4)):
        tifa = load_tifa_model(config['tifa_model'],device='cpu')
        g2p = build_g2p_pipeline(config['tifa_model'],
            japanese_g2p_engine='kashi-g2p-onnx',kashi_g2p_model_dir=config.get('kashi_model'))
        romaji = RomajiASROnnxModel.from_model_path(config['romaji_model'],device='cpu') if primary_language!='zh' else None

    routing = out/'routing'; routing.mkdir(exist_ok=True)
    mixed_rows = []
    for row in detection:
        langs = text_languages(row['raw_text'],primary_language)
        if not langs: langs=[config.get('language','ja')]
        if len(langs)>1:
            (routing/f'chunk_{row["index"]}.txt').write_text(row['raw_text'],encoding='utf-8')
            # Same audio samples as the native slicer; a hard link is sufficient.
            destination = routing/f'chunk_{row["index"]}.wav'
            if not destination.exists():os.link(row['audio'],destination)
            mixed_rows.append(row)
    with bounded_session_threads(config.get('cpu_threads',4)):
        mixed_pred, _ = run_tifa_fa(tifa,routing,language='mixed',g2p_pipeline=MixedG2P(g2p,primary_language)) if mixed_rows else ({},{})
    final_rows = []; route_audit = []
    for row in detection:
        langs = text_languages(row['raw_text'],primary_language) or [primary_language]; duration=row['end_seconds']-row['start_seconds']
        if len(langs)==1: spans=[dict(language=langs[0],start=0.,end=duration)]
        else:
            if f'chunk_{row["index"]}' not in mixed_pred:
                raise ValueError(f'Upstream TiFA could not locate the language change in chunk {row["index"]}')
            spans=language_spans(mixed_pred[f'chunk_{row["index"]}'][2],duration)
        source,_=sf.read(row['audio'],dtype='float32')
        for span in spans:
            index=len(final_rows); directory=out/'alignment_inputs'/span['language'];directory.mkdir(parents=True,exist_ok=True)
            path=directory/f'chunk_{index}.wav';a=round(span['start']*sr);b=round(span['end']*sr)
            sf.write(path,source[a:b],sr,subtype='PCM_24')
            final_rows.append(dict(line_index=index,native_chunk_index=row['index'],language=span['language'],
                audio=str(path),start_seconds=row['start_seconds']+a/sr,end_seconds=row['start_seconds']+b/sr))
        route_audit.append(dict(row,spans=spans))
    save(out/'language_routing.json',route_audit)
    empty_rows=[]
    for begin in range(0,len(final_rows),4):
        group=final_rows[begin:begin+4]
        english=[r for r in group if r['language']=='en']
        english_predictions=asr.transcribe(audio=[r['audio'] for r in english],language='English',
                          context=DEFAULT_QWEN_ASR_PROMPT_EN) if english else []
        english_results=iter(_sanitize_qwen_asr_results([dict(text=p.text) for p in english_predictions],'en'))
        chinese=[r for r in group if r['language']=='zh']
        chinese_predictions=asr.transcribe(audio=[r['audio'] for r in chinese],language='Chinese',context='') if chinese else []
        chinese_results=iter(_sanitize_qwen_asr_results([dict(text=p.text) for p in chinese_predictions],'zh'))
        for row in group:
            index=row['line_index'];directory=Path(row['audio']).parent
            if row['language']=='en':
                raw=next(english_results)
                chars, logs=process_asr_to_phonemes([raw],[index],directory,'en',None,lyric_output_mode='word')
                row.update(text=raw['text'],raw_asr=raw,chars=chars.get(f'chunk_{index}',[]),upstream_log=logs)
            elif row['language']=='zh':
                raw=next(chinese_results)
                chars,logs=process_asr_to_phonemes([raw],[index],directory,'zh',None,lyric_output_mode='hanzi')
                row.update(text=raw['text'],raw_asr=raw,chars=chars.get(f'chunk_{index}',[]),upstream_log=logs)
            else:
                info=sf.info(row['audio'])
                check_asr_capacity(info.frames, info.samplerate, romaji.session.get_inputs()[0].shape[-1], romaji.sample_rate)
                raw=romaji.transcribe([row['audio']],batch_size=1)[0]
                chars,logs=process_asr_to_phonemes([raw],[index],directory,'ja',None,
                        lyric_output_mode='kana',use_asr_phonemes=True,
                        japanese_g2p_engine='kashi-g2p-onnx',kashi_g2p_model_path=config.get('kashi_model',''))
                # Pinned-revision interface fix: TiFA prefers .txt, which
                # must carry the mora labels in .lab rather than separated phones.
                lab=directory/f'chunk_{index}.lab'
                if lab.exists():(directory/f'chunk_{index}.txt').write_bytes(lab.read_bytes())
                row.update(text=raw['text'],raw_asr=raw,chars=chars.get(f'chunk_{index}',[]),upstream_log=logs)
            if not row['chars']:
                samples,_=sf.read(row['audio'],dtype='float32')
                empty_rows.append(record_empty_asr(row,float(np.sqrt(np.mean(samples.astype(float)**2))),typical_rms))
                save(out/'unrecognized_segments.json',empty_rows)
        save(out/'recognition.json',final_rows)
        print('FINAL_ASR',min(begin+4,len(final_rows)),'/',len(final_rows),flush=True)
    final_rows=[r for r in final_rows if r['chars']]
    if not final_rows: raise ValueError('No recognized lyric segments; cannot render a song')
    del asr;gc.collect();torch.cuda.empty_cache()
    predictions={};displays={}
    for language in ['ja','zh','en']:
        directory=out/'alignment_inputs'/language
        if not directory.exists():continue
        with bounded_session_threads(config.get('cpu_threads',4)):
            pred,display=run_tifa_fa(tifa,directory,language=language,g2p_pipeline=g2p)
        predictions.update(pred);displays.update(display)
    save(out/'alignment.json',{stem:dict(duration=d,words=[dict(text=w.text,start=w.start,end=w.end,
        phones=[vars(p) for p in w.phonemes]) for w in words]) for stem,(_,d,words) in predictions.items()})
    with bounded_session_threads(config.get('cpu_threads',4)):
        game=load_game_model(config['game_model'],device='cpu')
    entries=[];native_notes=[]
    for row in final_rows:
        index=row['line_index'];stem=f'chunk_{index}';language=row['language']
        if stem not in predictions:raise ValueError(f'TiFA did not align chunk {index}')
        audio,game_sr=load_audio(row['audio'],game.samplerate)
        chars=[kana or roman for roman,kana in displays.get(stem,[])] if language=='ja' else row['chars']
        notes,processed=extract_pitches_and_align([dict(offset=0.,waveform=audio)],game_sr,
            {'chunk_0':predictions[stem]},{'chunk_0':chars},game,[j/8 for j in range(8)],.2,.02,.2,
            batch_size=1,language=language)
        if processed!={0}:raise ValueError(f'GAME did not process chunk {index}')
        duration=sf.info(row['audio']).duration
        data,audit=aligned_words_to_annotation(predictions[stem][2],notes,duration)
        if language=='ja':
            data['word']=[w if w in NON_LYRICS else _direct_moras_to_display_tokens('ja','kana',[w])[0] for w in data['word']]
        else:
            # Phones are aligner metadata. VR receives original Chinese
            # characters / English words and the native GAME note sequence.
            data['ph']=[p if p in NON_LYRICS or '/' in p else language+'/'+p for p in data['ph']]
        data.update(item_name=f'{config["song_id"]}#{index:02d}',song_id=config['song_id'],line_index=index,
            original_lyric=row['text'],source_window=row,bpm=config['bpm'],has_score=True,language=language,
            pronunciation_provenance=dict(source='upstream_ASR_only',reference_used=False,raw_asr=row['raw_asr']),
            note_engine='GAME-1.0.3-medium',quality_action='flag_only')
        data=quantize_annotation(data);report=require_conditioning([data])
        entries.append(data)
        save(out/'labels'/f'{index:03d}.json',data)
        save(out/'labels'/f'{index:03d}.audit.json',dict(mapping=audit,conditioning=report))
        for note in notes:
            note.onset+=row['start_seconds'];note.offset+=row['start_seconds'];native_notes.append(note)
        print('GAME',index+1,'/',len(final_rows),language,flush=True)
    native_notes.sort(key=lambda n:n.onset)
    _save_text(native_notes,out/'notes.csv','csv','midi',False)
    _save_midi(native_notes,out/'notes.mid',int(config['bpm']))
    save(out/'score.vr.json',entries)
    save(out/'manifest.json',dict(status='exported',source='Vocal2Midi audio-only',
        native_chunks=len(rows),exported_chunks=len(entries),languages=dict(Counter(r['language'] for r in final_rows)),
        references_used=False,lyrics_input_used=False,lrc_input_used=False,
        unrecognized_low_energy_segments=len(empty_rows),unrecognized_audio_preserved=True,
        score_sha256=sha(out/'score.vr.json'),quality_status='not_assessed',
        runtime_note='Qwen3-ASR Transformers/CUDA; upstream Japanese/Mandarin/English TiFA/GAME code'))


def cli():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);args=p.parse_args()
    config=json.loads(args.config.read_text());logging.basicConfig(level=logging.INFO,format='%(message)s')
    try:main(config)
    except Exception as error:
        save(Path(config['out'])/'failure.json',dict(error=type(error).__name__,message=str(error)))
        raise


if __name__=='__main__':cli()
