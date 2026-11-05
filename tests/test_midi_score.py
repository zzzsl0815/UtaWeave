import tempfile
from pathlib import Path
import unittest
import mido
from song2voice.midi_score import inspect_midi, notes_to_score, clean_lyrics
from song2voice.web.workflow import export_lyric_midi


class MidiScoreTests(unittest.TestCase):
    def test_tempo_map_note_off_velocity_zero_and_export_lyrics(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'source.mid';m=mido.MidiFile(ticks_per_beat=480)
            t=mido.MidiTrack();m.tracks.append(t)
            t.extend([mido.MetaMessage('set_tempo',tempo=500000),mido.Message('note_on',note=60,velocity=80,time=0),
                mido.Message('note_on',note=60,velocity=0,time=480),mido.MetaMessage('set_tempo',tempo=1000000),
                mido.Message('note_on',note=62,velocity=80),mido.Message('note_off',note=62,time=480)])
            m.save(p);info=inspect_midi(p);notes=info['tracks'][0]['notes']
            self.assertEqual([(n['start'],n['end']) for n in notes],[(0.,.5),(.5,1.5)])
            self.assertFalse(info['tracks'][0]['polyphonic'])
            out=Path(folder)/'lyric.mid';export_lyric_midi(p,0,notes,['き','み'],out)
            actual=inspect_midi(out)['tracks'][0]['notes']
            self.assertEqual([n['lyric'] for n in actual],['き','み'])
            self.assertEqual([(n['tick'],n['end_tick'],n['pitch']) for n in notes],[(n['tick'],n['end_tick'],n['pitch']) for n in actual])

    def test_melisma_and_rest_preserve_physical_time(self):
        notes=[dict(start=0.,end=.5,pitch=60),dict(start=.5,end=1.,pitch=62),dict(start=1.1,end=1.6,pitch=64)]
        score,offset=notes_to_score(notes,['き','-','み'],{'き':['k','i'],'み':['m','i']},120)
        self.assertEqual(offset,.5);self.assertEqual(score[0]['word'],['き','SP','み'])
        self.assertEqual(score[0]['pitch2word'],[0,0,1,2])
        self.assertAlmostEqual(sum(score[0]['word_dur']),1.6)
        self.assertEqual(score[0]['ph'],['k','i','SP','m','i'])
        with self.assertRaises(ValueError):notes_to_score(notes,['き','-','-'],{'き':['k','i']},120)
        with self.assertRaises(ValueError):notes_to_score(notes,['き','','み'],{'き':['k','i'],'み':['m','i']},120)

    def test_lrc_metadata_is_not_sung(self):
        self.assertEqual(clean_lyrics('[ar:artist]\n[00:03.21]こんにちは\n[00:04.0][00:06.0]世界'), 'こんにちは\n世界')
