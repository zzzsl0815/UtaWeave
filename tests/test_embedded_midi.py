from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import mido
from song2voice.midi_score import inspect_midi, embedded_midi_lyrics, require_render_confirmation, notes_to_score
from song2voice.web.workflow import prepare


def make_midi(path, labels, separate=False):
    midi=mido.MidiFile(charset='utf-8');notes=mido.MidiTrack();midi.tracks.append(notes)
    lyrics=mido.MidiTrack() if separate else notes
    if separate:midi.tracks.append(lyrics)
    for i,label in enumerate(labels):
        if label is not None:lyrics.append(mido.MetaMessage('lyrics',text=label,time=480 if separate and i else 0))
        notes.append(mido.Message('note_on',note=60+i,velocity=80))
        notes.append(mido.Message('note_off',note=60+i,time=480))
    midi.save(path)


class EmbeddedMidiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'input.mid'

    def test_same_track_keeps_exact_lyrics_pitch_and_tempo(self):
        make_midi(self.path,['春','-','风','SP'])
        info=inspect_midi(self.path);imported=embedded_midi_lyrics(info,0)
        self.assertEqual(imported['labels'],['春','-','风','SP'])
        score,_=notes_to_score(info['tracks'][0]['notes'],imported['labels'],{'春':['zh/ch','zh/un'],'风':['zh/f','zh/eng']},info['bpm'])
        self.assertEqual(score[0]['pitch'],[60,61,62,0]);self.assertEqual(score[0]['pitch_dur'],[.5]*4)
        self.assertEqual(score[0]['word'],['春','风','SP'])

    def test_unique_separate_lyrics_track_is_used_without_guessing(self):
        make_midi(self.path,list('春风吹过'),separate=True)
        info=inspect_midi(self.path);result=embedded_midi_lyrics(info,0)
        self.assertEqual(result['lyric_track'],1)
        self.assertEqual(result['labels'],list('春风吹过'))
        self.assertEqual([n['lyric'] for n in info['tracks'][0]['notes']],['']*4)

    def test_ambiguous_lyrics_track_needs_explicit_selection(self):
        make_midi(self.path,list('春风吹过'),separate=True)
        info=inspect_midi(self.path);other=deepcopy(info['lyric_tracks'][0]);other['index']=2
        info['lyric_tracks'].append(other)
        with self.assertRaisesRegex(ValueError,'唯一'):embedded_midi_lyrics(info,0)
        self.assertEqual(embedded_midi_lyrics(info,0,2)['lyric_track'],2)

    def test_missing_duplicate_and_unmatched_lyrics_are_never_silently_filled(self):
        make_midi(self.path,['春',None,'风'])
        with self.assertRaisesRegex(ValueError,'没有歌词'):embedded_midi_lyrics(inspect_midi(self.path),0)
        make_midi(self.path,['春','风']);info=inspect_midi(self.path)
        for event,message in [(dict(tick=0,text='秋'),'多条'),(dict(tick=1,text='秋'),'未对应')]:
            changed=deepcopy(info);changed['lyric_tracks'][0]['events'].append(event)
            with self.assertRaisesRegex(ValueError,message):embedded_midi_lyrics(changed,0)

    def test_newlines_mark_phrase_boundaries_without_changing_notes(self):
        make_midi(self.path,['春','风\n','吹','过']);info=inspect_midi(self.path)
        result=embedded_midi_lyrics(info,0)
        self.assertEqual(result['labels'],list('春风吹过'))
        self.assertEqual([(p['note_start'],p['note_end']) for p in result['phrases']],[(0,2),(2,4)])
        make_midi(self.path,['春','风','\n吹','过'])
        self.assertEqual(embedded_midi_lyrics(inspect_midi(self.path),0)['phrases'],result['phrases'])

    def test_only_server_prepared_embedded_scores_skip_manual_confirmation(self):
        prepared=dict(mode='midi',lyric_source='embedded',review_required=False,labels=['春'])
        require_render_confirmation(prepared,dict(labels=['春'],confirmed=False))
        with self.assertRaisesRegex(ValueError,'对应关系'):
            require_render_confirmation(prepared,dict(labels=['秋']))
        with self.assertRaisesRegex(ValueError,'核对'):
            require_render_confirmation(dict(mode='midi',labels=['春']),dict(labels=['春'],lyric_source='embedded',review_required=False))

    def test_prepare_never_fetches_or_reallocates_embedded_lyrics(self):
        uploads=self.root/'uploads';uploads.mkdir();token='a'*32;path=uploads/(token+'.mid')
        make_midi(path,list('春风吹过'))
        (uploads/(token+'.json')).write_text(json.dumps({'file':path.name}))
        job=self.root/'jobs'/('b'*32);job.mkdir(parents=True)
        (job/'status.json').write_text(json.dumps({'status':'queued'}))
        with patch('song2voice.web.workflow.lyrics',side_effect=AssertionError('No external lyrics')), \
             patch('song2voice.lyric_alignment.align_lrc',side_effect=AssertionError('No reallocation')), \
             patch('song2voice.web.workflow.g2p',return_value=[['zh/a']]*4) as g2p:
            prepare({'workspace':str(self.root)},job,dict(mode='midi',lyric_source='embedded',midi_asset=token,track=0,language='zh'))
        prepared=json.loads((job/'prepared.json').read_text());state=json.loads((job/'status.json').read_text())
        self.assertEqual(prepared['labels'],list('春风吹过'));self.assertFalse(prepared['review_required'])
        self.assertEqual(state['status'],'awaiting_voice');self.assertIsNone(prepared['alignment'])
        self.assertEqual(g2p.call_args.kwargs,dict(readings=list('春风吹过'),language='zh',indexed=True))
        self.assertEqual(prepared['segment_preview']['chunk_count'],1)
