import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf

from song2voice.score import sha
from song2voice.references import prepare_clip
from song2voice.voice_conversion.convert import convert, resolve_config, result_valid
from song2voice.voice_conversion.report import preview, build_report


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.guide = self.root/'guide.wav'
        sf.write(self.guide, .2*np.sin(2*np.pi*220*np.arange(32000)/16000), 16000, subtype='FLOAT')
        self.reference_run = prepare_clip(self.guide, self.root/'reference')
        self.config = dict(repo=str(self.root), assets=str(self.root), python=sys.executable, seed=1)
        self.config_path = self.root/'config.json'; self.save_config()
        self.sources = [dict(id='case_00', audio=str(self.guide), lyric='<script>bad</script>')]

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config))

    def run_conversion(self, runner):
        with patch('song2voice.voice_conversion.convert.runtime_recipe', return_value=dict(config=self.config)), \
             patch('song2voice.voice_conversion.convert.subprocess.run', side_effect=runner):
            return convert(self.config_path, self.sources, self.reference_run, self.root/'out')

    def fake_worker(self, command, **kwargs):
        request = json.loads(Path(command[-1]).read_text())
        for job in request['jobs']:
            Path(job['output']).write_bytes(Path(job['source']).read_bytes())
            Path(job['result']).write_text(json.dumps(dict(status='complete', input_signature=job['input_signature'],
                audio=job['output'], audio_sha256=sha(job['output']), seconds=2., duration_delta_ms=0.)))

    def test_frozen_inputs_resume_and_corrupt_output_regenerates(self):
        run = self.run_conversion(self.fake_worker)
        self.assertEqual(sha(run/'inputs/case_00.wav'), sha(self.guide))
        def unexpected(*args, **kwargs): self.fail('Cached conversion must not launch GPU worker')
        self.assertEqual(run, self.run_conversion(unexpected))
        (run/'seed_vc/case_00.wav').write_bytes(b'damaged')
        self.assertEqual(run, self.run_conversion(self.fake_worker))
        (run/'inputs/reference.wav').write_bytes(b'damaged')
        with self.assertRaisesRegex(ValueError, 'Frozen input corrupted'):
            self.run_conversion(unexpected)

    def test_changed_reference_and_changed_guide_have_different_cache_keys(self):
        old = self.run_conversion(self.fake_worker)
        wave, sr = sf.read(self.guide)
        sf.write(self.guide, wave*.5, sr)
        changed_guide = self.run_conversion(self.fake_worker)
        self.assertNotEqual(old, changed_guide)
        self.reference_run = prepare_clip(self.guide, self.root/'reference')
        self.assertNotEqual(changed_guide, self.run_conversion(self.fake_worker))

    def test_invalid_settings_and_path_ids_are_rejected(self):
        for key,value in [('pitch_shift',12), ('steps',0), ('cfg',float('nan')), ('seed',-1), ('fp16','false')]:
            with self.subTest(key=key):
                bad = dict(self.config, **{key:value}); self.config_path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): resolve_config(self.config_path)
        self.sources[0]['id']='../../outside'
        with self.assertRaisesRegex(ValueError,'guide ID'): self.run_conversion(self.fake_worker)

    def test_venv_interpreter_symlink_is_preserved(self):
        interpreter=self.root/'venv-python'; interpreter.symlink_to(sys.executable)
        self.config['python']=str(interpreter); self.save_config()
        self.assertEqual(resolve_config(self.config_path)['python'],str(interpreter))

    def test_partial_failure_leaves_no_success_pointer_and_resumes(self):
        def fail(*args, **kwargs): raise RuntimeError('test worker failure')
        with self.assertRaisesRegex(RuntimeError, 'test worker'):
            self.run_conversion(fail)
        self.assertFalse((self.root/'out/latest.json').exists())
        self.assertEqual(json.loads(next((self.root/'out').glob('*/status.json')).read_text())['status'], 'failed')
        run = self.run_conversion(self.fake_worker)
        self.assertEqual(json.loads((run/'status.json').read_text())['status'], 'complete')

    def test_preview_preserves_samples_and_report_escapes_lyrics(self):
        destination=self.root/'listen.wav'; info=preview(self.guide,destination)
        original,sr=sf.read(self.guide); matched,matched_sr=sf.read(destination)
        self.assertEqual(sr,matched_sr)
        np.testing.assert_allclose(matched,original*10**(info['gain_db']/20),atol=2e-7)
        run=self.run_conversion(self.fake_worker)
        page=(run/'index.html').read_text()
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;',page)
        self.assertNotIn('<script>bad</script>',page)
        (run/'seed_vc/case_00.json').write_text('{invalid')
        build_report(run,json.loads((run/'recipe.json').read_text()))
        self.assertIn('等待重新生成',(run/'index.html').read_text())


if __name__ == '__main__': unittest.main()
