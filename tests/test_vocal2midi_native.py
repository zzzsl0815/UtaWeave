from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from song2voice.vocal2midi_native_worker import validate_audio_only_config, text_languages, language_spans
from song2voice.vocal2midi import use_upstream


class AudioOnlyTests(unittest.TestCase):
    def test_old_reference_configuration_is_rejected_even_if_empty(self):
        for key in ['reference_moras','source_intervals','reference_audit','lrc','original_lyrics','lyrics_file']:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'reference inputs'):
                validate_audio_only_config(dict(audio='vocal.wav',**{key:{}}))
        validate_audio_only_config(dict(audio='vocal.wav'))

    def test_language_routing_uses_recognized_script(self):
        self.assertEqual(text_languages('誕生日 happy birthday'),['ja','en'])
        self.assertEqual(text_languages('今日は'),['ja'])
        self.assertEqual(text_languages('happy birthday'),['en'])
        self.assertEqual(text_languages(''),[])

    def test_language_change_covers_original_audio_once(self):
        def word(text,start,end,language):
            return NS(text=text,start=start,end=end,phonemes=[NS(text=language+'/a')])
        words=[word('僕',.1,.3,'ja'),word('は',.3,.6,'ja'),word('happy',.8,1.2,'en'),word('birthday',1.3,1.8,'en')]
        result=language_spans(words,2.)
        self.assertEqual([r['language'] for r in result],['ja','en'])
        self.assertAlmostEqual(result[0]['start'],0.)
        self.assertAlmostEqual(result[0]['end'],.7)
        self.assertAlmostEqual(result[1]['start'],.7)
        self.assertAlmostEqual(result[1]['end'],2.)

    def test_unqualified_mixed_phones_do_not_get_a_guessed_language(self):
        with self.assertRaises(ValueError):
            language_spans([NS(text='x',start=0.,end=1.,phonemes=[NS(text='a')])],1.)


class UpstreamEnglishTests(unittest.TestCase):
    def test_upstream_distinguishes_syllables_from_melisma(self):
        if not (ROOT/'vendor/Vocal2Midi/inference/romaji_asr/runtime.py').exists():
            self.skipTest('Bootstrap the pinned Vocal2Midi checkout for integration tests')
        try:
            use_upstream(ROOT/'vendor/Vocal2Midi')
            from inference.API.game_api import extract_vowel_boundaries
        except ImportError as error:
            self.skipTest(str(error))
        word=NS(text='happy',start=0.,end=1.,phonemes=[
            NS(text='hh',start=0.,end=.1),NS(text='ae',start=.1,end=.4),
            NS(text='p',start=.4,end=.5),NS(text='iy',start=.5,end=1.)])
        durations,voiced,lyrics=extract_vowel_boundaries([word],['happy'],language='en')
        self.assertEqual([s for s,v in zip(lyrics,voiced) if v],['happy','+'])
        self.assertAlmostEqual(sum(durations),1.)


if __name__=='__main__':unittest.main()
