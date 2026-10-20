import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from song2voice.annotation import lyric_windows,language_runs,intersect_notes_with_units


class InferenceCoverage(unittest.TestCase):
    def test_title_english_and_last_line_all_survive(self):
        rows=[dict(start_ms=1000,text='Song title'),dict(start_ms=5000,text='Happy birthday'),
              dict(start_ms=9000,text=''),dict(start_ms=14000,text='同じ歌名')]
        windows=lyric_windows(rows,18.,1000)
        self.assertEqual([r['text'] for r in windows],['Song title','Happy birthday','同じ歌名'])
        self.assertEqual(windows[-1]['end_sample'],18000)
        self.assertEqual(windows[1]['end_sample'],8700)

    def test_no_training_minimum_or_maximum_duration_gate(self):
        rows=[dict(start_ms=1000,text='あ'),dict(start_ms=1100,text='long phrase')]
        windows=lyric_windows(rows,50.,1000)
        self.assertEqual(len(windows),2)
        self.assertEqual(windows[0]['end_sample']-windows[0]['start_sample'],100)
        self.assertGreater(windows[1]['end_seconds']-windows[1]['start_seconds'],20.)

    def test_conflicting_timestamps_fail_instead_of_losing_a_line(self):
        with self.assertRaisesRegex(ValueError,'Conflicting lyrics'):
            lyric_windows([dict(start_ms=1000,text='one'),dict(start_ms=1000,text='two')],3.,1000)

    def test_language_routing_keeps_both_scripts(self):
        runs=language_runs('君と Happy birthday to you 歌おう')
        self.assertEqual([r['language'] for r in runs],['ja','en','ja'])
        self.assertEqual(''.join(r['text'] for r in runs),'君と Happy birthday to you 歌おう')

    def test_short_mora_is_preserved_without_changing_pitch(self):
        data=dict(word=['あ','い','う'],word_dur=[.49,.02,.49],pitch=[60],pitch_dur=[1.],pitch2word=[1],ph=['a','i','u'])
        before=copy.deepcopy(data)
        result,audit=intersect_notes_with_units(data)
        self.assertEqual(data,before)
        self.assertEqual(result['pitch2word'],[0,1,2])
        self.assertEqual(result['pitch'],[60,60,60])
        self.assertEqual(audit['original_missing_unit_indices'],[0,2])

    def test_unvoiced_lyrics_remain_explicit_without_pitch_filling(self):
        data=dict(word=['SP','あ','い'],word_dur=[.2,.3,.5],pitch=[70,0,62],pitch_dur=[.2,.3,.5],pitch2word=[0,1,2])
        result,audit=intersect_notes_with_units(data)
        self.assertEqual(result['pitch'],[0,0,62])
        self.assertEqual(audit['unvoiced_lyric_units'][0]['word'],'あ')
        self.assertFalse(audit['pitch_imputation'])

    def test_float32_boundary_drift_does_not_create_phantom_notes(self):
        data=dict(word=['あ','い','う'],word_dur=[.3,.3,.4],
            pitch=[60,0,64],pitch_dur=[.30000001192092896,.2999999523162842,.40000003576278687],pitch2word=[0,1,2])
        result,audit=intersect_notes_with_units(data)
        self.assertEqual(result['pitch'],[60,0,64])
        self.assertEqual(result['pitch2word'],[0,1,2])
        self.assertTrue(audit['boundary_adjustments'])
        for expected,actual in zip(data['word_dur'],result['pitch_dur']):self.assertAlmostEqual(expected,actual)

    def test_true_ten_millisecond_mora_is_not_numerical_noise(self):
        data=dict(word=['あ','ん','い'],word_dur=[.3,.01,.39],pitch=[60,62,64],
            pitch_dur=[.30000001,.00999998,.39000001],pitch2word=[0,1,2])
        result,_=intersect_notes_with_units(data)
        self.assertEqual(result['pitch'],[60,62,64])
        self.assertEqual(result['pitch2word'],[0,1,2])
        self.assertAlmostEqual(result['pitch_dur'][1],.01)

    def test_real_short_pitch_and_unvoiced_events_inside_word_survive(self):
        data=dict(word=['あ'],word_dur=[.5],pitch=[60,0,62],pitch_dur=[.24,.02,.24],pitch2word=[0,0,0])
        result,_=intersect_notes_with_units(data)
        self.assertEqual(result['pitch'],[60,0,62])
        self.assertAlmostEqual(result['pitch_dur'][1],.02)

    def test_boundary_fix_is_idempotent_and_conserves_every_word(self):
        # Accumulated float32-like drift across many words must not grow the
        # event count or cause an original lyric unit to disappear.
        lengths=[.01 if i%9==0 else .3 for i in range(100)]
        note_edges=[sum(lengths[:i])+(i%3-1)*1e-7 for i in range(1,100)]
        edges=[0.]+note_edges+[sum(lengths)]
        data=dict(word=['あ']*100,word_dur=lengths,pitch=[60+i%4 for i in range(100)],
            pitch_dur=[b-a for a,b in zip(edges,edges[1:])],pitch2word=list(range(100)))
        result,_=intersect_notes_with_units(data);again,_=intersect_notes_with_units(result)
        self.assertEqual(result,again)
        self.assertEqual(len(result['pitch']),100)
        self.assertEqual(result['pitch2word'],list(range(100)))
        self.assertAlmostEqual(sum(result['pitch_dur']),sum(lengths))


if __name__=='__main__':unittest.main()
