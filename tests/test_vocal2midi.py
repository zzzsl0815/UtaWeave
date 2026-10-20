from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from song2voice.vocal2midi import (require_asr_capacity, aligned_words_to_annotation,
                                 prepare_recognized_reading, use_upstream)
from song2voice.conditioning import quantize_annotation, require_conditioning


class BoundaryTests(unittest.TestCase):
    def test_static_model_never_silently_truncates_audio(self):
        require_asr_capacity(441000, 44100, 160000)
        require_asr_capacity(1000000, 44100, None)
        with self.assertRaisesRegex(ValueError, 'never truncate'):
            require_asr_capacity(441001, 44100, 160000)

    def test_exact_timing_short_mora_and_unknown_pitch_survive(self):
        words = [NS(start=0., end=.01, text='a', phonemes=[NS(start=0., end=.01, text='a')]),
                 NS(start=.01, end=.2, text='i', phonemes=[NS(start=.01, end=.2, text='i')])]
        notes = [NS(onset=0., offset=.1, pitch=60.1)]
        result, audit = aligned_words_to_annotation(words, notes, .2)
        self.assertEqual(result['word'], ['a', 'i'])
        self.assertAlmostEqual(result['word_dur'][0], .01)
        self.assertAlmostEqual(sum(result['pitch_dur']), .2)
        self.assertEqual(set(result['pitch2word']), {0, 1})
        self.assertEqual(result['pitch'][-1], 0)
        result['bpm'] = 120
        require_conditioning([quantize_annotation(result)])

    def test_overlapping_notes_do_not_produce_a_silent_retiming(self):
        words = [NS(start=0., end=.2, text='a', phonemes=[])]
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            aligned_words_to_annotation(words, [NS(onset=0.,offset=.15,pitch=60),
                                                NS(onset=.1,offset=.2,pitch=62)], .2)


class UpstreamProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT/'vendor/Vocal2Midi/inference/romaji_asr/runtime.py').exists():
            raise unittest.SkipTest('Bootstrap the pinned Vocal2Midi checkout for integration tests')
        try:
            use_upstream(ROOT/'vendor/Vocal2Midi')
            from inference.API import lfa_api
        except ImportError as e:
            raise unittest.SkipTest(f'Run integration tests in the labeling runtime: {e}')

    def test_foreign_moras_are_not_flat_romaji_reparsed(self):
        with tempfile.TemporaryDirectory() as folder:
            result = prepare_recognized_reading(dict(text='t u ts u t i', phonemes=['t','u','ts','u','t','i']), folder, 0)
            self.assertEqual(result['selected_moras'], ['tu','tsu','ti'])
            self.assertEqual(result['kana'], ['とぅ','つ','てぃ'])
            self.assertEqual(Path(folder,'chunk_0.txt').read_text(), 'tu tsu ti')
            self.assertEqual(Path(folder,'chunk_0.lab').read_text(), 'tu tsu ti')

    def test_repeated_vowels_n_and_geminate_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            r = prepare_recognized_reading(dict(text='a a N cl p i',phonemes=['a','a','N','cl','p','i']), folder, 0)
            self.assertEqual(r['selected_moras'], ['a','a','n','cl','pi'])

    def test_matched_reference_overrides_observation_without_losing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            r = prepare_recognized_reading(dict(text='a',phonemes=['a']), folder, 0, ['i','i'])
            self.assertEqual(r['selected_moras'], ['i','i'])
            self.assertEqual(r['observed_moras'], ['a'])
            self.assertEqual(r['selected_source'], 'matched_romaji_reference')

    def test_empty_asr_stays_unresolved_without_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'unresolved'):
                prepare_recognized_reading(dict(text='',phonemes=[]), folder, 0)
            r = prepare_recognized_reading(dict(text='',phonemes=[]), folder, 0, ['a'])
            self.assertEqual(r['selected_moras'], ['a'])


if __name__ == '__main__': unittest.main()
