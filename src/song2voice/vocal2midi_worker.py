"""Historical reference-priority experiment; retained for reproducibility.

The default audio-only path is song2voice.vocal2midi_native_worker, exposed
through `song2voice annotate`. This legacy module is not that entrypoint.

No song-specific readings are embedded. Optional reference_moras are produced
by the existing downloaded-romaji matching stage. With no reference, the same
worker uses only the acoustic recognition result.
"""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import time
from contextlib import contextmanager

from .score import save, sha
from .vocal2midi import (UPSTREAM_COMMIT, use_upstream, require_asr_capacity,
                         prepare_recognized_reading, aligned_words_to_annotation)


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


def recognize(config, out):
    import onnxruntime as ort
    import soundfile as sf
    from inference.romaji_asr.common import get_fixed_num_samples, load_vocab
    from inference.romaji_asr.runtime import RomajiASROnnxModel
    from inference.API.slicer_api import slice_audio_with_custom_bounds

    options = ort.SessionOptions()
    options.intra_op_num_threads = config.get('cpu_threads', 4)
    options.inter_op_num_threads = 1
    model_dir = Path(config['romaji_model'])
    session = ort.InferenceSession(str(model_dir/'model.onnx'), options,
                                  providers=['CPUExecutionProvider'])
    id2token, blank = load_vocab(model_dir/'phoneme_vocab.json')
    meta_path = model_dir/'model.meta.json'
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    sample_rate = int(meta.get('sample_rate', 16000))
    model = RomajiASROnnxModel(model_dir, session, id2token, blank, sample_rate, 'cpu')
    fixed_samples = get_fixed_num_samples(session)
    if config.get('source_intervals'):
        rows = json.loads(Path(config['source_intervals']).read_text())
        for row in rows:
            row['audio'] = str((Path(config['source_intervals']).parent/row['audio']).resolve())
    else:
        wave, sr = sf.read(config['audio'], dtype='float32', always_2d=True)
        wave = wave.mean(axis=1)
        maximum = min(config.get('slice_max_sec', 10.), fixed_samples/sample_rate if fixed_samples else 10.)
        chunks = slice_audio_with_custom_bounds(wave, sr, 'default',
                    min_len_sec=min(5., maximum), max_len_sec=maximum)
        rows = []
        for i, chunk in enumerate(chunks):
            path = out/'chunks'/f'chunk_{i}.wav'; path.parent.mkdir(exist_ok=True)
            sf.write(path, chunk['waveform'], sr, subtype='PCM_24')
            start = float(chunk['offset']); duration = len(chunk['waveform'])/sr
            rows.append(dict(line_index=i, audio=str(path), start_seconds=start,
                             end_seconds=start+duration, text='', sample_rate=sr))
    references = config.get('reference_moras', {})
    results = []; started = time.monotonic()
    for i, row in enumerate(rows):
        record = dict(row, chunk_index=i)
        try:
            if row.get('sha256') and sha(row['audio']) != row['sha256']:
                raise ValueError('Source crop differs from its recorded hash')
            info = sf.info(row['audio'])
            require_asr_capacity(info.frames, info.samplerate, fixed_samples, sample_rate)
            raw = model.transcribe([row['audio']], batch_size=1)[0]
            record.update(raw_asr=raw)
            reading = prepare_recognized_reading(raw, out/'alignment_inputs', i,
                          references.get(str(row['line_index'])))
            record.update(reading=reading, status='recognized')
            # An unsupported acoustic token remains visible rather than being
            # silently discarded by a dictionary or becoming an English phone.
            if reading['unsupported_moras']:
                record['review_flags'] = ['unmapped_asr_mora']
        except (ValueError, OSError) as e:
            record.update(status='unresolved', error=str(e))
        results.append(record)
        save(out/'recognition.json', dict(phrases=results, total_expected=len(rows),
            completed=len(results), providers=session.get_providers(),
            model_input_shape=session.get_inputs()[0].shape,
            elapsed_seconds=round(time.monotonic()-started, 2)))
        print('ASR', i+1, '/', len(rows), record['status'], flush=True)
    if any(r['status'] != 'recognized' for r in results):
        raise ValueError('Unresolved chunks retained in recognition.json; complete-score export withheld')
    return results


def align_and_extract(config, out, records):
    import numpy as np
    import soundfile as sf
    from inference.TiFA.runtime import load_tifa_model
    from inference.TiFA.aligner import run_tifa_fa
    from inference.TiFA.g2p_pipeline import build_g2p_pipeline
    from inference.API.game_api import load_game_model, extract_pitches_and_align
    from inference.API.lfa_api import _direct_moras_to_display_tokens
    from inference.io.note_io import _save_text
    from inference.io.audio_io import load_audio
    from .conditioning import quantize_annotation, require_conditioning

    directory = out/'alignment_inputs'
    for i, record in enumerate(records):
        wave, sr = sf.read(record['audio'], dtype='float32', always_2d=True)
        sf.write(directory/f'chunk_{i}.wav', wave.mean(axis=1), sr, subtype='PCM_24')
    with bounded_session_threads(config.get('cpu_threads', 4)):
        model = load_tifa_model(config['tifa_model'], device='cpu')
        g2p = build_g2p_pipeline(config['tifa_model'], japanese_g2p_engine='pyopenjtalk')
    pred, display = run_tifa_fa(model, directory, language='ja', g2p_pipeline=g2p)
    aligned = {}
    for stem, (_, duration, words) in pred.items():
        aligned[stem] = dict(duration=duration, words=[dict(start=w.start, end=w.end, text=w.text,
                    phones=[vars(p) for p in w.phonemes]) for w in words], display=display.get(stem, []))
    save(out/'alignment.json', aligned)
    missing = [i for i in range(len(records)) if f'chunk_{i}' not in pred]
    if missing:
        raise ValueError(f'TiFA did not align chunks {missing}; no pitch-only fallback may erase lyrics')
    with bounded_session_threads(config.get('cpu_threads', 4)):
        game = load_game_model(config['game_model'], device='cpu')
    entries = []; note_exports = []; statuses = []
    for i, record in enumerate(records):
        wave, sr = sf.read(record['audio'], dtype='float32', always_2d=True)
        duration = len(wave)/sr
        game_wave, game_sr = load_audio(record['audio'], game.samplerate)
        stem = f'chunk_{i}'
        chars = [p[1] or p[0] for p in display.get(stem, [])]
        if not chars: chars = record['reading']['kana']
        # One chunk at a time uses the native chunk_0 API. No timestamps are
        # quantized or shifted; add the source offset only at export time.
        notes, processed = extract_pitches_and_align(
            [dict(offset=0., waveform=np.ascontiguousarray(game_wave))], game_sr,
            {'chunk_0': pred[stem]}, {'chunk_0': chars}, game, config.get('ts', [i/8 for i in range(8)]),
            config.get('seg_threshold', .2), config.get('seg_radius', .02),
            config.get('est_threshold', .2), batch_size=1, language='ja')
        if processed != {0}:
            raise ValueError(f'GAME failed chunk {i}; retaining unresolved record')
        data, audit = aligned_words_to_annotation(pred[stem][2], notes, duration)
        selected = record['reading']['selected_moras']
        # TiFA returns romaji. Convert whole mora tokens to kana, preserving
        # foreign CV distinctions (tu=とぅ, tsu=つ) and repeated vowels.
        data['word'] = [w if w in {'SP', 'AP', 'EP'} else
                       _direct_moras_to_display_tokens('ja', 'kana', [w])[0]
                       for w in data['word']]
        unmapped = [w for w in data['word'] if w not in {'SP', 'AP', 'EP'} and w.isascii()]
        if unmapped:
            raise ValueError(f'Unsupported aligned Japanese moras: {unmapped}')
        data.update(item_name=f'{config["song_id"]}#{record["line_index"]:02d}',
            song_id=config['song_id'], line_index=record['line_index'], original_lyric=record.get('text', ''),
            bpm=config['bpm'], has_score=True, source_window=record,
            pronunciation_provenance=dict(source=record['reading']['selected_source'],
                raw_asr=record['raw_asr'], selected_moras=selected),
            alignment_engine='Vocal2Midi/TiFA-1.0-ST-ONNX', note_engine='GAME-1.0.3-medium',
            quality_action='flag_only', review_flags=['unvoiced_lyric_pitch'] if audit['unvoiced_lyric_units'] else [])
        data = quantize_annotation(data)
        reports = require_conditioning([data])
        save(out/'labels'/f'{i:03d}.json', data)
        save(out/'labels'/f'{i:03d}.audit.json', dict(mapping=audit, conditioning=reports))
        entries.append(data)
        for n in notes:
            n.onset += record['start_seconds']; n.offset += record['start_seconds']
            note_exports.append(n)
        statuses.append(dict(chunk=i, status='exported', lyric_units=reports[0]['lyric_units'],
                             unknown_pitch_units=len(audit['unvoiced_lyric_units'])))
        save(out/'progress.json', dict(stage='GAME', completed=len(entries), total=len(records)))
        print('GAME', i+1, '/', len(records), flush=True)
    save(out/'score.vr.json', entries)
    _save_text(note_exports, out/'notes.csv', 'csv', 'midi', False)
    manifest = dict(status='exported', expected_chunks=len(records), exported_chunks=len(entries),
        deleted_chunks=0, phrases=statuses, score_sha256=sha(out/'score.vr.json'),
        references_authoritative=True, no_reference_supported=True,
        quality_status='not_assessed',
        limitation='Recognition may omit or mishear sung syllables. Coverage of chunks is not transcription accuracy.')
    save(out/'manifest.json', manifest)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--stage', choices=['all', 'asr', 'score'], default='all')
    args = p.parse_args()
    config = json.loads(args.config.read_text())
    use_upstream(config['upstream_repo'])
    commit = subprocess.check_output(['git', '-C', config['upstream_repo'], 'rev-parse', 'HEAD'], text=True).strip()
    if commit != UPSTREAM_COMMIT: raise ValueError('Vocal2Midi revision differs from the tested integration')
    out = Path(config['out']); out.mkdir(parents=True, exist_ok=True)
    recipe = dict(config=config, worker_sha256=sha(Path(__file__)),
                  bridge_sha256=sha(Path(__file__).with_name('vocal2midi.py')), upstream_commit=commit)
    recipe['input_sha256'] = {k: sha(Path(config[k])) for k in
        ['source_intervals', 'audio', 'reference_audit'] if config.get(k)}
    recipe['model_sha256'] = {}
    for key in ['romaji_model', 'tifa_model', 'game_model']:
        root = Path(config[key])
        recipe['model_sha256'][key] = {str(p.relative_to(root)): sha(p)
            for p in sorted(root.iterdir()) if p.suffix in {'.onnx', '.json'}}
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    if (out/'recipe.json').exists():
        prior = json.loads((out/'recipe.json').read_text())
        if prior['sha256'] != recipe_hash: raise ValueError('Output belongs to different inputs/code; select a new directory')
    save(out/'recipe.json', dict(sha256=recipe_hash, **recipe))
    if (out/'manifest.json').exists():
        prior = json.loads((out/'manifest.json').read_text())
        if prior.get('status') == 'exported' and sha(out/'score.vr.json') == prior['score_sha256']:
            print('Already complete:', out/'manifest.json', flush=True)
            return
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    try:
        if args.stage in {'all', 'asr'}: records = recognize(config, out)
        else: records = json.loads((out/'recognition.json').read_text())['phrases']
        if args.stage != 'asr': align_and_extract(config, out, records)
    except Exception as e:
        save(out/'failure.json', dict(error=type(e).__name__, message=str(e), complete_score=False))
        raise


if __name__ == '__main__': main()
