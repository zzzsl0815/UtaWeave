from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import subprocess
import time
from threading import Thread
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from song2voice.web.server import Handler
from song2voice.web.workflow import failure_message


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ['uploads','jobs']:(self.root/name).mkdir()
        self.cfg=dict(workspace=str(self.root))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,config=self.cfg))
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def test_page_and_post_origin_and_private_artifacts(self):
        with urlopen(self.base+'/') as r:self.assertIn('MIDI + 歌词',r.read().decode())
        for headers in [{},{'X-UtaWeave':'local','Origin':'https://other.example'}]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.base+'/api/jobs',b'{}',headers))
            self.assertEqual(error.exception.code,400)
        job=self.root/'jobs'/('a'*32);job.mkdir();(job/'render.local.json').write_text('{"private":"path"}')
        with self.assertRaises(HTTPError) as error:urlopen(self.base+'/files/'+job.name+'/render.local.json')
        self.assertEqual(error.exception.code,404)
        outside=self.root/'private.wav';outside.write_bytes(b'secret');(job/'leak.wav').symlink_to(outside)
        with self.assertRaises(HTTPError) as error:urlopen(self.base+'/files/'+job.name+'/leak.wav')
        self.assertEqual(error.exception.code,404)

    def test_overflow_draft_is_rejected_before_any_worker_launch(self):
        job=self.root/'jobs'/('b'*32);job.mkdir()
        (job/'prepared.json').write_text(json.dumps(dict(labels=['き','み'],unmatched_moras=1)))
        payload=json.dumps(dict(labels=['き','み'],confirmed=True)).encode()
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base+'/api/jobs/'+job.name+'/render',payload,{'X-UtaWeave':'local'}))
        self.assertIn('未分配',error.exception.read().decode())
        self.assertFalse((job/'process.json').exists())

    def test_stale_page_cannot_submit_old_lyrics_after_preparing_again(self):
        job=self.root/'jobs'/('d'*32);job.mkdir()
        (job/'prepared.json').write_text(json.dumps(dict(labels=['き'],revision='new-revision')))
        for revision in [None,'old-revision']:
            payload=json.dumps(dict(labels=['み'],confirmed=True,prepared_revision=revision)).encode()
            for action in ['render','align','segments']:
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(self.base+'/api/jobs/'+job.name+'/'+action,payload,{'X-UtaWeave':'local'}))
                self.assertIn('版本过期',error.exception.read().decode())
        self.assertFalse((job/'process.json').exists())

    def test_midi_segment_preview_is_readonly_and_needs_no_runtime(self):
        job=self.root/'jobs'/('f'*32);job.mkdir()
        prepared=dict(mode='midi',notes=[dict(start=1+i,end=1.5+i,pitch=60) for i in range(6)],
                      labels=['ア']*6,bpm=120,revision='current')
        raw=json.dumps(prepared);(job/'prepared.json').write_text(raw)
        payload=json.dumps(dict(labels=prepared['labels'],prepared_revision='current')).encode()
        with urlopen(Request(self.base+'/api/jobs/'+job.name+'/segments',payload,{'X-UtaWeave':'local'})) as r:
            result=json.load(r)
        self.assertEqual(result['chunk_count'],1)
        self.assertEqual(result['chunks'][0]['midi_note_indices'],list(range(6)))
        self.assertLessEqual(result['longest_condition_seconds'],8.)
        self.assertEqual(list(job.iterdir()),[job/'prepared.json'])
        self.assertEqual((job/'prepared.json').read_text(),raw)
        payload=json.dumps(dict(labels=prepared['labels'],prepared_revision='current',connect_short_rests='false')).encode()
        with self.assertRaises(HTTPError):
            urlopen(Request(self.base+'/api/jobs/'+job.name+'/segments',payload,{'X-UtaWeave':'local'}))

    def test_worker_surfaces_current_stage_error_and_ignores_stale_runs(self):
        job=self.root/'jobs'/('c'*32);job.mkdir()
        state=dict(stage='render',updated_at=time.time()-1)
        (job/'status.json').write_text(json.dumps(state))
        run=job/'render/example';run.mkdir(parents=True)
        (run/'pipeline_status.json').write_text(json.dumps(dict(error='generate 阶段失败：invalid tempo')))
        error=subprocess.CalledProcessError(1,['pipeline'])
        self.assertIn('invalid tempo',failure_message(job,error))
        state['updated_at']=time.time()+1
        (job/'status.json').write_text(json.dumps(state))
        self.assertEqual(failure_message(job,error),str(error))
