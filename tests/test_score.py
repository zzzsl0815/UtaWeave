import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from song2voice.score import import_legacy, validate


def example():
    """Fabricated score; no source song or downloaded lyrics in the repository."""
    score = dict(schema_version='0.1', timebase='source_audio_seconds',
        song=dict(id='example', title='Example', language='ja', duration_seconds=3.),
        tempo_map=[dict(time_seconds=0., bpm=120.)],
        breathing=dict(status='unannotated', events=[]),
        phrases=[dict(id='phrase', start_seconds=.2, end_seconds=2.7, text='あさのうた')],
        units=[], notes=[], phones=[])
    for i, text in enumerate('あさのうた'):
        a = .2+i*.5
        score['units'].append(dict(id=f'u{i}', phrase_id='phrase', kind='mora', text=text,
                                   start_seconds=a, end_seconds=a+.5))
        score['notes'].append(dict(id=f'n{i}', unit_id=f'u{i}', midi_pitch=60+i,
                                   start_seconds=a, end_seconds=a+.5))
    return score


class ScoreIntegrity(unittest.TestCase):
    def test_fabricated_score(self):
        self.assertEqual(validate(example())['moras'], 5)

    def test_missing_mora_conditioning_is_rejected(self):
        score = example()
        score['notes'].pop(2)
        with self.assertRaisesRegex(ValueError, 'has no note conditioning'):
            validate(score)

    def test_cross_mora_note_and_timing_overlap_are_rejected(self):
        score = example()
        score['notes'][1]['start_seconds'] -= .1
        with self.assertRaisesRegex(ValueError, 'crosses owning mora/rest'):
            validate(score)

    def test_nonpositive_nonfinite_duplicate_and_unvoiced_lyric(self):
        for fault in ['zero', 'nan', 'duplicate', 'unvoiced']:
            score = example()
            if fault == 'zero': score['units'][0]['end_seconds'] = .2
            if fault == 'nan': score['notes'][0]['end_seconds'] = float('nan')
            if fault == 'duplicate': score['notes'][1]['id'] = score['notes'][0]['id']
            if fault == 'unvoiced': score['notes'][0]['midi_pitch'] = None
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                validate(score)

    def test_rest_and_midi_pitch_zero_remain_distinct(self):
        score = example()
        score['notes'][0]['midi_pitch'] = 0
        validate(score)  # MIDI pitch zero is valid; null denotes silence.
        score['units'][0].update(kind='rest', text='SP')
        with self.assertRaisesRegex(ValueError, 'non-lyric'):
            validate(score)
        score['notes'][0]['midi_pitch'] = None
        self.assertEqual(validate(score)['rests'], 1)

    def test_unannotated_breath_is_not_reported_as_detected(self):
        self.assertEqual(validate(example())['breathing_status'], 'unannotated')
        score = example()
        score['breathing'] = {}
        with self.assertRaisesRegex(ValueError, 'breathing status'):
            validate(score)

    def test_malformed_root_is_rejected(self):
        for malformed in [None, [], dict(song={}, breathing={}, units=None)]:
            with self.subTest(value=malformed), self.assertRaises(ValueError):
                validate(malformed)

    def test_legacy_clock_choice_is_explicit_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            labels = root/'labels/toy'
            labels.mkdir(parents=True)
            audio = root/'audio'
            audio.mkdir()
            (audio/'original_vocal.flac').write_bytes(b'test source content hash only')
            (labels/'metadata.json').write_text(json.dumps(dict(bpm=120., source_duration=2., language='ja')))
            data = dict(word=['あ'], word_dur=[.5], pitch=[60], pitch_dur=[.5], pitch2word=[0],
                        ph=['a'], ph_dur=[.5], ph2word=[0], note=['<NOTE_4>'])
            label = labels/'00_phrase.json'
            label.write_text(json.dumps(data))
            manifest = root/'manifest.json'
            phrase = dict(line_index=0, lyric='あ', kana='あ', source_window=dict(
                start_seconds=.5, end_seconds=1., start_sample=0, end_sample=50, sample_rate=100))
            manifest.write_text(json.dumps(dict(song_id='toy', title='Example', phrases=[phrase])))
            before = {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            seconds = import_legacy(manifest, 'source-seconds')
            samples = import_legacy(manifest, 'source-samples')
            self.assertEqual(seconds['units'][0]['start_seconds'], .5)
            self.assertEqual(samples['units'][0]['start_seconds'], 0.)
            self.assertEqual(seconds['warnings'][0]['code'], 'legacy_clock_disagreement')
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)
            with self.assertRaises(ValueError):
                import_legacy(manifest, 'automatic_guess')


if __name__ == '__main__':
    unittest.main()
