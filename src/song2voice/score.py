"""Canonical source-audio clock, mora/note ownership and explicit provenance."""
import hashlib
import json
import math
from pathlib import Path

VERSION = '0.1'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def validate(score):
    """Reject structurally bad conditioning without inventing missing labels."""
    if not isinstance(score, dict):
        raise ValueError('score must be an object')
    for field in ['phrases', 'units', 'notes', 'phones']:
        value = score.get(field, [])
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError(f'{field} must be an array of objects')
    if not isinstance(score.get('song'), dict) or not isinstance(score.get('breathing'), dict):
        raise ValueError('song and breathing must be objects')
    errors = []
    def check(condition, message):
        if not condition:
            errors.append(message)
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    check(score.get('schema_version') == VERSION, 'unsupported schema_version')
    song = score.get('song', {})
    duration = song.get('duration_seconds')
    check(number(duration) and duration > 0, 'song duration must be finite and positive')
    check(score.get('timebase') == 'source_audio_seconds', 'timebase must be source_audio_seconds')
    phrase_map, unit_map = {}, {}
    for collection, table in [('phrases', phrase_map), ('units', unit_map), ('notes', {}), ('phones', {})]:
        rows = score.get(collection, [])
        check(isinstance(rows, list), f'{collection} must be an array')
        if not isinstance(rows, list):
            continue
        prior_end = -1.
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f'{collection}[{i}] must be an object')
                continue
            rid = row.get('id')
            check(isinstance(rid, str) and bool(rid) and rid not in table, f'{collection}[{i}] duplicate/invalid id')
            if isinstance(rid, str):
                table[rid] = row
            a, b = row.get('start_seconds'), row.get('end_seconds')
            valid = number(a) and number(b) and 0 <= a < b
            check(valid, f'{collection}[{i}] invalid/nonpositive interval')
            if not valid:
                continue
            check(not number(duration) or b <= duration + 1e-4, f'{collection}[{i}] outside source audio')
            check(a >= prior_end - 1e-4, f'{collection}[{i}] overlap/out of order in single lead vocal')
            prior_end = b
    check(bool(phrase_map) and bool(unit_map) and bool(score.get('notes')), 'empty score')
    owned = {uid: [] for uid in unit_map}
    for unit in unit_map.values():
        check(unit.get('kind') in ['mora', 'rest', 'breath'], f"unit {unit['id']} unknown kind")
        check(isinstance(unit.get('text'), str) and bool(unit['text']), f"unit {unit['id']} missing text")
        phrase = phrase_map.get(unit.get('phrase_id'))
        check(phrase is not None, f"unit {unit['id']} missing phrase")
        if phrase and all(number(x.get(k)) for x in (unit, phrase) for k in ('start_seconds','end_seconds')):
            check(phrase['start_seconds']-1e-4 <= unit['start_seconds'] < unit['end_seconds'] <= phrase['end_seconds']+1e-4,
                  f"unit {unit['id']} outside phrase")
    for collection in ['notes', 'phones']:
        for event in score.get(collection, []):
            if not isinstance(event, dict):
                continue
            owner = unit_map.get(event.get('unit_id'))
            check(owner is not None, f"{collection} {event.get('id')} has unknown unit_id")
            if owner and all(number(x.get(k)) for x in (owner, event) for k in ('start_seconds','end_seconds')):
                check(owner['start_seconds']-1e-4 <= event['start_seconds'] < event['end_seconds'] <= owner['end_seconds']+1e-4,
                      f"{collection} {event.get('id')} crosses owning mora/rest")
            if collection == 'notes':
                pitch = event.get('midi_pitch')
                check(pitch is None or (type(pitch) is int and 0 <= pitch <= 127), f"note {event.get('id')} invalid pitch")
                if owner:
                    owned[owner['id']].append(event)
                    check(owner.get('kind') != 'mora' or pitch is not None, f"mora {owner['id']} has unvoiced/missing pitch")
                    check(owner.get('kind') == 'mora' or pitch is None, f"non-lyric {owner['id']} has voiced pitch")
    for uid, unit in unit_map.items():
        notes = owned[uid]
        check(bool(notes), f'unit {uid} has no note conditioning')
        if notes and all(number(e.get(k)) for e in [unit, *notes] for k in ('start_seconds', 'end_seconds')):
            covered = sum(n['end_seconds']-n['start_seconds'] for n in notes)
            check(abs(covered-(unit['end_seconds']-unit['start_seconds'])) < 1e-4, f'unit {uid} note duration coverage mismatch')
    for tempo in score.get('tempo_map', []):
        check(number(tempo.get('bpm')) and tempo['bpm'] > 0, 'invalid tempo')
    breath = score.get('breathing', {})
    check(breath.get('status') in ['unannotated', 'detected', 'reviewed'], 'breathing status must be explicit')
    if errors:
        raise ValueError('\n'.join(errors))
    return dict(status='passed', phrases=len(phrase_map), moras=sum(u['kind']=='mora' for u in unit_map.values()),
                rests=sum(u['kind']=='rest' for u in unit_map.values()), notes=len(score['notes']),
                phones=len(score.get('phones', [])), breathing_status=breath['status'],
                warnings=score.get('warnings', []),
                limitation='Structure validation does not establish lyric, note or timing accuracy.')


def import_legacy(manifest_path, timeline):
    """Import the full-song experiment format, with an explicit clock choice.

    This adapter is deliberately separate from the canonical score. It does
    not claim to transcribe arbitrary audio or certify the old pseudo labels.
    """
    if timeline not in ['source-seconds', 'source-samples']:
        raise ValueError('Choose source-seconds or source-samples explicitly')
    manifest_path = Path(manifest_path).resolve()
    parent = manifest_path.parent
    legacy = json.loads(manifest_path.read_text())
    song_id = str(legacy['song_id'])
    labels = parent/'labels'/song_id
    meta = json.loads((labels/'metadata.json').read_text())
    duration = meta.get('source_duration', meta.get('duration_ms', 0)/1000)
    score = dict(schema_version=VERSION, timebase='source_audio_seconds',
        song=dict(id=song_id, title=legacy['title'], language=meta.get('language','ja'), duration_seconds=duration),
        tempo_map=[dict(time_seconds=0., bpm=float(meta['bpm']), source=meta.get('bpm_source','legacy_unspecified'))],
        phrases=[], units=[], notes=[], phones=[],
        breathing=dict(status='unannotated', events=[]), warnings=[],
        provenance=dict(importer='legacy-full-song-v1', timeline_choice=timeline,
            manifest=str(manifest_path), manifest_sha256=sha(manifest_path), label_sources=[],
            source_vocal=str(parent/'audio/original_vocal.flac'),
            source_vocal_sha256=sha(parent/'audio/original_vocal.flac'),
            lyric_policy='Inherited experiment readings, including case-specific choices; not independently verified'))
    for row in sorted(legacy['phrases'], key=lambda r:r['source_window']['start_seconds']):
        index = int(row['line_index'])
        path = labels/f'{index:02d}_phrase.json'
        data = json.loads(path.read_text())
        window = row['source_window']
        sample_start = window['start_sample']/window['sample_rate']
        delta = window['start_seconds']-sample_start
        if abs(delta) > 1/window['sample_rate']:
            score['warnings'].append(dict(code='legacy_clock_disagreement', line_index=index,
                seconds_minus_sample_clock=delta, selected_clock=timeline))
        start = float(window['start_seconds'] if timeline == 'source-seconds' else sample_start)
        phrase_id = f'phrase_{index:04d}'
        duration = sum(data['word_dur'])
        score['phrases'].append(dict(id=phrase_id, start_seconds=start, end_seconds=start+duration,
                                    text=row['lyric'], reading=row['kana']))
        if len(data['word']) != len(data['word_dur']):
            raise ValueError(f'{path.name}: word/duration count mismatch')
        cursor = start
        for i, (word, seconds) in enumerate(zip(data['word'], data['word_dur'])):
            score['units'].append(dict(id=f'{phrase_id}_u{i:04d}', phrase_id=phrase_id,
                text=word, kind='rest' if word=='SP' else 'breath' if word=='AP' else 'mora',
                start_seconds=cursor, end_seconds=cursor+seconds))
            cursor += seconds
        for collection, text_field, dur_field, map_field in [
            ('notes','pitch','pitch_dur','pitch2word'), ('phones','ph','ph_dur','ph2word')]:
            if not (len(data[text_field]) == len(data[dur_field]) == len(data[map_field])):
                raise ValueError(f'{path.name}: {collection} count mismatch')
            cursor = start
            for i, (value, seconds, owner) in enumerate(zip(data[text_field], data[dur_field], data[map_field])):
                if type(owner) is not int or not 0 <= owner < len(data['word']):
                    raise ValueError(f'{path.name}: invalid original word mapping')
                event = dict(id=f'{phrase_id}_{collection}_{i:04d}', unit_id=f'{phrase_id}_u{owner:04d}',
                             start_seconds=cursor, end_seconds=cursor+seconds)
                if collection == 'notes':
                    event.update(midi_pitch=None if value==0 else value, vr_duration_token=data['note'][i])
                else:
                    event['phone'] = value
                score[collection].append(event)
                cursor += seconds
        score['provenance']['label_sources'].append(dict(path=str(path), sha256=sha(path),
            alignment=data.get('alignment_provenance', {}), note_mapping=data.get('full_song_note_mapping')))
    validate(score)
    return score
