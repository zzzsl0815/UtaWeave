import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf

from song2voice.references import prepare_clip, prepare_reference
from song2voice.selection import require_reference, resolve_reference_run
from song2voice.score import sha


class ReferenceInputs(unittest.TestCase):
    def test_provided_clip_is_exact_frozen_and_resumable_without_models(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); clip = root/'voice.wav'
            sf.write(clip, .1*np.sin(2*np.pi*220*np.arange(32000)/16000), 16000)
            run = prepare_reference(root/'out', clip=clip)
            self.assertEqual(run, prepare_reference(root/'out', clip=clip))
            selected = require_reference(root/'out')
            self.assertEqual(selected['status'], 'provided_clip')
            self.assertIsNone(selected['score'])
            self.assertEqual(sha(selected['audio']), sha(clip))
            clip.unlink()
            self.assertTrue(Path(require_reference(root/'out')['audio']).exists())
            Path(selected['audio']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'changed'):
                require_reference(root/'out')

    def test_invalid_silent_long_and_ambiguous_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); clip = root/'voice.wav'
            for wave in (np.zeros(32000), np.ones(26*16000)*.1, np.full(32000, np.nan)):
                sf.write(clip, wave, 16000, subtype='FLOAT')
                with self.assertRaises(ValueError):
                    prepare_clip(clip, root/'out')
            with self.assertRaises(ValueError): prepare_reference(root/'out', clip=clip, audio=clip)
            with self.assertRaises(ValueError): prepare_reference(root/'out', clip=clip, policy={})
            with self.assertRaises(ValueError): prepare_reference(root/'out', audio=clip)

    def test_latest_cannot_escape_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'latest.json').write_text(json.dumps(dict(run='../outside')))
            with self.assertRaisesRegex(ValueError, 'leaves'):
                resolve_reference_run(root)

    def test_reuse_separation_routes_to_same_automatic_selector(self):
        with patch('song2voice.reference_auto.prepare', return_value=Path('/test/frozen')) as automatic:
            self.assertEqual(prepare_reference('out', from_run='stems'), Path('/test/frozen'))
            self.assertEqual(automatic.call_args.args[:2], ('stems', 'out'))


if __name__ == '__main__': unittest.main()
