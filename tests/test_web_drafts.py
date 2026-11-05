import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import unittest
import test_web_server


class DraftTests(unittest.TestCase):
    setUp = test_web_server.WebServerTests.setUp
    stop = test_web_server.WebServerTests.stop

    def test_drafts_keep_invalid_input_separate_tabs_and_ignore_late_requests(self):
        job=self.root/'jobs'/('e'*32);job.mkdir()
        original=dict(labels=['キ'],revision='new')
        (job/'prepared.json').write_text(json.dumps(original))
        payload=dict(schema=1,job_id=job.name,client_id='a'*32,sequence=2,
                     prepared_revision='old',reading_mode='romaji',labels=['キ'],
                     fields={'note-0':'k'},phrase_drafts=[],updated_at=10)
        def post(value):
            with urlopen(Request(self.base+f'/api/jobs/{job.name}/draft',json.dumps(value).encode(),{'X-UtaWeave':'local'})) as r:return json.load(r)
        self.assertEqual(post(payload)['sequence'],2)
        self.assertEqual(post(dict(payload,sequence=1,fields={'note-0':'old'}))['sequence'],2)
        post(dict(payload,client_id='b'*32,fields={'note-0':'u'}))
        with urlopen(self.base+f'/api/jobs/{job.name}/drafts') as r:self.assertEqual(len(json.load(r)),2)
        with urlopen(self.base+f'/api/jobs/{job.name}/drafts/'+('a'*32)) as r:self.assertEqual(json.load(r)['fields']['note-0'],'k')
        self.assertEqual(json.loads((job/'prepared.json').read_text()),original)
        self.assertFalse((job/'process.json').exists())
        for patch in [dict(job_id='f'*32),dict(client_id='../x'),dict(sequence=0),dict(labels=[None])]:
            with self.assertRaises(HTTPError):post(dict(payload,**patch))
        with self.assertRaises(HTTPError):urlopen(self.base+f'/files/{job.name}/drafts/'+('a'*32)+'.json')
