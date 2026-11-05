import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from song2voice.reference_auto import policy_with,rank_candidates,window_stats,export_windows,prepare
from song2voice.selection import select_reference,require_reference
from song2voice.score import sha


def row(name, quality, seconds, effective=None, **extra):
    return dict(id=name,score=quality,seconds=seconds,effective_seconds=effective if effective is not None else seconds*.9,
        active_fraction=.9,maximum_quiet_seconds=.1,rms=.1,quiet_edges=True,**extra)


class AutoReferenceTests(unittest.TestCase):
    def test_longer_good_reference_beats_small_quality_advantage(self):
        result = rank_candidates([row('short',4.4,7),row('long',4.25,18)],policy_with())
        self.assertEqual(result[0]['id'],'long')
        self.assertTrue(result[0]['eligible'])

    def test_duration_cannot_buy_large_quality_loss(self):
        result = rank_candidates([row('short',4.4,7),row('bad_long',3.9,24)],policy_with())
        self.assertEqual(result[0]['id'],'short')
        self.assertFalse(result[1]['eligible'])
        with self.assertRaisesRegex(ValueError,'quality floor'):
            rank_candidates([row('bad',3.2,24)],policy_with())

    def test_padding_with_silence_does_not_improve_choice(self):
        quiet = row('padded',4.4,24,5.)
        quiet.update(active_fraction=.2,maximum_quiet_seconds=15.)
        result = rank_candidates([row('valid',4.2,7),quiet],policy_with())
        self.assertEqual(result[0]['id'],'valid')
        self.assertIn('long_silence',result[1]['rejection_reasons'])

    def test_limits_and_deterministic_ties(self):
        with self.assertRaises(ValueError):policy_with(dict(maximum_seconds=30))
        with self.assertRaises(ValueError):policy_with(dict(maximum_quality_drop=2))
        with self.assertRaises(ValueError):policy_with(dict(unknown=1))
        for value in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError): policy_with(dict(edge_penalty=value))
        a,b = row('a',4.2,12),row('b',4.2,12)
        self.assertEqual(rank_candidates([b,a],policy_with())[0]['id'],'a')

    def test_windows_search_full_source_without_previous_selection(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); sr=16000
            wave=(.1*np.sin(2*np.pi*220*np.arange(40*sr)/sr)).astype('float32')
            stem=root/'stem.wav'; sf.write(stem,wave,sr,subtype='FLOAT')
            export_windows(dict(out=str(root),stem=str(stem),source=dict(audio_sha256='source'),
                policy=policy_with(dict(candidates_per_duration=2))))
            rows=json.loads((root/'candidate_windows.json').read_text())['candidates']
            self.assertTrue(rows)
            self.assertEqual({round(r['seconds']) for r in rows}, set(policy_with()['durations']))
            for r in rows:
                self.assertLessEqual(r['end_seconds'],40.)
                self.assertEqual(r['role'],'search')
                self.assertAlmostEqual(sf.info(root/r['path']).duration,r['seconds'],places=4)
            x=np.r_[wave[:sr],np.zeros(2*sr)]
            self.assertGreater(window_stats(x,sr,0,3,.001)['maximum_quiet_seconds'],1.9)

    def test_automatic_choice_has_truthful_status_and_frozen_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            audio=root/'candidate.wav'
            audio.write_bytes(b'fixture content only')
            r=dict(id='c',path=audio.name,sha256=sha(audio),score=4.2,start_seconds=1.,end_seconds=19.)
            (root/'reference_candidates.json').write_text(json.dumps(dict(source=dict(audio_sha256='source'),candidates=[r])))
            selected=select_reference(root,'c',method='automatic',decision=dict(strategy='quality_duration'))
            self.assertEqual(selected['status'],'automatic_selected')
            self.assertEqual(require_reference(root)['decision']['strategy'],'quality_duration')
            audio.unlink()
            self.assertTrue(Path(require_reference(root)['audio']).exists())

    def test_separation_to_automatic_selection_resumes_without_models(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            origin=root/'separated';(origin/'stems').mkdir(parents=True)
            source=root/'source.wav'
            sf.write(source,.1*np.sin(2*np.pi*220*np.arange(26*16000)/16000),16000)
            stem=origin/'stems/vocal_native.wav';stem.write_bytes(source.read_bytes())
            weights=root/'fake.bin';weights.write_bytes(b'test weights')
            runtime=dict(python='test-python',singmos_weights=str(weights),singmos_ssl=str(weights))
            (origin/'separation_job.local.json').write_text(json.dumps(dict(audio=str(source),runtime=runtime)))
            (origin/'separation.json').write_text(json.dumps(dict(status='complete',source_sha256=sha(source),
                files={'stems/vocal_native.wav':dict(sha256=sha(stem))})))
            def score_stub(command,**kwargs):
                config=json.loads(Path(command[command.index('--config')+1]).read_text())
                run=Path(config['out']);data=json.loads((run/'candidate_windows.json').read_text())
                (run/'quality_scores.json').write_text(json.dumps(dict(status='complete',method='test stub',
                    scores=[dict(r,score=4.2) for r in data['candidates']])))
            with patch('song2voice.reference_auto.subprocess.run',side_effect=score_stub) as scorer:
                run=prepare(origin,root/'out',dict(candidates_per_duration=1))
                again=prepare(origin,root/'out',dict(candidates_per_duration=1))
                self.assertEqual(run,again)
                self.assertEqual(scorer.call_count,2)
            selected=require_reference(run)
            self.assertEqual(selected['status'],'automatic_selected')
            self.assertAlmostEqual(selected['source_window']['end_seconds']-selected['source_window']['start_seconds'],24.)
            crop=json.loads((run/'search/candidate_windows.json').read_text())['candidates'][0]
            (run/'search'/crop['path']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'crop changed'):
                prepare(origin,root/'out',dict(candidates_per_duration=1))


if __name__=='__main__':unittest.main()
