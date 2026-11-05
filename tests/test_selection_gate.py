from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from song2voice.score import sha
from song2voice.selection import (reference_status,require_reference,select_reference,
                                 SelectionRequired,StaleSelection)


def make_task(run):
    (run/'candidates').mkdir()
    rows = []
    for i in range(1,3):
        path = run/'candidates'/f'candidate_{i}.wav'
        path.write_bytes(b'score-stage validated fixture content '+bytes([i]))
        rows.append(dict(id=f'c{i}',shortlist_number=i,path=str(path.relative_to(run)),
            sha256=sha(path),score=4.6-i*.1,start_seconds=i*10.,end_seconds=i*10.+8.,
            processing='native_vocal'))
    audio = run/'source.flac'
    audio.write_bytes(b'fixture source content hash')
    manifest = dict(status='complete',source=dict(audio_sha256=sha(audio)),candidates=rows)
    (run/'reference_candidates.json').write_text(json.dumps(manifest))
    return manifest


class SelectionGate(unittest.TestCase):
    def test_waiting_then_freezes_portable_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            make_task(run)
            self.assertEqual(reference_status(run)['state'],'awaiting_reference_selection')
            with self.assertRaises(SelectionRequired):require_reference(run)
            chosen = select_reference(run,'c1')
            self.assertFalse(Path(chosen['audio']).is_absolute())
            self.assertEqual(sha(run/chosen['audio']),chosen['sha256'])
            (run/'candidates/candidate_1.wav').unlink()
            self.assertEqual(reference_status(run)['state'],'ready')
            self.assertTrue(Path(require_reference(run)['audio']).is_file())

    def test_stale_confirmation_rejected_and_switch_preserves_history(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            make_task(run)
            first = select_reference(run,'c1')
            before = (run/'reference.selected.json').read_bytes()
            with self.assertRaises(StaleSelection):
                select_reference(run,'c2',expected_manifest_sha256='stale')
            self.assertEqual((run/'reference.selected.json').read_bytes(),before)
            self.assertEqual(select_reference(run,'c1'),first)
            select_reference(run,'c2')
            self.assertEqual(reference_status(run)['selection']['candidate_id'],'c2')
            history = list((run/'selection_history').glob('*.json'))
            self.assertEqual(len(history),1)
            self.assertEqual(json.loads(history[0].read_text())['candidate_id'],'c1')

    def test_snapshot_damage_blocks_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            make_task(run)
            chosen = select_reference(run,'c1')
            (run/chosen['audio']).write_bytes(b'changed')
            self.assertEqual(reference_status(run)['state'],'invalid_reference')
            with self.assertRaises(SelectionRequired):require_reference(run)



if __name__=='__main__':unittest.main()
