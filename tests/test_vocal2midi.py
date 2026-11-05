from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from song2voice.vocal2midi import aligned_words_to_annotation
from song2voice.conditioning import quantize_annotation, require_conditioning


class BoundaryTests(unittest.TestCase):
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



if __name__ == '__main__': unittest.main()
