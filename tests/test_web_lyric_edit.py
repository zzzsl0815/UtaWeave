from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from song2voice.lyric_alignment import align_lrc,parse_lrc
from song2voice.web.workflow import realign


def reading(text):
    return dict(reading=' '.join(text),moras=list(text),units=[dict(text=c,lang='ja',weight=1,vowel='ア') for c in text],warnings=[])


class PhraseEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)
        timeline=[dict(start=1+i*.5,end=1.2+i*.5,pitch=60) for i in range(8)]
        a=align_lrc(timeline,parse_lrc('[00:01]アイウエ\n[00:03]カキクケ'),[reading('アイウエ'),reading('カキクケ')],offset=0,scale=1)
        self.data=dict(mode='midi',notes=timeline,labels=a.pop('labels'),revision='original',alignment=a,unmatched_moras=0)
        (self.job/'prepared.json').write_text(json.dumps(self.data))
        (self.job/'status.json').write_text(json.dumps(dict(status='awaiting_score')))

    def request(self,**kw):
        return dict(mode='phrase',prepared_revision='original',phrase_id=0,labels=['イイ']+self.data['labels'][1:-1]+['エエ'],
            reading='アオ',note_start=0,note_end=4,**kw)

    def test_phrase_edit_preserves_other_note_edits_timing_and_snapshot(self):
        request=self.request()
        with patch('song2voice.web.workflow.g2p',return_value=dict(lines=[reading('アオ')])):realign({},self.job,request)
        result=json.loads((self.job/'prepared.json').read_text())
        self.assertEqual(result['labels'][4:],request['labels'][4:])
        self.assertNotEqual(result['revision'],'original')
        self.assertEqual(result['notes'],self.data['notes'])
        self.assertEqual(result['alignment']['phrases'][0]['reading'],'ア オ')
        backup=json.loads(next((self.job/'alignment-history').glob('*.json')).read_text())
        self.assertEqual(backup['labels'],request['labels'])
        with self.assertRaisesRegex(ValueError,'版本过期'):realign({},self.job,request)

    def test_invalid_ranges_and_empty_reading_leave_original_score_untouched(self):
        for values in [dict(note_start=-1),dict(note_end=7),dict(reading=''),dict(note_end=0),dict(phrase_id=99)]:
            request=self.request();request.update(values)
            with self.assertRaises(ValueError):realign({},self.job,request)
            self.assertEqual(json.loads((self.job/'prepared.json').read_text()),self.data)

    def test_global_alignment_can_recover_using_edited_phrase_reading(self):
        with patch('song2voice.web.workflow.g2p',return_value=dict(lines=[reading('アオ')])):realign({},self.job,self.request())
        edited=json.loads((self.job/'prepared.json').read_text())
        realign({},self.job,dict(mode='global',offset=0,scale=1,labels=edited['labels'],prepared_revision=edited['revision']))
        result=json.loads((self.job/'prepared.json').read_text())
        self.assertEqual(result['alignment']['phrases'][0]['reading'],'ア オ')
        self.assertEqual(result['notes'],self.data['notes'])
