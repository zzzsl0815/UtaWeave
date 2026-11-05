import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from song2voice.rendering.audio import smooth_edges, clock_transform
from song2voice.rendering.generate import plan
from song2voice.rendering.runtime import runtime


class PortableRenderTests(unittest.TestCase):
    def test_short_tail_merges_with_context_without_dropping_unknown_pitch(self):
        with tempfile.TemporaryDirectory() as folder:
            stems=Path(folder)
            sf.write(stems/'vocal_native.wav',np.zeros(10*48000,dtype=np.float32),48000)
            score=[dict(line_index=i,source_window=dict(start_seconds=start),
                word=['あ','お'],word_dur=dur,pitch=pitches,note=['<NOTE_8>']*2,
                pitch_dur=dur,pitch2word=[0,1],ph=['a','o'],ph2word=[0,1])
                for i,start,dur,pitches in [(0,1.,[.4,.4],[60,62]),(1,4.,[.24,.01],[64,0])]]
            result=plan(score,whole_lines=True,stems=stems)
            self.assertEqual(result['chunk_count'],1)
            self.assertEqual(result['lyric_unit_count'],4)
            self.assertEqual(result['chunks'][0]['words'][-1]['pitch'],[0])

    def test_single_sustained_syllable_has_two_clock_endpoints(self):
        unit=dict(line_index=1,word_index=2,source_start=10.,source_end=11.,
                  generated_start=.2,generated_end=1.3,has_measured_or_inferred_pitch=True)
        result=clock_transform([unit])
        self.assertAlmostEqual(result['tempo_ratio'],1.1)
        self.assertAlmostEqual(result['placement_seconds']+.2/1.1,10.)
        self.assertAlmostEqual(result['placement_seconds']+1.3/1.1,11.)
        self.assertEqual(result['clock_basis'],'single_unit_endpoints')
        unit['generated_end']=3.2
        self.assertGreater(clock_transform([unit])['tempo_ratio'],1.25)
        unit['has_measured_or_inferred_pitch']=False
        self.assertGreater(clock_transform([unit])['tempo_ratio'],1.25)
        unit['source_end']=10.01
        with self.assertRaisesRegex(ValueError,'No usable clock span'):
            clock_transform([unit])

    def test_expanded_unknown_pitch_tail_cannot_escape_phrase_clock(self):
        units=[dict(line_index=1,word_index=0,source_start=231.,source_end=241.4,
                    generated_start=.23,generated_end=9.35,has_measured_or_inferred_pitch=True),
               dict(line_index=1,word_index=1,source_start=241.4,source_end=241.41,
                    generated_start=9.35,generated_end=10.16,has_measured_or_inferred_pitch=False)]
        result=clock_transform(units)
        self.assertAlmostEqual(result['placement_seconds']+.23/result['tempo_ratio'],231.)
        self.assertAlmostEqual(result['placement_seconds']+10.16/result['tempo_ratio'],241.41)
        self.assertGreater(result['tempo_ratio'],.8)
        self.assertLess(result['tempo_ratio'],1.25)

    def test_pause_planner_covers_each_unit_once_without_breaking_it(self):
        with tempfile.TemporaryDirectory() as folder:
            stems=Path(folder)
            audio=np.zeros(7*48000,dtype=np.float32)
            for start,end in [(1,2),(4,5)]:
                t=np.arange((end-start)*48000)/48000
                audio[start*48000:end*48000]=.1*np.sin(2*np.pi*220*t)
            sf.write(stems/'vocal_native.wav',audio,48000,subtype='FLOAT')
            score=[dict(line_index=i,source_window=dict(start_seconds=start),
                word=['あ'],word_dur=[1.],pitch=[60],note=['<NOTE_4>'],pitch_dur=[1.],
                pitch2word=[0],ph=['a'],ph2word=[0]) for i,start in enumerate([1.,4.])]
            result=plan(score,maximum=2.,whole_lines=True,stems=stems)
            self.assertEqual(result['chunk_count'],2)
            self.assertEqual([(w['line_index'],w['word_index']) for c in result['chunks'] for w in c['words']],[(0,0),(1,0)])
            self.assertTrue(result['all_lyrics_covered_once'])

    def test_instrumental_bleed_does_not_hide_aligned_lyric_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            stems=Path(folder)
            sf.write(stems/'vocal_native.wav',np.ones(8*48000,dtype=np.float32)*.03,48000)
            score=[dict(line_index=i,source_window=dict(start_seconds=start),
                word=['あ'],word_dur=[1.],pitch=[60],note=['<NOTE_4>'],pitch_dur=[1.],
                pitch2word=[0],ph=['a'],ph2word=[0]) for i,start in enumerate([1.,4.,6.])]
            result=plan(score,maximum=2.,whole_lines=True,stems=stems)
            self.assertTrue(result['all_lyrics_covered_once'])
            self.assertEqual(result['chunk_count'],3)
            self.assertTrue(result['chunks'][0]['right_boundary']['secondary_cut'])
            self.assertGreater(result['chunks'][0]['right_boundary']['time'],2.)
            self.assertLess(result['chunks'][0]['right_boundary']['time'],4.)

    def test_edge_taper_preserves_aligned_vowel_body(self):
        audio=np.zeros(48000,dtype=np.float32)
        audio[10000:30000]=.1
        rendered,audit=smooth_edges(audio,.2,.7)
        np.testing.assert_array_equal(rendered[9600:33600],audio[9600:33600])
        self.assertTrue(audit['lyric_body_unchanged'])

    def test_runtime_is_explicit_and_does_not_guess_an_old_workspace(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'utaweave render'):
                runtime()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps(dict(base_model='portable-base')))
            with patch.dict(os.environ,{'UTAWEAVE_RENDER_CONFIG':str(path)}):
                self.assertEqual(runtime()['base_model'],'portable-base')


if __name__=='__main__':unittest.main()
