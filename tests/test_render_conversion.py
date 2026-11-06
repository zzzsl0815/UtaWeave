"""Check that target changes preserve guides and converted audio is realigned."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf

from song2voice.score import sha
from song2voice.references import prepare_clip
from song2voice.rendering.pipeline import guide_recipe
from song2voice.rendering.convert import main as convert_phrases


class RenderConversionTests(unittest.TestCase):
    def test_target_timbre_changes_do_not_invalidate_guide_cache(self):
        cfg=dict(score='score',reference_run='alice',guide_reference_run='fixed',seed_vc_config='seed',seed=1)
        names=('rendering/generate.py','rendering/runtime.py','rendering/audio.py','rendering/align.py',
               'rendering/retry.py','rendering/select.py','conditioning.py','selection.py','rendering/midi_plan.py')
        recipe=dict(code_sha256={n:n for n in names},reference_sha256='alice',guide_reference_sha256='fixed',score_sha256='score')
        first=guide_recipe(cfg,recipe)
        cfg['reference_run']='bob';cfg['seed_vc_config']='more_steps';recipe['reference_sha256']='bob'
        self.assertEqual(first,guide_recipe(cfg,recipe))
        cfg['seed']=2
        self.assertNotEqual(first,guide_recipe(cfg,recipe))
        changed=guide_recipe(cfg,recipe)
        recipe['code_sha256']['rendering/midi_plan.py']='new planner'
        self.assertNotEqual(changed,guide_recipe(cfg,recipe))

    def test_conversion_preserves_plan_and_requires_new_alignment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); parent=root/'guide';parent.mkdir();native=root/'native';native.mkdir()
            source=parent/'chunk.wav';voice=native/'voice.wav'
            sf.write(source,.1*np.sin(np.arange(2*48000)*.03),48000,subtype='FLOAT')
            sf.write(voice,.2*np.sin(np.arange(2*44100)*.05),44100,subtype='FLOAT')
            reference=prepare_clip(source,root/'reference')
            manifest=dict(status='generated',reference=dict(id='guide_reference'),plan=dict(chunks=[dict(id='c0',words=['a'])]),
                results=[dict(id='c0',path='chunk.wav',sha256=sha(source),hit_length_cap=False)],protocol={})
            (parent/'manifest.json').write_text(json.dumps(manifest))
            row=dict(id='c0',audio=str(voice),audio_sha256=sha(voice))
            (native/'status.json').write_text(json.dumps(dict(results=[row])))
            runtime=root/'runtime.json';runtime.write_text(json.dumps(dict(seed_vc_config='seed',reference_run=str(reference))))
            with patch.dict(os.environ,UTAWEAVE_RENDER_CONFIG=str(runtime)), \
                 patch('song2voice.rendering.convert.convert',return_value=native):
                convert_phrases(parent,root/'out')
                before=(root/'out/manifest.json').read_bytes()
                convert_phrases(parent,root/'out')
                self.assertEqual((root/'out/manifest.json').read_bytes(),before)
            result=json.loads(before)
            self.assertEqual(result['status'],'generated')
            self.assertEqual(result['plan'],manifest['plan'])
            self.assertEqual(result['guide_reference'],manifest['reference'])
            self.assertTrue(result['protocol']['realign_after_conversion'])
            self.assertFalse((root/'out/generated_alignment.json').exists())
            self.assertEqual(sf.info(root/'out'/result['results'][0]['path']).samplerate,48000)
            self.assertEqual(sha(source),manifest['results'][0]['sha256'])
            self.assertEqual(sha(voice),row['audio_sha256'])


if __name__=='__main__':unittest.main()
