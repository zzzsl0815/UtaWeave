import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from song2voice.rendering.audio import smooth_edges
from song2voice.rendering.generate import plan
from song2voice.rendering.runtime import runtime


class PortableRenderTests(unittest.TestCase):
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
