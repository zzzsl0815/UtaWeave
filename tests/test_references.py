import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from song2voice.references import choose_diverse, select_reference
from song2voice.score import sha


class ReferenceSelection(unittest.TestCase):
    def test_scores_descend_and_overlapping_variants_do_not_fill_shortlist(self):
        rows = [dict(id='dry',start_seconds=0.,end_seconds=8.,score=4.1),
                dict(id='native',start_seconds=0.,end_seconds=8.,score=4.6),
                dict(id='shifted',start_seconds=1.,end_seconds=9.,score=4.5),
                dict(id='different',start_seconds=12.,end_seconds=20.,score=4.2)]
        self.assertEqual([r['id'] for r in choose_diverse(rows,3)],['native','different'])

    def test_human_selection_is_explicit_and_rejects_modified_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)
            audio=run/'candidate.wav'
            audio.write_bytes(b'test candidate content hash only')
            candidate=dict(id='candidate_001',path='candidate.wav',sha256=sha(audio),score=4.2,
                           start_seconds=1.,end_seconds=9.,processing='native_vocal')
            (run/'reference_candidates.json').write_text(json.dumps(dict(source=dict(audio_sha256='source-hash'),candidates=[candidate])))
            self.assertFalse((run/'reference.selected.json').exists())
            selected=select_reference(run,'candidate_001')
            self.assertEqual(selected['status'],'human_selected')
            self.assertEqual(selected['sha256'],candidate['sha256'])
            audio.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError,'changed'):
                select_reference(run,'candidate_001')


if __name__=='__main__':unittest.main()
