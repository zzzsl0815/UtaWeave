"""Small compatibility boundary around an unmodified, pinned Vocal2Midi tree.

Recognition, phoneme/mora conversion, alignment and notes stay upstream. This
module enforces our coverage contract and the input protocol between stages.
Neural dependencies are imported only in the worker environment.
"""
from pathlib import Path
import math
import sys

UPSTREAM_COMMIT = 'f589c183ec9a4b25ea234459b8a4b72ba993af50'


def use_upstream(repo):
    repo = Path(repo).resolve()
    if not (repo/'inference/romaji_asr/runtime.py').is_file():
        raise ValueError('A complete Vocal2Midi checkout is required')
    # Both TiFA's training source and Vocal2Midi use the name "inference".
    loaded = sys.modules.get('inference')
    if loaded is not None:
        locations = list(getattr(loaded, '__path__', []))
        if not any(Path(p).resolve().is_relative_to(repo) for p in locations):
            raise ValueError('Run Vocal2Midi in a separate process from TiFA training code')
    sys.path.insert(0, str(repo))


def require_asr_capacity(samples, sample_rate, fixed_samples, asr_sample_rate=16000):
    """Upstream prepare_batch truncates fixed-width inputs; forbid that loss."""
    if samples <= 0 or sample_rate <= 0:
        raise ValueError('Invalid audio clock')
    resampled_samples = math.ceil(samples * asr_sample_rate / sample_rate)
    if fixed_samples is not None and resampled_samples > fixed_samples:
        raise ValueError('Audio exceeds RomajiASR input capacity; split before recognition, never truncate')


def prepare_recognized_reading(result, temp_dir, index, reference_moras=None):
    """Reuse upstream normalization/mora joining and write a consistent FA pair.

    At the pinned upstream revision the unmatched direct-ASR .txt contains raw
    phones ("h a ...") while its .lab contains moras ("ha ..."). TiFA prefers
    .txt and expects words/moras. Set both to the selected mora sequence here,
    without editing the vendored project or changing its recognition result.

    reference_moras must already be matched by the reference-lyrics adapter;
    arbitrary unverified text must not be passed as an authoritative reading.
    """
    from inference.API.lfa_api import (
        _normalize_asr_phoneme_token, _phoneme_tokens_to_romaji_moras,
        _direct_moras_to_display_tokens, process_asr_to_phonemes,
    )
    directory = Path(temp_dir)
    directory.mkdir(parents=True, exist_ok=True)
    phones = [p for t in result.get('phonemes', [])
              if (p := _normalize_asr_phoneme_token(str(t)))]
    observed = [m for m in _phoneme_tokens_to_romaji_moras(phones)
                if m not in {'AP', 'EP', 'SP'}]
    selected = list(reference_moras) if reference_moras is not None else observed
    if not selected:
        raise ValueError('Empty recognition without a matched reference; retain chunk as unresolved')
    if any(not m or any(c.isspace() for c in m) for m in selected):
        raise ValueError('Expected individual mora tokens')
    # Keep the upstream ASR log and display conversion, then enforce the
    # recognized/verified mora protocol at the forced-aligner boundary.
    _, logs = process_asr_to_phonemes([result], [index], directory, 'ja', None,
                                    lyric_output_mode='kana', use_asr_phonemes=True)
    stem = f'chunk_{index}'
    text = ' '.join(selected)
    for suffix in ['.txt', '.lab']:
        (directory/(stem+suffix)).write_text(text, encoding='utf-8')
    kana = _direct_moras_to_display_tokens('ja', 'kana', selected)
    unknown = [m for m, k in zip(selected, kana) if k.isascii()]
    return dict(raw_asr=result, observed_moras=observed, selected_moras=selected,
                kana=kana, selected_source='matched_romaji_reference' if reference_moras is not None else 'RomajiASR',
                unsupported_moras=unknown, upstream_log=logs,
                compatibility_fix='TiFA .txt and .lab both receive selected mora tokens')


def aligned_words_to_annotation(words, notes, duration):
    """Preserve upstream FA units and native-second GAME notes, without MIDI.

    Unknown pitches remain zero and flagged by the caller. Do not infer
    pronunciations, expand grid durations or drop short moras in this bridge.
    """
    from .annotation import intersect_notes_with_units
    data = dict(word=[], word_dur=[], ph=[], ph_dur=[], ph2word=[],
                pitch=[], pitch_dur=[], pitch2word=[])
    cursor = 0.
    def add_word(text, start, end, phones):
        nonlocal cursor
        if start > cursor + 1e-5:
            add_word('SP', cursor, start, [])
        if start < cursor - 1e-5 or end <= start or end > duration + .01:
            raise ValueError('Invalid or overlapping upstream alignment')
        start = cursor
        owner = len(data['word']); data['word'].append(text)
        end = min(duration, end)
        data['word_dur'].append(end-start)
        phone_cursor = start
        for phone in phones:
            a, b = max(phone_cursor, float(phone.start)), min(end, float(phone.end))
            if b <= a: continue
            if a > phone_cursor + 1e-5:
                data['ph'].append('SP');data['ph_dur'].append(a-phone_cursor);data['ph2word'].append(owner)
            data['ph'].append(str(phone.text));data['ph_dur'].append(b-a);data['ph2word'].append(owner)
            phone_cursor = b
        if phone_cursor < end - 1e-5:
            data['ph'].append('SP');data['ph_dur'].append(end-phone_cursor);data['ph2word'].append(owner)
        cursor = end
    for word in words:
        add_word(str(word.text), float(word.start), float(word.end), word.phonemes)
    if cursor < duration - 1e-5: add_word('SP', cursor, duration, [])
    if not data['word']: raise ValueError('No aligned words; cannot export a lyric score')
    data['word_dur'][-1] += duration - sum(data['word_dur'])
    cursor = 0.
    for note in sorted(notes, key=lambda n: n.onset):
        start, end = max(0., float(note.onset)), min(duration, float(note.offset))
        if start < cursor - 1e-5 or end <= start:
            raise ValueError('Invalid or overlapping upstream GAME notes')
        if start > cursor + 1e-5:
            data['pitch'].append(0);data['pitch_dur'].append(start-cursor)
            cursor = start
        data['pitch'].append(round(float(note.pitch)));data['pitch_dur'].append(end-cursor)
        cursor = end
    if cursor < duration - 1e-5:
        data['pitch'].append(0);data['pitch_dur'].append(duration-cursor)
    if not data['pitch']: raise ValueError('GAME returned no note timeline')
    data['pitch_dur'][-1] += duration - sum(data['pitch_dur'])
    # The old map is intentionally empty: construct ownership from exact
    # intersections, including mora segments preceding a vowel nucleus.
    return intersect_notes_with_units(data)
