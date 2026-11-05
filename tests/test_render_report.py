import json
from pathlib import Path
import tempfile
import unittest

from song2voice.rendering.report import build_report
from song2voice.score import sha


class RenderReportTests(unittest.TestCase):
    def test_report_uses_local_exports_and_escapes_lyrics(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outputs = {}
            for name in ('mix', 'vocal', 'natural'):
                outputs[name] = dict(seconds=10.)
                for kind in ('mp3', 'wav'):
                    path = root/f'{name} clip.{kind}'
                    path.write_bytes(b'audio-export')
                    outputs[name].update({kind: str(path), kind+'_sha256': sha(path)})
            manifest = dict(status='complete', outputs=outputs,
                            plan=dict(chunks=[dict(words=[dict(start=3., word='<script>x</script>')])]))
            path = root/'manifest.json'
            path.write_text(json.dumps(manifest))
            page = build_report(path).read_text()
            self.assertIn('mix%20clip.mp3', page)
            self.assertIn('data-time="2.700"', page)
            self.assertIn('&lt;script&gt;x&lt;/script&gt;', page)
            self.assertNotIn(str(root), page)
            self.assertTrue((root/'listening_player.js').is_file())
            (root/'vocal clip.wav').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Export hash mismatch'):
                build_report(path)


if __name__ == '__main__':
    unittest.main()
