from copy import deepcopy
import unittest

from song2voice.lyric_alignment import parse_lrc,fit_clock,align_lrc,allocate_units
from song2voice.midi_score import notes_to_score,require_edited_mapping


def reading(text):
    return dict(reading=' '.join(text),units=[dict(text=c,lang='ja',weight=1,phones=['a'],vowel='ア') for c in text],moras=list(text),warnings=[])


def notes(starts,duration=.2):
    return [dict(start=t,end=t+duration,pitch=60) for t in starts]


class LyricAlignmentTests(unittest.TestCase):
    def test_multiple_stamps_empty_boundaries_metadata_and_duplicate_translations(self):
        parsed=parse_lrc('[ar:artist]\n[offset:200]\n[00:00]作曲: person\n[00:02][00:10]あい\n[00:05]\n[00:15]うえ\n[00:18]\n')
        self.assertEqual([(p['time'],p['end']) for p in parsed['lines']],[(2.,5.),(10.,15.),(15.,18.)])
        self.assertEqual(parsed['offset_tag_ms'],200)
        self.assertEqual(parsed['untimed_lines'],[])
        self.assertEqual(len(parse_lrc('[00:02]あ\n[00:02]あ')['lines']),1)
        with self.assertRaisesRegex(ValueError,'同一'):parse_lrc('[00:02]あ\n[00:02]翻译')
        self.assertEqual(parse_lrc('[00:02]あ\nuntimed')['untimed_lines'],['untimed'])
        with self.assertRaises(ValueError):parse_lrc('[00:60.1]あ')

    def test_many_to_one_and_many_to_many_keep_every_source_unit_once(self):
        for timeline,units in [(notes([0,.3,.6]),reading('アイウエオ')['units']),
                               (notes([0,.2,.4,.6,.8]),reading('アイウ')['units']),
                               (notes([0,.4,.8,1.2]),reading('アイ')['units'])]:
            original=deepcopy(timeline);result=allocate_units(timeline,units)
            self.assertFalse(result['unassigned'])
            self.assertEqual([i for ids in result['unit_indices'] for i in ids],list(range(len(units))))
            self.assertEqual(timeline,original)
            score,_=notes_to_score(timeline,result['labels'],{v:['a'] for v in result['labels']},120)
            self.assertTrue(score)
        self.assertIn('ア',allocate_units(notes([0,.4,.8]),reading('ア')['units'])['labels'])

    def test_english_words_are_not_repeated_as_filler_across_rests(self):
        units=[dict(text='hello',lang='en',weight=2,vowel=None)]
        result=allocate_units(notes([0,.5]),units)
        self.assertEqual(result['unassigned'],[0])
        self.assertEqual(result['labels'],['',''])
        # A contiguous melisma can hold the same word without rearticulating it.
        self.assertEqual(allocate_units(notes([0,.2]),units)['labels'],['hello','-'])

    def test_explicit_blank_lrc_preserves_instrumental_region_and_note_timing(self):
        timeline=notes([1,1.5,2,2.5,4,5,7,7.5,8,8.5]);before=deepcopy(timeline)
        parsed=parse_lrc('[00:02]アイウエ\n[00:04]\n[00:08]オカキク\n[00:10]')
        result=align_lrc(timeline,parsed,[reading('アイウエ'),reading('オカキク')],offset=-1,scale=1)
        self.assertEqual(result['unowned_notes'],[4,5]);self.assertEqual(result['labels'][4:6],['SP','SP'])
        self.assertEqual(result['phrases'][0]['note_indices'],[0,1,2,3])
        self.assertEqual(result['phrases'][1]['note_indices'],[6,7,8,9])
        self.assertEqual(result['unassigned_units'],0);self.assertEqual(timeline,before)
        self.assertFalse(result['exact_text_alignment_verified'])

    def test_joint_boundaries_never_overlap_or_drop_lyrics(self):
        parsed=parse_lrc('[00:01]アイウエ\n[00:02]カキ\n[00:03.8]クケコ')
        timeline=notes([1,1.2,1.4,1.6,2.1,2.3,3.6,3.8,4.0])
        result=align_lrc(timeline,parsed,[reading('アイウエ'),reading('カキ'),reading('クケコ')],offset=0,scale=1)
        ids=[n for p in result['phrases'] for n in p['note_indices']]
        self.assertEqual(ids,sorted(set(ids)))
        self.assertEqual(result['unassigned_units'],0)
        self.assertEqual(sum(len(p['units']) for p in result['phrases']),9)

    def test_clock_recovers_shift_and_small_scale_drift(self):
        times=[3,7.3,13.2,19,25.7,33,42.9,53,65,79,94,111,130]
        lines=[dict(time=t,end=times[i+1] if i+1<len(times) else None) for i,t in enumerate(times)]
        timeline=notes([t*1.02-1.3+j*.23 for t in times for j in range(4)])
        result=fit_clock(timeline,lines)
        self.assertAlmostEqual(result['offset_seconds'],-1.3,delta=.1)
        self.assertAlmostEqual(result['scale'],1.02,delta=.002)
        for kwargs in [dict(offset=float('nan')),dict(scale=0),dict(offset=301)]:
            with self.assertRaises(ValueError):fit_clock(timeline,lines,**kwargs)

    def test_lrc_incomplete_phrase_cannot_be_bypassed_by_editing_one_other_note(self):
        prepared=dict(labels=['ア','イ'],alignment=dict(unassigned_units=3),unmatched_moras=3)
        with self.assertRaisesRegex(ValueError,'整句'):require_edited_mapping(prepared,['アア','イ'])

    def test_abbreviated_refrain_is_flagged_not_certified(self):
        parsed=parse_lrc('[00:01]ラ\n[00:11]アイ')
        result=align_lrc(notes([1+i*.5 for i in range(22)]),parsed,[reading('ラ'),reading('アイ')],offset=0,scale=1)
        self.assertTrue(any('省略' in w for w in result['phrases'][0]['warnings']))
