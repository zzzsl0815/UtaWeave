from copy import deepcopy
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
import unittest

from song2voice.conditioning import compiled_duration, NOTE_UNITS
from song2voice.midi_score import notes_to_score
from song2voice.rendering.generate import plan
from song2voice.web.workflow import preview_midi_segments


class MidiPlanTests(unittest.TestCase):
    def make_score(self, notes, phrases=None, labels=None):
        labels=labels or ['ア']*len(notes)
        return notes_to_score(notes,labels,{'ア':['a']},120,phrases=phrases)[0]

    def assert_limits_and_coverage(self, split, notes):
        indices=[]
        for chunk in split['chunks']:
            self.assertLessEqual(chunk['content_seconds'],8.)
            self.assertLessEqual(compiled_duration(chunk['annotation']),8.)
            for word in chunk['words']:
                indices.extend(word['midi_note_indices'])
                expected=[notes[i] for i in word['midi_note_indices']]
                self.assertEqual(word['pitch'],[n['pitch'] for n in expected])
                self.assertAlmostEqual(word['start'],expected[0]['start'])
                self.assertAlmostEqual(word['end'],expected[-1]['end'])
        self.assertEqual(indices,list(range(len(notes))))

    def test_quarter_second_note_gaps_do_not_break_lyric_phrases(self):
        notes=[dict(start=1+i*.8,end=1.36+i*.8,pitch=60+i) for i in range(6)]
        phrases=[dict(id=0,note_start=0,note_end=6)]
        score=self.make_score(notes,phrases);before=deepcopy(score)
        split=plan(score,whole_lines=True)
        self.assertEqual(len(score),1)
        self.assertEqual(split['chunk_count'],1)
        self.assertEqual(split['chunks'][0]['lyric_phrase_ids'],[0])
        self.assertEqual(score,before)
        self.assert_limits_and_coverage(split,notes)

    def test_midi_plan_reads_timeline_header_never_placeholder_samples(self):
        notes=[dict(start=1+i,end=1.5+i,pitch=60) for i in range(6)]
        score=self.make_score(notes)
        with patch('song2voice.rendering.generate.sf.read',side_effect=AssertionError('No audio evidence')):
            with patch('song2voice.rendering.generate.sf.info',return_value=SimpleNamespace(duration=10.)):
                split=plan(score,stems=Path('/placeholder'))
        self.assertFalse(split['uses_audio_silence'])
        self.assertEqual(split['source_seconds'],10.)
        self.assertEqual(split['chunk_count'],1)

    def test_padding_counts_toward_eight_seconds_and_long_line_is_split(self):
        # Physical span fits eight seconds; quantized notes plus head/tail do not.
        notes=[dict(start=1+i,end=1.95+i,pitch=60) for i in range(8)]
        # Make a safe internal cut without extending the physical span.
        notes[3]['end']-=.1
        score=self.make_score(notes,[dict(id=0,note_start=0,note_end=8)])
        split=plan(score,maximum=30)
        self.assertEqual(split['chunk_count'],2)
        self.assertEqual(split['max_content_seconds'],8.)
        self.assert_limits_and_coverage(split,notes)

    def test_short_adjacent_lines_merge_but_not_across_long_interlude(self):
        notes=[dict(start=t,end=t+.6,pitch=60) for t in (1.,2.,3.,12.)]
        phrases=[dict(id=i,note_start=i,note_end=i+1) for i in range(4)]
        split=plan(self.make_score(notes,phrases))
        self.assertEqual([c['lyric_phrase_ids'] for c in split['chunks']],[[0,1,2],[3]])
        self.assertEqual(split['short_chunk_ids'],['chunk_01'])
        self.assert_limits_and_coverage(split,notes)

    def test_tied_syllable_is_atomic_across_lrc_boundary(self):
        notes=[dict(start=1.,end=2.,pitch=60),dict(start=2.,end=3.,pitch=62),dict(start=3.2,end=4.,pitch=64)]
        phrases=[dict(id=0,note_start=0,note_end=1),dict(id=1,note_start=1,note_end=3)]
        score=self.make_score(notes,phrases,['ア','-','ア'])
        split=plan(score)
        self.assertEqual(split['chunks'][0]['words'][0]['midi_note_indices'],[0,1])
        self.assert_limits_and_coverage(split,notes)

    def test_uncuttable_overlong_syllable_fails_without_truncating(self):
        notes=[dict(start=1.,end=10.,pitch=60)]
        with self.assertRaisesRegex(ValueError,'不会截断'):
            plan(self.make_score(notes))

    def test_preview_uses_same_clock_as_real_phonemes_and_is_readonly(self):
        notes=[dict(start=1+i*.8,end=1.36+i*.8,pitch=60) for i in range(14)]
        phrases=[dict(id=0,note_start=0,note_end=6),dict(id=1,note_start=6,note_end=14)]
        prepared=dict(mode='midi',notes=notes,labels=['ア']*len(notes),bpm=120,
                      alignment={'phrases':phrases},revision='r1')
        before=deepcopy(prepared)
        preview=preview_midi_segments(prepared,prepared['labels'])
        actual=plan(self.make_score(notes,phrases))
        self.assertEqual(preview['chunk_count'],actual['chunk_count'])
        self.assertEqual([c['estimated_duration'] for c in preview['chunks']],
                         [c['estimated_duration'] for c in actual['chunks']])
        self.assertEqual(prepared,before)
        self.assertEqual(preview['prepared_revision'],'r1')

    def test_phrase_ranges_cannot_overlap_or_reorder_notes(self):
        notes=[dict(start=1+i,end=1.5+i,pitch=60) for i in range(4)]
        with self.assertRaisesRegex(ValueError,'不重叠'):
            self.make_score(notes,[dict(id=0,note_start=0,note_end=3),dict(id=1,note_start=2,note_end=4)])

    def test_reducing_auto_sp_preserves_exact_clock_and_lyrics(self):
        notes=[dict(start=1+i*.432,end=1.2+i*.432,pitch=60+i) for i in range(8)]
        score=self.make_score(notes,[dict(id=0,note_start=0,note_end=8)]);before=deepcopy(score)
        original=plan(score);connected=plan(score,connect_short_rests=True)
        a=original['chunks'][0]['annotation'];b=connected['chunks'][0]['annotation']
        self.assertEqual(len(b['connected_short_rests']),7)
        self.assertEqual(b['word'].count('SP'),2)
        self.assertEqual(a['word'].count('SP'),9)
        self.assertAlmostEqual(compiled_duration(a),compiled_duration(b))
        self.assertAlmostEqual(sum(a['word_dur']),sum(b['word_dur']))
        def lyric_onsets(entry):
            t=0.;seen=set();onsets=[]
            for token,owner in zip(entry['note'],entry['pitch2word']):
                if owner not in seen and entry['word'][owner]!='SP':onsets.append(t)
                seen.add(owner);t+=NOTE_UNITS[token]*60/entry['bpm']
            return onsets
        for old,new in zip(lyric_onsets(a),lyric_onsets(b)):self.assertAlmostEqual(old,new)
        self.assertEqual([x for x in a['word'] if x!='SP'],[x for x in b['word'] if x!='SP'])
        self.assertEqual(original['chunks'][0]['words'],connected['chunks'][0]['words'])
        self.assertEqual(score,before)
        self.assert_limits_and_coverage(connected,notes)

    def test_reduction_preserves_phrase_boundaries_long_pauses_and_explicit_sp(self):
        notes=[dict(start=t,end=t+.2,pitch=60) for t in (1.,1.43,2.,2.43,2.85,3.1)]
        phrases=[dict(id=0,note_start=0,note_end=1),dict(id=1,note_start=1,note_end=6)]
        score=self.make_score(notes,phrases,['ア','ア','ア','SP','ア','ア'])
        split=plan(score,connect_short_rests=True)
        # First gap is a lyric boundary; second exceeds 300 ms; the next
        # interval includes an explicitly silent note; last gap is <75 ms.
        self.assertEqual(sum(len(c['annotation']['connected_short_rests']) for c in split['chunks']),0)

    def test_no_lrc_keeps_rest_policy_conservative(self):
        score=self.make_score([dict(start=1+i*.5,end=1.2+i*.5,pitch=60) for i in range(4)])
        split=plan(score,connect_short_rests=True)
        self.assertEqual(sum(len(c['annotation']['connected_short_rests']) for c in split['chunks']),0)

    def test_long_phrase_keeps_existing_cuts_and_eight_second_budget(self):
        notes=[dict(start=1+i*.43,end=1.2+i*.43,pitch=60) for i in range(46)]
        score=self.make_score(notes,[dict(id=0,note_start=0,note_end=46)])
        original=plan(score);connected=plan(score,connect_short_rests=True)
        self.assertGreater(connected['chunk_count'],1)
        self.assertEqual([c['midi_note_indices'] for c in original['chunks']],
                         [c['midi_note_indices'] for c in connected['chunks']])
        self.assertEqual([c['estimated_duration'] for c in original['chunks']],
                         [c['estimated_duration'] for c in connected['chunks']])
        self.assert_limits_and_coverage(connected,notes)
