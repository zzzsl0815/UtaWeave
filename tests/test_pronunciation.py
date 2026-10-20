from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from song2voice.pronunciation import adapt_english_annotation,project_phone_durations,english_phones_to_romaji
from song2voice.conditioning import quantize_annotation,require_conditioning


def provider(word,phones,context=None):
    return dict(reading='ハッピー',word=['は','っぴ','い'],ph=['h','a','cl','p','i','i'],
        ph2word=[0,0,1,1,1,2],romaji='happii',romaji_units=['ha','ppi','i'],source='synthetic_test_dictionary')


def source():
    return quantize_annotation(dict(item_name='mixed',line_index=0,word=['あ','SP','happy'],word_dur=[.2,.1,.9],
        ph=['a','SP','en/hh','en/ae','en/p','en/iy'],ph_dur=[.2,.1,.1,.2,.2,.4],ph2word=[0,1,2,2,2,2],
        pitch=[60,0,62,64],pitch_dur=[.2,.1,.45,.45],pitch2word=[0,1,2,2],bpm=120,languages=['ja','en']))


class JapaneseEnglish(unittest.TestCase):
    def test_all_source_words_and_time_budgets_survive(self):
        raw=source();original=deepcopy(raw);result,audit=adapt_english_annotation(raw,provider)
        self.assertEqual(raw,original)
        self.assertEqual(result['word'],['あ','SP','は','っぴ','い'])
        self.assertEqual(result['ph'][:2],original['ph'][:2])
        self.assertAlmostEqual(sum(result['word_dur'][2:]),.9)
        self.assertAlmostEqual(sum(result['pitch_dur']),sum(original['pitch_dur']))
        self.assertEqual({v['source_word_index'] for v in result['source_word_map']},{0,1,2})
        self.assertTrue(require_conditioning([result])[0]['valid_for_render'])
        self.assertEqual(audit[0]['romaji'],'happii')

    def test_pitch_trajectory_is_retained_across_new_moras(self):
        result,_=adapt_english_annotation(source(),provider)
        self.assertEqual(set(result['pitch']),{0,60,62,64})
        before={p:sum(d for x,d in zip(source()['pitch'],source()['pitch_dur']) if x==p) for p in [62,64]}
        for pitch in [62,64]:
            self.assertAlmostEqual(sum(d for p,d in zip(result['pitch'],result['pitch_dur']) if p==pitch),before[pitch])

    def test_reapplying_the_adapter_does_not_rewrite_japanese(self):
        first,_=adapt_english_annotation(source(),provider);second,audit=adapt_english_annotation(first,provider)
        self.assertEqual(first,second);self.assertEqual(audit,[])

    def test_phone_projection_conserves_diphthong_time(self):
        durations,audit=project_phone_durations(['en/b','en/er','en/th','en/d','en/ey'],[.1,.2,.2,.1,.4],
            ['b','a','a','s','u','d','e','i'])
        self.assertAlmostEqual(sum(durations),1.)
        self.assertTrue(all(d>0 for d in durations))
        self.assertFalse(audit['measured_target_boundaries'])

    def test_invalid_projection_and_non_japanese_output_fail(self):
        with self.assertRaises(ValueError):project_phone_durations(['en/unsupported'],[.2],['a'])
        def wrong(word,phones,context):
            r=provider(word,phones);r['word'][0]='happy';return r
        with self.assertRaises(ValueError):adapt_english_annotation(source(),wrong)

    def test_existing_phone_path_can_supply_a_dictionary_independent_reading(self):
        self.assertEqual(english_phones_to_romaji(['en/hh','en/ae','en/p','en/iy']),'hapii')
        self.assertEqual(english_phones_to_romaji(['en/b','en/er','en/th','en/d','en/ey']),'baasudei')
        self.assertEqual(english_phones_to_romaji(['en/t','en/uw']),'tuu')


if __name__=='__main__':unittest.main()
